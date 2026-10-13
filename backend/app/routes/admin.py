# backend/app/routes/admin.py

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.dependencies import get_db, require_role
from app.models.user import User
from app.models.voucher import VoucherTemplate

router = APIRouter(prefix="/admin", tags=["admin"])

# All required Jinja2 placeholders that every template version must contain
_REQUIRED_PLACEHOLDERS = [
    "{{vendor_name}}",
    "{{invoice_number}}",
    "{{invoice_date}}",
    "{{line_items_table}}",
    "{{tax_summary_table}}",
    "{{subtotal}}",
    "{{total_tax}}",
    "{{grand_total}}",
    "{{bank_account_number}}",
    "{{bank_name}}",
    "{{payment_reference}}",
    "{{authorization_disclaimer}}",
]


@router.post(
    "/templates",
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new voucher template version (System Admin only)",
)
async def create_template(
    version: str = Form(...),
    template_content: str = Form(...),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("SYSTEM_ADMIN")),
) -> dict:
    """
    Upload a new Jinja2 voucher template version.

    - Validates that all required placeholders are present.
    - Within a single transaction: activates the new template and
      deactivates the previously active one.
    - Returns 201 with `template_id` and `version`.
    """
    # ── Validate version string ───────────────────────────────────────────────
    version = version.strip()
    if not version:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="version must not be empty.",
        )

    # ── Check all required placeholders are present ───────────────────────────
    missing = [p for p in _REQUIRED_PLACEHOLDERS if p not in template_content]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Template is missing required placeholders: {missing}",
        )

    # ── Check version uniqueness ──────────────────────────────────────────────
    existing = await db.execute(
        select(VoucherTemplate).where(VoucherTemplate.version == version)
    )
    if existing.scalars().first() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A template with version '{version}' already exists.",
        )

    # ── Within a single transaction: deactivate old, insert new ──────────────
    # Deactivate any currently active template
    await db.execute(
        update(VoucherTemplate)
        .where(VoucherTemplate.is_active.is_(True))
        .values(is_active=False)
    )

    new_template = VoucherTemplate(
        template_id=uuid.uuid4(),
        version=version,
        template_content=template_content,
        is_active=True,
        created_by=admin.user_id,
        created_at=datetime.now(timezone.utc),
    )
    db.add(new_template)
    await db.flush()

    return {
        "template_id": str(new_template.template_id),
        "version": new_template.version,
    }
