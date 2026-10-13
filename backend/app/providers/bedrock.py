# backend/app/providers/bedrock.py
"""
Amazon Bedrock extraction provider.

Supports:
  - Amazon Nova Lite / Nova Pro  (amazon.nova-lite-v1:0, amazon.nova-pro-v1:0)
  - Anthropic Claude 3.x          (anthropic.claude-3-*)

Model is selected via BEDROCK_MODEL_ID env var. Credentials come from the
AWS credential provider chain (env vars → IAM role → ~/.aws/credentials).
NEVER hardcode keys here.

--- EDIT THE PROMPT BELOW to change what Bedrock extracts and the JSON schema ---
"""
from __future__ import annotations

import asyncio
import json
import logging
from functools import partial
from uuid import UUID

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings
from app.providers.base import ExtractionProvider
from app.schemas.extraction import ExtractionResult

logger = logging.getLogger(__name__)


# =============================================================================
# EXTRACTION PROMPT — EDIT THIS to change the JSON schema / extraction rules
# =============================================================================
EXTRACTION_SYSTEM_PROMPT = """You are an expert invoice data extraction assistant.
Your job is to read one or more invoice documents or screenshots and extract
structured financial data from them.

Return ONLY a valid JSON object matching the schema below.
Do NOT include any explanation, markdown fences, or extra text — JSON only.

REQUIRED JSON SCHEMA:
{
  "header": {
    "vendor_name": "<string or null>",
    "invoice_number": "<string or null>",
    "invoice_date": "<YYYY-MM-DD or null>",
    "currency": "<ISO 4217 e.g. IDR, USD, or null>",
    "subtotal": <number or null>,
    "total_amount": <number or null>,
    "tax_amount": <number or null>,
    "fee_amount": <number or null>,
    "rounding_adj": <number or null>
  },
  "line_items": [
    {
      "sequence_number": <integer starting at 1>,
      "description": "<string or null>",
      "quantity": <number or null>,
      "unit": "<string e.g. pcs, kg, paket or null>",
      "unit_price": <number or null>,
      "line_total": <number or null>
    }
  ],
  "payment_details": {
    "bank_account_number": "<string or null>",
    "bank_name": "<string or null>",
    "payment_reference": "<string or null>"
  },
  "extraction_prompt_version": "bedrock-v1.0",
  "raw_response": null
}

EXTRACTION RULES:
1. Use null for any field you cannot find. NEVER invent or guess values.
2. All numeric values must be plain numbers — no currency symbols, no commas.
3. Dates must be in YYYY-MM-DD format.
4. sequence_number starts at 1 and increases by 1 for each line item.
5. Extract EVERY line item visible in the documents.
6. If multiple documents show the same invoice, merge into one result.
7. For Indonesian invoices: subtotal is "DPP", tax_amount is "PPN", total_amount is "Total".
"""
# =============================================================================


def _build_nova_request(evidence_files: list[tuple[bytes, str]]) -> dict:
    """
    Build a Nova Lite/Pro InvokeModel request body.
    Nova uses the Converse-compatible messages format via InvokeModel.
    https://docs.aws.amazon.com/nova/latest/userguide/using-images.html
    """
    content = []
    for i, (file_bytes, content_type) in enumerate(evidence_files):
        if content_type == "application/pdf":
            import base64
            content.append({
                "document": {
                    "format": "pdf",
                    "name": f"invoice_{i+1}",
                    "source": {"bytes": file_bytes},
                }
            })
        else:
            fmt_map = {"image/png": "png", "image/jpeg": "jpeg", "image/jpg": "jpeg"}
            fmt = fmt_map.get(content_type, "jpeg")
            content.append({
                "image": {
                    "format": fmt,
                    "source": {"bytes": file_bytes},
                }
            })

    content.append({
        "text": "Extract all invoice information from the document(s) above and return the JSON."
    })

    return {
        "system": [{"text": EXTRACTION_SYSTEM_PROMPT}],
        "messages": [{"role": "user", "content": content}],
        "inferenceConfig": {"maxTokens": 4096, "temperature": 0},
    }


