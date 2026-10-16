"""Validation issues set EXCEPTION; work ownership and demo approval remain independent."""

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select

from app.models.audit import AuditEventType
from app.models.draft import Draft
from app.services.audit_service import log_event
from app.services.workflow import stage_of, validation_status
from app.validation.engine import ValidationEngine


async def validate_request(db, req, actor_id=None, *, data=None):
    await db.flush()
    draft = data.draft if data is not None else await db.scalar(select(Draft).where(Draft.request_id == req.request_id))
    if not draft:
        return []
    issues = [asdict(flag) for flag in await ValidationEngine.run(req.request_id, draft, db, data=data)]
    old = req.validation_issues or []
    if old != issues:
        await log_event(
            db,
            AuditEventType.FLAG_APPLIED if issues else AuditEventType.FLAG_DISMISSED,
            {"old_value": old, "new_value": issues, "advisory": True},
            req.request_id,
            actor_id,
        )
    stage = stage_of(req)
    if stage:
        req.workflow_stage = stage
    previous = req.status
    req.validation_issues = issues
    req.flags = [issue["flag_type"] for issue in issues]
    req.status = validation_status(req)
    if previous != req.status:
        req.updated_at = datetime.now(UTC)
        await log_event(
            db,
            AuditEventType.STATUS_CHANGED,
            {
                "from": previous.value,
                "to": req.status.value,
                "reason": "VALIDATION",
                "workflow_stage": req.workflow_stage,
            },
            req.request_id,
            actor_id,
        )
    await db.flush()
    return issues
