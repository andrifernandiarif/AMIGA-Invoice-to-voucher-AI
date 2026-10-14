# backend/app/models/tax_assignment.py

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Numeric, String
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class IndonesianTaxType(str, enum.Enum):
    PPH21 = "PPH21"
    PPH22 = "PPH22"
    PPH23 = "PPH23"
    PPH26 = "PPH26"
    PPH4_2 = "PPH4_2"
    PPH25 = "PPH25"
    PPN = "PPN"
    NO_TAX = "NO_TAX"


class TaxAssignment(Base):
    __tablename__ = "tax_assignments"

    tax_assignment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    line_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("line_items.line_item_id"), unique=True, nullable=False
    )
    tax_type: Mapped[IndonesianTaxType] = mapped_column(
        SAEnum(IndonesianTaxType, name="indonesiantaxtype", create_type=False), nullable=False
    )
    tax_rate: Mapped[float] = mapped_column(Numeric(7, 6), nullable=False)
    tax_amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    tax_base: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    base_is_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tax_effect: Mapped[str] = mapped_column(String(10), nullable=False, default="ADD")
    rate_profile: Mapped[str | None] = mapped_column(String(80), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_by: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
