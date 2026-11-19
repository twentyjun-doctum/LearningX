"""Faculty invite, upload queue, real parser, and signed review integration."""
import hashlib
import json
import os
import secrets
import unittest
from unittest.mock import patch

import fitz

from test_api import app
from learningx import engine_adapter, private_store
from learningx.northflank_worker import poll_once
from learningx.storage import Material, SessionLocal, User
from fastapi.testclient import TestClient
from test_store import FakeS3


class FacultyBetaTests(unittest.TestCase):
    def test_upload_and_signed_review(self):
        self._check_upload_and_review(guest=False)

    def test_guest_classroom_with_real_parser(self):
        self._check_upload_and_review(guest=True)

    def test_guest_sessions_can_exceed_previous_daily_limit(self):
        origin = "https://learning-x-one.vercel.app"
        with TestClient(app) as client, patch.dict(os.environ, {"NIT_CORS_ORIGINS": origin}):
            responses = [client.post("/v1/faculty/guest", headers={"Origin": origin}) for _ in range(21)]
        self.assertEqual([response.status_code for response in responses], [201] * 21)
        self.assertEqual(len({response.json()["access_token"] for response in responses}), 21)

    def _check_upload_and_review(self, guest):
        with fitz.open() as pdf:
            cover = pdf.new_page()
            cover.insert_text((72, 85), "Media Literacy", fontsize=22)
            lesson = pdf.new_page()
            lesson.insert_textbox(
                fitz.Rect(72, 72, 520, 400),
                "Agenda setting describes how repeated news coverage changes which public "
                "issues audiences regard as important. Researchers compare the prominence "
                "of topics in media with the prominence of those topics in public surveys. "
                "Several explanations can account for the resulting association, including "
                "shared responses to an external event. Longitudinal studies measure "
                "coverage before and after the change in public attention.",
                fontsize=14,
            )
            if guest:
                pdf.embfile_add("test-padding.bin", os.urandom(60 * 1024 * 1024 - 64 * 1024))
            payload = pdf.tobytes()
        if guest:
            assert 40 * 1024 * 1024 < len(payload) <= 60 * 1024 * 1024

        env = {
            "NIT_WORKER_BACKEND": "northflank_poll",
            "NIT_REVIEW_SIGNING_KEY": secrets.token_hex(32),
            "AWS_ACCESS_KEY_ID": "local-test-key",
            "AWS_SECRET_ACCESS_KEY": "local-test-secret",
            "AWS_ENDPOINT_URL_S3": "https://test.neon.invalid",
            "AWS_REGION": "ap-southeast-1",
            "NIT_STORAGE_BUCKET": "learningx-private-lessons",
        }
        with TestClient(app) as client, patch.dict(os.environ, env):
            with SessionLocal() as db:
                faculty = User(email=f"beta-faculty-{guest}@test.local", role="faculty")
                db.add(faculty)
                db.flush()
                faculty_id = faculty.id
                db.commit()
            code = secrets.token_urlsafe(32)
            invites = json.dumps({hashlib.sha256(code.encode()).hexdigest(): faculty_id})
            storage = FakeS3()
            store = private_store.PrivateArtifactStore(client=storage)
            with patch.dict(os.environ, {"NIT_BETA_FACULTY_INVITES": invites}), patch.object(
                private_store, "PrivateArtifactStore", return_value=store
            ):
                if guest:
                    endpoint = "/v1/faculty/guest"
                    origin = "https://learning-x-one.vercel.app"
                    with patch.dict(os.environ, {"NIT_CORS_ORIGINS": origin}):
                        assert client.post(endpoint, json={}).status_code == 403
                        session = client.post(endpoint, json={}, headers={"Origin": origin})
                    assert session.status_code == 201, session.text
                else:
                    endpoint = "/v1/beta/faculty/session"
                    assert client.post(endpoint, json={"code": secrets.token_urlsafe(32)}).status_code == 401
                    session = client.post(endpoint, json={"code": code})
                    assert session.status_code == 200, session.text
                assert session.headers["Cache-Control"] == "no-store"
                headers = {"Authorization": "Bearer " + session.json()["access_token"]}
                course = client.post("/v1/courses", json={"title": "Media Literacy"}, headers=headers)
                assert course.status_code == 201, course.text
                course_id = course.json()["id"]
                uploads = f"/v1/courses/{course_id}/uploads"
                if guest:
                    for filename, limit in (("lesson.pdf", 60), ("lesson.pptx", 40)):
                        rejected = client.post(uploads + "/initiate", headers=headers, json={
                            "filename": filename, "size_bytes": limit * 1024 * 1024 + 1,
                        })
                        assert rejected.status_code == 413, rejected.text
                init = client.post(uploads + "/initiate", headers=headers, json={
                    "filename": "lesson.pdf", "size_bytes": len(payload),
                })
                assert init.status_code == 201, init.text
                upload = init.json()
                upload_id = upload["upload_id"]
                path = uploads + "/" + upload_id
                storage.put_object(
                    Bucket=store.bucket, Key=f"incoming/{course_id}/{upload_id}/source.pdf",
                    Body=payload, ContentType=upload["headers"]["Content-Type"],
                )
                queued = client.post(path + "/complete", headers=headers)
                assert queued.status_code == 202, queued.text
                assert queued.json()["status"] == "QUEUED"
                assert poll_once()["result"] == "READY"
                status = client.get(path, headers=headers).json()
                assert status["status"] == "READY"
                assert status["analysis_coverage"]["native_extraction"] == "COMPLETE"
                assert status["analysis_coverage"]["vision"]["status"] == "NOT_REQUIRED"
                assert status["analysis_coverage"]["semantic_analysis"] == "NOT_RUN"
                assert status["analysis_coverage"]["faculty_approval_required"] is True
                material_id = status["material_id"]
                url = f"/v1/materials/{material_id}"
                candidates = client.get(url + "/candidates", headers=headers).json()["eligible_candidates"]
                assert candidates
                selected = next(item for item in candidates if "Agenda setting" in item["preview"])
                approved = client.post(url + "/approve", headers=headers, json={"items": [selected]})
                assert approved.status_code == 200, approved.text
                assert approved.json()["approved_count"] == 1
                with SessionLocal() as db, store.workspace(db.get(Material, material_id)) as parsed:
                    assert engine_adapter.verified_count(parsed) == 1
                if guest:
                    policy = client.put(f"/v1/courses/{course_id}/policy", headers=headers,
                                        json={"policy": "RESTRICTED_HINT_ONLY", "teacher_approved": True})
                    assert policy.status_code == 200
                    opened = client.post(f"/v1/courses/{course_id}/sessions", headers=headers,
                                         json={"material_id": material_id})
                    assert opened.status_code == 201, opened.text
                    code = opened.json()["join_code"]
                    assert len(code) == 6
                    joined = client.post("/v1/join", json={"code": code})
                    assert joined.status_code == 200
                    live = f"/v1/sessions/{opened.json()['id']}/live"
                    full = client.get(live + "/document", headers=headers)
                    assert full.status_code == 200 and full.content == payload
                    slide = joined.json()["initial_live"]["slide"]
                    student_headers = {"Authorization": "Bearer " + joined.json()["access_token"]}
                    page = client.get(live + "/page/" + slide["version"], headers=student_headers)
                    assert page.status_code == 200
                    with fitz.open(stream=page.content, filetype="pdf") as rendered:
                        assert len(rendered) == 1
                        assert "Media Literacy" in rendered[0].get_text()
