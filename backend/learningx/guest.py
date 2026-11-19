"""Temporary classroom ownership without an account sign-in."""
import os
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, Response
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
    guest_id = uuid.uuid4().hex
    db.add(User(id=guest_id, email=f"guest-{guest_id}{GUEST_DOMAIN}", role="faculty", active=True))
    db.commit()
    response.headers["Cache-Control"] = "no-store"
    return {"access_token": issue_token(guest_id, minutes=240)}
