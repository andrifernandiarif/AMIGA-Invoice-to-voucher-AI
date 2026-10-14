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

The active Indonesian voucher contains Nomor, Nama, Peruntukan, Tanggal/Periode, and Nominal; an item table with NO, TANGGAL, KETERANGAN, JUMLAH, HARGA, and TOTAL; and blank Diajukan Oleh / Disetujui Oleh signature, name, and role lines. The PDF contains no AI notices or generated-by footer.

Client PIC can edit voucher number/name/purpose/period and each item's date during refinement; Finance Reviewer can correct submitted values during review. A blank voucher number uses a stable request-based number; Nama defaults to the vendor and Tanggal/Periode to the invoice date or request date. Purpose and individual item dates remain blank unless entered. For refined requests, Nominal and the bottom TOTAL use item amounts plus assigned tax rows, fees and rounding, minus explicit withholding. Extracted header tax/total are preserved as reference and are not added a second time. Amounts use Indonesian separators.

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

Restart any backend started without `--reload`. `GET /health` should return `"workflow": "client-refinement-v2"`; if that marker is absent, an older process is still serving port 8000. The frontend proxy's `/api/health` should return the same marker.

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
