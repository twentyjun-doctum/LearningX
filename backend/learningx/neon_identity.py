"""Faculty sign-in with Google through Neon Auth (Managed Better Auth).

The static frontend signs in with Neon Auth directly (Google, Neon's shared
OAuth credentials for now) and sends the Neon-issued JWT here. This module
verifies it against Neon's JWKS (EdDSA, iss = aud = the auth origin) and
exchanges it for LearningX's own short session token, so every existing API and
permission check stays unchanged. No cookie is shared between the two origins.

A Google account becomes a faculty login only after it is linked once to an
operator-issued beta invite (NIT_BETA_FACULTY_INVITES). Linking is by the Neon
user id (``sub``), never by email, so an unverified or reused email cannot claim
someone else's account.
"""
import base64
import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from .security import issue_token, session
from .storage import Base, User

# Public endpoint of the learningx-pilot main branch (not a secret); override per environment.
DEFAULT_AUTH_URL = "https://ep-empty-breeze-b3t239qi.neonauth.c-4.ap-southeast-1.aws.neon.tech/learningx/auth"
JWKS_TTL_SECONDS = 600

router = APIRouter(prefix="/v1")


def auth_url() -> str:
    return os.getenv("NIT_NEON_AUTH_URL", DEFAULT_AUTH_URL).rstrip("/")


def auth_origin() -> str:
    parsed = urlparse(auth_url())
    return f"{parsed.scheme}://{parsed.netloc}"


class FacultyIdentity(Base):
    """A Neon Auth user linked to one LearningX faculty account (new table only)."""
    __tablename__ = "faculty_identities"
    provider_sub: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), unique=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False, default="neon_google")
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                default=lambda: datetime.now(timezone.utc))


# ---------- JWKS and token verification ----------

_jwks = {"keys": {}, "fetched": 0.0, "url": None}
_jwks_lock = threading.Lock()


def _fetch_jwks(url):
    response = httpx.get(url, timeout=5.0)
    response.raise_for_status()
    keys = {}
    for jwk in response.json().get("keys", []):
        if jwk.get("kty") == "OKP" and jwk.get("crv") == "Ed25519" and jwk.get("kid") and jwk.get("x"):
            raw = base64.urlsafe_b64decode(jwk["x"] + "=" * (-len(jwk["x"]) % 4))
            keys[jwk["kid"]] = Ed25519PublicKey.from_public_bytes(raw)
    return keys


def signing_key(kid, fetch=None):
    url = auth_url() + "/.well-known/jwks.json"
    with _jwks_lock:
        fresh = _jwks["url"] == url and time.monotonic() - _jwks["fetched"] < JWKS_TTL_SECONDS
        if not fresh or kid not in _jwks["keys"]:
            # Unknown kid: Neon may have rotated keys, so refetch once (rate limited by the TTL check above).
            if fresh and time.monotonic() - _jwks["fetched"] < 10:
                raise HTTPException(401, "Invalid sign-in token")
            try:
                _jwks.update(keys=(fetch or _fetch_jwks)(url), fetched=time.monotonic(), url=url)
            except Exception as exc:
                raise HTTPException(503, "Sign-in verification is unavailable") from exc
        key = _jwks["keys"].get(kid)
    if key is None:
        raise HTTPException(401, "Invalid sign-in token")
    return key


def verify(token: str, fetch=None) -> dict:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Invalid sign-in token") from exc
    if header.get("alg") != "EdDSA" or not header.get("kid"):
        raise HTTPException(401, "Invalid sign-in token")
    key = signing_key(header["kid"], fetch)
    try:
        claims = jwt.decode(token, key=key, algorithms=["EdDSA"], issuer=auth_origin(), audience=auth_origin(),
                            options={"require": ["exp", "iat", "sub"]}, leeway=30)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(401, "Invalid sign-in token") from exc
    if claims.get("banned") or not isinstance(claims.get("sub"), str) or not 0 < len(claims["sub"]) <= 64:
        raise HTTPException(401, "Invalid sign-in token")
    return claims


# ---------- exchange ----------

class NeonSessionIn(BaseModel):
    token: str = Field(min_length=20, max_length=8192)
    invite: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{43}$")


def _invited_faculty(db, code):
    """Same rule as /v1/beta/faculty/session: a per-faculty 256-bit operator invite."""
    try:
        invites = json.loads(os.getenv("NIT_BETA_FACULTY_INVITES", "{}"))
    except ValueError:
        invites = {}
    if not isinstance(invites, dict) or not invites:
        raise HTTPException(503, "Faculty beta is not enabled")
    user_id = invites.get(hashlib.sha256(code.encode("ascii")).hexdigest())
    user = db.get(User, user_id) if isinstance(user_id, str) and user_id else None
    if not user or not user.active or user.role != "faculty":
        raise HTTPException(401, "Invalid beta invitation")
    return user


@router.post("/auth/neon/session")
def neon_session(data: NeonSessionIn, response: Response, db: Session = Depends(session)):
    claims = verify(data.token)
    response.headers["Cache-Control"] = "no-store"
    identity = db.get(FacultyIdentity, claims["sub"])
    if identity is None:
        if not data.invite:
            # First Google sign-in: the account must be linked once with a beta invite.
            raise HTTPException(403, {"code": "invite_required"})
        user = _invited_faculty(db, data.invite)
        if db.query(FacultyIdentity).filter_by(user_id=user.id).first():
            raise HTTPException(409, {"code": "invite_already_linked"})
        db.add(FacultyIdentity(provider_sub=claims["sub"], user_id=user.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(409, {"code": "invite_already_linked"})
        identity = db.get(FacultyIdentity, claims["sub"])
    user = db.get(User, identity.user_id)
    if not user or not user.active or user.role != "faculty":
        raise HTTPException(403, {"code": "account_inactive"})
    return {"access_token": issue_token(user.id, minutes=60), "expires_in": 3600}