def _build_claude_request(evidence_files: list[tuple[bytes, str]]) -> dict:
    """Build a Claude 3.x Converse API request body."""
    import base64
    content = []
    for file_bytes, content_type in evidence_files:
        if content_type == "application/pdf":
            b64 = base64.standard_b64encode(file_bytes).decode()
            content.append({
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            })
        else:
            fmt_map = {"image/png": "image/png", "image/jpeg": "image/jpeg", "image/jpg": "image/jpeg"}
            mt = fmt_map.get(content_type, "image/jpeg")
            b64 = base64.standard_b64encode(file_bytes).decode()
            content.append({
                "type": "image",
                "source": {"type": "base64", "media_type": mt, "data": b64},
            })
    content.append({"type": "text", "text": "Extract all invoice data and return the JSON."})

    return {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 4096,
        "temperature": 0,
        "system": EXTRACTION_SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": content}],
    }


class BedrockExtractionProvider(ExtractionProvider):
    """
    Calls Amazon Bedrock Runtime via boto3.
    Auto-detects Nova vs Claude from BEDROCK_MODEL_ID.
    """

    def __init__(self) -> None:
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=settings.AWS_REGION,
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID or None,
            aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY or None,
        )
        self.model_id = settings.BEDROCK_MODEL_ID
        self._is_nova = "nova" in self.model_id.lower()

    def _invoke_sync(self, evidence_files: list[tuple[bytes, str]]) -> str:
        """Synchronous Bedrock call — offloaded to thread pool by extract()."""
        if self._is_nova:
            # Nova models: use Converse API
            body = _build_nova_request(evidence_files)
            response = self._client.converse(
                modelId=self.model_id,
                system=body["system"],
                messages=body["messages"],
                inferenceConfig=body["inferenceConfig"],
            )
            output = response["output"]["message"]["content"]
            for block in output:
                if "text" in block:
                    return block["text"].strip()
            return "{}"
        else:
            # Claude models: use InvokeModel
            import json as _json
            body = _build_claude_request(evidence_files)
            response = self._client.invoke_model(
                modelId=self.model_id,
                contentType="application/json",
                accept="application/json",
                body=_json.dumps(body).encode(),
            )
            resp_body = _json.loads(response["body"].read())
            # Claude returns content as a list of blocks
            for block in resp_body.get("content", []):
                if block.get("type") == "text":
                    return block["text"].strip()
            return "{}"

    async def extract(
        self,
        evidence_files: list[tuple[bytes, str]],
        request_id: UUID,
    ) -> ExtractionResult:
        loop = asyncio.get_event_loop()
        try:
            raw_text = await loop.run_in_executor(
                None, partial(self._invoke_sync, evidence_files)
            )
        except (BotoCoreError, ClientError) as exc:
            logger.error("Bedrock call failed for request %s: %s", request_id, exc)
            raise

        # Strip markdown fences if model added them despite instructions
        raw_text = raw_text.strip()
        if raw_text.startswith("```"):
            lines = raw_text.split("\n")
            raw_text = "\n".join(
                l for l in lines if not l.strip().startswith("```")
            ).strip()

        try:
            parsed = json.loads(raw_text)
            parsed["raw_response"] = raw_text
            result = ExtractionResult.model_validate(parsed)
        except Exception as exc:
            logger.error(
                "Failed to parse Bedrock response for %s: %s\nRaw:\n%s",
                request_id, exc, raw_text,
            )
            result = ExtractionResult(
                extraction_prompt_version="bedrock-v1.0",
                raw_response=raw_text,
            )

        logger.info(
            "Bedrock extraction complete for %s — %d line items, header=%s",
            request_id,
            len(result.line_items),
            result.header.vendor_name if result.header else "None",
        )
        return result
