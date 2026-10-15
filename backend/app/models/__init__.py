from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# Import all models so Alembic metadata and SQLAlchemy registry are populated
from app.models.audit import AuditEvent, AuditEventType, FieldRevision  # noqa: E402, F401
from app.models.client import Client  # noqa: E402, F401
from app.models.client_assignment import ClientAssignment  # noqa: E402, F401
from app.models.draft import Draft, ExtractionStatus  # noqa: E402, F401
from app.models.evidence_file import EvidenceFile  # noqa: E402, F401
from app.models.invoice_fields import (  # noqa: E402, F401
    HeaderFields,
    LineExtractionStatus,
    LineItem,
    PaymentDetails,
)
from app.models.request import InvoiceRequest, RequestStatus  # noqa: E402, F401
from app.models.telemetry import MetricType, TelemetryRecord  # noqa: E402, F401
from app.models.user import User, UserRole  # noqa: E402, F401
from app.models.voucher import VoucherPDF, VoucherTemplate  # noqa: E402, F401
from app.models.extraction_job import ExtractionJob, ExtractionJobStatus  # noqa: E402, F401
from app.models.tax_assignment import IndonesianTaxType, TaxAssignment  # noqa: E402, F401

from app.models.chunk_embedding import ChunkEmbedding  # noqa: E402, F401
