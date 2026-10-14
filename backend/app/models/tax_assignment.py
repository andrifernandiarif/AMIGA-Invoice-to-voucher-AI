# backend/app/models/tax_assignment.py

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum as SAEnum, ForeignKey, Numeric
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class IndonesianTaxType(str, enum.Enum):
    PPH21 = "PPH21"
    PPH22 = "PPH22"
    PPH23 = "PPH23"
    PPH25 = "PPH25"
    PPN = "PPN"
    NO_TAX = "NO_TAX"


# Hardcoded Indonesian tax rates (Decimal values stored as strings for safe import)
TAX_RATES: dict[IndonesianTaxType, str] = {
    IndonesianTaxType.PPH21: "0.0500",
    IndonesianTaxType.PPH22: "0.0150",
    IndonesianTaxType.PPH23: "0.0200",
    IndonesianTaxType.PPH25: "0.0200",
    IndonesianTaxType.PPN:   "0.1100",
    IndonesianTaxType.NO_TAX: "0.0000",
}


class TaxAssignment(Base):
    __tablename__ = "tax_assignments"

    tax_assignment_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    line_item_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("line_items.line_item_id"), unique=True, nullable=False
    )
    tax_type: Mapped[IndonesianTaxType] = mapped_column(
        SAEnum(IndonesianTaxType, name="indonesiantaxtype", create_type=False), nullable=False
    )
    tax_rate: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    tax_amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assigned_by: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False
    )
