"""Page snapshots for live classrooms. Only the currently shared page is served."""
import asyncio
from collections import OrderedDict
from functools import lru_cache
from threading import Lock
from types import SimpleNamespace
import json

from fastapi import HTTPException
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .private_store import read_material_artifact
from .storage import uid
from .storage import Base


class LiveSlide(Base):
    __tablename__ = "live_slides"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    version: Mapped[str] = mapped_column(String(64))
    content: Mapped[str] = mapped_column(Text, deferred=True)


class QuestionSlide(Base):
    __tablename__ = "question_slides"
    question_id: Mapped[str] = mapped_column(ForeignKey("session_questions.id"), primary_key=True)
    page: Mapped[int] = mapped_column(Integer)


_revisions = OrderedDict()
_revision_lock = Lock()
_pdf_lock = Lock()


def revision(session_id):
    with _revision_lock:
        return _revisions.get(session_id, "")


def notify(session_id):
    with _revision_lock:
        _revisions[session_id] = uid()
        _revisions.move_to_end(session_id)
        if len(_revisions) > 1024:
            _revisions.popitem(last=False)


async def wait_for_update(session_id, after, request):
    # Keep no database transaction open while waiting. The deadline also
    # refreshes state written by another API process.
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 2.5
    while revision(session_id) == after and loop.time() < deadline:
        await asyncio.sleep(0.08)
        if await request.is_disconnected():
            break


def _identity(material):
    return material.id, material.course_id, material.out_dir


@lru_cache(maxsize=2)
def _source(identity):
    material_id, course_id, out_dir = identity
    return read_material_artifact(SimpleNamespace(id=material_id, course_id=course_id, out_dir=out_dir),
                                  "source.pdf", 40 * 1024 * 1024)


def source_pdf(material):
    return _source(_identity(material))


@lru_cache(maxsize=8)
def _pages(identity):
    material_id, course_id, out_dir = identity
    material = SimpleNamespace(id=material_id, course_id=course_id, out_dir=out_dir)
    raw = read_material_artifact(material, "parsed/document.json", 24 * 1024 * 1024)
    pages = json.loads(raw)["pages"]
    return {
        entry["page"]: (
            bool(entry.get("hidden_slide")),
            "\n".join(block["text"] for block in entry["blocks"]
                       if isinstance(block.get("text"), str)
                       and block.get("origin") in ("native", "native_fallback")
                       and block.get("type") != "notes"
                       and block.get("content_role") != "teacher_note")[:12000],
        )
        for entry in pages
    }


def build_snapshot(material, page):
    pages = _pages(_identity(material))
    if page not in pages or pages[page][0]:
        raise HTTPException(422, "Slide does not exist or is hidden")
    # Only metadata is saved on navigation. The browser requests page bytes
    # separately and retains pages it has already rendered.
    return max(pages), {"pdf": material.filename.lower().endswith(".pdf"), "text": pages[page][1]}


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
