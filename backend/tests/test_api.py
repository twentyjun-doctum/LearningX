"""Pilot backend authorization and workflow tests (frozen engine mocked only)."""
import hashlib
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

TEMP=tempfile.TemporaryDirectory(prefix="learningx-tests-")
os.environ["DATABASE_URL"]="sqlite:///"+str(Path(TEMP.name)/"test.sqlite3")
os.environ["NIT_JWT_SECRET"]="test-only-signing-key-12345678901234567890"
os.environ["NIT_STORAGE_DIR"]=str(Path(TEMP.name)/"private")

from fastapi.testclient import TestClient
from learningx.api import app
from learningx.storage import SessionLocal, User, Material
from learningx.security import issue_token
from learningx import engine_adapter as nit

class BackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)
        cls.client.__enter__()
        with SessionLocal() as db:
            cls.faculty=User(email="faculty@test.local",role="faculty")
            cls.student=User(email="student@test.local",role="student")
            cls.other=User(email="other@test.local",role="student")
            db.add_all([cls.faculty,cls.student,cls.other])
            db.commit()
            cls.faculty_id=cls.faculty.id
            cls.student_id=cls.student.id
            cls.other_id=cls.other.id
        cls.tf=issue_token(cls.faculty_id)
        cls.ts=issue_token(cls.student_id)
        cls.to=issue_token(cls.other_id)

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None,None,None)

    def req(self,method,path,token=None,**kwargs):
        headers={"Authorization":"Bearer "+token} if token else {}
        return self.client.request(method,path,headers=headers,**kwargs)

    def test_01_auth_and_course_acl(self):
        self.assertEqual(self.req("GET","/health").status_code,200)
        self.assertEqual(self.req("POST","/v1/courses",json={"title":"Test Course"}).status_code,401)
        self.assertEqual(self.req("POST","/v1/courses",self.ts,json={"title":"Test Course"}).status_code,403)
        r=self.req("POST","/v1/courses",self.tf,json={"title":"Pilot Communications","policy":"RESTRICTED_HINT_ONLY"})
        self.assertEqual(r.status_code,201,r.text)
        self.__class__.course_id=r.json()["id"]
        self.assertEqual(self.req("POST",f"/v1/courses/{self.course_id}/members",self.ts,
            json={"user_id":self.other_id}).status_code,403)
        self.assertEqual(self.req("POST",f"/v1/courses/{self.course_id}/members",self.tf,
            json={"user_id":self.student_id}).status_code,201)

    def test_02_material_review_and_student_cannot_approve(self):
        cid=self.course_id
        def fake_ingest(path,out):
            out.mkdir()
            (out/"document.json").write_text("{}",encoding="utf8")
            return {"source":{"sha256":hashlib.sha256(path.read_bytes()).hexdigest()},"pages":[{"page":1}]}
        with patch.object(nit,"ingest",side_effect=fake_ingest):
            r=self.req("POST",f"/v1/courses/{cid}/materials",self.tf,
                files={"file":("lesson.pdf",b"%PDF-1.7\nTEST", "application/pdf")})
        self.assertEqual(r.status_code,201,r.text)
        self.__class__.material_id=r.json()["id"]
        self.assertEqual(self.req("GET",f"/v1/materials/{self.material_id}/candidates",self.ts).status_code,403)
        with patch.object(nit,"candidates",return_value=[{"page":1,"block_id":"b1","block_sha256":"a"*64}]),patch.object(nit,"verified_count",return_value=0):
            r=self.req("GET",f"/v1/materials/{self.material_id}/candidates",self.tf)
            self.assertEqual(r.status_code,200)
        data={"items":[{"page":1,"block_id":"b1","block_sha256":"a"*64}]}
        self.assertEqual(self.req("POST",f"/v1/materials/{self.material_id}/approve",self.ts,json=data).status_code,403)
        with patch.object(nit,"approve_material",return_value={"approved":1}):
            r=self.req("POST",f"/v1/materials/{self.material_id}/approve",self.tf,json=data)
            self.assertEqual(r.status_code,200)

    def test_03_coach_ownership_and_revision(self):
        cid=self.course_id;mid=self.material_id
        payload={"material_id":mid,"question":"커뮤니케이션 정의의 전제를 어떻게 확인할까요?"}
        self.assertEqual(self.req("POST",f"/v1/courses/{cid}/questions",self.to,json=payload).status_code,403)
        self.assertEqual(self.req("POST",f"/v1/courses/{cid}/questions",self.tf,json=payload).status_code,403)
        with patch.object(nit,"coach_question",return_value={"mode":"DETERMINISTIC_FALLBACK",
            "evidence_refs":[],"follow_up_question":"다른 기준은 무엇일까요?"}) as mock:
            r=self.req("POST",f"/v1/courses/{cid}/questions",self.ts,json=payload)
            self.assertEqual(r.status_code,201,r.text)
            self.assertEqual(mock.call_count,1)
        qid=r.json()["question_id"]
        self.assertEqual(self.req("POST",f"/v1/questions/{qid}/revisions",self.to,
            json={"text":"수정 질문입니다. 비교할 기준은 무엇인가요?"}).status_code,403)
        self.assertEqual(self.req("POST",f"/v1/questions/{qid}/revisions",self.ts,
            json={"text":"수정 질문입니다. 비교할 기준은 무엇인가요?"}).status_code,201)
        self.assertEqual(len(self.req("GET",f"/v1/courses/{cid}/questions/mine",self.to).json().get("items",[])),0)
        self.assertEqual(len(self.req("GET",f"/v1/courses/{cid}/questions/mine",self.ts).json()["items"][0]["revisions"]),1)

    def test_04_engine_missing_fails_closed(self):
        with patch.dict(os.environ,{"NIT_ENGINE_ROOT":"/nonexistent/learningx-engine"}):
            with self.assertRaises(Exception) as x:nit.root()
            self.assertEqual(x.exception.status_code,503)

    def test_05_invalid_token_and_upload(self):
        self.assertEqual(self.req("GET",f"/v1/courses/{self.course_id}/questions/mine","not-a-token").status_code,401)
        r=self.req("POST",f"/v1/courses/{self.course_id}/materials",self.tf,
            files={"file":("../bad.pdf",b"not a pdf","application/pdf")})
        self.assertEqual(r.status_code,422)
        r=self.req("POST",f"/v1/courses/{self.course_id}/materials",self.tf,
            files={"file":("bad.exe",b"malicious","application/octet-stream")})
        self.assertEqual(r.status_code,415)

    def test_06_course_listing_does_not_leak_membership(self):
        cid=self.course_id
        r=self.req("GET","/v1/courses/mine",self.ts)
        self.assertEqual(r.status_code,200)
        self.assertIn(cid,[item["id"] for item in r.json()["items"]])
        r=self.req("GET","/v1/courses/mine",self.to)
        self.assertNotIn(cid,[item["id"] for item in r.json()["items"]])
        self.assertEqual(self.req("GET",f"/v1/courses/{cid}/materials",self.to).status_code,403)

    def test_07_forged_token_and_evidence_boundary(self):
        from learningx.security import secret
        from learningx.storage import Material
        import jwt
        from datetime import datetime,timedelta,timezone
        forged=jwt.encode({"sub":self.student_id,"aud":"learningx","iss":"learningx-server",
            "iat":datetime.now(timezone.utc),"exp":datetime.now(timezone.utc)+timedelta(minutes=1)},
            "attacker-secret-at-least-32-characters-long",algorithm="HS256")
        self.assertEqual(self.req("GET","/v1/courses/mine",forged).status_code,401)
        with patch.object(nit,"coach_question",side_effect=Exception("must not call")) as mock:
            r=self.req("POST",f"/v1/courses/{self.course_id}/questions",self.to,
                json={"material_id":self.material_id,"question":"다른 학생 질문을 조회하겠습니다."})
            self.assertEqual(r.status_code,403)
            mock.assert_not_called()

    def test_08_neon_readiness_endpoint(self):
        r=self.req("GET","/health/db")
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(r.json()["database"],"connected")

    def test_09_render_free_does_not_accept_ephemeral_lesson_uploads(self):
        with patch.dict(os.environ,{"RENDER_SERVICE_ID":"srv_test",
            "NIT_STORAGE_DIR":"/tmp/learningx-lessons"}):
            r=self.req("POST",f"/v1/courses/{self.course_id}/materials",self.tf,
                files={"file":("lesson.pdf",b"%PDF-1.7\\nTEST","application/pdf")})
        self.assertEqual(r.status_code,503,r.text)
        self.assertIn("Durable document storage",r.json()["detail"])

    def test_10_vercel_requires_durable_course_storage(self):
        cid=self.course_id
        with patch.dict(os.environ,{"NIT_SERVERLESS_RUNTIME":"1"}):
            with patch.object(nit,"ingest",side_effect=AssertionError("must not ingest")) as mock:
                r=self.req("POST",f"/v1/courses/{cid}/materials",self.tf,
                    files={"file":("lesson.pdf",b"%PDF-1.7\\nTest", "application/pdf")})
                self.assertEqual(r.status_code,503,r.text)
                self.assertIn("durable",r.json()["detail"])
                mock.assert_not_called()

    def test_11_vercel_still_checks_course_acl_before_upload_guard(self):
        cid=self.course_id
        with patch.dict(os.environ,{"NIT_SERVERLESS_RUNTIME":"1"}):
            r=self.req("POST",f"/v1/courses/{cid}/materials",self.to,
                files={"file":("lesson.pdf",b"%PDF-1.7\\nTest","application/pdf")})
            self.assertEqual(r.status_code,403,r.text)

    def test_12_serverless_s3_upload_approve_and_coach_roundtrip(self):
        from learningx import private_store
        from learningx.private_store import PrivateArtifactStore
        from test_store import FakeS3
        test_env={
            "NIT_SERVERLESS_RUNTIME":"1",
            "AWS_ACCESS_KEY_ID":"local-test-key",
            "AWS_SECRET_ACCESS_KEY":"local-test-secret",
            "AWS_ENDPOINT_URL_S3":"https://test.neon.invalid",
            "AWS_REGION":"ap-southeast-1",
            "NIT_STORAGE_BUCKET":"learningx-private-lessons",
        }
        fake=FakeS3()
        store=PrivateArtifactStore.__new__(PrivateArtifactStore)
        store.bucket="learningx-private-lessons"
        store.client=fake
        def ingest(source,out):
            out.mkdir(parents=True)
            document={"source":{"sha256":hashlib.sha256(source.read_bytes()).hexdigest()},
                      "pages":[{"page":1,"blocks":[]}]}
            (out/"document.json").write_text(json.dumps(document),encoding="utf8")
            return document
        def sign(out,reviewer,selected):
            (out/"approved_evidence.json").write_text('{"signature":"faculty"}',encoding="utf8")
            return {"approved":len(selected)}
        def verify(out):
            return 1 if (out/"approved_evidence.json").is_file() else 0
        def coach(out,student,question,policy,teacher_approved):
            self.assertTrue((out/"approved_evidence.json").is_file())
            return {"mode":"COACHED", "follow_up_question":"비교 기준을 어떻게 정의할까요?","evidence_refs":[]}
        with patch.dict(os.environ,test_env), \
             patch.object(private_store,"PrivateArtifactStore",return_value=store), \
             patch.object(nit,"ingest",side_effect=ingest), \
             patch.object(nit,"verified_count",side_effect=verify), \
             patch.object(nit,"approve_material",side_effect=sign), \
             patch.object(nit,"coach_question",side_effect=coach), \
             patch.object(nit,"candidates",return_value=[{"page":1,"block_id":"b1","block_sha256":"a"*64}]):
            cid=self.course_id
            uploaded=self.req("POST",f"/v1/courses/{cid}/materials",self.tf,
                files={"file":("private.pdf",b"%PDF-1.7\nConfidential","application/pdf")})
            self.assertEqual(uploaded.status_code,201,uploaded.text)
            mid=uploaded.json()["id"]
            with SessionLocal() as db:
                record=db.get(Material,mid)
                self.assertTrue(record.out_dir.startswith("s3://learningx-private-lessons/"))
            self.assertEqual(self.req("GET",f"/v1/materials/{mid}/candidates",self.ts).status_code,403)
            candidate=self.req("GET",f"/v1/materials/{mid}/candidates",self.tf)
            self.assertEqual(candidate.status_code,200,candidate.text)
            self.assertEqual(candidate.json()["approved_count"],0)
            approved=self.req("POST",f"/v1/materials/{mid}/approve",self.tf,
                json={"items":[{"page":1,"block_id":"b1","block_sha256":"a"*64}]})
            self.assertEqual(approved.status_code,200,approved.text)
            self.assertEqual(self.req("GET",f"/v1/materials/{mid}/candidates",self.tf).json()["approved_count"],1)
            result=self.req("POST",f"/v1/courses/{cid}/questions",self.ts,
                json={"material_id":mid,"question":"어떤 이론적 기준을 비교할 수 있나요?"})
            self.assertEqual(result.status_code,201,result.text)
            self.assertEqual(result.json()["coaching"]["mode"],"COACHED")
            listing=self.req("GET",f"/v1/courses/{cid}/materials",self.ts)
            self.assertEqual(listing.status_code,200,listing.text)
            self.assertTrue(any(x["id"]==mid and x["status"]=="FACULTY_SIGNED" for x in listing.json()["items"]))
