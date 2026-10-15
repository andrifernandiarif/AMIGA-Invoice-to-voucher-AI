# AMIGA invoice-to-voucher workspace

A React frontend connected to the FastAPI extraction and review backend. Includes sign-in, a client-scoped request dashboard, multi-file evidence upload, side-by-side invoice review, editable draft fields, revision history, approval/rejection, and voucher preview/download.

## Run locally

Start the backend from its own directory so `.env` is loaded correctly:

```sh
cd backend
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8000
```

Configure `backend/.env` with `DATABASE_URL`, S3 bucket/region and AWS credentials, and `JWT_SECRET`. Use `EXTRACTION_PROVIDER=mock` for the existing mock extraction provider, or configure Bedrock for live extraction. The application needs PostgreSQL and S3; the mock extraction provider does not replace these services. Existing active users need active client assignments. Voucher generation uses the active editable voucher template, or seeds the bundled template on the first approval. WeasyPrint's native dependencies must be installed.

On macOS, install the native PDF libraries once with `brew install pango`. The backend discovers the Homebrew library path automatically. `pydyf==0.10.0` is pinned for compatibility with WeasyPrint 62.3. See the [WeasyPrint installation guide](https://doc.courtbouillon.org/weasyprint/v62.3/first_steps.html) for other platforms.

Start the frontend in a second terminal:

```sh
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. Vite proxies `/api` to the backend on port 8000. Sign in with an existing active username. The existing development authentication endpoint does **not** verify passwords. Production deployment requires a production authentication implementation. For a built frontend, configure your reverse proxy to forward `/api/*` to the backend with the `/api` prefix stripped and serve `index.html` for frontend routes.

Roles control the workflow: Client PIC and Intake Support upload evidence; Client PIC refines extracted fields, assigns item taxes and submits for review; Finance Reviewer reviews, edits, approves or rejects; System Admin can correct drafts; Finance Reviewer, Service Lead and System Admin can read revisions. Acceptance automatically generates the voucher. Client PIC and Finance Reviewer can download the voucher and each evidence file separately. The server enforces client assignments and prevents self-approval.

## Workspace APIs

The existing extraction, approval, rejection and voucher endpoints are supplemented by:

- `GET /auth/me`: authenticated account information.
- `GET /requests`: requests for active client assignments.
- `GET /requests/{request_id}`: extracted draft and evidence metadata.
- `GET /evidence/{file_key}`: authenticated evidence bytes.
- `PUT /requests/{request_id}/fields`: typed field correction and revision/audit recording, with no discrepancy validation or flags. Paths: `header.vendor_name`, `line_items.0.description` (zero-based display index), `payment.bank_name`.
- `GET /requests/{request_id}/revisions`: ordered correction history for authorized roles.
- `POST /telemetry/page-load`: authenticated, bounded page-load timing record.

Client listing and uploads require active client assignments. Extraction creates a READY_FOR_REFINEMENT draft for Client PIC. Client PIC edits extracted fields and applies per-item taxes, then explicitly submits to Finance Reviewer. Submitted drafts become READY_FOR_REVIEW and client edits are locked. Finance Reviewers compare the submitted values and tax rows against evidence and accept or reject. The finance queue excludes unsubmitted drafts. Automated discrepancy, duplicate, and missing-field flags are disabled. Historical flags do not block acceptance. Technical extraction failures still display a processing error.

Acceptance generates and stores a PDF using the active template in the approval transaction. Rendering or S3 failures leave the request awaiting review. Downloads fetch the already generated PDF; evidence is downloaded independently via `GET /evidence/{file_key}?download=true`. Only a Client PIC voucher download records delivery to the client.

System Admins can use the **Voucher templates** screen to import/edit HTML and activate a new named version. Template changes affect future approvals; generated vouchers preserve their original PDF and template version. `GET /admin/templates` lists versions and the bundled default; `POST /admin/templates` saves and activates a version.

## Voucher layout

The active Indonesian voucher contains Nomor, Nama, Peruntukan, Tanggal/Periode, and Nominal; an item table with NO, TANGGAL, KETERANGAN, JUMLAH, HARGA, and TOTAL; and Diajukan Oleh / Disetujui Oleh signature blocks with account/client names and professional roles. The submitter’s optional uploaded signature is included; external approver signature spaces remain empty. The PDF contains no AI notices or generated-by footer.

Client PIC can correct extracted name/item fields and upload metadata during refinement; Finance Reviewer can correct submitted extracted values during review. Voucher number and creation datetime are generated by the backend. Purpose and external approvers are captured at upload. Nominal shows the extracted/reviewed document total; the bottom TOTAL uses item amounts plus assigned tax rows, fees and rounding, minus explicit withholding. Extracted header tax is not added a second time. Amounts use Indonesian separators.

The preview displays the actual PDF using `GET /requests/{request_id}/voucher-pdf?preview=true`, which does not record a client delivery. Authorized regeneration uses `POST /requests/{request_id}/voucher-pdf?regenerate=true`, preserves previous PDFs, and keeps the approval/delivery state unchanged. Template changes normally affect future vouchers; existing vouchers can be regenerated explicitly.

## Manual item tax assignment

Each extracted item has one tax dropdown. It includes PPh 21, PPh 22, PPh 23, PPh 26, PPh 4(2) and PPN, with labelled transaction-specific presets and manual effective-rate/base options. Tax defaults to an additional voucher cost as requested; choose **Potong dari pembayaran** explicitly to treat it as withholding. Tax rows appear immediately after their source item. Rates and money use Decimal arithmetic; taxes round to two decimal places with half-up rounding. When quantity/unit price changes, the item total and automatic tax base are recalculated. A manual tax base remains fixed.

DJP references checked on 15 October 2026: [PPh 21/TER](https://pajak.go.id/id/artikel/serba-serbi-ter-pph-pasal-21-pajak-baru-atau-formula-baru), [PPh 22](https://www.pajak.go.id/id/pph-pasal-22), [PPh 23/26](https://pajak.go.id/id/pph-pasal-2326), [PPh 4(2)](https://pajak.go.id/id/pemotongan-pajak-penghasilan-pasal-4-ayat-2-3), and [PPN DPP rules](https://pajak.go.id/index.php/id/siaran-pers/pemerintah-terbitkan-aturan-dpp-nilai-lain-dan-besaran-tertentu-ppn). PPh 25 is not offered for invoice items: it is an [income-tax instalment](https://www.pajak.go.id/id/artikel/angsuran-pph-pasal-25-mengapa-pajak-dibayar-setiap-bulan). These are manual assignments, not an automatic determination of legal applicability, TER brackets, exemptions or treaty eligibility. Choose the manual option for other rates or historical periods.

- `GET /tax-options`: authenticated preset catalogue, notes and DJP links.
- `PUT /requests/{request_id}/items/{item_id}/tax`: apply/remove one tax assignment; body contains `profile`, optional `rate_percent`, optional `base_amount` override, and `effect` (`ADD` / `DEDUCT`). The server computes the amount.
- `POST /requests/{request_id}/submit-review`: Client PIC submits saved refinements and taxes. Finance approval/rejection are unavailable before submission. Assignment/field revisions and submission actors are recorded.

Migrations 0007–0008 add the refinement stage and explicit tax parameters, and move existing unapproved pending requests to Client PIC refinement. Already approved vouchers retain their original content and history.

## Updating an already running workspace

Run `alembic upgrade head` from `backend` after updating the code. Migration `0004` restores evidence metadata for older uploads from their audit history and moves successfully extracted legacy exception drafts into manual review. Original S3 files and audit events are retained; requests without a successfully extracted draft remain processing failures.

Restart any backend started without `--reload`. `GET /health` should return `"workflow": "voucher-schema-v3"`; if that marker is absent, an older process is still serving port 8000. The frontend proxy's `/api/health` should return the same marker.

## Checks

```sh
cd frontend
npm run build
npm test
```

```sh
cd backend
.venv/bin/python -m pytest -q
```

Backend tests use isolated SQLite databases and mock external extraction/storage services; they do not require production credentials. Live S3, Bedrock and native PDF rendering need a configured environment for end-to-end verification.

## Voucher information and signatures (schema v3)

Run migration `0009` with `alembic upgrade head`. It adds company codes and counters, request purpose/approvers and generated identity, and account full name, professional role and optional signature. It activates the editable `v3.0-id` template for future generations; stored PDF versions remain unchanged.

- **Nomor:** `<client_code>-<sequence>`, starting at 1 independently for each company. The counter is allocated atomically in the PDF transaction. Failed generation rolls back numbering and approval; regeneration reuses the number and original creation datetime. Existing generated requests count once, regardless of how many PDF versions they have.
- **Nama:** extracted `voucher_name`, falling back to the extracted vendor; correct it during refinement if necessary.
- **Peruntukan:** required upload purpose, stored separately from extracted data.
- **Tanggal/Periode:** first voucher creation datetime, displayed in Asia/Jakarta (WIB); invoice and line dates remain separate extracted values.
- **Nominal:** extracted/reviewed document total. The table's bottom TOTAL is calculated from items and explicitly assigned taxes/fees/rounding. Tax rows remain visible separately.
- **Diajukan Oleh:** the uploader's account full name, professional role and optional PNG/JPEG signature. These are snapshotted when Client PIC submits for review and preserved for regeneration. A missing signature leaves an empty signature space.
- **Disetujui Oleh:** one or more external client approvers supplied at upload, each with full name and professional role (for example BOD or Manager). Each gets an empty signature space. Finance review does not fill or sign these external approval blocks.

Complete **Your account** before uploading if an existing account has no full name or professional role. System administrators can use **Create user** to provision these fields, optional signatures and client assignments. The professional role is independent of the system access role. Missing historical personal/approver details are never invented. Existing drafts can complete upload details during refinement before submitting.

API additions:

- `POST /clients` accepts an optional unique uppercase `client_code` (letters, digits and hyphens, maximum 40 characters). Omitted codes are generated deterministically from company name and ID. `GET /clients` returns codes for assigned companies; admin-only `GET /clients/all` supplies the account creation client selector.
- `POST /users` requires `full_name` and `job_title` alongside username, system role and assignments. Optional `signature_data` is a PNG/JPEG base64 data URL (under 2 MB, at most 4 megapixels), decoded and normalized to PNG. Images are excluded from user responses and audit payloads.
- `GET /users/me/profile` / `PUT /users/me/profile` read/update the current user's profile. To remove a signature, send `remove_signature: true` without replacement data. Changes apply to future submissions; submitted vouchers keep their snapshot.
- Multipart `POST /requests` requires `voucher_purpose` and `approvers` (a JSON array of `{full_name, job_title}`, 1–12 people).
- `PUT /requests/{id}/voucher-metadata` accepts `{voucher_purpose, approvers}` during Client PIC refinement only. Submission freezes this metadata; generated number/date cannot be overridden through extracted-field editing.
- Request detail exposes purpose, approvers, generated voucher number/date and submitter name/title, without returning signature image data.

Templates support `submitter.full_name`, `submitter.job_title`, optional `submitter.signature_data` and the `approvers` loop (each person's `full_name` and `job_title`), alongside existing voucher/item variables. The default voucher has no AI-related messages.

## Invoice retrieval chat and Telegram

Install the updated backend dependencies, then run `alembic upgrade head`. Migration `0010` enables `vector` on PostgreSQL/Neon and creates `chunk_embeddings` with a `vector(1024)` column, JSONB metadata, a unique `(request_id, chunk_type)` constraint and an IVFFlat cosine index. **Titan V2 uses 1024 dimensions here, not 1536**: [AWS documents 1024/512/256 for V2](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html). Titan G1's 1536-dimensional embeddings are incompatible with this schema.

Configure `TITAN_EMBED_MODEL_ID=amazon.titan-embed-text-v2:0`. `TITAN_EMBED_REGION` normally falls back to `AWS_REGION`; the local `.env` uses Sydney (`ap-southeast-2`) because [Titan V2 is unavailable in Singapore](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-supported.html). Extraction, answer generation and S3 continue using the existing region/settings. The runtime identity needs Bedrock InvokeModel access to the embedding model in the embedding region and Converse access to `BEDROCK_MODEL_ID` in `AWS_REGION`. No credentials or token values are included in the tracked example configuration.

The shared async entry point is `app.services.rag_service.ask(question, user, db, request_id=None)`. It returns a string with a `chunks_used` attribute for API callers. Both the JWT-authenticated web endpoint and Telegram call this function. Search filters by active client assignments before applying its limit, and the service checks the live relational assignment again before sending any context to Nova. System administrators also require a client assignment for retrieval. Request-scoped questions outside the caller's assignments return 404 without invoking Bedrock.

Each indexed request has four bounded chunks: `HEADER`, `LINE_ITEMS`, `PAYMENT`, and `TAX_SUMMARY`. All contain request/client IDs, vendor, invoice number and status metadata. Signature images are excluded. Large records are explicitly marked as truncated. Embeddings are written atomically; repeated indexing updates the same rows. Answers use the current database status alongside indexed context, so a download/delivery status change is not misreported using the old indexed status. Retrieval is semantic, not an exhaustive report: the generator must not claim an invoice is the latest or produce a global count without sufficient context.

Finance approval calls indexing after the PDF/approval transaction commits. Indexing errors or the 45-second indexing timeout are logged without rolling back approval. The backfill script retries approved/delivered requests that have no embeddings, with 100 ms between requests:

```sh
cd backend
.venv/bin/python -m scripts.backfill_embeddings
# Also repair partially indexed requests (fewer than four chunks):
.venv/bin/python -m scripts.backfill_embeddings --retry-partial
```

The default approval hook and backfill index `AMIGA_REVIEWED` / `SENT_TO_CLIENT` records. They do not populate unapproved `EXCEPTION` requests. A question about an exception without indexed context should receive an insufficient-context answer. `index_request` is reusable for additional lifecycle hooks if that scope is needed later.

The frontend provides **Invoice chat** at `/chat`, with an optional request selector, suggested questions, retrieved-section counts, and retry support. Each question is independent; include the invoice number when asking follow-up questions. The interface uses the authenticated API below.

The web API accepts all existing system roles with a valid JWT:

```sh
curl http://localhost:8000/chat \
  -H "Authorization: Bearer $INVOICE_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the invoice number and current status?"}'
```

Optional body field `request_id` scopes retrieval to one UUID. Response: `{"answer":"...","chunks_used":4}`. Empty/overlong questions return 422; upstream failures return a generic 503 without disclosing SDK errors or invoice data in logs. No retrieved context means no answer-model call.

For Telegram, privately add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_USER_MAP` to `backend/.env`. The map is JSON, such as `{"123456789":"SYSTEM_USER_UUID"}`; replace both values with the actual private chat ID and the user's UUID from `/auth/me`. Only mapped private chats with an active user are accepted, and every question still checks current client assignments. Run the bot as a separate process:

```sh
cd backend
.venv/bin/python -m telegram_bot.bot
```

The bot handles `/start` and free text, sends plain-text replies, and splits long messages. The FastAPI process never starts Telegram polling. No real Telegram token or chat mapping is provisioned automatically.

Tests mock Bedrock and Telegram; PostgreSQL vector operations also have an opt-in integration test using the configured database. Synthetic integration chunks are rolled back:

```sh
cd backend
.venv/bin/pytest -q
RUN_PGVECTOR_INTEGRATION=1 .venv/bin/pytest -q tests/test_vector_store_integration.py
```

For a small initial corpus the IVFFlat index has one list to preserve recall. As the corpus grows, rebuild it with an appropriate list count and run `ANALYZE`; see the [pgvector index guidance](https://github.com/pgvector/pgvector#ivfflat).
