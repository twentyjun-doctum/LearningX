"""Live classroom sessions: join codes, anonymous students, question board.

Scope (Question-up redesign MVP):
- Faculty opens a session for one faculty-signed material and gets a 6-char join code.
- Students join anonymously with the code; no name or email is collected.
- Students ask, revise (re-coached each time) and submit questions inside the session.
- Faculty sees submitted questions only, anonymised, can filter, hide and project them.
- Faculty exports the session as CSV without any student identifier.

Live slide snapshots and slide-linked question indexes use the same session permissions.
"""
import csv
import io
import json
import os
import secrets
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from . import engine_adapter as nit
from . import private_store
from .guest import is_guest
from .live_slides import LiveSlide, QuestionSlide, build_snapshot, notify, page_pdf, revision, source_pdf, visible_pages, wait_for_update
from .security import actor, issue_token, require_course, session
from .storage import Audit, Base, Course, Material, Member, Question, Revision, User, uid

router = APIRouter(prefix="/v1")

JOIN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I
JOIN_LENGTH = 6
STUDENT_TOKEN_MINUTES = 240
LEVEL_MAP = {"clarify": "shallow", "apply": "mid", "analyze": "mid",
             "evaluate": "deep", "create": "deep"}
LEVEL_LABEL = {"shallow": "얕은", "mid": "중간", "deep": "깊은"}


def now():
    return datetime.now(timezone.utc)


class ClassSession(Base):
    __tablename__ = "class_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), nullable=False)
    join_code: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="OPEN")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    projected_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class SessionParticipant(Base):
    __tablename__ = "session_participants"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("session_id", "user_id", name="uq_session_participant"),)


class SessionQuestion(Base):
    __tablename__ = "session_questions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), nullable=False, index=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), unique=True, nullable=False)
    student_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    level: Mapped[str] = mapped_column(String(12), nullable=False, default="shallow")
    first_level: Mapped[str] = mapped_column(String(12), nullable=False, default="shallow")
    latest_text: Mapped[str] = mapped_column(Text, nullable=False)
    latest_coaching_json: Mapped[str] = mapped_column(Text, nullable=False)
    submitted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    projected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)


# ---------- schemas ----------

class OpenSession(BaseModel):
    material_id: str = Field(min_length=1, max_length=64)


class SessionState(BaseModel):
    status: Literal["OPEN", "PAUSED", "CLOSED"]


class JoinRequest(BaseModel):
    code: str = Field(min_length=JOIN_LENGTH, max_length=JOIN_LENGTH)


class ShareSlide(BaseModel):
    page: int = Field(ge=1)


class SessionAsk(BaseModel):
    slide_page: int | None = Field(default=None, ge=1)
    question: str = Field(min_length=5, max_length=2000)


class SessionRevise(BaseModel):
    text: str = Field(min_length=5, max_length=2000)


# ---------- helpers ----------

def _audit(db, who, course, action, material=None, detail=""):
    db.add(Audit(actor_id=who, course_id=course, action=action, material_id=material, detail=detail[:400]))


