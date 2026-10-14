import enum
import uuid
from datetime import date

from sqlalchemy import Date, Enum as SAEnum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class LineExtractionStatus(str, enum.Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"


class HeaderFields(Base):
    __tablename__ = "header_fields"

    header_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drafts.draft_id"), unique=True, nullable=False
    )
    voucher_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    voucher_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    voucher_purpose: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    voucher_period: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vendor_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    subtotal: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    total_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    tax_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    fee_amount: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    rounding_adj: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)


class LineItem(Base):
    __tablename__ = "line_items"

    line_item_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("drafts.draft_id"), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    item_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(50), nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    line_total: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    extraction_status: Mapped[LineExtractionStatus] = mapped_column(
        SAEnum(LineExtractionStatus, name="lineextractionstatus"), nullable=False
    )


class PaymentDetails(Base):
    __tablename__ = "payment_details"

    payment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    draft_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("drafts.draft_id"), unique=True, nullable=False
    )
    bank_account_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    bank_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    payment_reference: Mapped[str | None] = mapped_column(String(300), nullable=True)
