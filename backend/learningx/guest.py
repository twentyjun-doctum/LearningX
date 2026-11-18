"""Temporary classroom ownership without an account sign-in."""
import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .security import issue_token, session
from .storage import User

router = APIRouter()
GUEST_DOMAIN = "@guest.question-up.local"
GUEST_UPLOAD_LIMIT = 40 * 1024 * 1024


def is_guest(user):
    return user.email.endswith(GUEST_DOMAIN)


@router.post("/v1/faculty/guest", status_code=201)
def start_guest(request: Request, response: Response, db: Session = Depends(session)):
    origins = {origin.strip() for origin in os.getenv("NIT_CORS_ORIGINS", "").split(",") if origin.strip()}
    if request.headers.get("origin") not in origins:
        raise HTTPException(403, "Guest classroom origin denied")
    # Reserve a unique database slot, shared across API replicas and restarts.
    day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
    prefix = f"guest-{day}-"
    used = {email for (email,) in db.query(User.email).filter(User.email.startswith(prefix)).all()}
    for slot in range(20):
        email = f"{prefix}{slot}{GUEST_DOMAIN}"
        if email in used:
            continue
        user = User(id=uuid.uuid4().hex, email=email, role="faculty", active=True)
        db.add(user)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            continue
        response.headers["Cache-Control"] = "no-store"
        return {"access_token": issue_token(user.id, minutes=240)}
    raise HTTPException(429, "Daily guest classroom capacity reached")
