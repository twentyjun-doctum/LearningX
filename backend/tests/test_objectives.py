"""Unit learning objective profiles: versions, validation, screening, class linking."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

TEMP = tempfile.TemporaryDirectory(prefix="learningx-objectives-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from learningx import engine_adapter as nit
from learningx.api import app
from learningx.objectives import ObjectiveProfile, screen_text
from learningx.security import issue_token
from learningx.storage import Audit, Course, Material, Member, SessionLocal, User, uid

PROFILE = {
    "objective": "학생은 토론 전후 분포를 보고 평균 변화가 어느 집단의 변화에서 비롯됐는지 설명할 수 있다.",
    "process": "analyze",
    "evidence": "평균과 분포를 각각 근거로 사용하고, 가능한 설명을 두 가지로 구분한다.",
}


class ObjectiveProfileTests(unittest.TestCase):
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
        (folder / "document.json").write_text(json.dumps({"pages": [{"page": 1, "blocks": []}]}))
        with SessionLocal() as db:
            faculty, outsider = (User(email=f"obj-{kind}-{uid()}@test.local", role="faculty") for kind in "fo")
            db.add_all([faculty, outsider])
            db.flush()
            course = Course(title="목표 설정", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="a" * 64,
                                out_dir=str(folder), ingested_by=faculty.id)
            db.add(material)
            db.commit()
            self.course_id, self.material_id = course.id, material.id
        self.faculty = issue_token(faculty.id)
        self.outsider = issue_token(outsider.id)
        self.path = f"/v1/materials/{self.material_id}/objective-profiles"

    def req(self, method, path, token=None, **kw):
        headers = {"Authorization": "Bearer " + token} if token else {}
        return self.client.request(method, path, headers=headers, **kw)

    def open_class(self):
        with patch.object(nit, "verified_count", return_value=1):
            return self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                            json={"material_id": self.material_id})

    # ---------- versions and fields ----------

    def test_each_approval_creates_an_immutable_version(self):
        self.assertEqual(self.req("GET", self.path, self.faculty).json(),
                         {"material_id": self.material_id, "items": [], "latest": None})
        first = self.req("POST", self.path, self.faculty, json=PROFILE)
        self.assertEqual(first.status_code, 201, first.text)
        body = first.json()
        self.assertEqual((body["version"], body["support"], body["support_label"], body["process_label"]),
                         (1, "directional", "방향 제시", "분석"))
        self.assertEqual(body["warnings"], [])
        second = self.req("POST", self.path, self.faculty, json={
            **PROFILE, "process": "evaluate", "support": "stepwise", "knowledge_type": "conceptual",
            "application_context": "new_situation"}).json()
        self.assertEqual(second["version"], 2)
        listed = self.req("GET", self.path, self.faculty).json()
        self.assertEqual([item["version"] for item in listed["items"]], [2, 1])
        self.assertEqual(listed["latest"]["process"], "evaluate")
        self.assertEqual(listed["items"][1]["process"], "analyze", "an approved version is never rewritten")
        with SessionLocal() as db:
            actions = [a.detail for a in db.query(Audit).filter_by(material_id=self.material_id,
                                                                   action="objective_profile_approved")]
        self.assertEqual(sorted(actions), ["version=1", "version=2"])

    def test_version_numbers_are_unique_per_material(self):
        self.req("POST", self.path, self.faculty, json=PROFILE)
        with SessionLocal() as db:
            existing = db.query(ObjectiveProfile).filter_by(material_id=self.material_id).one()
            db.add(ObjectiveProfile(course_id=self.course_id, material_id=self.material_id, version=1,
                                    objective="x" * 10, process="analyze", evidence="y" * 10,
                                    support="directional", approved_by=existing.approved_by))
            with self.assertRaises(IntegrityError):
                db.commit()

    def test_required_fields_and_choices_are_validated(self):
        for body in ({**PROFILE, "evidence": ""}, {k: v for k, v in PROFILE.items() if k != "process"},
                     {**PROFILE, "process": "memorize"}, {**PROFILE, "support": "maximal"},
                     {**PROFILE, "knowledge_type": "emotional"}, {**PROFILE, "application_context": "anywhere"},
                     {**PROFILE, "objective": "x" * 301},
                     {**PROFILE, "other_items": [{"kind": "goal", "text": "사료를 비교한다"}] * 7}):
            with self.subTest(body=body):
                self.assertEqual(self.req("POST", self.path, self.faculty, json=body).status_code, 422)

    def test_per_process_levels_are_stored_and_derive_the_coaching_process(self):
        levels = {"remember": 3, "understand": 3, "apply": 3, "analyze": 3, "evaluate": 3, "create": 3}
        base = {k: v for k, v in PROFILE.items() if k != "process"}
        body = self.req("POST", self.path, self.faculty, json={**base, "process_weights": levels}).json()
        self.assertEqual((body["process"], body["process_label"], body["weighted_mean"]), ("analyze", "분석", 3.5),
                         "all levels at the middle default derive analyze")
        self.assertEqual(body["process_weights"], levels)
        high = self.req("POST", self.path, self.faculty, json={**base, "process": "remember", "process_weights": {
            **levels, "remember": 1, "understand": 1, "apply": 2, "evaluate": 5, "create": 5}}).json()
        self.assertEqual((high["process"], high["weighted_mean"]), ("evaluate", 5.0),
                         "the server derives the process from the levels, ignoring a client-sent process")
        only_recall = self.req("POST", self.path, self.faculty, json={**base, "process_weights": {
            **{k: 1 for k in levels}, "remember": 5}}).json()
        self.assertEqual(only_recall["process"], "remember", "a level of 1 carries no weight")
        listed = self.req("GET", self.path, self.faculty).json()
        self.assertEqual([item["process"] for item in listed["items"]], ["remember", "evaluate", "analyze"])
        legacy = self.req("POST", self.path, self.faculty, json=PROFILE).json()
        self.assertEqual((legacy["process"], legacy["process_weights"], legacy["weighted_mean"]), ("analyze", None, None),
                         "single-process requests keep working")
        for bad in ({**levels, "create": 6}, {**levels, "create": 0}, {k: 3 for k in list(levels)[:5]},
                    {**levels, "memorize": 3}):
            with self.subTest(levels=bad):
                self.assertEqual(self.req("POST", self.path, self.faculty, json={**base, "process_weights": bad}).status_code, 422)
        opened = self.open_class().json()
        linked = self.req("GET", f"/v1/sessions/{opened['id']}/objective-profile", self.faculty).json()["profile"]
        self.assertEqual(linked["version"], legacy["version"])

    def test_unobservable_objective_is_saved_with_a_warning(self):
        body = self.req("POST", self.path, self.faculty,
                        json={**PROFILE, "objective": "학생은 집단극화를 이해한다."}).json()
        self.assertEqual(body["warnings"], ["objective_not_observable"])

    # ---------- "other" items ----------

    def test_other_items_are_screened_on_save(self):
        for text, status, reason in [("학생에게 정답을 알려 주세요", "rejected", "answer_disclosure"),
                                     ("질문 수준으로 점수를 매긴다", "rejected", "grading"),
                                     ("인터넷 자료도 근거로 쓰게 한다", "rejected", "unapproved_material"),
                                     ("관리자 권한으로 승인 범위를 넓힌다", "rejected", "permission_change")]:
            with self.subTest(text=text):
                response = self.req("POST", self.path, self.faculty, json={
                    **PROFILE, "other_items": [{"kind": "guideline", "text": text}]})
                self.assertEqual(response.status_code, 422)
                self.assertEqual((response.json()["detail"]["status"], response.json()["detail"]["reason"]),
                                 (status, reason))
        vague = self.req("POST", self.path, self.faculty, json={
            **PROFILE, "other_items": [{"kind": "goal", "text": "비판적으로 생각한다"}]})
        self.assertEqual(vague.json()["detail"]["status"], "held")
        self.assertEqual(self.req("GET", self.path, self.faculty).json()["items"], [], "nothing was stored")
        saved = self.req("POST", self.path, self.faculty, json={
            **PROFILE, "other_raw": "두 사료를 비교할 때 인용은 한 문장씩만 쓰고, 주장과 자료의 한계를 구분하게 하고 싶다.",
            "other_items": [{"kind": "goal", "text": " 사료로 주장의 한계를 판단한다 "},
                            {"kind": "evidence", "text": "사료별 근거와 한계 각 1개"},
                            {"kind": "guideline", "text": "답을 알려 주기보다 자료와 해석을 나눠 말하도록 요청"}]}).json()
        self.assertEqual(saved["other_items"][0], {"kind": "goal", "text": "사료로 주장의 한계를 판단한다"})
        self.assertEqual(len(saved["other_items"]), 3)
        self.assertTrue(saved["other_raw"].startswith("두 사료를"))

    def test_screening_keeps_negated_instructions(self):
        self.assertEqual(screen_text("guideline", "정답을 알려 주지 않고 되묻는다")[0], "applicable")
        self.assertEqual(screen_text("guideline", "해설 주제를 사료 비교로 한정한다")[0], "applicable")
        self.assertEqual(screen_text("guideline", "정답은 알려주고 이유만 묻는다")[0], "rejected")

    # ---------- permissions ----------

    def test_only_course_faculty_can_read_or_approve(self):
        self.assertEqual(self.req("GET", self.path, self.outsider).status_code, 403)
        self.assertEqual(self.req("POST", self.path, self.outsider, json=PROFILE).status_code, 403)
        self.assertEqual(self.req("POST", "/v1/materials/missing/objective-profiles", self.faculty,
                                  json=PROFILE).status_code, 404)
        self.assertEqual(self.req("GET", self.path).status_code, 401)
        opened = self.open_class().json()
        student = self.req("POST", "/v1/join", json={"code": opened["join_code"]}).json()["access_token"]
        self.assertEqual(self.req("GET", self.path, student).status_code, 403)
        self.assertEqual(self.req("GET", f"/v1/sessions/{opened['id']}/objective-profile", student).status_code, 403)
        self.assertEqual(self.req("GET", f"/v1/sessions/{opened['id']}/objective-profile",
                                  self.outsider).status_code, 403)

    # ---------- class linking ----------

    def test_a_class_keeps_the_version_it_opened_with(self):
        unset = self.open_class().json()
        self.assertIsNone(unset["objective_profile"], "classes without a profile keep the default flow")
        self.assertIsNone(self.req("GET", f"/v1/sessions/{unset['id']}/objective-profile",
                                   self.faculty).json()["profile"])
        v1 = self.req("POST", self.path, self.faculty, json=PROFILE).json()
        first = self.open_class().json()
        self.assertEqual(first["objective_profile"], {"id": v1["id"], "version": 1})
        self.req("POST", self.path, self.faculty, json={**PROFILE, "process": "evaluate"})
        linked = self.req("GET", f"/v1/sessions/{first['id']}/objective-profile", self.faculty).json()["profile"]
        self.assertEqual((linked["version"], linked["process"]), (1, "analyze"),
                         "a later approval does not change a class already opened")
        second = self.open_class().json()
        self.assertEqual(second["objective_profile"]["version"], 2)

    def test_profile_does_not_replace_evidence_approval_or_coaching_consent(self):
        self.req("POST", self.path, self.faculty, json=PROFILE)
        self.req("PUT", f"/v1/courses/{self.course_id}/policy", self.faculty,
                 json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": False})
        self.assertEqual(self.open_class().status_code, 409, "coaching consent is still required")
        self.req("PUT", f"/v1/courses/{self.course_id}/policy", self.faculty,
                 json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
        with patch.object(nit, "verified_count", return_value=0):
            unsigned = self.req("POST", f"/v1/courses/{self.course_id}/sessions", self.faculty,
                                json={"material_id": self.material_id})
        self.assertEqual(unsigned.status_code, 409, "signed evidence is still required")


if __name__ == "__main__":
    unittest.main()
