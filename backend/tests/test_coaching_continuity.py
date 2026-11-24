"""Quoting any page, conversation context on revisions, and question frames."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-continuity-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, uid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nit_engine"))
from runtime import coaching as engine  # noqa: E402

FRAMES = ["공포 소구가 ___ 상황에서 더 효과적인 이유는 무엇일까요?",
          "공포 소구와 ___를 어떤 기준으로 비교할 수 있을까요?"]


def coached(level, follow_up="두 개념을 비교하면 어떤 차이가 있을까요?"):
    return {"mode": "MODEL_VALIDATED", "question_level": level, "follow_up_question": follow_up,
            "feedback": "질문의 근거와 전제를 구체화해 보세요.", "evidence_refs": [],
            "question_frames": FRAMES}


class ContinuityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def setUp(self):
        folder = Path(TEMP.name) / ("lesson-" + uid())
        folder.mkdir(parents=True)
        (folder / "document.json").write_text(json.dumps({"pages": [
            {"page": n, "blocks": [], **({"hidden_slide": True} if n == 4 else {})} for n in range(1, 7)
        ]}), encoding="utf-8")
        with SessionLocal() as db:
            faculty = User(email=f"cont-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="설득 커뮤니케이션", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="c" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            course_id, material_id = course.id, material.id
        self.faculty = issue_token(faculty.id)
        with patch.object(nit, "verified_count", return_value=1):
            opened = self.req("POST", f"/v1/courses/{course_id}/sessions", self.faculty,
                              json={"material_id": material_id}).json()
        self.sid = opened["id"]
        self.req("PUT", f"/v1/sessions/{self.sid}/chat", self.faculty, json={"open": True})
        self.student = self.req("POST", "/v1/join", json={"code": opened["join_code"]}).json()["access_token"]

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def ask(self, text, **extra):
        with patch.object(nit, "coach_question", return_value=coached("clarify")) as coach:
            res = self.req("POST", f"/v1/sessions/{self.sid}/questions", self.student,
                           json={"question": text, **extra})
        return res, coach

    def test_a_quote_can_come_from_any_visible_page_without_a_live_page(self):
        anchor = {"x": .4, "y": .3, "quote": "공포 소구의 효과", "rects": []}
        for page in (2, 6):
            res, _ = self.ask("이 문구에서 말하는 효과는 왜 생기나요?", slide_page=page, anchor=anchor)
            self.assertEqual(res.status_code, 201, res.text)
        mine = self.req("GET", f"/v1/sessions/{self.sid}/mine", self.student).json()["items"]
        self.assertEqual(sorted(item["slide_page"] for item in mine), [2, 6])
        for page in (4, 9):
            res, coach = self.ask("이 문구에서 말하는 효과는 왜 생기나요?", slide_page=page, anchor=anchor)
            self.assertEqual(res.status_code, 422, page)
            coach.assert_not_called()

    def test_a_revision_sends_the_first_question_and_the_last_exchange(self):
        res, coach = self.ask("공포 소구란 무엇인가요?")
        first = res.json()
        self.assertNotIn("history", coach.call_args.kwargs)
        self.assertEqual(first["coaching"]["question_frames"], FRAMES)
        with patch.object(nit, "coach_question", return_value=coached("analyze")) as coach:
            revised = self.req("POST", f"/v1/session-questions/{first['id']}/revisions", self.student,
                               json={"text": "공포 소구가 청소년에게 더 효과적인 이유는 무엇인가요?"})
        self.assertEqual(revised.status_code, 201, revised.text)
        self.assertEqual(coach.call_args.kwargs["history"], {
            "first_question": "공포 소구란 무엇인가요?",
            "previous_question": "공포 소구란 무엇인가요?",
            "previous_follow_up": "두 개념을 비교하면 어떤 차이가 있을까요?",
            "previous_level": "shallow", "turn": 2})
        with patch.object(nit, "coach_question", return_value=coached("evaluate")) as coach:
            self.req("POST", f"/v1/session-questions/{first['id']}/revisions", self.student,
                     json={"text": "공포 소구와 유머 소구 중 어느 쪽이 더 효과적일지 어떤 기준으로 판단할까요?"})
        history = coach.call_args.kwargs["history"]
        self.assertEqual(history["first_question"], "공포 소구란 무엇인가요?")
        self.assertEqual(history["previous_question"], "공포 소구가 청소년에게 더 효과적인 이유는 무엇인가요?")
        self.assertEqual((history["previous_level"], history["turn"]), ("mid", 3))

    def test_a_starter_draft_shows_coaching_and_can_be_revised_onto_the_board(self):
        res, coach = self.ask("공포 소구 설명 부탁드립니다")
        draft = res.json()
        coach.assert_called_once()
        self.assertTrue(draft["starter"])
        self.assertEqual(draft["coaching"]["question_frames"], FRAMES)
        self.assertEqual(self.req("POST", f"/v1/session-questions/{draft['id']}/submit", self.student).status_code, 409)
        with patch.object(nit, "coach_question", return_value=coached("analyze")):
            revised = self.req("POST", f"/v1/session-questions/{draft['id']}/revisions", self.student,
                               json={"text": "공포 소구가 청소년에게 더 효과적인 이유는 무엇인가요?"}).json()
        self.assertFalse(revised["starter"])
        self.assertNotEqual(revised["suitability"]["status"], "needs_revision")


class EngineFrameTests(unittest.TestCase):
    def test_frames_are_optional_and_cleaned(self):
        base = {"schema_version": engine.SCHEMA, "question_level": "clarify", "evidence_ids": [],
                "follow_up_question": "어떤 상황을 먼저 떠올리면 좋을까요?"}
        self.assertNotIn("question_frames", engine.validate_model_response(json.dumps(base), []))
        raw = {**base, "question_frames": [*FRAMES]}
        self.assertEqual(engine.validate_model_response(json.dumps(raw), [])["question_frames"], FRAMES)
        with self.assertRaises(engine.InvalidModelOutput):
            engine.validate_model_response(json.dumps({**base, "extra": 1}), [])

    def test_clean_frames_drops_leaks_and_non_frames(self):
        cleaned = engine._clean_frames([
            "정답은 ( )입니다?",                       # answer leak
            "공포 소구가 효과적인 이유는 무엇일까요?",   # no blank
            "공포 소구와 [ ]를 어떤 기준으로 비교할 수 있을까요?",
            "공포 소구와 [ ]를 어떤 기준으로 비교할 수 있을까요?",
        ], "공포 소구가 뭐예요?", [])
        self.assertEqual(cleaned, ["공포 소구와 ___를 어떤 기준으로 비교할 수 있을까요?"])
        self.assertEqual(engine._clean_frames("not a list", "질문", []), [])

    def test_history_reaches_the_model_prompt(self):
        seen = {}

        class Gateway:
            def generate(self, system, user):
                if "user" not in seen:  # the retry appends a correction line
                    seen["system"], seen["user"] = system, json.loads(user)
                raise engine.GatewayFailure("stop after capture")

        evidence = [{"page": 1, "block_id": "b1", "text": "공포 소구는 위협을 강조해 태도 변화를 이끄는 방법이다."}]
        with patch.object(engine, "signed_evidence_coach", return_value={"evidence": evidence}):
            result = engine.run_coaching("out", "공포 소구가 왜 효과적인가요?", "PERMITTED", True, "k" * 32,
                                         "student", Gateway(), history={"first_question": "공포 소구란?",
                                                                        "previous_follow_up": "어디에 쓰일까요?",
                                                                        "turn": 2, "ignored": "x"})
        self.assertIn("conversation", seen["user"])
        self.assertEqual(seen["user"]["conversation"], {"first_question": "공포 소구란?",
                                                       "previous_follow_up": "어디에 쓰일까요?", "turn": 2})
        self.assertIn(engine.SYSTEM_HISTORY, seen["system"])
        self.assertEqual(result["question_frames"], [])


if __name__ == "__main__":
    unittest.main()
