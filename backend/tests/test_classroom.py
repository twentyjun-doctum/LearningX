"""Live classroom session flow (frozen engine mocked; ACL and anonymity are real)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-classroom-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient
from learningx.api import app
from learningx.storage import SessionLocal, User, Course, Member, Material, Question
from learningx.security import issue_token
from learningx import engine_adapter as nit
from learningx import classroom


def coaching(level):
    return {"mode": "MODEL_VALIDATED", "question_level": level,
            "follow_up_question": "두 개념을 비교하면 어떤 차이가 있을까요?",
            "feedback": "질문의 근거와 전제를 구체화해 보세요.",
            "evidence_refs": [{"id": "p3:b1", "page": 3}],
            "model_observability": {"provider": "secret-provider", "model": "x"}}


class ClassroomTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()
        out = Path(TEMP.name) / "private" / "mat"
        out.mkdir(parents=True, exist_ok=True)
        with SessionLocal() as db:
            fac = User(email="cls-faculty@test.local", role="faculty")
            other = User(email="cls-other@test.local", role="faculty")
            db.add_all([fac, other])
            db.flush()
            course = Course(title="의제설정 이론", policy="RESTRICTED_HINT_ONLY", teacher_approved=False, created_by=fac.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=fac.id, role="faculty"))
            mat = Material(course_id=course.id, filename="lesson.pdf", sha256="a" * 64, out_dir=str(out), ingested_by=fac.id)
            db.add(mat)
            db.commit()
            cls.fac_id, cls.other_id, cls.course_id, cls.material_id = fac.id, other.id, course.id, mat.id
        cls.tf = issue_token(cls.fac_id)
        cls.to = issue_token(cls.other_id)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def test_full_flow(self):
        cid, mid = self.course_id, self.material_id
        # Coaching consent is required before opening.
        with patch.object(nit, "verified_count", return_value=3):
            r = self.req("POST", f"/v1/courses/{cid}/sessions", self.tf, json={"material_id": mid})
        self.assertEqual(r.status_code, 409, r.text)
        self.req("PUT", f"/v1/courses/{cid}/policy", self.tf, json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
        # Unsigned material cannot open a session.
        with patch.object(nit, "verified_count", return_value=0):
            r = self.req("POST", f"/v1/courses/{cid}/sessions", self.tf, json={"material_id": mid})
        self.assertEqual(r.status_code, 409)
        # Non-member faculty cannot open.
        with patch.object(nit, "verified_count", return_value=3):
            self.assertEqual(self.req("POST", f"/v1/courses/{cid}/sessions", self.to, json={"material_id": mid}).status_code, 403)
            r = self.req("POST", f"/v1/courses/{cid}/sessions", self.tf, json={"material_id": mid})
        self.assertEqual(r.status_code, 201, r.text)
        sess = r.json()
        code, sid = sess["join_code"], sess["id"]
        self.assertEqual(len(code), 6)
        self.assertTrue(all(ch in classroom.JOIN_ALPHABET for ch in code))

        # Bad code is rejected; lowercase input works.
        self.assertEqual(self.req("POST", "/v1/join", json={"code": "ZZZZZZ"}).status_code, 404)
        j = self.req("POST", "/v1/join", json={"code": code.lower()})
        self.assertEqual(j.status_code, 200, j.text)
        ts = j.json()["access_token"]
        self.assertNotIn("join_code", j.json()["session"])
        j2 = self.req("POST", "/v1/join", json={"code": code})
        ts2 = j2.json()["access_token"]

        # Student cannot see the faculty board or export.
        self.assertEqual(self.req("GET", f"/v1/sessions/{sid}/board", ts).status_code, 403)
        self.assertEqual(self.req("GET", f"/v1/sessions/{sid}/export", ts).status_code, 403)

        # Ask → shallow, revise → mid → deep, submit.
        with patch.object(nit, "coach_question", return_value=coaching("clarify")):
            a = self.req("POST", f"/v1/sessions/{sid}/questions", ts, json={"question": "의제설정 이론은 누가 만들었나요?"})
        self.assertEqual(a.status_code, 201, a.text)
        body = a.json()
        self.assertEqual(body["level"], "shallow")
        self.assertNotIn("model_observability", body["coaching"])
        self.assertNotIn("id", body["coaching"]["evidence_refs"][0])
        rid = body["id"]
        with patch.object(nit, "coach_question", return_value=coaching("analyze")):
            r = self.req("POST", f"/v1/session-questions/{rid}/revisions", ts, json={"text": "의제설정 이론과 프레이밍 이론은 무엇이 다른가요?"})
        self.assertEqual(r.json()["level"], "mid")
        with patch.object(nit, "coach_question", return_value=coaching("evaluate")):
            r = self.req("POST", f"/v1/session-questions/{rid}/revisions", ts,
                         json={"text": "의제설정 이론을 유튜브 추천 알고리즘에 적용하면 언론의 영향력은 어떻게 달라지나요?"})
        self.assertEqual(r.json()["level"], "deep")
        # Another student cannot revise or submit someone else's question.
        self.assertEqual(self.req("POST", f"/v1/session-questions/{rid}/submit", ts2).status_code, 404)
        # Unsubmitted questions are invisible on the board.
        b = self.req("GET", f"/v1/sessions/{sid}/board", self.tf).json()
        self.assertEqual(b["items"], [])
        s = self.req("POST", f"/v1/session-questions/{rid}/submit", ts)
        self.assertEqual(s.status_code, 200)
        # Submitted questions cannot be revised.
        self.assertEqual(self.req("POST", f"/v1/session-questions/{rid}/revisions", ts, json={"text": "다른 질문으로 바꿔 볼게요"}).status_code, 409)

        with patch.object(nit, "coach_question", return_value=coaching("clarify")):
            a2 = self.req("POST", f"/v1/sessions/{sid}/questions", ts2, json={"question": "채플힐 연구는 언제 했나요?"})
        rid2 = a2.json()["id"]
        self.req("POST", f"/v1/session-questions/{rid2}/submit", ts2)

        b = self.req("GET", f"/v1/sessions/{sid}/board", self.tf).json()
        self.assertEqual(b["counts"], {"all": 2, "shallow": 1, "mid": 0, "deep": 1})
        self.assertEqual(b["items"][0]["level"], "deep")
        self.assertIsNotNone(b["items"][0]["original"])
        flat = str(b)
        self.assertNotIn("anon+", flat)
        self.assertNotIn("student", flat.lower().replace("students", ""))
        self.assertEqual(b["session"]["participants"], 2)
        self.assertEqual(b["session"]["questions_deep"], 1)
        deep_only = self.req("GET", f"/v1/sessions/{sid}/board?level=deep", self.tf).json()
        self.assertEqual(len(deep_only["items"]), 1)

        # Project, then hide the other; projected endpoint returns the item.
        p = self.req("POST", f"/v1/session-questions/{rid}/project", self.tf)
        self.assertEqual(p.status_code, 200)
        pj = self.req("GET", f"/v1/sessions/{sid}/projected", self.tf).json()
        self.assertEqual(pj["item"]["id"], rid)
        self.assertEqual(pj["next_id"], rid2)
        self.req("POST", f"/v1/session-questions/{rid2}/hide", self.tf)
        b = self.req("GET", f"/v1/sessions/{sid}/board", self.tf).json()
        self.assertEqual(b["counts"]["all"], 1)
        self.assertEqual(self.req("POST", f"/v1/session-questions/{rid2}/project", self.tf).status_code, 409)

        # Student sees only their own questions.
        mine = self.req("GET", f"/v1/sessions/{sid}/mine", ts).json()
        self.assertEqual(len(mine["items"]), 1)
        self.assertTrue(mine["items"][0]["submitted"])

        # Pause blocks new questions and joins.
        self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "PAUSED"})
        with patch.object(nit, "coach_question", return_value=coaching("clarify")):
            self.assertEqual(self.req("POST", f"/v1/sessions/{sid}/questions", ts2, json={"question": "매콤스와 쇼는 누구인가요?"}).status_code, 423)
        self.assertEqual(self.req("POST", "/v1/join", json={"code": code}).status_code, 423)
        self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "OPEN"})

        # Close: code expires, join refused, CSV excludes hidden and identities.
        c = self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "CLOSED"})
        self.assertIsNone(c.json()["join_code"])
        self.assertEqual(self.req("POST", "/v1/join", json={"code": code}).status_code, 404)
        e = self.req("GET", f"/v1/sessions/{sid}/export", self.tf)
        self.assertEqual(e.status_code, 200)
        text = e.content.decode("utf-8-sig")
        self.assertIn("처음 질문", text)
        self.assertIn("유튜브", text)
        self.assertNotIn("채플힐", text)
        self.assertNotIn("anon+", text)
        self.assertEqual(self.req("PUT", f"/v1/sessions/{sid}/state", self.tf, json={"status": "OPEN"}).status_code, 409)

        # Underlying question rows exist for the existing audit trail.
        with SessionLocal() as db:
            self.assertEqual(db.query(Question).filter_by(course_id=cid).count(), 2)

    def test_join_throttle(self):
        classroom._FAILED_JOINS.clear()
        codes = [self.req("POST", "/v1/join", json={"code": "QQQQQQ"}).status_code for _ in range(classroom.JOIN_FAIL_LIMIT + 1)]
        self.assertEqual(codes[-1], 429)
        classroom._FAILED_JOINS.clear()


if __name__ == "__main__":
    unittest.main()
