"""Organization directory and delegated account management."""

import csv
import io
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.models.audit import AuditEventType
from app.models.client import Client
from app.models.client_assignment import ClientAssignment
from app.models.user import User, UserRole
from app.permissions import ADMIN_ROLES, OPERATIONAL_ROLES, permissions, require_permission
from app.repositories.user_repository import DuplicateUsernameError, user_repository
from app.schemas.user_schemas import AddAssignmentRequest, CreateUserRequest, UpdateRoleRequest, UserResponse
from app.services.audit_service import log_event
from app.services.passwords import hash_password
from app.services.signatures import normalize_signature

router = APIRouter(prefix="/users", tags=["users"])


async def target_user(db, actor, user_id):
    user = await db.get(User, user_id)
    if not user:
        raise HTTPException(404, "User not found.")
    if actor.role.value != "SUPER_ADMIN" and user.role.value in ADMIN_ROLES:
        raise HTTPException(403, "Only Super Admin can manage administrative accounts.")
    return user


def check_role(actor, role):
    if role == UserRole.USER_ADMIN:
        raise HTTPException(422, "User Admin has been consolidated into System Admin.")
    if actor.role.value != "SUPER_ADMIN" and role.value not in OPERATIONAL_ROLES:
        raise HTTPException(403, "Only Super Admin can grant administrative roles.")


async def protect_last_super(db, user, removing):
    if removing and user.role.value == "SUPER_ADMIN" and user.is_active:
        # Lock every super administrator in deterministic order to serialize concurrent removals.
        admins = (
            await db.scalars(
                select(User)
                .where(User.role == UserRole.SUPER_ADMIN)
                .order_by(User.user_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
        if sum(a.is_active for a in admins) <= 1:
            raise HTTPException(409, "The last active Super Admin cannot be removed.")


async def check_client(db, actor, client_id):
    client = await db.get(Client, client_id)
    if not client or not client.is_active:
        raise HTTPException(422, "Choose an active client.")
    if actor.role.value == "SERVICE_LEAD" and not await user_repository.is_assignment_active(
        db, actor.user_id, client_id
    ):
        raise HTTPException(403, "You can manage assignments only for your assigned clients.")


@router.get("")
async def directory(
    search: str = Query(default="", max_length=200),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(get_current_user),
):
    if actor.role.value not in ("SUPER_ADMIN", "SYSTEM_ADMIN", "SERVICE_LEAD"):
        raise HTTPException(403, "User directory is unavailable for this role.")
    stmt = select(User).where(User.username.ilike(f"%{search}%")).order_by(User.username)
    if actor.role.value != "SUPER_ADMIN":
        stmt = stmt.where(User.role.in_([UserRole(r) for r in OPERATIONAL_ROLES]))
    lead_clients = None
    if actor.role.value == "SERVICE_LEAD":
        lead_clients = select(ClientAssignment.client_id).where(
            ClientAssignment.user_id == actor.user_id, ClientAssignment.is_active.is_(True)
        )
        stmt = stmt.where(
            User.user_id.in_(
                select(ClientAssignment.user_id).where(
                    ClientAssignment.client_id.in_(lead_clients), ClientAssignment.is_active.is_(True)
                )
            )
        )
    rows = (await db.scalars(stmt.offset(offset).limit(limit))).all()
    assignment_stmt = select(ClientAssignment).where(
        ClientAssignment.user_id.in_([u.user_id for u in rows]), ClientAssignment.is_active.is_(True)
    )
    if lead_clients is not None:
        assignment_stmt = assignment_stmt.where(ClientAssignment.client_id.in_(lead_clients))
    assignments = (await db.scalars(assignment_stmt)).all()
    result = [
        {
            **UserResponse.model_validate(user).model_dump(),
            "assignments": [a.client_id for a in assignments if a.user_id == user.user_id],
        }
        for user in rows
    ]
    return result


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    body: CreateUserRequest, db: AsyncSession = Depends(get_db), actor: User = Depends(require_permission("users"))
):
    check_role(actor, body.role)
    if body.assignments and "assignments" not in permissions(actor):
        raise HTTPException(403, "Ask a System Admin or Super Admin to assign companies after account creation.")
    for assignment in body.assignments:
        await check_client(db, actor, assignment.client_id)
    encoded = hash_password(body.temporary_password)
    try:
        user = await user_repository.create_user(db, body.username, body.role)
    except DuplicateUsernameError as exc:
        raise HTTPException(409, str(exc))
    user.full_name, user.job_title = body.full_name.strip(), body.job_title.strip()
    if not user.full_name or not user.job_title:
        raise HTTPException(422, "Enter full name and professional role.")
    user.signature_data = normalize_signature(body.signature_data)
    user.password_hash, user.must_change_password = encoded, True
    for assignment in body.assignments:
        await user_repository.add_assignment(db, user.user_id, assignment.client_id)
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        {"action": "USER_CREATED", "user_id": str(user.user_id), "role": body.role.value},
        actor_id=actor.user_id,
    )
    return user


@router.patch("/{user_id}/role", response_model=UserResponse)
async def update_role(
    user_id: uuid.UUID,
    body: UpdateRoleRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users")),
):
    user = await target_user(db, actor, user_id)
    check_role(actor, body.role)
    await protect_last_super(db, user, body.role != UserRole.SUPER_ADMIN)
    old = user.role.value
    user.role = body.role
    user.updated_at = datetime.now(UTC)
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        {"action": "ROLE_UPDATED", "user_id": str(user_id), "old_value": old, "new_value": body.role.value},
        actor_id=actor.user_id,
    )
    return user


