# backend/app/routes/clients.py
"""
Client management endpoints.
GET  /clients        — list all active clients (any authenticated user)
POST /clients        — create a new client (any authenticated user, for dev convenience)
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_current_user, get_db
from app.models.client import Client
from app.models.user import User

router = APIRouter(prefix="/clients", tags=["clients"])


class ClientOut(BaseModel):
    client_id: uuid.UUID
    client_name: str

    model_config = {"from_attributes": True}


class ClientCreate(BaseModel):
    client_name: str


@router.get("", response_model=list[ClientOut])
async def list_clients(
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Return all active clients. Use the client_id when calling POST /requests."""
    result = await db.execute(
        select(Client).where(Client.is_active == True).order_by(Client.client_name)
    )
    return result.scalars().all()


@router.post("", response_model=ClientOut, status_code=status.HTTP_201_CREATED)
async def create_client(
    body: ClientCreate,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
):
    """Create a new client company."""
    # Check for duplicate name
    existing = await db.execute(
        select(Client).where(Client.client_name == body.client_name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, f"Client '{body.client_name}' already exists.")

    client = Client(
        client_id=uuid.uuid4(),
        client_name=body.client_name,
        is_active=True,
    )
    db.add(client)
    await db.commit()
    await db.refresh(client)
    return client
