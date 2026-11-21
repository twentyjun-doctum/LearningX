"""Question lists must not issue one query per question (board polls every 5 s)."""
import csv
import io
import os
import tempfile
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

TEMP = tempfile.TemporaryDirectory(prefix="learningx-queries-")
os.environ.setdefault("DATABASE_URL", "sqlite:///" + str(Path(TEMP.name) / "test.sqlite3"))
os.environ.setdefault("NIT_JWT_SECRET", "test-only-signing-key-12345678901234567890")
os.environ.setdefault("NIT_STORAGE_DIR", str(Path(TEMP.name) / "private"))

from fastapi.testclient import TestClient
from sqlalchemy import event

from learningx.api import app
from learningx.classroom import ClassSession, SessionParticipant, SessionQuestion
from learningx.security import issue_token
from learningx.storage import Course, Material, Member, Question, SessionLocal, User, engine, uid


@contextmanager
def count_queries():
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


class QuestionQueryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)

    def seed(self, questions, sessions=1, one_student=False):
        """One course with `sessions` classes, each holding `questions` varied questions."""
        start = datetime(2026, 11, 21, tzinfo=timezone.utc)
        with SessionLocal() as db:
            faculty = User(email=f"q-fac-{uid()}@test.local", role="faculty")
            db.add(faculty)
            db.flush()
            course = Course(title="쿼리 수 검증", policy="PERMITTED", teacher_approved=True, created_by=faculty.id)
            db.add(course)
            db.flush()
            db.add(Member(course_id=course.id, user_id=faculty.id, role="faculty"))
            material = Material(course_id=course.id, filename="lesson.pdf", sha256="b" * 64,
                                out_dir=str(Path(TEMP.name) / "mat"), ingested_by=faculty.id)
            db.add(material)
            db.flush()
            session_ids, students = [], []
            for n in range(sessions):
                s = ClassSession(course_id=course.id, material_id=material.id, join_code=uid()[:12],
                                 created_by=faculty.id, status="OPEN" if n == 0 else "CLOSED")
                db.add(s)
                db.flush()
                session_ids.append(s.id)
                student = None
                for i in range(questions):
                    if student is None or not one_student:
                        student = User(email=f"anon+{uid()}@join.question-up.local", role="student")
                        db.add(student)
                        db.flush()
                        db.add(Member(course_id=course.id, user_id=student.id, role="student"))
                        db.add(SessionParticipant(session_id=s.id, user_id=student.id))
                    original = f"처음 질문 {i}번은 무엇인가요?"
                    q = Question(course_id=course.id, material_id=material.id, student_id=student.id,
                                 text=original, feedback_json="{}")
                    db.add(q)
                    db.flush()
                    revised = i % 3 == 0
                    db.add(SessionQuestion(
                        session_id=s.id, question_id=q.id, student_id=student.id,
                        level=("shallow", "mid", "deep")[i % 3], latest_text=original + (" 수정" if revised else ""),
                        latest_coaching_json="{}", submitted=i % 4 != 3,
                        submitted_at=start + timedelta(seconds=i), hidden=i % 5 == 4,
                        projected_at=start if i % 6 == 0 else None))
                    students.append((s.id, student.id))
            db.commit()
        return issue_token(faculty.id), course.id, session_ids, students

    def queries_for(self, method, path, token):
        with count_queries() as statements:
            response = self.client.request(method, path, headers={"Authorization": "Bearer " + token})
        self.assertEqual(response.status_code, 200, response.text)
        return len(statements), response

    def assert_constant(self, path_for, small=2, large=30, sessions=1, token_for=None, one_student=False):
        counts = []
        for size in (small, large):
            faculty, course_id, session_ids, students = self.seed(size, sessions, one_student)
            token = token_for(students) if token_for else faculty
            counts.append(self.queries_for("GET", path_for(course_id, session_ids[0]), token)[0])
        self.assertEqual(counts[0], counts[1], f"queries grew with question count: {counts}")

    def test_board_query_count_is_constant(self):
        self.assert_constant(lambda c, s: f"/v1/sessions/{s}/board?include_hidden=true")

    def test_session_list_query_count_is_constant(self):
        self.assert_constant(lambda c, s: f"/v1/courses/{c}/sessions", sessions=3)

    def test_export_query_count_is_constant(self):
        self.assert_constant(lambda c, s: f"/v1/sessions/{s}/export")

    def test_student_list_query_count_is_constant(self):
        def first_student(students):
            session_id, student_id = students[0]
            return issue_token(student_id, session_id=session_id)
        self.assert_constant(lambda c, s: f"/v1/sessions/{s}/mine", token_for=first_student, one_student=True)

    def test_summaries_match_rows(self):
        faculty, course_id, session_ids, _ = self.seed(30, sessions=2)
        _, response = self.queries_for("GET", f"/v1/courses/{course_id}/sessions", faculty)
        items = {item["id"]: item for item in response.json()["items"]}
        with SessionLocal() as db:
            for session_id in session_ids:
                rows = db.query(SessionQuestion).filter_by(session_id=session_id, submitted=True).all()
                originals = {q.id: q.text for q in db.query(Question).filter(
                    Question.id.in_([r.question_id for r in rows]))}
                expected = {
                    "participants": db.query(SessionParticipant).filter_by(session_id=session_id).count(),
                    "questions_submitted": len(rows),
                    "questions_deep": sum(r.level == "deep" for r in rows),
                    "questions_revised": sum(r.latest_text != originals[r.question_id] for r in rows),
                    "questions_projected": sum(r.projected_at is not None for r in rows),
                }
                self.assertEqual({k: items[session_id][k] for k in expected}, expected)
                self.assertGreater(expected["questions_revised"], 0)
        closed = [item for item in items.values() if item["status"] == "CLOSED"]
        self.assertTrue(closed and all(item["join_code"] is None for item in closed))

    def test_board_and_export_keep_order_originals_and_hiding(self):
        faculty, _, (session_id,), _ = self.seed(12)
        _, board = self.queries_for("GET", f"/v1/sessions/{session_id}/board", faculty)
        body = board.json()
        with SessionLocal() as db:
            visible = (db.query(SessionQuestion)
                       .filter_by(session_id=session_id, submitted=True, hidden=False)
                       .order_by(SessionQuestion.submitted_at, SessionQuestion.id).all())
            originals = {q.id: q.text for q in db.query(Question)}
            self.assertEqual([item["id"] for item in body["items"]], [r.id for r in visible])
            for item, row in zip(body["items"], visible):
                original = originals[row.question_id]
                self.assertEqual(item["original"], original if original != row.latest_text else None)
        self.assertEqual(body["counts"]["all"], len(visible))
        with_hidden = self.queries_for("GET", f"/v1/sessions/{session_id}/board?include_hidden=true", faculty)[1].json()
        self.assertGreater(len(with_hidden["items"]), len(body["items"]))
        self.assertEqual(with_hidden["counts"], body["counts"])
        _, export = self.queries_for("GET", f"/v1/sessions/{session_id}/export", faculty)
        lines = list(csv.reader(io.StringIO(export.content.decode("utf-8-sig"))))[1:]
        self.assertEqual(len(lines), len(visible))
        for line, row in zip(lines, visible):
            original = originals[row.question_id]
            self.assertEqual(line[2], original)
            self.assertEqual(line[3], row.latest_text if row.latest_text != original else "")


if __name__ == "__main__":
    unittest.main()
