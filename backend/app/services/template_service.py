# backend/app/services/template_service.py

import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.voucher import VoucherTemplate

# Path to the bundled default template file (relative to this module)
_DEFAULT_TEMPLATE_PATH = Path(__file__).parent.parent / "templates" / "voucher_default.html"


async def load_active_template(db: AsyncSession) -> VoucherTemplate:
    """
    Return the single VoucherTemplate row where is_active=True.
    Raises HTTPException(404) if no active template exists.
    """
    result = await db.execute(
        select(VoucherTemplate).where(VoucherTemplate.is_active.is_(True))
    )
    template = result.scalars().first()
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active voucher template found.",
        )
    return template


async def seed_default_template(db: AsyncSession) -> None:
    """
    Idempotent: inserts the bundled voucher_default.html as version='v1.0'
    with is_active=True only if no active template already exists.
    """
    result = await db.execute(
        select(VoucherTemplate).where(VoucherTemplate.is_active.is_(True))
    )
    if result.scalars().first() is not None:
        # An active template already exists — nothing to do.
        return

    content = _DEFAULT_TEMPLATE_PATH.read_text(encoding="utf-8")
    template = VoucherTemplate(
        template_id=uuid.uuid4(),
        version="v1.0",
        template_content=content,
        is_active=True,
        created_by=None,
        created_at=datetime.now(timezone.utc),
    )
    db.add(template)
    await db.flush()
