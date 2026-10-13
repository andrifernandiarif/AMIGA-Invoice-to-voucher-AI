# backend/app/routes/users.py

import uuid

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
    try:
        user = await user_repository.create_user(db, body.username, body.role)
    except DuplicateUsernameError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

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
