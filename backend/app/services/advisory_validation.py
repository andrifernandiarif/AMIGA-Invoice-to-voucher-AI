"""Validation issues are advisory and never change workflow status."""

from dataclasses import asdict
from sqlalchemy import select
from app.models.draft import Draft
from app.models.audit import AuditEventType
from app.services.audit_service import log_event
from app.validation.engine import ValidationEngine


async def validate_request(db, req, actor_id=None):
    await db.flush()
    draft = await db.scalar(select(Draft).where(Draft.request_id == req.request_id))
    if not draft:
        return []
    issues = [asdict(flag) for flag in await ValidationEngine.run(req.request_id, draft, db)]
    old = req.validation_issues or []
    if old != issues:
        await log_event(
            db,
            AuditEventType.FLAG_APPLIED if issues else AuditEventType.FLAG_DISMISSED,
            {"old_value": old, "new_value": issues, "advisory": True},
            req.request_id,
            actor_id,
        )
    req.validation_issues = issues
    req.flags = [issue["flag_type"] for issue in issues]
    await db.flush()
    return issues
