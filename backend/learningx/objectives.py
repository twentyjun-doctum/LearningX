"""Unit learning objective profiles (learning objective control panel, spec v0.1).

A faculty member sets, per material, one representative learning objective,
its target cognitive process (revised Bloom), the performance evidence to look
for in student explanations, the coaching support level, optional knowledge
type and application context, and approved "other" items. Each approval
creates an immutable version. Opening a class links the material's latest
approved version; classes without a link keep the existing coaching flow.

The profile never widens evidence approval or replaces the AI coaching
consent; both stay separate. Free-text "other" input is faculty intent: items
asking to reveal answers, use unapproved material, change permissions or
grade students are never stored as instructions.
"""
import json
import re
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, Session, mapped_column

from .security import actor, require_course, session
from .storage import Audit, Base, Material, User, uid

router = APIRouter(prefix="/v1")

PROCESSES = ("remember", "understand", "apply", "analyze", "evaluate", "create")
SUPPORT_LEVELS = ("minimal", "directional", "stepwise")
KNOWLEDGE_TYPES = ("factual", "conceptual", "procedural", "metacognitive")
APPLICATION_CONTEXTS = ("within_lesson", "similar_case", "new_situation")
OTHER_KINDS = ("goal", "evidence", "guideline")
PROCESS_LABEL = {"remember": "기억", "understand": "이해", "apply": "적용",
                 "analyze": "분석", "evaluate": "평가", "create": "창안"}
SUPPORT_LABEL = {"minimal": "힌트 최소", "directional": "방향 제시", "stepwise": "단계별 안내"}
MAX_OTHER_ITEMS = 6


def now():
    return datetime.now(timezone.utc)


