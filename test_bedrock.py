#!/usr/bin/env python3
"""
Quick Bedrock extraction test.
Usage:
    python test_bedrock.py invoice.pdf
    python test_bedrock.py invoice.pdf receipt.jpg screenshot.png

Drop any PDF/JPG/PNG files as arguments and get the extracted JSON back.
No DB, no S3, no auth — pure Bedrock call.
"""
import base64
import json
import sys
from pathlib import Path

import boto3

# ── Config (reads from env or hardcode for quick test) ────────────────────────
AWS_REGION = "ap-southeast-1"
MODEL_ID    = "apac.amazon.nova-lite-v1:0"

# ── Prompt — edit this to change what gets extracted ──────────────────────────
SYSTEM_PROMPT = """You are an expert invoice data extraction assistant.
Read the provided invoice documents or images and extract structured financial data.

Return ONLY a valid JSON object — no explanations, no markdown fences, just raw JSON:

{
  "header": {
    "vendor_name": "<string or null>",
    "invoice_number": "<string or null>",
    "invoice_date": "<YYYY-MM-DD or null>",
    "currency": "<ISO 4217 e.g. IDR, USD or null>",
    "subtotal": <number or null>,
    "total_amount": <number or null>,
    "tax_amount": <number or null>
  },
  "line_items": [
    {
      "sequence_number": <integer starting at 1>,
      "description": "<string or null>",
      "quantity": <number or null>,
      "unit": "<string or null>",
      "unit_price": <number or null>,
      "line_total": <number or null>
    }
  ],
  "payment_details": {
    "bank_account_number": "<string or null>",
    "bank_name": "<string or null>",
    "payment_reference": "<string or null>"
  }
}

Rules:
- Use null for any field you cannot find. NEVER fabricate values.
- Numbers must be plain digits — no currency symbols, no commas.
- Extract ALL line items you can see.
- For Indonesian invoices: DPP = subtotal, PPN = tax_amount.
"""

CONTENT_TYPES = {
    ".pdf":  "application/pdf",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
}


def build_content_blocks(files: list[Path]) -> list[dict]:
    blocks = []
    for f in files:
        ext = f.suffix.lower()
        ct = CONTENT_TYPES.get(ext)
        if ct is None:
            print(f"  ⚠ Skipping unsupported file: {f.name}")
            continue

        data = f.read_bytes()
        print(f"  📄 {f.name}  ({len(data) // 1024} KB, {ct})")

        if ct == "application/pdf":
            blocks.append({
                "document": {
                    "format": "pdf",
                    "name": f.stem,
                    "source": {"bytes": data},
                }
            })
        else:
            fmt = "png" if ext == ".png" else "jpeg"
            blocks.append({
                "image": {
                    "format": fmt,
                    "source": {"bytes": data},
                }
            })

    blocks.append({
        "text": "Extract all invoice information from the document(s) above and return the JSON."
    })
    return blocks


def extract(files: list[Path]) -> dict:
    client = boto3.client(
        "bedrock-runtime",
        region_name=AWS_REGION,
    )

    content = build_content_blocks(files)

    print(f"\n🚀 Calling Bedrock  model={MODEL_ID} ...")
    response = client.converse(
        modelId=MODEL_ID,
        system=[{"text": SYSTEM_PROMPT}],
        messages=[{"role": "user", "content": content}],
        inferenceConfig={"maxTokens": 4096, "temperature": 0},
    )

    raw_text = ""
    for block in response["output"]["message"]["content"]:
        if "text" in block:
            raw_text = block["text"].strip()
            break

    # Strip accidental markdown fences
    if raw_text.startswith("```"):
        lines = raw_text.splitlines()
        raw_text = "\n".join(
            ln for ln in lines if not ln.strip().startswith("```")
        ).strip()

    return json.loads(raw_text)


def main():
    if len(sys.argv) < 2:
        print("Usage: python test_bedrock.py file1.pdf [file2.jpg ...]")
        print("\nDrop your test invoices into the test_invoices/ folder then run:")
        print("  python test_bedrock.py test_invoices/invoice.pdf")
        sys.exit(1)

    files = [Path(p) for p in sys.argv[1:]]
    missing = [f for f in files if not f.exists()]
    if missing:
        print(f"❌ File(s) not found: {missing}")
        sys.exit(1)

    print(f"\n📂 Files to extract ({len(files)}):")
    result = extract(files)

    print("\n✅ Extraction result:\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
