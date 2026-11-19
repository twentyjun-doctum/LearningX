"""Page snapshots for live classrooms. Keep page assets separate from navigation state."""
import asyncio
from collections import OrderedDict
from functools import lru_cache
from threading import Lock
from types import SimpleNamespace
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .private_store import has_material_artifact, read_material_artifact
from .storage import uid
from .storage import Base


class LiveSlide(Base):
    __tablename__ = "live_slides"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, deferred=True)


class LiveTransition(Base):
    __tablename__ = "live_transitions"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    version: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("session_id", "version"),)
    page: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


class QuestionSlide(Base):
    __tablename__ = "question_slides"
    question_id: Mapped[str] = mapped_column(ForeignKey("session_questions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer)


_revisions = OrderedDict()
_revision_lock = Lock()
_pdf_lock = Lock()
_waiters = {}


def revision(session_id):
    with _revision_lock:
        return _revisions.get(session_id, "")


def notify(session_id):
    with _revision_lock:
        for loop, event in _waiters.get(session_id, ()):
            loop.call_soon_threadsafe(event.set)
        _revisions[session_id] = uid()
        _revisions.move_to_end(session_id)
        if len(_revisions) > 1024:
            _revisions.popitem(last=False)


async def wait_for_update(session_id, after):
    loop = asyncio.get_running_loop()
    event = asyncio.Event()
    waiter = (loop, event)
    with _revision_lock:
        if _revisions.get(session_id, "") != after:
            return
        _waiters.setdefault(session_id, set()).add(waiter)
    try:
        await asyncio.wait_for(event.wait(), timeout=2.5)
    except TimeoutError:
        pass
    finally:
        with _revision_lock:
            waiters = _waiters[session_id]
            waiters.remove(waiter)
            if not waiters:
                del _waiters[session_id]


def _identity(material):
    return material.id, material.course_id, material.out_dir, Path(material.filename).suffix.lower()


def require_preview(material):
    if Path(material.filename).suffix.lower() == ".pptx" and not has_material_artifact(material, "parsed/live-preview.pdf"):
        raise HTTPException(409, "LIVE_PREVIEW_UNAVAILABLE")


@lru_cache(maxsize=2)
def _source(identity):
    material_id, course_id, out_dir, suffix = identity
    record = SimpleNamespace(id=material_id, course_id=course_id, out_dir=out_dir)
    name = "source.pdf" if suffix == ".pdf" else "parsed/live-preview.pdf"
    try:
        return read_material_artifact(record, name, 40 * 1024 * 1024)
    except HTTPException as exc:
        if suffix == ".pptx" and exc.status_code == 404:
            raise HTTPException(409, "LIVE_PREVIEW_UNAVAILABLE") from exc
        raise


def source_pdf(material):
    return _source(_identity(material))


@lru_cache(maxsize=8)
def _pages(identity):
    material_id, course_id, out_dir, _ = identity
    material = SimpleNamespace(id=material_id, course_id=course_id, out_dir=out_dir)
    raw = read_material_artifact(material, "parsed/document.json", 24 * 1024 * 1024)
    pages = json.loads(raw)["pages"]
    return {entry["page"]: bool(entry.get("hidden_slide")) for entry in pages}


def visible_pages(material):
    return sorted(page for page, hidden in _pages(_identity(material)).items() if not hidden)


def build_snapshot(material, page):
    pages = _pages(_identity(material))
    if page not in pages or pages[page]:
        raise HTTPException(422, "Slide does not exist or is hidden")
    # Only metadata is saved on navigation. The browser requests page bytes
    # separately and retains pages it has already rendered.
    return max(pages), {"pdf": True, "before": max((n for n in pages if n < page and not pages[n]), default=None),
                        "after": min((n for n in pages if n > page and not pages[n]), default=None)}


@lru_cache(maxsize=6)
def _page_pdf(identity, page):
    import pymupdf

    source = _source(identity)
    with _pdf_lock, pymupdf.open(stream=source, filetype="pdf") as original, pymupdf.open() as result:
        if page > len(original):
            raise HTTPException(404, "PDF page unavailable")
        result.insert_pdf(original, from_page=page - 1, to_page=page - 1,
                          links=False, annots=False, widgets=False)
        return result.tobytes(garbage=4, deflate=True)


def page_pdf(material, page):
    return _page_pdf(_identity(material), page)
