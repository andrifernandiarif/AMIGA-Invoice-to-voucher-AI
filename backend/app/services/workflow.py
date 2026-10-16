"""Validation severity and work ownership are separate from demo approval authority."""

from app.models.request import RequestStatus

REFINEMENT = "REFINEMENT"
FINANCE_REVIEW = "FINANCE_REVIEW"
UNAPPROVED = {RequestStatus.READY_FOR_REFINEMENT, RequestStatus.READY_FOR_REVIEW, RequestStatus.EXCEPTION}


def stage_of(req):
    if getattr(req, "status", None) not in UNAPPROVED:
        return None
    if getattr(req, "workflow_stage", None):
        return req.workflow_stage
    # Compatibility with pre-migration fixtures and historical records.
    if req.status == RequestStatus.READY_FOR_REFINEMENT:
        return REFINEMENT
    if req.status == RequestStatus.READY_FOR_REVIEW or getattr(req, "refinement_submitted_at", None):
        return FINANCE_REVIEW
    return None


def validation_status(req):
    stage = stage_of(req)
    if not stage:
        return req.status
    return (
        RequestStatus.EXCEPTION
        if req.flags
        else (RequestStatus.READY_FOR_REFINEMENT if stage == REFINEMENT else RequestStatus.READY_FOR_REVIEW)
    )


async def finish_extraction(db, req):
    """Shared finalization for asyncio and RQ; never regress a submitted/approved request."""
    from app.services.advisory_validation import validate_request

    if req.status not in UNAPPROVED | {RequestStatus.PROCESSING, RequestStatus.QUEUED}:
        return
    if stage_of(req) is None:
        req.workflow_stage = REFINEMENT
        req.status = RequestStatus.READY_FOR_REFINEMENT
    await validate_request(db, req)
