# backend/app/schemas/request_schemas.py

import uuid
from datetime import date, datetime

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
    request_type: str
    channel: str
    request_date: date
    status: RequestStatus
    flags: list[str]
    invoice_page_count: int | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
