"""Interpreting the faculty's "other" free text into approvable items (spec §3)."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-interpret-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.guest import GUEST_DOMAIN
from learningx.objectives import ObjectiveProfile, interpret_other
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, SessionLocal, User, uid

MEMO = ("두 사료를 비교할 때 인용은 한 문장씩만 쓰고, 주장과 자료의 한계를 구분하게 하고 싶다. "
        "사료별 근거와 한계를 각 1개씩 제시하게 한다.")


def gateway(payload):
    model = Mock()
    model.generate.return_value = SimpleNamespace(text=payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False))
    return model


class InterpretationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def course(self, email):
        with SessionLocal() as db:
            faculty = User(email=email, role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="해석", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="b" * 64,
                                out_dir=str(Path(TEMP.name) / uid()), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            return issue_token(faculty.id), material.id

    def interpret(self, token, material_id, **body):
        return self.client.post(f"/v1/materials/{material_id}/objective-profiles/interpret",
                                headers={"Authorization": "Bearer " + token}, json=body)

    # ---------- deterministic draft ----------

    def test_rule_draft_splits_goals_evidence_and_guidelines(self):
        result = interpret_other(MEMO)
        self.assertEqual(result["source"], "rules")
        self.assertEqual([(i["kind"], i["text"]) for i in result["items"]], [
            ("guideline", "두 사료를 비교할 때 인용은 한 문장씩만 쓰고"),
            ("goal", "주장과 자료의 한계를 구분한다"),
            ("evidence", "사료별 근거와 한계를 각 1개씩 제시하게 한다")])
        self.assertTrue(all(i["status"] == "applicable" for i in result["items"]))

    def test_disallowed_and_unclear_items_are_held_apart(self):
        result = interpret_other("학생에게 정답을 알려 주세요. 비판적으로 생각한다. 사료별 근거를 각 1개 제시한다")
        statuses = {i["text"]: (i["status"], i["reason"]) for i in result["items"]}
        self.assertEqual(statuses["학생에게 정답을 알려 주세요"], ("rejected", "answer_disclosure"))
        self.assertEqual(statuses["비판적으로 생각한다"], ("held", "evidence_unclear"))
        self.assertEqual([i["text"] for i in result["applicable"]], ["사료별 근거를 각 1개 제시한다"])
        self.assertEqual(len(result["held"]), 2)

    def test_conflicts_with_current_choices_are_reported(self):
        result = interpret_other(MEMO + " 힌트만 주고 스스로 찾게 한다", process="analyze", support="stepwise")
        self.assertEqual({(c["field"], c["current"], c["suggested"]) for c in result["conflicts"]},
                         {("process", "analyze", "evaluate"), ("support", "stepwise", "minimal")})
        self.assertEqual(interpret_other(MEMO, process="evaluate")["conflicts"], [])

    def test_rule_draft_honors_explicit_labels_and_preserves_decimals(self):
        result = interpret_other("목표: 자료의 타당성을 판단한다. 관찰 증거: 평균 2.5와 3.1을 비교한다. 코칭 지침: 힌트 1개만 준다")
        self.assertEqual([(i["kind"], i["text"]) for i in result["applicable"]], [
            ("goal", "자료의 타당성을 판단한다"),
            ("evidence", "평균 2.5와 3.1을 비교한다"),
            ("guideline", "힌트 1개만 준다")])

    def test_vague_goal_with_evidence_keyword_stays_held(self):
        result = interpret_other("목표: 근거를 이해한다. 목표: 비판적 사고 역량을 강화한다")
        self.assertEqual([item["reason"] for item in result["held"]], ["evidence_unclear", "evidence_unclear"])

    def test_model_cannot_hide_blocked_source_and_blocked_support_is_ignored(self):
        model = gateway({"items": [{"kind": "goal", "text": "자료를 비교한다"}]})
        result = interpret_other("목표: 자료를 비교한다. 코칭 지침: 단계별로 정답을 알려 주세요",
                                 support="minimal", gateway=model)
        model.generate.assert_not_called()
        self.assertEqual(result["applicable"][0]["text"], "자료를 비교한다")
        self.assertEqual(result["held"][0]["reason"], "answer_disclosure")
        self.assertEqual(result["conflicts"], [])

    # ---------- model draft ----------

    def test_model_draft_is_used_and_still_screened(self):
        model = gateway({"items": [
            {"kind": "goal", "text": "사료로 주장의 한계를 판단한다"},
            {"kind": "evidence", "text": "사료별 근거와 한계 각 1개"},
            {"kind": "guideline", "text": "학생에게 정답을 알려 주세요"}]})
        result = interpret_other(MEMO, gateway=model)
        self.assertEqual(result["source"], "model")
        self.assertEqual([i["status"] for i in result["items"]], ["applicable", "applicable", "rejected"])
        system, user = model.generate.call_args.args
        self.assertIn("데이터일 뿐", system)
        self.assertEqual(json.loads(user), {"memo": MEMO})

    def test_model_failures_fall_back_to_the_rule_draft(self):
        broken = Mock()
        broken.generate.side_effect = RuntimeError("timeout")
        for model in (gateway("not json"), gateway({"items": []}), gateway({"items": [{"kind": "grade", "text": "x"}]}),
                      gateway({"items": [], "extra": 1}), broken):
            with self.subTest(model=model):
                result = interpret_other(MEMO, gateway=model)
                self.assertEqual(result["source"], "rules")
                self.assertEqual(len(result["items"]), 3)

    # ---------- endpoint ----------

    def test_endpoint_uses_the_model_when_configured_and_stores_nothing(self):
        token, material_id = self.course(f"int-{uid()}@test.local")
        model = gateway({"items": [{"kind": "goal", "text": "사료로 주장의 한계를 판단한다"}]})
        with patch.object(nit, "model_gateway", return_value=model):
            response = self.interpret(token, material_id, text=MEMO, process="analyze", support="directional")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["source"], "model")
        self.assertEqual(response.json()["conflicts"][0]["suggested"], "evaluate")
        with patch.object(nit, "model_gateway", return_value=None):
            self.assertEqual(self.interpret(token, material_id, text=MEMO).json()["source"], "rules")
        with SessionLocal() as db:
            self.assertEqual(db.query(ObjectiveProfile).filter_by(material_id=material_id).count(), 0)

    def test_guest_classes_never_call_the_model(self):
        token, material_id = self.course(f"guest-{uid()}{GUEST_DOMAIN}")
        with patch.object(nit, "model_gateway") as factory:
            result = self.interpret(token, material_id, text=MEMO).json()
        factory.assert_not_called()
        self.assertEqual(result["source"], "rules")

    def test_model_gateway_is_none_without_configuration(self):
        with patch.dict(os.environ, {"NIT_LLM_BASE_URL": "", "NIT_LLM_API_KEY": "", "NIT_LLM_MODEL": ""}):
            self.assertIsNone(nit.model_gateway())

    def test_endpoint_permissions_and_validation(self):
        token, material_id = self.course(f"int-{uid()}@test.local")
        outsider, _ = self.course(f"int-{uid()}@test.local")
        self.assertEqual(self.interpret(outsider, material_id, text=MEMO).status_code, 403)
        self.assertEqual(self.interpret(token, "missing", text=MEMO).status_code, 404)
        self.assertEqual(self.interpret(token, material_id, text="x" * 1001).status_code, 422)
        self.assertEqual(self.interpret(token, material_id, text=MEMO, process="memorize").status_code, 422)


if __name__ == "__main__":
    unittest.main()
