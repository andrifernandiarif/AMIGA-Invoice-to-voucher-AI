"""Versioned, editable voucher templates used for future approvals."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, status
from jinja2 import Environment, TemplateSyntaxError, meta
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, require_role
from app.models.user import User
from app.models.voucher import VoucherTemplate
from app.services.template_service import _DEFAULT_TEMPLATE_PATH

router = APIRouter(prefix="/admin", tags=["admin"])
_REQUIRED_FIELDS = {
    "authorization_disclaimer",
    "voucher_number",
    "voucher_name",
    "voucher_purpose",
    "voucher_period",
    "voucher_nominal",
    "line_items_table",
}
_LEGACY_FIELDS = {"vendor_name", "invoice_number", "line_items_table", "authorization_disclaimer"}


@router.get("/templates")
async def list_templates(db: AsyncSession = Depends(get_db), admin: User = Depends(require_role("SYSTEM_ADMIN"))):
    rows = (await db.execute(select(VoucherTemplate).order_by(VoucherTemplate.created_at.desc()))).scalars().all()
    return {
        "templates": [
            {
                "template_id": t.template_id,
                "version": t.version,
                "template_content": t.template_content,
                "is_active": t.is_active,
                "created_at": t.created_at,
            }
            for t in rows
        ],
        "default_template": _DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8"),
    }


@router.post("/templates", status_code=status.HTTP_201_CREATED)
async def create_template(
    version: str = Form(...),
    template_content: str = Form(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("SYSTEM_ADMIN")),
):
    """Save and activate a new version. Already generated vouchers remain unchanged."""
    version = version.strip()
    if not version or len(version) > 100:
        raise HTTPException(422, "Version must contain 1–100 characters.")
    if not template_content.strip() or len(template_content) > 250000:
        raise HTTPException(422, "Template must contain 1–250,000 characters.")
    try:
        variables = meta.find_undeclared_variables(Environment().parse(template_content))
    except TemplateSyntaxError as exc:
        raise HTTPException(422, f"Invalid template syntax at line {exc.lineno}: {exc.message}")
    if "authorization_disclaimer" not in variables:
        raise HTTPException(422, "Template must include authorization_disclaimer.")
    missing = _REQUIRED_FIELDS - variables
    if missing and not _LEGACY_FIELDS.issubset(variables):
        raise HTTPException(422, f"Template is missing required fields: {', '.join(sorted(missing))}")
    existing = await db.scalar(select(VoucherTemplate).where(VoucherTemplate.version == version))
    if existing:
        raise HTTPException(409, f"A template with version '{version}' already exists.")
    await db.execute(update(VoucherTemplate).where(VoucherTemplate.is_active.is_(True)).values(is_active=False))
    template = VoucherTemplate(
        template_id=uuid.uuid4(),
        version=version,
        template_content=template_content,
        is_active=True,
        created_by=admin.user_id,
        created_at=datetime.now(UTC),
    )
    db.add(template)
    await db.flush()
    return {"template_id": template.template_id, "version": template.version}
