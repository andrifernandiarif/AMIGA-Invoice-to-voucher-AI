# backend/app/routes/auth.py
"""
Dev-only auth endpoint.
POST /auth/token  — returns a JWT for any existing, active username.
No password required (no password column exists on User yet).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import create_access_token
from app.dependencies import get_db
from app.repositories.user_repository import user_repository

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/token")
async def login(
    username: str = Form(...),
    password: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
):
    """
    Exchange a username for a Bearer JWT.
    Password is accepted but ignored (no password column on User yet).

    Available usernames: test_client_pic, test_finance_reviewer, test_reviewer
    """
    user = await user_repository.find_by_username(db, username)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or inactive account.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = create_access_token(user.user_id, user.role.value)
    return {"access_token": token, "token_type": "bearer"}
