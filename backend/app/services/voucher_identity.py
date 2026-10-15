"""Company-scoped numbering and immutable identity for a generated voucher."""

import re
import unicodedata
from datetime import UTC, datetime

from sqlalchemy import select, update
from app.models.client import Client
from app.models.user import User


def default_client_code(name, client_id):
    stem = (
        re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().upper())[:6]
        or "CLIENT"
    )
    return f"{stem}-{client_id.hex.upper()}"


async def prepare_voucher_identity(db, req):
    # Caller locks the request first; the atomic company update also serializes
    # two different requests for the same company. Both roll back on PDF failure.
    if not req.voucher_number:
        client = await db.scalar(select(Client).where(Client.client_id == req.client_id).with_for_update())
        if not client.client_code:
            client.client_code = default_client_code(client.client_name, client.client_id)
            await db.flush()
        sequence = await db.scalar(
            update(Client)
            .where(Client.client_id == req.client_id)
            .values(voucher_sequence=Client.voucher_sequence + 1)
            .returning(Client.voucher_sequence)
        )
        req.voucher_number = f"{client.client_code}-{sequence}"
    if not req.voucher_created_at:
        req.voucher_created_at = datetime.now(UTC)
    if req.submitter_snapshot is None:
        uploader = await db.scalar(select(User).where(User.user_id == req.uploader_id))
        req.submitter_snapshot = {
            "full_name": uploader.full_name or "",
            "job_title": uploader.job_title or "",
            "signature_data": uploader.signature_data or "",
        }
    await db.flush()
