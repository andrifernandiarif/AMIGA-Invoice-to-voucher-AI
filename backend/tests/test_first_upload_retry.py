"""First-upload extraction uses original documents even when local PDF augmentation is unavailable."""

import builtins
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select

from app.models.draft import Draft
from app.models.extraction_job import ExtractionJobStatus
from app.providers.bedrock import BedrockExtractionProvider, _pdf_text
from app.services import extraction_worker
from app.services.combine_invoices import combine_invoices
from app.services.extraction_status import describe_extraction
from tests.test_approval import db_session  # noqa: F401
from tests.test_combined_extraction import invoices
from tests.test_manual_workflow import client_for, seed
from tests.test_organization_workflows import valid_pdf

# ruff: noqa: F811


async def test_pdf_extraction_works_without_optional_local_pdf_parser():
    original_import = builtins.__import__

    def without_pypdf(name, *args, **kwargs):
        if name == "pypdf":
            raise ModuleNotFoundError("No module named 'pypdf'")
        return original_import(name, *args, **kwargs)

    provider = object.__new__(BedrockExtractionProvider)
    provider.model_id, provider._is_nova = "amazon.nova-lite-v1:0", True
    provider._client = MagicMock()
    first, second = invoices()

    def converse(**kwargs):
        blocks = kwargs["messages"][0]["content"]
        assert [b for b in blocks if "document" in b]
        assert not any("Untrusted source text" in b.get("text", "") for b in blocks)
        data = blocks[0]["document"]["source"]["bytes"]
        return {"output": {"message": {"content": [{"text": (first if data == b"one" else second).model_dump_json()}]}}}

    provider._client.converse.side_effect = converse
    with patch("builtins.__import__", side_effect=without_pypdf):
        result = await provider.extract([(b"one", "application/pdf"), (b"two", "application/pdf")], uuid.uuid4())
        assert _pdf_text([(b"image", "image/png")]) == []
    assert len(result.source_invoices) == 2
    assert str(result.header.total_amount) == "4497.30"


async def test_first_upload_retries_transient_failure_automatically_with_original_files(db_session):
    data = await seed(db_session)
    first_pdf, second_pdf = valid_pdf(), valid_pdf()
    async with await client_for(db_session, data["uploader"]) as client:
        with patch("app.routes.requests.file_store.upload", AsyncMock()) as upload:
            response = await client.post(
                "/requests",
                data={
                    "client_id": str(data["client"].client_id),
                    "request_date": "2026-10-16",
                    "voucher_purpose": "First-upload retry regression",
                    "approvers": '[{"full_name":"Client Manager","job_title":"Manager"}]',
                },
                files=[
                    ("files", ("first.pdf", first_pdf, "application/pdf")),
                    ("files", ("second.pdf", second_pdf, "application/pdf")),
                ],
            )
        assert response.status_code == 201
        assert upload.await_count == 2
        rid = uuid.UUID(response.json()["request_id"])
        assert (await client.get(f"/requests/{rid}")).json()["extraction"]["attempt"] == 1

        @asynccontextmanager
        async def factory():
            yield db_session

        result = combine_invoices(list(invoices()))
        provider = MagicMock()
        provider.extract = AsyncMock(side_effect=[ValueError("Temporary model response failure"), result])
        download = AsyncMock(side_effect=[first_pdf, second_pdf, first_pdf, second_pdf])
        with (
            patch.object(extraction_worker, "AsyncSessionLocal", factory),
            patch.object(extraction_worker, "get_extraction_provider", return_value=provider),
            patch.object(extraction_worker.file_store, "download", download),
        ):
            job = await extraction_worker._claim_pending_job(db_session)
            assert job.request_id == rid
            await extraction_worker._process_job(job)
            await db_session.refresh(job)
            detail = (await client.get(f"/requests/{rid}")).json()
            assert job.status == ExtractionJobStatus.PENDING
            assert detail["status"] == "PROCESSING"
            assert detail["extraction"]["attempt"] == 2
            assert "Retrying extraction automatically" in detail["extraction"]["message"]
            job = await extraction_worker._claim_pending_job(db_session)
            await extraction_worker._process_job(job)
        detail = (await client.get(f"/requests/{rid}")).json()
        await db_session.refresh(job)
        assert job.status == ExtractionJobStatus.DONE and job.error_detail is None
        assert detail["status"] == "READY_FOR_REFINEMENT"
        assert detail["extraction"]["message"] is None
        assert len(detail["draft"]["source_invoices"]) == 2
        assert len(detail["draft"]["line_items"]) == 7
        assert len((await db_session.scalars(select(Draft).where(Draft.request_id == rid))).all()) == 1
        calls = provider.extract.call_args_list
        assert calls[0].kwargs["evidence_files"] == calls[1].kwargs["evidence_files"]
        assert len(calls) == 2 and download.await_count == 4
        assert download.call_args_list[:2] == download.call_args_list[2:]


def test_retry_progress_never_exposes_raw_worker_errors():
    description = describe_extraction(
        SimpleNamespace(
            status=ExtractionJobStatus.FAILED, retry_count=3, error_detail="No module named 'pypdf'; secret credential"
        )
    )
    assert description["attempt"] == 3
    assert "secret" not in description["message"] and "pypdf" not in description["message"]
    assert "could not finish" in description["message"]
