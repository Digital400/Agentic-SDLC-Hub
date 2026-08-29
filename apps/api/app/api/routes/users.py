"""Minimal read-only Users endpoint.

There is no authentication yet (see docs/mvp-plan.md — "no auth" is
explicit out-of-scope), so the frontend has no other way to know which
users exist for "created by" / "reviewer" / "triggered by" fields. This
exists to unblock those, not as a users management API.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import User
from app.schemas.user import UserRead

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserRead])
def list_users(db: Session = Depends(get_db)) -> list[User]:
    return db.query(User).order_by(User.created_at).all()
