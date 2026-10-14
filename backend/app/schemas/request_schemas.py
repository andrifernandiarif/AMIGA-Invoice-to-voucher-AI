# backend/app/schemas/request_schemas.py

import uuid
from datetime import date, datetime
from typing import Optional, List

from pydantic import BaseModel

from app.models.request import RequestStatus


class CreateRequestResponse(BaseModel):
    request_id: uuid.UUID
    status: RequestStatus
    created_at: datetime

    model_config = {"from_attributes": True}


class RequestSummary(BaseModel):
    request_id: uuid.UUID
    client_id: uuid.UUID
    client_name: Optional[str] = None
    request_type: str
    channel: str
    request_date: date
    status: RequestStatus
    flags: List[str]
    invoice_page_count: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class HeaderFieldsResponse(BaseModel):
    header_id: Optional[uuid.UUID] = None
    draft_id: Optional[uuid.UUID] = None
    vendor_name: Optional[str] = None
    invoice_number: Optional[str] = None
    invoice_date: Optional[str] = None
    currency: Optional[str] = None
    subtotal: Optional[str] = None
    total_amount: Optional[str] = None
    tax_amount: Optional[str] = None
    fee_amount: Optional[str] = None
    rounding_adj: Optional[str] = None

    model_config = {"from_attributes": True}


class LineItemResponse(BaseModel):
    line_item_id: uuid.UUID
    draft_id: uuid.UUID
    sequence_number: int
    description: Optional[str] = None
    quantity: Optional[str] = None
    unit: Optional[str] = None
    unit_price: Optional[str] = None
    line_total: Optional[str] = None
    extraction_status: Optional[str] = None

    model_config = {"from_attributes": True}


class PaymentDetailsResponse(BaseModel):
    payment_id: Optional[uuid.UUID] = None
    draft_id: Optional[uuid.UUID] = None
    bank_account_number: Optional[str] = None
    bank_name: Optional[str] = None
    payment_reference: Optional[str] = None

    model_config = {"from_attributes": True}


class DraftResponse(BaseModel):
    draft_id: uuid.UUID
    ai_label_visible: bool
    extraction_status: str
    header_fields: Optional[HeaderFieldsResponse] = None
    line_items: List[LineItemResponse]
    payment_details: Optional[PaymentDetailsResponse] = None


class EvidenceFileResponse(BaseModel):
    evidence_id: uuid.UUID
    request_id: uuid.UUID
    file_key: str
    original_filename: str
    content_type: str
    file_size_bytes: int
    upload_order: int
    uploaded_at: datetime

    model_config = {"from_attributes": True}


class RequestDetailResponse(BaseModel):
    request_id: uuid.UUID
    client_id: uuid.UUID
    status: RequestStatus
    flags: List[str]
    evidence_files: List[dict]
    draft: Optional[DraftResponse] = None

    model_config = {"from_attributes": True}


class FieldRevisionResponse(BaseModel):
    revision_id: uuid.UUID
    request_id: uuid.UUID
    field_path: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    actor_id: uuid.UUID
    revised_at: datetime

    model_config = {"from_attributes": True}


class TelemetryRecordResponse(BaseModel):
    record_id: uuid.UUID
    metric_type: str
    subject_id: str
    duration_ms: int
    page_count: Optional[int] = None
    is_breach: bool
    breach_threshold_ms: Optional[int] = None
    measured_at: datetime

    model_config = {"from_attributes": True}
