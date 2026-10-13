import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Enum as SAEnum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class MetricType(str, enum.Enum):
    EXTRACTION_DURATION = "EXTRACTION_DURATION"
    PAGE_LOAD_DURATION = "PAGE_LOAD_DURATION"
    PDF_GENERATION_DURATION = "PDF_GENERATION_DURATION"


class TelemetryRecord(Base):
    __tablename__ = "telemetry_records"

    record_id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    metric_type: Mapped[MetricType] = mapped_column(SAEnum(MetricType, name="metrictype"), nullable=False)
    subject_id: Mapped[str] = mapped_column(String(500), nullable=False)
    duration_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_breach: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    breach_threshold_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