class ObjectiveProfile(Base):
    __tablename__ = "objective_profiles"
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    course_id: Mapped[str] = mapped_column(ForeignKey("courses.id"), nullable=False, index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("materials.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    objective: Mapped[str] = mapped_column(Text, nullable=False)
    process: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence: Mapped[str] = mapped_column(Text, nullable=False)
    support: Mapped[str] = mapped_column(String(16), nullable=False)
    knowledge_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    application_context: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The faculty's free text is kept for editing only; runs use approved items.
    other_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    other_items: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    approved_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, nullable=False)
    __table_args__ = (UniqueConstraint("material_id", "version", name="uq_objective_profile_version"),)


class SessionObjectiveProfile(Base):
    """Which approved profile version a class uses. A separate table keeps the
    existing class_sessions schema unchanged (create_all adds tables, not columns)."""
    __tablename__ = "session_objective_profiles"
    session_id: Mapped[str] = mapped_column(ForeignKey("class_sessions.id"), primary_key=True)
    profile_id: Mapped[str] = mapped_column(ForeignKey("objective_profiles.id"), nullable=False)


# ---------- screening of faculty free text ----------

_DISALLOWED = (
    (re.compile(r"(정답|모범\s*답안?|답|해설|풀이)(을|를|은|는)?\s*(바로\s*|먼저\s*|직접\s*)?(알려|공개|제공|보여|말해|가르쳐|주(어|기|세요|고|도록))"), "answer_disclosure"),
    (re.compile(r"(점수|성적|채점|등급|학점|평가\s*점수)"), "grading"),
    (re.compile(r"(권한|관리자|승인\s*범위\s*(를|을)?\s*(넓|확대|무시))"), "permission_change"),
    (re.compile(r"(인터넷|웹\s*검색|외부\s*자료|교안\s*(밖|외)의?\s*자료|승인(되지|하지|\s*안\s*된|\s*안\s*한)\S*\s*자료)"), "unapproved_material"),
)
_VAGUE = re.compile(r"(이해한다|이해할\s*수\s*있다|안다|알\s*수\s*있다|생각한다|생각할\s*수\s*있다|느낀다|익힌다|감상한다|비판적으로\s*사고)")
_OBSERVABLE = re.compile(r"(설명|구분|구별|비교|식별|제시|계산|판단|정당화|제안|찾|고르|선택|분류|예시|예를|말한다|말할|쓴다|쓸|적는다|적을|나눈다|나눌|근거|사용해|사용하여|대조|연결|요약|정의|측정|해석)")


# "답을 알려 주기보다 …", "정답을 공개하지 않고 …": the faculty states what not to do.
_NEGATED = re.compile(r"^\s*\S{0,4}?\s*(보다|대신|말고|않|지\s*말|금지|없이)")


def screen_text(kind: str, text: str) -> tuple[str, str | None]:
    """Return ("applicable" | "held" | "rejected", reason).

    rejected: an instruction LearningX never executes (answers, grading,
    permissions, unapproved material). held: a goal or evidence without an
    observable way to confirm it; the faculty must add one before it applies.
    """
    for pattern, reason in _DISALLOWED:
        for match in pattern.finditer(text):
            if not _NEGATED.search(text[match.end():match.end() + 12]):
                return "rejected", reason
    if kind in ("goal", "evidence") and _VAGUE.search(text) and not _OBSERVABLE.search(text):
        return "held", "evidence_unclear"
    return "applicable", None


def objective_warnings(objective: str, evidence: str) -> list[str]:
    warnings = []
    if _VAGUE.search(objective) and not _OBSERVABLE.search(objective):
        warnings.append("objective_not_observable")
    if _VAGUE.search(evidence) and not _OBSERVABLE.search(evidence):
        warnings.append("evidence_not_observable")
    return warnings


# ---------- API ----------

class OtherItem(BaseModel):
    kind: Literal["goal", "evidence", "guideline"]
    text: str = Field(min_length=2, max_length=200)


class ProfileIn(BaseModel):
    objective: str = Field(min_length=5, max_length=300)
    process: Literal["remember", "understand", "apply", "analyze", "evaluate", "create"]
    evidence: str = Field(min_length=5, max_length=300)
    support: Literal["minimal", "directional", "stepwise"] = "directional"
    knowledge_type: Literal["factual", "conceptual", "procedural", "metacognitive"] | None = None
    application_context: Literal["within_lesson", "similar_case", "new_situation"] | None = None
    other_raw: str | None = Field(default=None, max_length=1000)
    other_items: list[OtherItem] = Field(default_factory=list, max_length=MAX_OTHER_ITEMS)

    @field_validator("objective", "evidence", "other_raw")
    @classmethod
    def strip(cls, value):
        return value.strip() if isinstance(value, str) else value


def _material(db, material_id, user):
    material = db.get(Material, material_id)
    if not material:
        raise HTTPException(404, "Material not found")
    require_course(db, material.course_id, user, "faculty")
    return material


def public_profile(profile: ObjectiveProfile | None) -> dict | None:
    if profile is None:
        return None
    return {
        "id": profile.id, "material_id": profile.material_id, "version": profile.version,
        "objective": profile.objective, "process": profile.process,
        "process_label": PROCESS_LABEL[profile.process], "evidence": profile.evidence,
        "support": profile.support, "support_label": SUPPORT_LABEL[profile.support],
        "knowledge_type": profile.knowledge_type, "application_context": profile.application_context,
        "other_raw": profile.other_raw, "other_items": json.loads(profile.other_items),
        "approved_at": profile.approved_at.isoformat() if profile.approved_at else None,
    }


def latest_profile(db, material_id) -> ObjectiveProfile | None:
    return (db.query(ObjectiveProfile).filter_by(material_id=material_id)
            .order_by(ObjectiveProfile.version.desc()).first())


def session_profile(db, session_id) -> ObjectiveProfile | None:
    link = db.get(SessionObjectiveProfile, session_id)
    return db.get(ObjectiveProfile, link.profile_id) if link else None


def link_latest_profile(db, session_id, material_id) -> ObjectiveProfile | None:
    """Called when a class opens: the class keeps this version for its lifetime."""
    profile = latest_profile(db, material_id)
    if profile is not None:
        db.add(SessionObjectiveProfile(session_id=session_id, profile_id=profile.id))
    return profile


@router.get("/materials/{material_id}/objective-profiles")
def list_profiles(material_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    material = _material(db, material_id, user)
    rows = (db.query(ObjectiveProfile).filter_by(material_id=material.id)
            .order_by(ObjectiveProfile.version.desc()).limit(50).all())
    return {"material_id": material.id, "items": [public_profile(row) for row in rows],
            "latest": public_profile(rows[0]) if rows else None}


@router.post("/materials/{material_id}/objective-profiles", status_code=201)
def approve_profile(material_id: str, data: ProfileIn, db: Session = Depends(session),
                    user: User = Depends(actor)):
    # Lock the material row so two approvals cannot claim the same version.
    material = db.query(Material).filter_by(id=material_id).with_for_update().first()
    if not material:
        raise HTTPException(404, "Material not found")
    require_course(db, material.course_id, user, "faculty")
    items = []
    for item in data.other_items:
        status, reason = screen_text(item.kind, item.text.strip())
        if status != "applicable":
            raise HTTPException(422, {"code": "other_item_not_applicable", "kind": item.kind,
                                      "status": status, "reason": reason})
        items.append({"kind": item.kind, "text": item.text.strip()})
    version = (db.query(func.max(ObjectiveProfile.version))
               .filter(ObjectiveProfile.material_id == material.id).scalar() or 0) + 1
    profile = ObjectiveProfile(
        course_id=material.course_id, material_id=material.id, version=version,
        objective=data.objective, process=data.process, evidence=data.evidence, support=data.support,
        knowledge_type=data.knowledge_type, application_context=data.application_context,
        other_raw=data.other_raw or None, other_items=json.dumps(items, ensure_ascii=False),
        approved_by=user.id)
    db.add(profile)
    db.add(Audit(actor_id=user.id, course_id=material.course_id, action="objective_profile_approved",
                 material_id=material.id, detail=f"version={version}"))
    db.commit()
    return {**public_profile(profile), "warnings": objective_warnings(data.objective, data.evidence)}


@router.get("/sessions/{session_id}/objective-profile")
def class_profile(session_id: str, db: Session = Depends(session), user: User = Depends(actor)):
    from .classroom import ClassSession  # imported lazily to avoid a module cycle

    current = db.get(ClassSession, session_id)
    if not current:
        raise HTTPException(404, "Session not found")
    require_course(db, current.course_id, user, "faculty")
    return {"session_id": current.id, "profile": public_profile(session_profile(db, current.id))}