@router.post("/import-csv")
async def import_csv(
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users")),
):
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(422, "Upload a CSV file.")

    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(422, "CSV must use UTF-8 encoding.") from exc

    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        raise HTTPException(422, "CSV must contain a header and at least one account.")
    if len(rows) > 100:
        raise HTTPException(422, "CSV import is limited to 100 accounts.")

    required = {"username", "full_name", "job_title", "role", "temporary_password"}
    headers = set(rows[0].keys()) if rows[0] else set()
    missing = sorted(required - headers)
    if missing:
        raise HTTPException(422, f"CSV is missing required columns: {', '.join(missing)}.")

    prepared = []
    usernames = set()
    for number, row in enumerate(rows, start=2):
        values = {key: (value or "").strip() for key, value in row.items() if key}
        username = values.get("username", "")
        if not username or not values.get("full_name") or not values.get("job_title"):
            raise HTTPException(422, f"Row {number} must include username, full name, and professional role.")
        if not values.get("temporary_password") or len(values["temporary_password"]) < 12:
            raise HTTPException(422, f"Row {number} temporary password must be at least 12 characters.")
        if username in usernames:
            raise HTTPException(422, f"Row {number} duplicates username '{username}'.")
        usernames.add(username)
        try:
            role = UserRole(values["role"].upper())
        except ValueError as exc:
            raise HTTPException(422, f"Row {number} has an invalid role.") from exc
        check_role(actor, role)
        client_ids = []
        for value in values.get("client_ids", "").split(","):
            if value.strip():
                try:
                    client_ids.append(uuid.UUID(value.strip()))
                except ValueError as exc:
                    raise HTTPException(422, f"Row {number} has an invalid client ID.") from exc
        if client_ids and "assignments" not in permissions(actor):
            raise HTTPException(403, "You do not have permission to import company assignments.")
        prepared.append((number, values, role, client_ids))

    existing = (await db.scalars(select(User).where(User.username.in_(usernames)))).all()
    if existing:
        raise HTTPException(409, f"Username '{existing[0].username}' is already taken.")

    for number, values, role, client_ids in prepared:
        for client_id in client_ids:
            try:
                await check_client(db, actor, client_id)
            except HTTPException as exc:
                raise HTTPException(exc.status_code, f"Row {number}: {exc.detail}") from exc

    for _, values, role, client_ids in prepared:
        user = await user_repository.create_user(db, values["username"], role)
        user.full_name = values["full_name"]
        user.job_title = values["job_title"]
        user.password_hash = hash_password(values["temporary_password"])
        user.must_change_password = True
        for client_id in client_ids:
            await user_repository.add_assignment(db, user.user_id, client_id)
        await log_event(
            db,
            AuditEventType.USER_MODIFIED,
            {"action": "USER_CREATED_FROM_CSV", "user_id": str(user.user_id), "role": role.value},
            actor_id=actor.user_id,
        )

    return {"imported": len(prepared)}


class AccountState(BaseModel):
    is_active: bool


@router.patch("/{user_id}/status", response_model=UserResponse)
async def update_status(
    user_id: uuid.UUID,
    body: AccountState,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users")),
):
    user = await target_user(db, actor, user_id)
    if not body.is_active and user.user_id == actor.user_id:
        raise HTTPException(409, "You cannot remove your own account.")
    await protect_last_super(db, user, not body.is_active)
    user.is_active = body.is_active
    user.token_version = (user.token_version or 0) + 1
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        {"action": "ACCOUNT_STATE", "user_id": str(user_id), "is_active": body.is_active},
        actor_id=actor.user_id,
    )
    return user


class ResetPassword(BaseModel):
    temporary_password: str = Field(min_length=12, max_length=128)


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: uuid.UUID,
    body: ResetPassword,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("users")),
):
    user = await target_user(db, actor, user_id)
    user.password_hash = hash_password(body.temporary_password)
    user.must_change_password = True
    user.token_version = (user.token_version or 0) + 1
    await log_event(
        db, AuditEventType.USER_MODIFIED, {"action": "PASSWORD_RESET", "user_id": str(user_id)}, actor_id=actor.user_id
    )
    return {"message": "Temporary password set. Existing sessions revoked."}


@router.post("/{user_id}/assignments", status_code=201)
async def add_assignment(
    user_id: uuid.UUID,
    body: AddAssignmentRequest,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("assignments")),
):
    await target_user(db, actor, user_id)
    await check_client(db, actor, body.client_id)
    assignment = await user_repository.add_assignment(db, user_id, body.client_id)
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        {"action": "ASSIGNMENT_ADDED", "user_id": str(user_id), "client_id": str(body.client_id)},
        actor_id=actor.user_id,
    )
    return {"assignment_id": assignment.assignment_id, "client_id": body.client_id}


@router.delete("/{user_id}/assignments/{client_id}", status_code=204)
async def remove_assignment(
    user_id: uuid.UUID,
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_permission("assignments")),
):
    await target_user(db, actor, user_id)
    await check_client(db, actor, client_id)
    await user_repository.revoke_assignment(db, user_id, client_id)
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        {"action": "ASSIGNMENT_REVOKED", "user_id": str(user_id), "client_id": str(client_id)},
        actor_id=actor.user_id,
    )


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
    user_id: uuid.UUID, db: AsyncSession = Depends(get_db), actor: User = Depends(require_permission("users"))
):
    return await target_user(db, actor, user_id)
