import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class AuditEventType(str, enum.Enum):
    REQUEST_CREATED = "REQUEST_CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    FLAG_APPLIED = "FLAG_APPLIED"
    FLAG_DISMISSED = "FLAG_DISMISSED"
    FIELD_REVISED = "FIELD_REVISED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DUPLICATE_DISMISSED = "DUPLICATE_DISMISSED"
    REALLOCATED = "REALLOCATED"
    VOUCHER_GENERATED = "VOUCHER_GENERATED"
    VOUCHER_DOWNLOADED = "VOUCHER_DOWNLOADED"
    UNAUTHORIZED_APPROVAL_ATTEMPT = "UNAUTHORIZED_APPROVAL_ATTEMPT"
    USER_MODIFIED = "USER_MODIFIED"
    PROCESSING_FAILED = "PROCESSING_FAILED"


class AuditEvent(Base):
    __tablename__ = "audit_events"

    event_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("requests.request_id"), nullable=True
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        SAEnum(AuditEventType, name="auditeventtype"), nullable=False
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FieldRevision(Base):
    __tablename__ = "field_revisions"

    revision_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    request_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("requests.request_id"), nullable=False
    )
    field_path: Mapped[str] = mapped_column(Text, nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    revised_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
