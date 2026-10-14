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

Roles control the workflow: Client PIC and Intake Support upload evidence; Finance Reviewer reviews, edits, approves or rejects; System Admin can correct drafts; Finance Reviewer, Service Lead and System Admin can read revisions. Acceptance automatically generates the voucher. Client PIC and Finance Reviewer can download the voucher and each evidence file separately. The server enforces client assignments and prevents self-approval.

## Workspace APIs

The existing extraction, approval, rejection and voucher endpoints are supplemented by:

- `GET /auth/me`: authenticated account information.
- `GET /requests`: requests for active client assignments.
- `GET /requests/{request_id}`: extracted draft and evidence metadata.
- `GET /evidence/{file_key}`: authenticated evidence bytes.
- `PUT /requests/{request_id}/fields`: typed field correction and revision/audit recording, with no discrepancy validation or flags. Paths: `header.vendor_name`, `line_items.0.description` (zero-based display index), `payment.bank_name`.
- `GET /requests/{request_id}/revisions`: ordered correction history for authorized roles.
- `POST /telemetry/page-load`: authenticated, bounded page-load timing record.

Client listing and uploads require active client assignments. Extraction sends drafts directly to manual review. Reviewers compare extracted values against all uploaded evidence and accept or reject. Automated discrepancy, duplicate, and missing-field flags are disabled. Historical flags do not block acceptance. Technical extraction failures still display a processing error.

Acceptance generates and stores a PDF using the active template in the approval transaction. Rendering or S3 failures leave the request awaiting review. Downloads fetch the already generated PDF; evidence is downloaded independently via `GET /evidence/{file_key}?download=true`. Only a Client PIC voucher download records delivery to the client.

System Admins can use the **Voucher templates** screen to import/edit HTML and activate a new named version. Template changes affect future approvals; generated vouchers preserve their original PDF and template version. `GET /admin/templates` lists versions and the bundled default; `POST /admin/templates` saves and activates a version.

## Updating an already running workspace

Run `alembic upgrade head` from `backend` after updating the code. Migration `0004` restores evidence metadata for older uploads from their audit history and moves successfully extracted legacy exception drafts into manual review. Original S3 files and audit events are retained; requests without a successfully extracted draft remain processing failures.

Restart any backend started without `--reload`. `GET /health` should return `"workflow": "manual-review-v1"`; if that marker is absent, an older process is still serving port 8000. The frontend proxy's `/api/health` should return the same marker.

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
