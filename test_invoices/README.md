# Test Invoices

Drop your invoice files here to test extraction.

## Supported formats
- `.pdf` — invoice PDFs
- `.png` / `.jpg` / `.jpeg` — invoice photos, chat screenshots, scanned images

## How to test extraction (no auth needed)

```bash
# Single file
curl -X POST http://localhost:8000/test/extract \
  -F "files=@sample_invoice.pdf"

# Multiple files
curl -X POST http://localhost:8000/test/extract \
  -F "files=@invoice.pdf" \
  -F "files=@bank_details_screenshot.png"
```

Or use the Swagger UI at http://localhost:8000/docs → POST /test/extract

## To edit the extraction prompt / JSON schema

Open:  `backend/app/providers/bedrock.py`

Find the block marked:
```
# =============================================================================
# EXTRACTION PROMPT — EDIT THIS to change the JSON schema / extraction rules
# =============================================================================
EXTRACTION_SYSTEM_PROMPT = """..."""
```

Edit the prompt string, then restart the server. No other code changes needed.
