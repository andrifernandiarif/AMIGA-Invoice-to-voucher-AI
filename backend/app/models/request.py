import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class RequestStatus(str, enum.Enum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    READY_FOR_REFINEMENT = "READY_FOR_REFINEMENT"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    EXCEPTION = "EXCEPTION"
    AMIGA_REVIEWED = "AMIGA_REVIEWED"
    SENT_TO_CLIENT = "SENT_TO_CLIENT"
    REJECTED = "REJECTED"


class InvoiceRequest(Base):
    __tablename__ = "requests"

    request_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    client_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("clients.client_id"), nullable=False)
    uploader_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    assigned_reviewer: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )
    request_type: Mapped[str] = mapped_column(String(100), nullable=False)
    channel: Mapped[str] = mapped_column(String(100), nullable=False)
    request_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(SAEnum(RequestStatus, name="requeststatus"), nullable=False)
    workflow_stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    flags: Mapped[list] = mapped_column(ARRAY(String), nullable=False, server_default="{}")
    invoice_file_key: Mapped[str] = mapped_column(String(500), nullable=False)
    invoice_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refinement_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    voucher_purpose: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    approvers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    voucher_number: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    voucher_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    submitter_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    validation_issues: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
