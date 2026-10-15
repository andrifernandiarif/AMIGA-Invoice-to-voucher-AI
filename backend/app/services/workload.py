"""Shared WIB deadline semantics for dashboards and allocation responses."""

from datetime import datetime
from zoneinfo import ZoneInfo
from app.models.request import RequestStatus

CLOSED = {RequestStatus.AMIGA_REVIEWED, RequestStatus.SENT_TO_CLIENT, RequestStatus.REJECTED}


def today_wib():
    return datetime.now(ZoneInfo("Asia/Jakarta")).date()


def overdue(request):
    return bool(request.due_date and request.due_date < today_wib() and request.status not in CLOSED)
