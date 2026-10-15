# backend/app/routes/users.py

import uuid

from datetime import UTC, datetime
from pydantic import BaseModel, Field
from app.models.client import Client
from app.services.signatures import normalize_signature

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.models.audit import AuditEventType
from app.models.user import User
from app.repositories.user_repository import DuplicateUsernameError, user_repository
from app.schemas.user_schemas import (
    AddAssignmentRequest,
    AssignmentResponse,
    CreateUserRequest,
    UpdateRoleRequest,
    UserResponse,
)
from app.services.audit_service import log_event

router = APIRouter(prefix="/users", tags=["users"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserResponse)
async def create_user(
    body: CreateUserRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("SYSTEM_ADMIN")),
):
    """System Admin creates a new user account with at least one client assignment."""
    signature = normalize_signature(body.signature_data)
    if not body.full_name.strip() or not body.job_title.strip():
        raise HTTPException(422, "Enter the full name and professional role.")
    for assignment in body.assignments:
        client = await db.get(Client, assignment.client_id)
        if not client or not client.is_active:
            raise HTTPException(422, "Choose an active client assignment.")
    try:
        user = await user_repository.create_user(db, body.username, body.role)
    except DuplicateUsernameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    user.full_name = body.full_name.strip()
    user.job_title = body.job_title.strip()
    user.signature_data = signature

    for a in body.assignments:
        await user_repository.add_assignment(db, user.user_id, a.client_id)

    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        payload={
            "action": "USER_CREATED",
            "new_user_id": str(user.user_id),
            "username": user.username,
            "role": user.role.value,
            "assignments": [str(a.client_id) for a in body.assignments],
        },
        actor_id=admin.user_id,
    )
    return user


class ProfileUpdate(BaseModel):
    full_name: str = Field(min_length=1, max_length=300)
    job_title: str = Field(min_length=1, max_length=200)
    signature_data: str | None = Field(default=None, max_length=3000000)
    remove_signature: bool = False


@router.get("/me/profile")
async def profile(user: User = Depends(get_current_user)):
    return {"full_name": user.full_name or "", "job_title": user.job_title or "", "signature_data": user.signature_data}


@router.put("/me/profile", response_model=UserResponse)
async def save_profile(body: ProfileUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    if not body.full_name.strip() or not body.job_title.strip():
        raise HTTPException(422, "Enter your full name and professional role.")
    user.full_name = body.full_name.strip()
    user.job_title = body.job_title.strip()
    if body.signature_data is not None:
        user.signature_data = normalize_signature(body.signature_data)
    elif body.remove_signature:
        user.signature_data = None
    user.updated_at = datetime.now(UTC)
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        payload={"action": "PROFILE_UPDATED", "user_id": str(user.user_id), "has_signature": bool(user.signature_data)},
        actor_id=user.user_id,
    )
    await db.flush()
    return user


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role("SYSTEM_ADMIN")),
):
    """System Admin retrieves a user by ID."""
    user = await user_repository.find_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")
    return user


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_role(
    user_id: uuid.UUID,
    body: UpdateRoleRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("SYSTEM_ADMIN")),
):
    """System Admin updates a user's role."""
    user = await user_repository.find_by_id(db, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

    old_role = user.role.value
    updated = await user_repository.update_role(db, user_id, body.role)

    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        payload={
            "action": "ROLE_UPDATED",
            "user_id": str(user_id),
            "previous_role": old_role,
            "new_role": body.role.value,
        },
        actor_id=admin.user_id,
    )
    return updated


@router.post("/{user_id}/assignments", response_model=AssignmentResponse, status_code=status.HTTP_201_CREATED)
async def add_assignment(
    user_id: uuid.UUID,
    body: AddAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("SYSTEM_ADMIN")),
):
    """System Admin adds a client assignment to a user."""
    assignment = await user_repository.add_assignment(db, user_id, body.client_id)
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        payload={"action": "ASSIGNMENT_ADDED", "user_id": str(user_id), "client_id": str(body.client_id)},
        actor_id=admin.user_id,
    )
    return assignment


@router.delete("/{user_id}/assignments/{client_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_assignment(
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("SYSTEM_ADMIN")),
):
    """System Admin revokes a client assignment from a user."""
    await user_repository.revoke_assignment(db, user_id, client_id)
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        payload={"action": "ASSIGNMENT_REVOKED", "user_id": str(user_id), "client_id": str(client_id)},
        actor_id=admin.user_id,
    )
