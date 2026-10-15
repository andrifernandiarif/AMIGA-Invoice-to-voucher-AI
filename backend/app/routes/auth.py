"""Password login and session-version revocation."""

import time
from collections import OrderedDict, deque
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import create_access_token
from app.dependencies import get_current_user, get_db
from app.models.user import User
from app.repositories.user_repository import user_repository
from app.services.passwords import hash_password, verify_password
from app.services.audit_service import log_event
from app.models.audit import AuditEventType

router = APIRouter(prefix="/auth", tags=["auth"])
# Bounded per-worker sliding windows, keyed by transport peer (never an untrusted forwarded header).
_attempts = OrderedDict()


def throttle(key):
    now = time.monotonic()
    entries = _attempts.pop(key, deque())
    while entries and entries[0] <= now - 300:
        entries.popleft()
    _attempts[key] = entries
    while len(_attempts) > 4096:
        _attempts.popitem(last=False)
    if len(entries) >= 10:
        raise HTTPException(429, "Too many sign-in attempts. Try again in five minutes.")
    entries.append(now)


@router.post("/token")
async def login(
    request: Request,
    username: str = Form(..., max_length=200),
    password: str = Form(..., max_length=128),
    db: AsyncSession = Depends(get_db),
):
    throttle(request.client.host if request.client else "unknown")
    user = await user_repository.find_by_username(db, username)
    valid = verify_password(user.password_hash if user else None, password)
    if not user or not valid or not user.is_active:
        raise HTTPException(401, "Invalid username or password.", headers={"WWW-Authenticate": "Bearer"})
    return {
        "access_token": create_access_token(user.user_id, user.role.value, user.token_version or 0),
        "token_type": "bearer",
    }


class PasswordChange(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


@router.post("/change-password")
async def change_password(
    body: PasswordChange, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    if not verify_password(user.password_hash, body.current_password):
        raise HTTPException(400, "Current password is incorrect.")
    if body.current_password == body.new_password:
        raise HTTPException(422, "Choose a different password.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version = (user.token_version or 0) + 1
    await log_event(db, AuditEventType.USER_MODIFIED, {"action": "PASSWORD_CHANGED"}, actor_id=user.user_id)
    return {"message": "Password changed. Sign in with your new password."}