def _session_or_404(db, session_id) -> ClassSession:
    s = db.get(ClassSession, session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


def _faculty_session(db, session_id, user) -> ClassSession:
    s = _session_or_404(db, session_id)
    require_course(db, s.course_id, user, "faculty")
    return s


def _participant_session(db, session_id, user) -> ClassSession:
    s = _session_or_404(db, session_id)
    require_course(db, s.course_id, user, "student")
    if not db.query(SessionParticipant).filter_by(session_id=s.id, user_id=user.id).first():
        raise HTTPException(403, "Not a participant of this session")
    return s


def _new_code(db) -> str:
    for _ in range(20):
        code = "".join(secrets.choice(JOIN_ALPHABET) for _ in range(JOIN_LENGTH))
        if not db.query(ClassSession).filter_by(join_code=code).first():
            return code
    raise HTTPException(503, "Could not allocate a join code")


def _level(coaching: dict) -> str:
    return LEVEL_MAP.get(str(coaching.get("question_level", "")), "shallow")


def _student_coaching(coaching: dict) -> dict:
    """Only fields a student screen needs; never model metadata."""
    return {
        "mode": coaching.get("mode"),
        "question_level": coaching.get("question_level"),
        "level": _level(coaching),
        "follow_up_question": coaching.get("follow_up_question"),
        "feedback": coaching.get("feedback"),
        "evidence_refs": [{"page": r.get("page")} for r in coaching.get("evidence_refs", []) if isinstance(r, dict)],
    }


def _coach(db, s: ClassSession, user: User, text: str) -> dict:
    m = db.get(Material, s.material_id)
    c = db.get(Course, s.course_id)
    if not m or not c:
        raise HTTPException(404, "Session material not found")
    with private_store.material_workspace(m) as out:
        owner = db.get(User, c.created_by)
        if owner and is_guest(owner):
            return nit.coach_question(out, user.id, text, c.policy, c.teacher_approved, use_model=False)
        return nit.coach_question(out, user.id, text, c.policy, c.teacher_approved)


def _iso(dt):
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _public_session(db, s: ClassSession, faculty=False) -> dict:
    c = db.get(Course, s.course_id)
    data = {"id": s.id, "course_id": s.course_id, "course_title": c.title if c else "",
            "status": s.status, "created_at": _iso(s.created_at), "closed_at": _iso(s.closed_at)}
    if faculty:
        rows = db.query(SessionQuestion).filter_by(session_id=s.id).all()
        submitted = [r for r in rows if r.submitted]
        data.update({
            "join_code": s.join_code if s.status != "CLOSED" else None,
            "material_id": s.material_id,
            "participants": db.query(SessionParticipant).filter_by(session_id=s.id).count(),
            "questions_submitted": len(submitted),
            "questions_deep": sum(1 for r in submitted if r.level == "deep"),
            "questions_revised": sum(1 for r in submitted if r.latest_text != _original_text(db, r)),
            "questions_projected": sum(1 for r in submitted if r.projected_at),
            "projected_id": s.projected_id,
        })
    return data


def _original_text(db, row: SessionQuestion) -> str:
    q = db.get(Question, row.question_id)
    return q.text if q else row.latest_text


def _board_item(db, row: SessionQuestion) -> dict:
    original = _original_text(db, row)
    return {"id": row.id, "level": row.level, "level_label": LEVEL_LABEL[row.level],
            "text": row.latest_text,
            "original": original if original != row.latest_text else None,
            "submitted_at": _iso(row.submitted_at), "hidden": row.hidden,
            "projected": row.projected_at is not None}


# ---------- join throttling (per-process, best effort) ----------

_FAILED_JOINS: dict[str, deque] = defaultdict(deque)
JOIN_FAIL_WINDOW = 60.0
JOIN_FAIL_LIMIT = 10


def _throttle(request: Request):
    key = request.client.host if request.client else "unknown"
    q = _FAILED_JOINS[key]
    t = time.monotonic()
    while q and t - q[0] > JOIN_FAIL_WINDOW:
        q.popleft()
    if len(q) >= JOIN_FAIL_LIMIT:
        raise HTTPException(429, "Too many attempts; wait a minute")
    return key, q


# ---------- faculty endpoints ----------

@router.post("/courses/{course_id}/sessions", status_code=201)
def open_session(course_id: str, data: OpenSession, db: Session = Depends(session), user: User = Depends(actor)):
    require_course(db, course_id, user, "faculty")
    c = db.get(Course, course_id)
    m = db.get(Material, data.material_id)
    if not c or not m or m.course_id != course_id:
        raise HTTPException(404, "Material not found")
    if c.policy == "PROHIBITED" or (c.policy != "PERMITTED" and not c.teacher_approved):
        raise HTTPException(409, "Allow AI question coaching for this course before opening a session")
    with private_store.material_workspace(m) as out:
        if nit.verified_count(out) <= 0:
            raise HTTPException(409, "Approve evidence for this material before opening a session")
    pages = visible_pages(m)
    if not pages:
        raise HTTPException(422, "No visible pages are available")
    first = pages[0]
    total, content = build_snapshot(m, first)
    if m.filename.lower().endswith(".pptx"):
        source_pdf(m)
    for old in db.query(ClassSession).filter(ClassSession.course_id == course_id,
                                             ClassSession.status != "CLOSED").all():
        old.status = "CLOSED"
        old.closed_at = now()
    s = ClassSession(course_id=course_id, material_id=m.id, join_code=_new_code(db), created_by=user.id)
    db.add(s)
    db.flush()
    db.add(LiveSlide(session_id=s.id, page=first, total=total, version=uid(), content=json.dumps(content)))
    _audit(db, user.id, course_id, "session_opened", m.id)
    db.commit()
    return _public_session(db, s, faculty=True)


@router.get("/courses/{course_id}/sessions")
def list_sessions(course_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    require_course(db, course_id, user, "faculty")
    rows = (db.query(ClassSession).filter_by(course_id=course_id)
            .order_by(ClassSession.created_at.desc()).limit(50).all())
    return {"items": [_public_session(db, s, faculty=True) for s in rows]}


@router.get("/sessions/{session_id}")
def get_session(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _session_or_404(db, session_id)
    member = db.query(Member).filter_by(course_id=s.course_id, user_id=user.id).first()
    if not member:
        raise HTTPException(403, "Course access denied")
    return _public_session(db, s, faculty=member.role == "faculty")


@router.put("/sessions/{session_id}/state")
def set_state(session_id: str, data: SessionState, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    if s.status == "CLOSED":
        raise HTTPException(409, "Session already closed")
    s.status = data.status
    if data.status == "CLOSED":
        s.closed_at = now()
    _audit(db, user.id, s.course_id, "session_" + data.status.lower(), s.material_id)
    db.commit()
    notify(s.id)
    return _public_session(db, s, faculty=True)


@router.get("/sessions/{session_id}/live")
async def live_snapshot(session_id: str, response: Response, version: str = "",
                        after: str | None = None, db: Session = Depends(session), user: User = Depends(actor)):
    if after is not None and after == revision(session_id):
        await run_in_threadpool(_live_access, db, session_id, user)
        await run_in_threadpool(db.rollback)
        await wait_for_update(session_id, after)
    stamp = revision(session_id)
    response.headers["Cache-Control"] = "no-store"
    snapshot = await run_in_threadpool(_snapshot, db, session_id, user, version)
    return {**snapshot, "revision": stamp}


def _live_access(db, session_id, user):
    current = _session_or_404(db, session_id)
    member = db.query(Member).filter_by(course_id=current.course_id, user_id=user.id).first()
    faculty = member is not None and member.role == "faculty"
    if not faculty:
        _participant_session(db, session_id, user)
    return current, faculty


def _snapshot(db, session_id, user, version):
    current, faculty = _live_access(db, session_id, user)
    if current.status == "CLOSED":
        return {"status": current.status, "slide": None, "index": []}
    slide = db.get(LiveSlide, session_id)
    query = db.query(SessionQuestion.id, SessionQuestion.level, QuestionSlide.page).join(
        QuestionSlide, QuestionSlide.question_id == SessionQuestion.id
    ).filter(SessionQuestion.session_id == session_id)
    if faculty:
        query = query.filter(SessionQuestion.submitted.is_(True), SessionQuestion.hidden.is_(False))
    else:
        query = query.filter(SessionQuestion.student_id == user.id)
    index = [{"id": row.id, "level": row.level, "page": row.page}
             for row in query.order_by(SessionQuestion.created_at.desc()).limit(500)]
    snapshot = None
    if slide:
        snapshot = {"page": slide.page, "total": slide.total, "version": slide.version}
        if version != slide.version:
            snapshot.update(json.loads(slide.content))
            if faculty and snapshot.get("pdf"):
                snapshot["visible_pages"] = visible_pages(db.get(Material, current.material_id))
    return {"status": current.status, "slide": snapshot, "index": index}


@router.get("/sessions/{session_id}/live/document")
def live_document(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    current = _faculty_session(db, session_id, user)
    if current.status == "CLOSED":
        raise HTTPException(409, "Class has ended")
    material = db.get(Material, current.material_id)
    pdf = source_pdf(material)
    slide = db.get(LiveSlide, session_id)
    # Bring sessions opened before automatic sharing onto the same page format.
    if slide is None or "after" not in json.loads(slide.content):
        db.refresh(current, with_for_update=True)
        if current.status == "CLOSED":
            raise HTTPException(409, "Class has ended")
        slide = db.get(LiveSlide, session_id, populate_existing=True)
        if slide is None or "after" not in json.loads(slide.content):
            pages = visible_pages(material)
            if not pages:
                raise HTTPException(422, "No visible pages are available")
            number = slide.page if slide and slide.page in pages else pages[0]
            total, content = build_snapshot(material, number)
            db.merge(LiveSlide(session_id=session_id, page=number, total=total, version=uid(),
                               content=json.dumps(content)))
            db.commit()
            notify(session_id)
    return _pdf_response(pdf)


@router.get("/sessions/{session_id}/live/page/{version}")
def live_page(session_id: str, version: str, page: int | None = None, db: Session = Depends(session), user: User = Depends(actor)):
    current, _ = _live_access(db, session_id, user)
    slide = db.get(LiveSlide, session_id)
    if current.status == "CLOSED" or not slide or slide.version != version:
        raise HTTPException(409, "This page is no longer being shared")
    content = json.loads(slide.content)
    if not content.get("pdf"):
        raise HTTPException(404, "No PDF for this page")
    material = db.get(Material, current.material_id)
    target = slide.page if page is None else page
    if target not in (slide.page, content.get("before"), content.get("after")):
        raise HTTPException(403, "This page is outside the active slide window")
    return _pdf_response(page_pdf(material, target))


def _pdf_response(content):
    return Response(content, media_type="application/pdf",
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": 'attachment; filename="lesson.pdf"'})


@router.put("/sessions/{session_id}/live")
def share_slide(session_id: str, data: ShareSlide, db: Session = Depends(session), user: User = Depends(actor)):
    current = _faculty_session(db, session_id, user)
    if current.status == "CLOSED":
        raise HTTPException(409, "Class has ended")
    material = db.get(Material, current.material_id)
    total, content = build_snapshot(material, data.page)
    # Serialize presenters sharing the same classroom.
    db.refresh(current, with_for_update=True)
    if current.status == "CLOSED":
        raise HTTPException(409, "Class has ended")
    db.merge(LiveSlide(session_id=session_id, page=data.page, total=total, version=uid(),
                       content=json.dumps(content, ensure_ascii=False)))
    db.commit()
    notify(session_id)
    return {"page": data.page, "total": total, **content}


@router.get("/sessions/{session_id}/board")
def board(session_id: str, level: Literal["all", "shallow", "mid", "deep"] = "all",
          include_hidden: bool = False, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    q = db.query(SessionQuestion).filter_by(session_id=s.id, submitted=True)
    rows = q.order_by(SessionQuestion.submitted_at.desc()).all()
    counts = {"all": 0, "shallow": 0, "mid": 0, "deep": 0}
    items = []
    for r in rows:
        if r.hidden and not include_hidden:
            continue
        if not r.hidden:
            counts["all"] += 1
            counts[r.level] += 1
        if level == "all" or r.level == level:
            items.append(_board_item(db, r))
    order = {"deep": 0, "mid": 1, "shallow": 2}
    items.sort(key=lambda x: (order[x["level"]], -(datetime.fromisoformat(x["submitted_at"]).timestamp())))
    return {"session": _public_session(db, s, faculty=True), "counts": counts, "items": items}


def _faculty_row(db, row_id, user):
    row = db.get(SessionQuestion, row_id)
    if not row:
        raise HTTPException(404, "Question not found")
    s = _faculty_session(db, row.session_id, user)
    return row, s


@router.post("/session-questions/{row_id}/hide")
def hide(row_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _faculty_row(db, row_id, user)
    row.hidden = True
    if s.projected_id == row.id:
        s.projected_id = None
    _audit(db, user.id, s.course_id, "session_question_hidden", s.material_id)
    db.commit()
    notify(s.id)
    return {"id": row.id, "hidden": True}


@router.post("/session-questions/{row_id}/unhide")
def unhide(row_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _faculty_row(db, row_id, user)
    row.hidden = False
    db.commit()
    notify(s.id)
    return {"id": row.id, "hidden": False}


@router.post("/session-questions/{row_id}/project")
def project(row_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _faculty_row(db, row_id, user)
    if not row.submitted or row.hidden:
        raise HTTPException(409, "Only visible submitted questions can be projected")
    row.projected_at = row.projected_at or now()
    s.projected_id = row.id
    db.commit()
    return {"session_id": s.id, "projected": _board_item(db, row)}


@router.get("/sessions/{session_id}/projected")
def projected(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    row = db.get(SessionQuestion, s.projected_id) if s.projected_id else None
    nxt = None
    if row:
        remaining = (db.query(SessionQuestion)
                     .filter(SessionQuestion.session_id == s.id, SessionQuestion.submitted.is_(True),
                             SessionQuestion.hidden.is_(False), SessionQuestion.projected_at.is_(None))
                     .all())
        order = {"deep": 0, "mid": 1, "shallow": 2}
        remaining.sort(key=lambda r: (order[r.level], _iso(r.submitted_at) or ""))
        nxt = remaining[0].id if remaining else None
    return {"session": _public_session(db, s, faculty=True),
            "item": _board_item(db, row) if row else None, "next_id": nxt}


def _csv_cell(text):
    # Spreadsheet applications evaluate leading formulas even in quoted CSV cells.
    if text.startswith(("\t", "\r", "\n")) or text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


@router.get("/sessions/{session_id}/export")
def export(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _faculty_session(db, session_id, user)
    rows = (db.query(SessionQuestion).filter_by(session_id=s.id, submitted=True, hidden=False)
            .order_by(SessionQuestion.submitted_at).all())
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["제출 시각", "질문 수준", "처음 질문", "다시 쓴 질문", "화면에 띄움"])
    for r in rows:
        original = _original_text(db, r)
        w.writerow([_iso(r.submitted_at), LEVEL_LABEL[r.level], _csv_cell(original),
                    _csv_cell(r.latest_text) if r.latest_text != original else "",
                    "예" if r.projected_at else ""])
    _audit(db, user.id, s.course_id, "session_exported", s.material_id)
    db.commit()
    body = "﻿" + buf.getvalue()
    return Response(content=body.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="question-up-{s.id[:8]}.csv"',
                             "Cache-Control": "no-store"})


# ---------- student endpoints ----------

@router.post("/join")
def join(data: JoinRequest, request: Request, response: Response, db: Session = Depends(session)):
    key, failures = _throttle(request)
    code = data.code.strip().upper()
    s = db.query(ClassSession).filter_by(join_code=code).first() if all(ch in JOIN_ALPHABET for ch in code) else None
    if not s or s.status == "CLOSED":
        failures.append(time.monotonic())
        raise HTTPException(404, "No open class with this code")
    if s.status == "PAUSED":
        raise HTTPException(423, "The class is not taking questions right now")
    student = User(email=f"anon+{uid()}@join.question-up.local", role="student", active=True)
    db.add(student)
    db.flush()
    db.add(Member(course_id=s.course_id, user_id=student.id, role="student"))
    db.add(SessionParticipant(session_id=s.id, user_id=student.id))
    _audit(db, student.id, s.course_id, "student_joined_anonymously", s.material_id)
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": issue_token(student.id, minutes=STUDENT_TOKEN_MINUTES, session_id=s.id),
            "session": _public_session(db, s)}


@router.post("/sessions/{session_id}/questions", status_code=201)
def session_ask(session_id: str, data: SessionAsk, db: Session = Depends(session), user: User = Depends(actor)):
    s = _participant_session(db, session_id, user)
    if s.status != "OPEN":
        raise HTTPException(423, "The class is not taking questions right now")
    slide = db.get(LiveSlide, session_id)
    page = slide.page if slide else None
    if data.slide_page is not None and (slide is None or data.slide_page > slide.total):
        raise HTTPException(422, "Invalid question slide")
    page = data.slide_page or page
    coaching = _coach(db, s, user, data.question)
    q = Question(course_id=s.course_id, material_id=s.material_id, student_id=user.id,
                 text=data.question, feedback_json=json.dumps(coaching, ensure_ascii=False))
    db.add(q)
    db.flush()
    lvl = _level(coaching)
    row = SessionQuestion(session_id=s.id, question_id=q.id, student_id=user.id, level=lvl, first_level=lvl,
                          latest_text=data.question, latest_coaching_json=json.dumps(coaching, ensure_ascii=False))
    db.add(row)
    if page is not None:
        db.flush()
        db.add(QuestionSlide(question_id=row.id, page=page))
    _audit(db, user.id, s.course_id, "session_question_coached", s.material_id, detail=str(coaching.get("mode")))
    db.commit()
    notify(s.id)
    return {"id": row.id, "level": row.level, "coaching": _student_coaching(coaching)}


def _own_row(db, row_id, user):
    row = db.get(SessionQuestion, row_id)
    if not row or row.student_id != user.id:
        raise HTTPException(404, "Question not found")
    s = _participant_session(db, row.session_id, user)
    return row, s


@router.post("/session-questions/{row_id}/revisions", status_code=201)
def session_revise(row_id: str, data: SessionRevise, db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _own_row(db, row_id, user)
    if s.status != "OPEN":
        raise HTTPException(423, "The class is not taking questions right now")
    if row.submitted:
        raise HTTPException(409, "Question already submitted")
    coaching = _coach(db, s, user, data.text)
    db.add(Revision(question_id=row.question_id, student_id=user.id, text=data.text))
    row.latest_text = data.text
    row.level = _level(coaching)
    row.latest_coaching_json = json.dumps(coaching, ensure_ascii=False)
    _audit(db, user.id, s.course_id, "session_question_revised", s.material_id)
    db.commit()
    notify(s.id)
    return {"id": row.id, "level": row.level, "coaching": _student_coaching(coaching)}


@router.post("/session-questions/{row_id}/submit")
def session_submit(row_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    row, s = _own_row(db, row_id, user)
    if s.status != "OPEN":
        raise HTTPException(423, "The class is not taking questions right now")
    if not row.submitted:
        row.submitted = True
        row.submitted_at = now()
        _audit(db, user.id, s.course_id, "session_question_submitted", s.material_id)
        db.commit()
        notify(s.id)
    return {"id": row.id, "submitted": True, "level": row.level, "text": row.latest_text,
            "original": _original_text(db, row)}


@router.get("/sessions/{session_id}/mine")
def mine(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    s = _participant_session(db, session_id, user)
    rows = (db.query(SessionQuestion).filter_by(session_id=s.id, student_id=user.id)
            .order_by(SessionQuestion.created_at.desc()).all())
    pages = dict(db.query(QuestionSlide.question_id, QuestionSlide.page).join(
        SessionQuestion, SessionQuestion.id == QuestionSlide.question_id
    ).filter(SessionQuestion.session_id == session_id, SessionQuestion.student_id == user.id).all())
    return {"session": _public_session(db, s),
            "items": [{"id": r.id, "slide_page": pages.get(r.id), "level": r.level, "level_label": LEVEL_LABEL[r.level],
                       "text": r.latest_text, "original": _original_text(db, r),
                       "submitted": r.submitted, "submitted_at": _iso(r.submitted_at),
                       "coaching": _student_coaching(json.loads(r.latest_coaching_json))} for r in rows]}
