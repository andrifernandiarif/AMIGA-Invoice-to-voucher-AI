# backend/app/routes/clients.py
"""
Client management endpoints.
GET  /clients        — list all active clients (any authenticated user)
POST /clients        — create a new client (any authenticated user, for dev convenience)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db, require_role
from app.models.audit import AuditEventType
from app.services.audit_service import log_event
from app.models.client import Client
from app.services.voucher_identity import default_client_code
from app.models.client_assignment import ClientAssignment
from app.models.user import User

router = APIRouter(prefix="/clients", tags=["clients"])


class ClientOut(BaseModel):
    client_id: uuid.UUID
    client_name: str
    client_code: str | None = None

    model_config = {"from_attributes": True}


class ClientCreate(BaseModel):
    client_name: str = Field(min_length=1, max_length=300)
    client_code: str | None = Field(default=None, pattern=r"^[A-Z0-9][A-Z0-9-]{0,39}$")


@router.get("", response_model=list[ClientOut])
async def list_clients(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return all active clients. Use the client_id when calling POST /requests."""
    result = await db.execute(
        select(Client)
        .join(ClientAssignment, ClientAssignment.client_id == Client.client_id)
        .where(
            Client.is_active.is_(True), ClientAssignment.is_active.is_(True), ClientAssignment.user_id == _user.user_id
        )
        .order_by(Client.client_name)
    )
    if _user.role.value == "SUPER_ADMIN":
        return (await db.scalars(select(Client).where(Client.is_active.is_(True)).order_by(Client.client_name))).all()
    return result.scalars().all()


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Create a new client company."""
    if _user.role.value not in ("SUPER_ADMIN", "SYSTEM_ADMIN"):
        raise HTTPException(403, "Only administrators can create clients.")
    # Check for duplicate name
    existing = await db.execute(select(Client).where(Client.client_name == body.client_name))
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Client '{body.client_name}' already exists.")

    client_id = uuid.uuid4()
    code = body.client_code or default_client_code(body.client_name, client_id)
    if await db.scalar(select(Client).where(Client.client_code == code)):
        raise HTTPException(409, "Company code already exists.")
    client = Client(
        client_id=client_id,
        client_code=code,
        client_name=body.client_name,
        is_active=True,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client


@router.get("/all", response_model=list[ClientOut])
async def all_clients(db: AsyncSession = Depends(get_db), admin: User = Depends(require_role("SYSTEM_ADMIN"))):
    return (await db.scalars(select(Client).where(Client.is_active.is_(True)).order_by(Client.client_name))).all()


@router.delete("/{client_id}", status_code=204)
async def remove_client(
    client_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    actor: User = Depends(require_role("SYSTEM_ADMIN")),
):
    """Archive a company and revoke access without deleting financial history."""
    client = await db.get(Client, client_id)
    if client is None:
        raise HTTPException(404, "Company not found.")
    if not client.is_active:
        return
    client.is_active = False
    await db.execute(
        update(ClientAssignment)
        .where(ClientAssignment.client_id == client_id, ClientAssignment.is_active.is_(True))
        .values(is_active=False, revoked_at=datetime.now(UTC))
    )
    await log_event(
        db,
        AuditEventType.USER_MODIFIED,
        {"action": "COMPANY_REMOVED", "client_id": str(client_id)},
        actor_id=actor.user_id,
    )
