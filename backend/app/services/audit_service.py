# backend/app/services/audit_service.py

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditEvent, AuditEventType


async def log_event(
    db: AsyncSession,
    event_type: AuditEventType,
    payload: dict,
    request_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
) -> AuditEvent:
    """Append an immutable audit event. Caller manages session commit."""
    event = AuditEvent(
        event_id=uuid.uuid4(),
        request_id=request_id,
        event_type=event_type,
        actor_id=actor_id,
        payload=payload,
        occurred_at=datetime.now(timezone.utc),
    )
    db.add(event)
    await db.flush()
    return event
