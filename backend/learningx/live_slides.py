"""Page snapshots for live classrooms. Only the currently shared page is served."""
import base64
import json

from fastapi import HTTPException
from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .private_store import material_workspace
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


def build_snapshot(material, page):
    with material_workspace(material) as out:
        doc = json.loads((out / "document.json").read_text(encoding="utf-8"))
        pages = doc["pages"]
        if page > len(pages) or pages[page - 1]["page"] != page:
            raise HTTPException(422, "Slide does not exist")
        entry = pages[page - 1]
        if entry.get("hidden_slide"):
            raise HTTPException(422, "Hidden slides cannot be shared")
        source = out.parent / "source.pdf"
        if source.is_file():
            import fitz

            with fitz.open(source) as pdf:
                p = pdf[page - 1]
                scale = min(1.5, 1100 / max(p.rect.width, p.rect.height))
                pixels = p.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False, annots=False)
                return len(pages), {"image": base64.b64encode(pixels.tobytes("jpeg")).decode("ascii")}
        lines = [
            block["text"] for block in entry["blocks"]
            if isinstance(block.get("text"), str)
            and block.get("origin") in ("native", "native_fallback")
            and block.get("type") != "notes"
            and block.get("content_role") != "teacher_note"
        ]
        return len(pages), {"text": "\n".join(lines)[:12000]}
