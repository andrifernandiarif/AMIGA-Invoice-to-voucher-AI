# backend/app/routes/test_extract.py
"""
TEST ENDPOINT — remove before production.
POST /test/extract  — upload files and get raw Bedrock extraction JSON back.
No auth, no DB writes, no S3. Pure extraction smoke test.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, UploadFile, Depends
from app.permissions import require_permission
from fastapi.responses import JSONResponse

from app.providers import get_extraction_provider

router = APIRouter(prefix="/test", tags=["test"])

ACCEPTED = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}


@router.post("/extract", dependencies=[Depends(require_permission("templates"))])
async def test_extract(files: List[UploadFile]):
    """
    Upload one or more invoice files.
    Returns the raw ExtractionResult JSON from Bedrock.
    No auth required — for local testing only.
    """
    from uuid import uuid4
    evidence = []
    for f in files:
        ct = (f.content_type or "").lower()
        if ct not in ACCEPTED:
            return JSONResponse(
                status_code=415,
                content={"error": f"'{f.filename}': unsupported type '{ct}'"}
            )
        data = await f.read()
        evidence.append((data, ct))

    provider = get_extraction_provider()
    result = await provider.extract(evidence, uuid4())

    return result.model_dump(mode="json")
