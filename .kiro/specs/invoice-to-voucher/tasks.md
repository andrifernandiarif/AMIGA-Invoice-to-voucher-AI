# Implementation Plan: AMIGA Robot Admin — Invoice-to-Voucher Processing Platform

## Overview

This plan converts the AMIGA Robot Admin design into an ordered series of incremental coding tasks. The implementation language is **Python 3.12+** for the backend (FastAPI + SQLAlchemy 2.x async) and **TypeScript** for the frontend (React + Vite). Each task builds on the previous, ending with all components wired together into a working system.

The backend lives under `backend/` and the frontend under `frontend/`. Infrastructure is shared at the repo root.

---

## Tasks

---

### Phase 1: Project Scaffold and Infrastructure

- [x] 1. Initialize monorepo structure and tooling
  - Create `backend/` (Python 3.12+ with FastAPI) and `frontend/` (React + TypeScript + Vite) project skeletons
  - Create `backend/pyproject.toml` with all runtime and dev dependencies: `fastapi`, `uvicorn`, `sqlalchemy[asyncio]`, `alembic`, `asyncpg`, `pydantic[email]`, `pydantic-settings`, `pyjwt`, `boto3`, `weasyprint` (or `reportlab`), `jinja2==3.1.4`, `pytest`, `pytest-asyncio`, `hypothesis`, `httpx`, `moto`, `ruff` — all with pinned versions; add `jinja2==3.1.4` to both `requirements.txt` and `requirements-dev.txt` (must be explicit even if a transitive dep of moto)
  - Configure `ruff` for linting and formatting in `pyproject.toml` (`[tool.ruff]` section)
  - Install PostgreSQL 16+ natively on your machine (e.g. `brew install postgresql@16`); create the `amiga_db` database and start it
  - Populate `backend/.env` with all environment variables from the design: `DATABASE_URL`, `S3_BUCKET`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `JWT_SECRET`, `JWT_ALGORITHM`, `JWT_EXPIRY_SECONDS`, `EXTRACTION_PROVIDER`, `BEDROCK_MODEL_ID`, `CALCULATION_TOLERANCE`, `AUTHORIZATION_DISCLAIMER`
  - Create `frontend/` React + TypeScript + Vite skeleton with `package.json`
  - _Requirements: foundational for all_

- [x] 2. Define and apply the database schema
  - [x] 2.1 Write Alembic migration files for all 12 entities: `Client`, `User`, `ClientAssignment`, `Request`, `Draft`, `HeaderFields`, `LineItem`, `PaymentDetails`, `FieldRevision`, `AuditEvent`, `VoucherPDF`, `TelemetryRecord`
    - Include all columns, types, constraints, and foreign keys exactly as specified in the Data Models section of the design
    - Use `NUMERIC(18,4)` for all financial columns (`subtotal`, `total_amount`, `tax_amount`, `fee_amount`, `rounding_adj`, `unit_price`, `line_total`, `quantity`)
    - Add `INSERT`-only database role grants for `AuditEvent` and `FieldRevision` tables; add DDL triggers that raise an error on `UPDATE` or `DELETE` for those tables
    - _Requirements: 2.1, 11.1, 17.1_
  - [x] 2.3 Write Alembic migration `0002_evidence_files_tax_template.py` for the four new entities: `EvidenceFile`, `TaxAssignment`, `VoucherTemplate`, and the updated `VoucherPDF` schema (adding `template_version`, `template_hash` columns); include the `indonesiantaxtype` enum (`PPH21, PPH22, PPH23, PPH25, PPN, NO_TAX`) and all foreign key constraints
    - _Requirements: 1.9, 21.1, 22.2, 22.8_
  - [x] 2.4 Write Alembic migration `0003_extraction_job_queue.py` for the `ExtractionJob` table: `job_id UUID PK DEFAULT uuid_generate_v4()`, `request_id UUID FK → Request NOT NULL`, `status extraction_job_status ENUM(PENDING, IN_PROGRESS, DONE, FAILED) DEFAULT PENDING`, `retry_count INT DEFAULT 0`, `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`, `started_at TIMESTAMPTZ`, `completed_at TIMESTAMPTZ`, `error_detail TEXT`; add index on `(status, created_at)` to support efficient polling
    - _Requirements: 4.1_
  - [ ]* 2.2 Write pytest tests for schema constraints (unique username, immutable `request_id`, audit-table trigger rejects `UPDATE`/`DELETE`)
    - _Requirements: 2.1, 11.1_
  - [x] 2.3 Write Alembic migration `0002_evidence_files_tax_template.py` for the four new entities: `EvidenceFile`, `TaxAssignment`, `VoucherTemplate`, and the updated `VoucherPDF` schema (adding `template_version VARCHAR(100)` and `template_hash CHAR(64)` columns); include the `indonesiantaxtype` enum (`PPH21, PPH22, PPH23, PPH25, PPN, NO_TAX`) with its hardcoded rate mapping and all foreign key constraints; ensure exactly one `VoucherTemplate` row can be `is_active=TRUE` at a time
    - _Requirements: 1.9, 21.1, 22.2, 22.8_

- [x] 3. Configure file storage and message queue clients
  - [x] 3.1 Implement `app/storage.py`: `S3FileStore` class using the `boto3` S3 client (standard AWS regional endpoint — no custom endpoint override); expose `async upload(key: str, data: bytes, content_type: str) -> None` and `async download(key: str) -> bytes` methods; read bucket and region from `Pydantic Settings` (`S3_BUCKET`, `AWS_REGION`); credentials resolved from the AWS credential provider chain (env vars, IAM role, or `~/.aws/credentials`)
    - _Requirements: 1.1, 15.1_
  - [x] 3.2 Implement `app/services/extraction_worker.py`: PostgreSQL-based job queue polling loop; uses `SELECT ... FOR UPDATE SKIP LOCKED` to claim `PENDING` `ExtractionJob` rows atomically; polls every 2 seconds; marks job `IN_PROGRESS` on pickup, `DONE` on success; on failure increments `retry_count` and resets to `PENDING`; after `retry_count >= 3` marks `FAILED` and transitions Request to `EXCEPTION` with a `PROCESSING_FAILED` AuditEvent; started as an asyncio background task via FastAPI `lifespan` in `app/main.py` — no separate worker process needed
    - _Requirements: 4.1_
  - [x] 3.4 Create `backend/app/templates/voucher_default.html`: the default Jinja2 HTML voucher template containing all required placeholders (`{{vendor_name}}`, `{{invoice_number}}`, `{{invoice_date}}`, `{{line_items_table}}`, `{{tax_summary_table}}`, `{{subtotal}}`, `{{total_tax}}`, `{{grand_total}}`, `{{bank_account_number}}`, `{{bank_name}}`, `{{payment_reference}}`, `{{authorization_disclaimer}}`); styled with inline CSS; `{{line_items_table}}` iterates over a list of dicts with keys `description`, `quantity`, `unit`, `unit_price`, `line_total`, `tax_type`, `tax_amount`; `{{tax_summary_table}}` iterates over a list of dicts with keys `tax_type`, `subtotal`
    - _Requirements: 22.1, 22.3, 22.5_
  - [x] 3.5 Create `app/services/template_service.py`: `load_active_template(db) -> VoucherTemplate`; `seed_default_template(db)` — reads `app/templates/voucher_default.html` and inserts it as `version="v1.0"` with `is_active=True` if no active template exists; add a `POST /admin/templates` endpoint (System Admin only) to upload a new template version and set it as active, deactivating the previous one
    - _Requirements: 22.1, 22.2_
  - [ ]* 3.3 Write pytest tests for `S3FileStore` (using `moto` to mock the boto3 S3 client) and for the PostgreSQL job queue polling loop (using a test database session — insert a `PENDING` job, run one polling iteration, verify it transitions to `IN_PROGRESS`)
    - _Requirements: 1.1, 4.1_

- [x] 3.4 Create `backend/app/templates/voucher_default.html`: default Jinja2 HTML voucher template containing all required placeholders (`{{vendor_name}}`, `{{invoice_number}}`, `{{invoice_date}}`, `{{line_items_table}}`, `{{tax_summary_table}}`, `{{subtotal}}`, `{{total_tax}}`, `{{grand_total}}`, `{{bank_account_number}}`, `{{bank_name}}`, `{{payment_reference}}`, `{{authorization_disclaimer}}`); `{{line_items_table}}` loops over a list of dicts with keys `description`, `quantity`, `unit`, `unit_price`, `line_total`, `tax_type`, `tax_amount`; `{{tax_summary_table}}` loops over a list of dicts with keys `tax_type`, `subtotal`; styled with inline CSS for clean PDF rendering via WeasyPrint
  - _Requirements: 22.1, 22.3, 22.5_

- [x] 3.5 Create `app/services/template_service.py`: `load_active_template(db: AsyncSession) -> VoucherTemplate` that queries the single `is_active=True` row and raises if none found; `seed_default_template(db)` that reads `app/templates/voucher_default.html` and inserts it as `version="v1.0"` with `is_active=True` if no active template exists (idempotent); add `POST /admin/templates` FastAPI endpoint (System Admin only) to upload a new Jinja2 template version (validated to contain all required placeholders), set it as `is_active=True`, and deactivate the previous active template in the same transaction
  - _Requirements: 22.1, 22.2_

- [x] 4. Checkpoint — Ensure Alembic migrations run cleanly against the local native PostgreSQL instance and all Phase 1 tests pass. Ask the user if any questions arise.

---

### Phase 2: User Management and Authentication

- [x] 5. Implement User & Auth Service — data layer
  - [x] 5.1 Create `app/repositories/user_repository.py`: async SQLAlchemy methods `find_by_username(username) -> User | None`, `create_user(data) -> User`, `update_role(user_id, role) -> User`, `add_assignment(user_id, client_id) -> ClientAssignment`, `revoke_assignment(user_id, client_id) -> None`, `is_assignment_active(user_id, client_id) -> bool`; all queries use parameterized SQLAlchemy expressions (no raw string interpolation)
    - _Requirements: 19.1, 19.2, 18.1, 18.3_
  - [ ]* 5.2 Write pytest tests for `UserRepository` covering: duplicate username rejection, missing required field rejection, role modification, assignment revocation, `is_assignment_active` returns `False` after revocation
    - _Requirements: 19.6, 19.7, 19.2_

- [x] 6. Implement JWT authentication
  - [x] 6.1 Implement `app/auth.py`: `create_access_token(user_id: UUID, role: str) -> str` using `PyJWT` (`jwt.encode`) with `HS256` algorithm, embedding `user_id` and `role` claims plus `exp`; `decode_token(token: str) -> dict` using `jwt.decode` — raises `HTTPException(status_code=401)` on invalid signature, expired token, or missing claims
    - _Requirements: 18.1, 18.2_
  - [x] 6.2 Implement `app/dependencies.py`: `get_db` async session dependency yielding an `AsyncSession`; `get_current_user` FastAPI dependency using `Depends(OAuth2PasswordBearer)` + `decode_token` — on failure raises `HTTPException(401)`; on success returns the `User` ORM object loaded from DB
    - _Requirements: 18.1_
  - [ ]* 6.3 Write pytest tests for `create_access_token` / `decode_token` (valid token, expired token, tampered signature) and for `get_current_user` dependency (missing header, invalid token, valid token returns correct user)
    - _Requirements: 18.1_

- [x] 7. Implement authorization dependencies (role + client assignment)
  - [x] 7.1 Implement `require_role(*roles: str)` FastAPI dependency factory in `app/dependencies.py`: raises `HTTPException(status_code=403, detail="You do not have authority to perform this action.")` if `current_user.role` is not in the allowed set
    - _Requirements: 12.3, 13.1, 18.2_
  - [x] 7.2 Implement `require_client_assignment(request_id: UUID)` FastAPI dependency: calls `UserRepository.is_assignment_active(current_user.user_id, client_id)` via a direct DB lookup on every request (no caching); raises `HTTPException(status_code=403, detail="You are not authorized to access Requests for this client.")` with no client data in the response body if the assignment is inactive or absent
    - _Requirements: 18.1, 18.2, 18.3, 18.4_
  - [ ]* 7.3 Write Hypothesis property test for **Property 16** (client isolation): for any combination of `(has_role, has_active_assignment, operation_type)`, verify access is granted only when both conditions are true; revoked assignments are denied on the very next request without re-auth
    - **Property 16: Client Isolation**
    - **Validates: Requirements 18.1, 18.2, 18.3, 18.4**

- [x] 8. Implement User & Auth Service — HTTP routes
  - [x] 8.1 Implement `POST /users` FastAPI router (System Admin only via `Depends(require_role("SYSTEM_ADMIN"))`): validate required fields (username, role, at least one `ClientAssignment`) using a Pydantic v2 request schema; reject duplicate usernames with `HTTPException(409)`; reject `SYSTEM_ADMIN` accounts from holding approval authority with `HTTPException(422)`; create user and return `201` within 5 seconds
    - _Requirements: 19.1, 19.3, 19.6, 19.7_
  - [x] 8.2 Implement `PATCH /users/{id}/role` and `POST`/`DELETE /users/{id}/assignments` FastAPI routers (System Admin only): apply changes within 5 seconds; write a `USER_MODIFIED` event to the system audit log
    - _Requirements: 19.2, 19.5_
  - [x] 8.3 Implement `GET /users/{id}` FastAPI router (System Admin only)
    - _Requirements: 19.1_
  - [ ]* 8.4 Write pytest tests for user management routes: missing fields, duplicate username, System_Admin approval-authority rejection, assignment revocation propagates immediately
    - _Requirements: 19.1, 19.3, 19.6, 19.7_
  - [ ]* 8.5 Write Hypothesis property test for **Property 3** (missing metadata fields identified by name): for any non-empty subset of required fields omitted from a user-creation or invoice-upload request, the error response SHALL identify each missing field by name
    - **Property 3: Missing Metadata Fields Named in Error**
    - **Validates: Requirements 1.7, 19.7**

- [x] 9. Checkpoint — Run all Phase 2 tests. Ensure auth dependencies correctly gate a sample protected route. Ask the user if any questions arise.

---

### Phase 3: Invoice Intake

- [x] 10. Implement Request Service — data layer
  - [x] 10.1 Create `app/repositories/request_repository.py`: async SQLAlchemy methods `create_request(data) -> Request`, `get_by_id(request_id) -> Request | None`, `list_for_user(user_id, role, client_ids, statuses) -> list[Request]`, `update_status(request_id, new_status) -> Request`, `apply_flag(request_id, flag) -> None`, `remove_flag(request_id, flag) -> None`, `set_assigned_reviewer(request_id, reviewer_id) -> None`; all queries use parameterized SQLAlchemy expressions
    - _Requirements: 1.1, 2.1, 3.1, 12.1_
  - [x] 10.2 Implement `RequestIdGenerator` in `app/services/request_service.py`: generate a `uuid.uuid4()`; on collision (checked against DB) retry up to 5 times; after 5 failures raise `RequestIdGenerationError`; assign and persist within 3 seconds
    - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - [ ]* 10.3 Write pytest tests for `RequestIdGenerator`: single-attempt success, collision on first attempt then success, five consecutive collisions then `RequestIdGenerationError`
    - _Requirements: 2.3_
  - [ ]* 10.4 Write Hypothesis property test for **Property 4** (all Request IDs are unique): for any batch of N sequentially created Requests (N up to 500), all generated IDs SHALL be distinct
    - **Property 4: Unique Request IDs**
    - **Validates: Requirements 2.1**

- [x] 11. Implement invoice upload endpoint
  - [x] 11.1 Implement `POST /requests` FastAPI multipart endpoint: accept one or more files (`List[UploadFile]`) plus form metadata (`client_id`, `request_type`, `channel`, `request_date`); validate all files are PDF, PNG, JPG, or JPEG and each is ≤ 20 MB — reject the entire Upload_Session on any violation naming the offending file; validate metadata fields — reject with `422` naming missing fields; if `role == INTAKE_SUPPORT` verify active `ClientAssignment`; on success: generate `request_id`, store all files to S3 under `evidence/{request_id}/{upload_order}_{filename}`, persist one `EvidenceFile` row per file, create the `Request` in QUEUED status, write a `REQUEST_CREATED` `AuditEvent` including all file names and formats, and return `201` with `request_id` and the list of stored `evidence_id`s
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 1.8, 1.9, 2.1, 2.4_
  - [ ]* 11.2 Write pytest tests for the upload endpoint: non-PDF rejected with format name in error, oversized file rejected, missing metadata field named in error, Intake_Support without assignment rejected, valid upload returns 201 with `request_id`
    - _Requirements: 1.2, 1.3, 1.6, 1.7_
  - [ ]* 11.3 Write Hypothesis property test for **Property 1** (valid uploads create QUEUED requests with uploader identity): for any valid upload by a `Client_PIC` or `Intake_Support` with active assignment, the system SHALL create a Request in `QUEUED` status and the `AuditTrail` SHALL contain `REQUEST_CREATED` with the uploader's account identifier and a non-null UTC timestamp
    - **Property 1: Valid Uploads Create Queued Requests**
    - **Validates: Requirements 1.1, 1.4, 1.5**
  - [ ]* 11.4 Write Hypothesis property test for **Property 2** (non-PDF and oversized files rejected): for any non-PDF file, the error names the received format; for any PDF > 20 MB, a size error is returned
    - **Property 2: Invalid File Formats and Sizes Rejected**
    - **Validates: Requirements 1.2, 1.3**

- [x] 12. Enqueue extraction job
  - [x] 12.1 After a Request is persisted in `QUEUED` status, insert an `ExtractionJob` row into the `extraction_jobs` table with `status=PENDING`, `request_id`, and `invoice_page_count`; the extraction worker will set Request status to `PROCESSING` when it picks up the job
    - _Requirements: 4.1_
  - [ ]* 12.2 Write pytest tests verifying that uploading a Request inserts an `ExtractionJob` row with `status=PENDING` and the correct `request_id`; and that when the extraction worker processes it, the Request status transitions to `PROCESSING` and a `STATUS_CHANGED` `AuditEvent` is written
    - _Requirements: 4.1_

- [x] 13. Checkpoint — Confirm end-to-end: upload a PDF via `POST /requests`, see `QUEUED` request in DB, verify an `ExtractionJob` row appears in the `extraction_jobs` PostgreSQL table with `status=PENDING`. Ask the user if any questions arise.

---

### Phase 4: AI Extraction Pipeline

- [x] 14. Implement Robot Admin Engine — PDF extraction
  - [x] 14.1 In `app/services/extraction_worker.py`: implement `process_extraction_job(job: ExtractionJob, db: AsyncSession)` — set Request status to `PROCESSING`; fetch all Evidence File bytes from `S3FileStore`; check idempotency (if `Draft` already exists for `request_id`, skip creation and proceed to validation); call `ExtractionProvider.extract(evidence_files, request_id)` — provider selected via `EXTRACTION_PROVIDER` env var (`"mock"` or `"bedrock"`); persist `Draft`, `HeaderFields`, `LineItem[]`, and `PaymentDetails` records to the DB; record `start_time` (from the moment status transitioned to `PROCESSING`) and `end_time` for telemetry; on failure increment `retry_count`; after `retry_count >= 3` set `ExtractionJob.status = FAILED` and transition Request to `EXCEPTION` with a `PROCESSING_FAILED` `AuditEvent`
    - _Requirements: 4.1, 4.3, 4.4, 5.1, 5.2, 20.1_
  - [x] 14.2 After persisting extraction output, call `ValidationEngine.run(draft, db)` to collect flags; update Request status to `READY_FOR_REVIEW` (no flags) or `EXCEPTION` (at least one flag); write `STATUS_CHANGED` and `FLAG_APPLIED` `AuditEvent`s via `AuditService`
    - _Requirements: 4.2, 4.6, 5.3, 6.1, 6.2_
  - [ ]* 14.3 Write pytest tests for the extraction job: full extraction success → `READY_FOR_REVIEW`, partial extraction (missing bank account) → `EXCEPTION` + `MISSING_EVIDENCE`, extraction failure after 3 retries → `EXCEPTION` + `PROCESSING_FAILED`
    - _Requirements: 4.1, 4.6, 6.1, 6.2_

- [x] 15. Implement extraction SLA timer and telemetry
  - [ ] 15.1 In `extract_invoice`, after writing extraction output to storage, compute `duration_ms = int((end_time - start_time).total_seconds() * 1000)`; persist a `TelemetryRecord` with `metric_type = EXTRACTION_DURATION`; if `duration_ms > threshold` (15 000 ms for ≤ 5 pages, 30 000 ms for 6–20 pages), also persist a breach event with `is_breach = True`
    - _Requirements: 20.1, 20.4, 20.5_
  - [ ]* 15.2 Write pytest tests for SLA timer: duration under threshold → no breach record, duration over threshold → breach record with correct fields (`subject_id`, `duration_ms`, `page_count`, `measured_at`)
    - _Requirements: 20.4, 20.5_

- [x] 16. Checkpoint — Manually trigger a mock extraction job; verify `Draft`, `HeaderFields`, `LineItems`, `PaymentDetails`, status transition, `AuditEvent`s, and `TelemetryRecord` are all created. Ask the user if any questions arise.

---

### Phase 5: Validation Engine

- [x] 17. Implement completeness validation (MISSING_EVIDENCE)
  - [x] 17.1 Implement `app/validation/completeness.py`: `CompletenessValidator.validate(draft: Draft) -> ValidationResult`; check that `PaymentDetails` contains non-`None`, non-empty `bank_account_number`, `bank_name`, and `payment_reference`; check each `LineItem` for missing `description`, `quantity`, `unit`, `unit_price`, or `line_total`; return a `ValidationResult` dataclass with a list of missing field names
    - _Requirements: 5.3, 6.1, 6.2, 6.3_
  - [ ]* 17.2 Write Hypothesis property test for **Property 6** (MISSING_EVIDENCE flag for any missing required field): for any extraction result missing one or more required fields, the `AMIGA_System` SHALL apply `MISSING_EVIDENCE` AND set status to `EXCEPTION`; the exception UX SHALL list each missing `Payment_Details` field by name
    - **Property 6: MISSING_EVIDENCE Applied for Any Missing Required Field**
    - **Validates: Requirements 4.6, 5.3, 6.1, 6.2, 6.3**

- [x] 18. Implement calculation validation
  - [x] 18.1 Implement `app/validation/calculation.py`: `CalculationValidator.validate(header: HeaderFields, line_items: list[LineItem], tolerance: Decimal) -> ValidationResult`; use Python `Decimal` (not `float`) for all arithmetic; compute `sum(Decimal(li.line_total) for li in line_items)` and compare against `Decimal(header.subtotal)`; compute `subtotal + tax + fees + rounding_adj` and compare against `Decimal(header.total_amount)`; return flagged fields with `computed` and `extracted` values for each discrepancy
    - _Requirements: 7.1, 7.2, 7.3_
  - [ ]* 18.2 Write pytest tests for `CalculationValidator`: exact tolerance boundary (no flag), just-over tolerance (flag), just-under tolerance (no flag), both checks discrepant (both fields flagged)
    - _Requirements: 7.1, 7.2_
  - [ ]* 18.3 Write Hypothesis property test for **Property 7** (calculation discrepancies beyond tolerance flagged with computed and extracted values): for any invoice where `|sum(line_totals) − subtotal| > tolerance` OR `|subtotal + tax + fees + rounding − total| > tolerance`, the system SHALL flag the field and display computed vs. extracted; within tolerance → no flag
    - **Property 7: Calculation Discrepancies Flagged Accurately**
    - **Validates: Requirements 7.1, 7.2, 7.3**

- [x] 19. Implement duplicate detection
  - [x] 19.1 Implement `app/validation/duplicate.py`: `DuplicateDetector.detect(request_id: UUID, vendor_name: str, invoice_number: str, db: AsyncSession) -> list[MatchResult]`; execute the async SQLAlchemy query with `func.lower()` for case-insensitive match on `vendor_name` and `invoice_number`, excluding `REJECTED`/`VOIDED` statuses, excluding the current `request_id`; if matches found, apply `POSSIBLE_DUPLICATE` flag and store match references (`request_id`, `status`, `invoice_date`, `total_amount`)
    - _Requirements: 8.1, 8.2, 8.3_
  - [x] 19.2 Implement `POST /requests/{id}/dismiss-duplicate` FastAPI router (Finance Reviewer only): remove the `POSSIBLE_DUPLICATE` flag; write a `FLAG_DISMISSED` `AuditEvent`
    - _Requirements: 8.4_
  - [ ]* 19.3 Write pytest tests for `DuplicateDetector`: exact-match case-insensitive hit → flag applied, `REJECTED`/`VOIDED` excluded → no flag, no match → no flag
    - _Requirements: 8.1, 8.2_
  - [ ]* 19.4 Write Hypothesis property test for **Property 8** (duplicate detection is case-insensitive and excludes REJECTED/VOIDED): for any new Request, `POSSIBLE_DUPLICATE` is applied if and only if a matching non-`REJECTED`/`VOIDED` Request exists (case-insensitive vendor + invoice number); exception UX displays matching Request details
    - **Property 8: Duplicate Detection Case-Insensitive, Excludes Terminal States**
    - **Validates: Requirements 8.1, 8.2, 8.3**

- [x] 20. Wire validators into the extraction pipeline
  - [x] 20.1 Implement `app/validation/engine.py`: `ValidationEngine.run(draft: Draft, db: AsyncSession) -> list[Flag]`; call `CompletenessValidator.validate`, `CalculationValidator.validate`, and `DuplicateDetector.detect` sequentially; aggregate all flags and return them; `READY_FOR_REVIEW` is only set when the returned flag list is empty
    - _Requirements: 4.2, 4.5, 6.2_
  - [ ]* 20.2 Write pytest integration tests: upload → extraction (all valid) → `READY_FOR_REVIEW`; upload → extraction (missing bank account) → `EXCEPTION` + `MISSING_EVIDENCE`; upload → extraction (total mismatch) → `EXCEPTION` + calc flag
    - _Requirements: 4.2, 6.2, 7.1_

- [x] 21. Checkpoint — Run all Phase 5 tests. Verify that a mock extraction with a missing payment field correctly transitions the Request to `EXCEPTION` with the `MISSING_EVIDENCE` flag and correct `AuditEvent`s. Ask the user if any questions arise.

---

### Phase 5.5: Indonesian Tax Assignment

- [ ] 50. Implement tax assignment model and calculation engine
  - [ ] 50.1 Create `app/models/tax_assignment.py`: `IndonesianTaxType` Python `enum.Enum` with values `PPH21, PPH22, PPH23, PPH25, PPN, NO_TAX`; `TAX_RATES: dict[IndonesianTaxType, Decimal]` with values `{PPH21: Decimal("0.0500"), PPH22: Decimal("0.0150"), PPH23: Decimal("0.0200"), PPH25: Decimal("0.0200"), PPN: Decimal("0.1100"), NO_TAX: Decimal("0.0000")}`; `TaxAssignment` SQLAlchemy model with `unique` constraint on `line_item_id` (one assignment per item)
    - _Requirements: 21.1, 21.2_
  - [ ] 50.2 Create `app/services/tax_service.py`: `compute_tax_amount(line_total: Decimal, tax_type: IndonesianTaxType) -> Decimal` using only `Decimal` multiplication (no `float`); `compute_tax_summary(tax_assignments: list[TaxAssignment], line_items: list[LineItem]) -> dict` returning `{"per_type": {tax_type: subtotal}, "total_tax": Decimal, "grand_total": Decimal}` where `grand_total = subtotal + total_tax`
    - _Requirements: 21.2, 21.3, 21.4_
  - [ ] 50.3 Implement `PUT /requests/{id}/line-items/{line_item_id}/tax` FastAPI endpoint (Finance Reviewer + active ClientAssignment, Request must be in `READY_FOR_REVIEW` or `EXCEPTION` status): accept `{ tax_type: str }`; validate `tax_type` is a valid `IndonesianTaxType`; compute `tax_amount = compute_tax_amount(Decimal(str(line_item.line_total)), tax_type)`; upsert the `TaxAssignment` row; write a `FIELD_REVISED` AuditEvent recording old and new tax type and computed amount; return `{ line_item_id, tax_type, tax_rate, tax_amount }` — reject with `409` if Request is `AMIGA_REVIEWED` or later (locked)
    - _Requirements: 21.1, 21.3, 21.5, 21.7_
  - [ ] 50.4 Implement `GET /requests/{id}/tax-summary` FastAPI endpoint (Finance Reviewer + active ClientAssignment): return `{ line_items: [{..., tax_type, tax_rate, tax_amount}], per_type_subtotals: {type: subtotal}, total_tax, grand_total }` computed via `tax_service.compute_tax_summary`; if Request is `AMIGA_REVIEWED` or later, mark as `locked: true` in the response
    - _Requirements: 21.4, 21.6, 21.7_
  - [ ]* 50.5 Write pytest unit tests for `tax_service`: for each of the 6 tax types verify `compute_tax_amount` returns exact `Decimal` result; verify `float` is never used; verify `compute_tax_summary` grand_total equals sum of per-type subtotals; verify a line_item with no assignment contributes 0 tax
    - _Requirements: 21.2, 21.3_

- [ ] 51. Build `TaxAssignmentPanel` React component
  - [ ] 51.1 For each extracted `LineItem` in the review workspace, render a dropdown selector with options: PPh 21 (5%), PPh 22 (1.5%), PPh 23 (2%), PPh 25 (2%), PPN (11%), No Tax; display the computed tax amount next to each selector, updating on selection via `PUT /requests/{id}/line-items/{lid}/tax`
    - _Requirements: 21.1, 21.3_
  - [ ] 51.2 Render a live tax summary section below the line items table: per-type subtotals and grand total fetched from `GET /requests/{id}/tax-summary`; disable all selectors if Request is locked (`AMIGA_REVIEWED` or later)
    - _Requirements: 21.4, 21.6, 21.7_

- [ ] 52. Checkpoint — Assign tax types to all line items in a `READY_FOR_REVIEW` request; verify computed tax amounts, per-type subtotals, grand total, AuditEvents, and that selectors are disabled after approval. Ask the user if any questions arise.

---

### Phase 6: Review Workspace and Corrections

- [x] 22. Implement draft field update endpoint
  - [x] 22.1 Implement `PUT /requests/{id}/fields` FastAPI router (Finance Reviewer + active `ClientAssignment` via `Depends`): accept a Pydantic v2 request body `{ field_path: str, new_value: str }`; load the current value from the appropriate table (`HeaderFields` / `LineItem` / `PaymentDetails`) by `field_path`; if new value differs from current value, atomically update the field value AND insert a `FieldRevision` record within a single SQLAlchemy async transaction; on transaction failure, roll back both writes and return `HTTPException(500, "The change could not be saved. Please try again.")`; append a `FIELD_REVISED` `AuditEvent` referencing `revision_id`; skip entirely (no-op) if `new_value == old_value`
    - _Requirements: 11.1, 11.2, 11.5_
  - [x] 22.2 Implement `GET /requests/{id}/revisions` FastAPI router (Finance Reviewer, Service Lead, System Admin only): return the complete `FieldRevision` list for the Request ordered by `revised_at` ascending
    - _Requirements: 11.4, 17.3_
  - [x] 22.3 Write pytest tests for field update: value unchanged → no revision written, value changed → revision persisted, persistence failure → field unchanged + error, unauthorized role → 403
    - _Requirements: 11.1, 11.5_
  - [x] 22.4 Write Hypothesis property test for **Property 11** (field revisions complete, ordered, authorized roles only): for any field modification where `new_value ≠ old_value`, a `FieldRevision` with correct `field_path`, old value, new value, actor `user_id`, and UTC timestamp SHALL be recorded; revisions returned in non-decreasing `revised_at` order; endpoint returns 403 for any role other than Finance_Reviewer, Service_Lead, or System_Admin
    - **Property 11: Field Revisions Complete, Ordered, Access-Controlled**
    - **Validates: Requirements 11.1, 11.2, 11.4, 17.2, 17.3**

- [x] 23. Implement review workspace React UI
  - [x] 23.1 Build the `ReviewWorkspace` component: two-panel layout (PDF viewer panel on the left, editable draft form panel on the right); both panels are simultaneously visible without scrolling between them; PDF viewer uses `react-pdf` to render the invoice file fetched from the backend; form panel renders all `HeaderFields`, `LineItems`, and `PaymentDetails` as individually editable inputs; render the "AI-generated draft — not approved" label persistently at the top of the form panel
    - _Requirements: 4.2, 10.1, 10.3, 10.4_
  - [x] 23.2 For each editable field, display the original AI-extracted value adjacent to the edit control (read-only); call `PUT /requests/{id}/fields` on field blur/save; show a confirmation prompt when the user attempts to navigate away with unsaved changes
    - _Requirements: 10.4, 10.6_
  - [x] 23.3 Handle PDF load failure: display an error message in the PDF panel and a direct download link for the original file
    - _Requirements: 10.5_
  - [x] 23.4 On workspace mount, begin measuring the time from navigation request to the point when both panels are interactive; report this duration to `POST /telemetry/page-load` with `page = REVIEW_WORKSPACE`
    - _Requirements: 10.2, 20.2, 20.7_
  - [x] 23.5 Write unit tests for `ReviewWorkspace`: renders AI label, unsaved-changes prompt fires on navigation, PDF load failure shows error + download link, field edit calls the update API
    - _Requirements: 4.2, 10.4, 10.5, 10.6_

- [x] 24. Checkpoint — Load the review workspace in the browser against a `READY_FOR_REVIEW` request; confirm two-panel layout, AI label, inline original-value display, and field save. Ask the user if any questions arise.

---

### Phase 7: Approval and Rejection Workflow

- [x] 25. Implement Finance Reviewer approval
  - [x] 25.1 Implement `POST /requests/{id}/approve` FastAPI router (Finance Reviewer + active `ClientAssignment`): enforce SOD — if `request.uploader_id == current_user.user_id` raise `HTTPException(403, "Self-approval is not permitted.")`; if any active EXCEPTION flag exists raise `HTTPException(409, "Unresolved exceptions must be addressed before this Request can be approved.")`; if `current_user.role != FINANCE_REVIEWER` raise `HTTPException(403, "You do not have authority to approve Requests.")`; on success transition status to `AMIGA_REVIEWED`, persist an `APPROVED` `AuditEvent` with approver `user_id` + UTC timestamp; record `UNAUTHORIZED_APPROVAL_ATTEMPT` `AuditEvent` for `ROBOT_ADMIN` or `SYSTEM_ADMIN` role attempts
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 4.5_
  - [ ] 25.2 Write pytest tests for approval: self-approval rejected, wrong role rejected, `EXCEPTION` status blocked, valid approval transitions to `AMIGA_REVIEWED`, `SYSTEM_ADMIN` attempt logged as `UNAUTHORIZED_APPROVAL_ATTEMPT`
    - _Requirements: 12.2, 12.3, 12.4, 12.5_
  - [ ]* 25.3 Write Hypothesis property test for **Property 12** (approval enforces SOD and role restriction): for any approval action, (non-`FINANCE_REVIEWER` role → rejected) AND (uploader == actor → self-approval error) AND (`EXCEPTION` status → rejected); valid approval → `AMIGA_REVIEWED` + `APPROVED` `AuditEvent`
    - **Property 12: Approval Enforces Segregation of Duties**
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.5, 4.5**

- [x] 26. Implement Finance Reviewer rejection
  - [x] 26.1 Implement `POST /requests/{id}/reject` FastAPI router (Finance Reviewer + active `ClientAssignment`): validate `rejection_reason` length in [1, 500] using a Pydantic v2 `Field(min_length=1, max_length=500)` constraint — reject with `422` otherwise; enforce SOD (uploader ≠ reviewer); transition status to `REJECTED`; persist `REJECTED` `AuditEvent` with reason, reviewer `user_id`, and UTC timestamp; make the rejection reason visible to authorized `Client_PIC`s on the Request detail endpoint
    - _Requirements: 13.1, 13.2, 13.3, 13.4_
  - [ ]* 26.2 Write pytest tests for rejection: 0-char reason rejected, 501-char reason rejected, 1-char and 500-char reasons accepted, self-rejection rejected, valid rejection transitions to `REJECTED` with reason in `AuditEvent`
    - _Requirements: 13.1, 13.4_
  - [ ]* 26.3 Write Hypothesis property test for **Property 13** (rejection enforces SOD and reason length): for any rejection, (reason length ∉ [1, 500] → rejected) AND (uploader == actor → self-rejection error); valid rejection → `REJECTED` status + `REJECTED` `AuditEvent`; reason visible to authorized `Client_PIC`
    - **Property 13: Rejection Enforces SOD and Reason Length**
    - **Validates: Requirements 13.1, 13.2, 13.3, 13.4**

- [x] 27. Checkpoint — Test approval and rejection flows end-to-end: attempt self-approval (should fail), attempt approval from wrong role (should fail), approve a `READY_FOR_REVIEW` request (should succeed), reject with valid and invalid reasons. Ask the user if any questions arise.

---

### Phase 8: Voucher Generation and Handover

- [x] 28. Implement Voucher Service — generation
  - [x] 28.1 Implement `POST /requests/{id}/voucher-pdf` FastAPI router (`Client_PIC` + active `ClientAssignment`): verify `request.status == AMIGA_REVIEWED`; load the active `VoucherTemplate` via `template_service.load_active_template(db)`; build Jinja2 context from approved `HeaderFields`, `LineItems` (with their `TaxAssignment` rows), `PaymentDetails`, `tax_service.compute_tax_summary()` result, and `settings.AUTHORIZATION_DISCLAIMER`; render HTML using `jinja2.Environment(loader=BaseLoader()).from_string(template.template_content).render(context)`; convert to PDF using `weasyprint.HTML(string=rendered_html).write_pdf()`; compute `disclaimer_hash = sha256(disclaimer_text.encode()).hexdigest()` and `template_hash = sha256(rendered_html.encode()).hexdigest()`; upload PDF bytes to S3 at `vouchers/{request_id}/{voucher_pdf_id}.pdf`; persist `VoucherPDF` record with `file_key`, `generated_by`, `generated_at`, `template_version`, `disclaimer_hash`, `template_hash`; write `VOUCHER_GENERATED` AuditEvent; record generation duration in `TelemetryRecord`; SLA is **5 seconds** (updated from 3s); if generation fails return `HTTPException(500)` and do NOT write AuditEvent or transition status
    - _Requirements: 15.1, 15.2, 15.4, 15.5, 15.6, 21.6, 22.3, 22.4, 22.5, 22.7, 22.8, 20.3, 20.6_
  - [x] 28.2 Enforce status guard: if `request.status != AMIGA_REVIEWED`, return error stating the Request has not completed independent review
    - _Requirements: 15.3_
  - [ ] 28.3 Write pytest tests for PDF generation: non-`AMIGA_REVIEWED` status blocked, generation success creates `VoucherPDF` record + `AuditEvent`, generation failure leaves no `AuditEvent` and no status transition, disclaimer text present in output
    - _Requirements: 15.3, 15.5, 15.6_

- [ ] 29. Implement voucher preview
  - [ ] 29.1 Implement `GET /requests/{id}/voucher-preview` FastAPI router (Finance Reviewer or Client_PIC + active `ClientAssignment`): return a Pydantic v2 response schema containing all approved `HeaderFields`, `LineItems`, `PaymentDetails`, and the `AUTHORIZATION_DISCLAIMER` text for the frontend to render as a read-only preview; respond within 3 seconds
    - _Requirements: 14.1, 14.2, 14.3_
  - [ ] 29.2 Build the `VoucherPreview` React component: render all approved fields and the disclaimer in a formatted, read-only layout; show an error message and retry button if the preview endpoint returns an error
    - _Requirements: 14.2, 14.4_
  - [ ]* 29.3 Write unit tests for `VoucherPreview` component: renders disclaimer, displays all approved fields, shows error + retry on API failure
    - _Requirements: 14.2, 14.4_
  - [ ]* 29.4 Write Hypothesis property test for **Property 14** (disclaimer appears in all voucher outputs): for any Request in `AMIGA_REVIEWED` status, both the in-system preview response and the generated PDF SHALL contain the `AUTHORIZATION_DISCLAIMER` text; the PDF SHALL include all approved `HeaderFields`, `LineItems`, and `PaymentDetails`
    - **Property 14: Disclaimer Present in All Voucher Outputs**
    - **Validates: Requirements 14.2, 15.2, 15.4**

- [ ] 30. Implement voucher download and SENT_TO_CLIENT transition
  - [ ] 30.1 Implement `GET /requests/{id}/voucher-pdf` FastAPI router (`Client_PIC` + active `ClientAssignment`): verify `request.status == AMIGA_REVIEWED`; fetch PDF bytes from `S3FileStore`; idempotency check — if no prior `SENT_TO_CLIENT` `AuditEvent` exists, write `VOUCHER_DOWNLOADED` and `STATUS_CHANGED (AMIGA_REVIEWED → SENT_TO_CLIENT)` `AuditEvent`s, then transition Request status to `SENT_TO_CLIENT`; stream PDF bytes using FastAPI `StreamingResponse(content=iter([pdf_bytes]), media_type="application/pdf")`; display `SENT_TO_CLIENT` UTC timestamp on the Request detail screen once recorded
    - _Requirements: 16.1, 16.2, 16.5_
  - [ ] 30.2 Enforce authorization guards: no active `ClientAssignment` → `HTTPException(403)` with no client data; `status != AMIGA_REVIEWED` → error stating voucher not yet available
    - _Requirements: 16.3, 16.4_
  - [ ]* 30.3 Write pytest tests for download: status ≠ `AMIGA_REVIEWED` blocked, missing assignment blocked, first download triggers `SENT_TO_CLIENT` transition, second download does NOT create a duplicate `SENT_TO_CLIENT` event (idempotency)
    - _Requirements: 16.1, 16.2, 16.3, 16.4_
  - [ ]* 30.4 Write Hypothesis property test for **Property 15** (PDF generation and download gated on AMIGA_REVIEWED; exactly one SENT_TO_CLIENT event): for any non-`AMIGA_REVIEWED` request, both generation and download denied; for any number of download attempts on an `AMIGA_REVIEWED` request by an authorized `Client_PIC`, exactly one `SENT_TO_CLIENT` event in `AuditTrail`; unauthorized `Client_PIC` denied with no client data
    - **Property 15: Download Gated on Status; Exactly One SENT_TO_CLIENT Event**
    - **Validates: Requirements 15.3, 16.1, 16.2, 16.3, 16.4**

- [ ] 31. Checkpoint — Full handover flow: approve a request, generate PDF, download it twice, verify exactly one `SENT_TO_CLIENT` event and `status = SENT_TO_CLIENT`. Ask the user if any questions arise.

---

### Phase 9: Queue Dashboard and Exception UX

- [ ] 32. Implement queue dashboard API
  - [ ] 32.1 Implement `GET /requests` FastAPI router with role-based and assignment-based filtering via `Depends(get_current_user)`:
    - Finance_Reviewer: statuses `{READY_FOR_REVIEW, EXCEPTION, AMIGA_REVIEWED}` for the user's assigned clients
    - Client_PIC: statuses `{AMIGA_REVIEWED, SENT_TO_CLIENT}` for the user's assigned clients
    - Service_Lead: all statuses for the user's assigned clients
    - Return Pydantic response schema with at minimum: `request_id`, `client_name`, `vendor_name`, `status`, `created_at`
    - Query must resolve within 2 seconds for up to 500 results
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
  - [ ] 32.2 Implement `POST /requests/{id}/reallocate` FastAPI router (Service Lead only): update `assigned_reviewer`; write a `REALLOCATED` `AuditEvent` with actor `user_id`, previous reviewer `user_id`, new reviewer `user_id`, and UTC timestamp
    - _Requirements: 3.7_
  - [ ]* 32.3 Write pytest tests for queue dashboard: Finance_Reviewer only sees permitted statuses and own-client requests, Client_PIC filtered correctly, Service_Lead sees all statuses, non-assigned client request excluded
    - _Requirements: 3.1, 3.2, 3.3, 3.4_
  - [ ]* 32.4 Write Hypothesis property test for **Property 5** (queue dashboard returns only authorized requests filtered by role): for any user with a specific role and set of active `ClientAssignment`s, the dashboard returns only the client's requests in the role-permitted statuses and includes all required display fields
    - **Property 5: Queue Dashboard Filtered by Role and Assignment**
    - **Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6**

- [ ] 33. Implement Exception UX
  - [ ] 33.1 Implement the exception detail API — extend `GET /requests/{id}` to include in the Pydantic response: all active flags, per-flag details (`MISSING_EVIDENCE`: list of missing field names; `POSSIBLE_DUPLICATE`: matching request summaries with `request_id`/`status`/`invoice_date`/`total_amount`; calculation discrepancy: flagged field name + computed + extracted values)
    - _Requirements: 6.3, 7.3, 8.3, 9.1, 9.2_
  - [ ] 33.2 Build the `ExceptionPanel` React component: display all active flags with visual distinction (color/icon) and the flag identifier label; per-flag next-action instructions (supply missing field / compare matching request / correct inconsistent value); prevent approval action while EXCEPTION flags are active (hide/disable approve button)
    - _Requirements: 9.1, 9.2, 9.3, 9.4_
  - [ ]* 33.3 Write unit tests for `ExceptionPanel`: renders all flag types, correct per-flag instruction shown, approve button absent in `EXCEPTION` status
    - _Requirements: 9.1, 9.3, 9.4_
  - [ ]* 33.4 Write Hypothesis property test for **Property 9** (exception UX displays all active flags with per-flag instructions): for any Request in `EXCEPTION` status with any non-empty combination of flags, all flags are displayed and each flag's specific next-action instruction is present
    - **Property 9: Exception UX Shows All Active Flags**
    - **Validates: Requirements 9.1, 9.3**

- [ ] 34. Build queue dashboard React UI
  - [ ] 34.1 Build the `QueueDashboard` component: fetch `GET /requests`, display the filtered list in a table with columns for `request_id`, client name, vendor name, status, and submission timestamp; link each row to the appropriate detail screen (`ReviewWorkspace`, `VoucherPreview`, or read-only detail depending on status); measure and report page load time to `POST /telemetry/page-load` with `page = QUEUE_DASHBOARD`
    - _Requirements: 3.5, 3.6, 20.2, 20.7_
  - [ ]* 34.2 Write unit tests for `QueueDashboard`: renders correct columns, navigation to correct detail screen per status, page load telemetry called
    - _Requirements: 3.6, 20.2_
  - [ ]* 34.3 Write Hypothesis property test for **Property 10** (AI-generated draft label always visible): for any Request that has completed extraction (full or partial), the Draft carries the persistent "AI-generated draft — not approved" label and status is `READY_FOR_REVIEW` or `EXCEPTION` — never `AMIGA_REVIEWED` without an explicit Finance_Reviewer approval action
    - **Property 10: AI Draft Label Always Visible After Extraction**
    - **Validates: Requirements 4.2, 4.5**

- [ ] 35. Checkpoint — Browse the queue dashboard as each role; verify correct status filtering and client isolation. Open an EXCEPTION request and confirm all flag types and instructions render. Ask the user if any questions arise.

---

### Phase 10: Audit Timeline

- [ ] 36. Implement Audit Service — read API
  - [ ] 36.1 Implement `GET /requests/{id}/audit` FastAPI router (Finance Reviewer, Service Lead, System Admin + active `ClientAssignment`): async SQLAlchemy query joining `AuditEvent` and `FieldRevision` for the given `request_id`; merge and sort by `occurred_at` / `revised_at` ascending; return combined timeline within 3 seconds; include for each event: `event_type`, `actor_id`, `payload`, UTC timestamp; include for each revision: `field_path`, `old_value`, `new_value`, `actor_id`, UTC timestamp
    - _Requirements: 17.1, 17.2, 17.3, 17.4, 17.5_
  - [ ]* 36.2 Write pytest tests for audit read API: Finance_Reviewer can access, Client_PIC cannot, events and revisions in correct chronological order, response includes all required fields
    - _Requirements: 17.3, 17.4, 17.5_

- [ ] 37. Implement Audit Timeline React UI
  - [ ] 37.1 Build the `AuditTimeline` component accessible from the Request detail screen for Finance_Reviewers, Service_Leads, and System_Admins: render each event and revision in chronological order with event type, actor identity, UTC timestamp, and payload details; display field revisions with old/new value side by side; the component must be read-only
    - _Requirements: 17.2, 17.3, 17.5_
  - [ ]* 37.2 Write unit tests for `AuditTimeline`: renders events in order, displays revision old/new values, no edit controls present
    - _Requirements: 17.3, 17.5_

- [ ] 38. Enforce 7-year retention policy
  - [ ] 38.1 Write an Alembic migration that adds a table-level `RULE` or DDL trigger preventing `DELETE` on `AuditEvent` and `FieldRevision` rows with `occurred_at` / `revised_at` within 7 years of current date; document the cold-storage archival strategy in a `docs/audit-retention.md` file (no code execution required beyond the migration)
    - _Requirements: 17.1_

- [ ] 39. Checkpoint — Submit a full workflow (upload → review → edit fields → approve → download); view the Audit Timeline and verify every event appears in correct chronological order. Ask the user if any questions arise.

---

### Phase 11: Telemetry

- [ ] 40. Implement Telemetry Service — ingestion
  - [ ] 40.1 Implement `POST /telemetry/page-load` FastAPI internal router: accept Pydantic body `{ page: str, duration_ms: int }`; persist a `TelemetryRecord` with `metric_type = PAGE_LOAD_DURATION`; if `duration_ms > 2000` (for queue dashboard or review workspace), also persist a breach event with `is_breach = True`
    - _Requirements: 20.2, 20.7_
  - [ ] 40.2 Implement `POST /telemetry/pdf-generation` FastAPI internal router (called by Voucher Service): accept Pydantic body `{ job_id: str, duration_ms: int }`; persist a `TelemetryRecord` with `metric_type = PDF_GENERATION_DURATION`; if `duration_ms > 3000`, persist a breach event
    - _Requirements: 20.3, 20.6_
  - [ ]* 40.3 Write pytest tests for telemetry ingestion: under-threshold → no breach record, over-threshold → breach record with correct fields
    - _Requirements: 20.6, 20.7_

- [ ] 41. Implement Telemetry Service — aggregated metrics dashboard API
  - [ ] 41.1 Implement `GET /telemetry/metrics` FastAPI router (Service Lead, System Admin only): use SQLAlchemy async queries with `percentile_cont` or in-Python computation to derive p50, p90, p99 durations, total measurement count, breach count, and `breach_rate = (breach_count / total_count * 100)` per `metric_type` from `TelemetryRecord`; cache aggregations and refresh at intervals ≤ 60 seconds (e.g., store last-computed result and `computed_at`; recompute only if `> 60s` old); return aggregated result
    - _Requirements: 20.8_
  - [ ] 41.2 Implement `GET /telemetry/breaches` FastAPI router (Service Lead, System Admin only): return `TelemetryRecord` rows where `is_breach = True`, ordered by `measured_at` descending
    - _Requirements: 20.4, 20.5, 20.6, 20.7_
  - [ ]* 41.3 Write pytest tests for metrics API: `p50 ≤ p90 ≤ p99`, `breach_rate` calculation correct, refresh interval guard
    - _Requirements: 20.8_
  - [ ]* 41.4 Write Hypothesis property test for **Property 17** (telemetry breach events recorded when SLA exceeded; aggregated metrics consistent): for any measured operation exceeding its threshold, a breach event is recorded with correct fields; for any set of telemetry records, `p50 ≤ p90 ≤ p99` and `breach_rate = (breach_count / total_count) × 100`
    - **Property 17: Telemetry SLA Breaches Recorded; Aggregated Metrics Consistent**
    - **Validates: Requirements 20.1, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8**

- [ ] 42. Build Telemetry Dashboard React UI
  - [ ] 42.1 Build the `TelemetryDashboard` component (Service Lead and System Admin only): display per-metric-type cards showing p50/p90/p99, total count, breach count, and breach rate; auto-refresh every 60 seconds; fetch from `GET /telemetry/metrics`
    - _Requirements: 20.8_
  - [ ]* 42.2 Write unit tests for `TelemetryDashboard`: displays all metric types, auto-refresh fires at ≤ 60 s interval, unauthorized role sees 403
    - _Requirements: 20.8_

- [ ] 43. Checkpoint — Run several mock operations (fast extraction, slow extraction, fast PDF, slow PDF, fast page load, slow page load); check the telemetry dashboard shows correct p50/p90/p99 and breach records. Ask the user if any questions arise.

---

### Phase 12: Property-Based Tests

- [ ] 44. Set up the property-based testing framework
  - [ ] 44.1 Add `hypothesis` (with `hypothesis[numpy]` if needed) to `pyproject.toml` dev dependencies (`[project.optional-dependencies]` or `[tool.pytest.ini_options]`); create `backend/tests/pbt/` directory with `__init__.py` and `conftest.py` (async test setup with `pytest-asyncio`); configure Hypothesis settings in `conftest.py`: `settings.register_profile("ci", max_examples=100)`; activate profile in `conftest.py` with `settings.load_profile("ci")`; add `pytest` ini config to discover `tests/pbt/` automatically
    - _Requirements: all PBT properties_

- [ ] 45. Write remaining standalone property tests
  - [ ]* 45.1 Consolidate and verify all 17 Hypothesis property tests exist in `backend/tests/pbt/` with the `@given` decorator and `@settings(max_examples=100)`:
    - Property 1 (valid uploads): `tests/pbt/test_property_01_valid_uploads.py` — _Requirements: 1.1, 1.4, 1.5_
    - Property 2 (invalid files rejected): `tests/pbt/test_property_02_invalid_files.py` — _Requirements: 1.2, 1.3_
    - Property 3 (missing metadata named): `tests/pbt/test_property_03_missing_metadata.py` — _Requirements: 1.7, 19.7_
    - Property 4 (unique request IDs): `tests/pbt/test_property_04_unique_ids.py` — _Requirements: 2.1_
    - Property 5 (queue dashboard filtering): `tests/pbt/test_property_05_queue_dashboard.py` — _Requirements: 3.1–3.4, 3.6_
    - Property 6 (MISSING_EVIDENCE): `tests/pbt/test_property_06_missing_evidence.py` — _Requirements: 4.6, 5.3, 6.1–6.3_
    - Property 7 (calculation validation): `tests/pbt/test_property_07_calculation.py` — _Requirements: 7.1–7.3_
    - Property 8 (duplicate detection): `tests/pbt/test_property_08_duplicate_detection.py` — _Requirements: 8.1–8.3_
    - Property 9 (exception UX flags): `tests/pbt/test_property_09_exception_ux.py` — _Requirements: 9.1, 9.3_
    - Property 10 (AI draft label): `tests/pbt/test_property_10_ai_label.py` — _Requirements: 4.2, 4.5_
    - Property 11 (field revisions): `tests/pbt/test_property_11_field_revisions.py` — _Requirements: 11.1, 11.2, 11.4, 17.2, 17.3_
    - Property 12 (approval SOD): `tests/pbt/test_property_12_approval_sod.py` — _Requirements: 12.1–12.3, 12.5, 4.5_
    - Property 13 (rejection SOD + reason): `tests/pbt/test_property_13_rejection.py` — _Requirements: 13.1–13.4_
    - Property 14 (disclaimer in outputs): `tests/pbt/test_property_14_disclaimer.py` — _Requirements: 14.2, 15.2, 15.4_
    - Property 15 (download gated + one SENT_TO_CLIENT): `tests/pbt/test_property_15_download.py` — _Requirements: 15.3, 16.1–16.4_
    - Property 16 (client isolation): `tests/pbt/test_property_16_client_isolation.py` — _Requirements: 18.1–18.4_
    - Property 17 (telemetry SLA): `tests/pbt/test_property_17_telemetry.py` — _Requirements: 20.1, 20.3–20.8_

- [ ] 46. Checkpoint — Run `pytest backend/tests/pbt/ -v` and confirm all 17 Hypothesis property tests pass with ≥ 100 examples each. Ask the user if any questions arise.

---

### Phase 13: Integration and Performance Tests

- [ ] 47. Write end-to-end integration tests
  - [ ]* 47.1 Write integration test using `pytest-asyncio` + `httpx.AsyncClient` against the FastAPI app: full happy path — upload PDF → mock extraction → Finance Reviewer corrects a field → approve → Client_PIC generates PDF → Client_PIC downloads PDF; assert: Request status = `SENT_TO_CLIENT`, Audit_Trail contains `REQUEST_CREATED` / `STATUS_CHANGED` (×4) / `FIELD_REVISED` / `APPROVED` / `VOUCHER_GENERATED` / `VOUCHER_DOWNLOADED` / `STATUS_CHANGED (AMIGA_REVIEWED → SENT_TO_CLIENT)`, exactly one `SENT_TO_CLIENT` event
    - _Requirements: 1.1, 4.1, 11.1, 12.1, 15.1, 16.1, 17.1_
  - [ ]* 47.2 Write integration test for client isolation: create User A with `ClientAssignment` for Client 1 only; verify User A cannot list, read, update, or download any Request belonging to Client 2; revoke User A's Client 1 assignment at runtime and confirm the very next request for Client 1 data is denied
    - _Requirements: 18.1, 18.2, 18.3, 18.4_
  - [ ]* 47.3 Write integration test for telemetry breach pipeline: mock an extraction job that runs for 16 000 ms (> 15 000 ms threshold for ≤ 5-page PDF); assert a `TelemetryRecord` breach event is written with the correct fields and appears in `GET /telemetry/breaches`
    - _Requirements: 20.4_
  - [ ]* 47.4 Write integration test for exception resolution flow: upload → extraction with `MISSING_EVIDENCE` → Finance Reviewer supplies missing payment fields → all flags clear → status transitions to `READY_FOR_REVIEW` → Finance Reviewer approves
    - _Requirements: 6.1, 6.2, 9.4, 12.1_

- [ ] 48. Write performance tests
  - [ ]* 48.1 Write a locust or pytest-benchmark performance test that seeds 500 Requests for a single client and measures `GET /requests` response time; assert p95 ≤ 2 000 ms
    - _Requirements: 3.5_
  - [ ]* 48.2 Write a locust or pytest-benchmark performance test that measures Voucher PDF generation time end-to-end (Jinja2 render + WeasyPrint conversion + S3 upload); assert p95 ≤ 5 000 ms
    - _Requirements: 15.1_

- [ ] 49. Final checkpoint — Run the full test suite (`pytest backend/`, `pytest backend/tests/pbt/`, integration tests, performance tests); all required tasks pass. Review any remaining optional tasks with the user and decide whether to include them in the MVP. Ask the user if any questions arise.

---

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. All unmarked tasks are required for a complete, production-ready implementation.
- Implementation language is **Python 3.12+** (backend: FastAPI + SQLAlchemy 2.x async) and **TypeScript** (frontend: React + Vite).
- PBT library: **Hypothesis** (`pip install hypothesis`). Each property test uses `@given` with `hypothesis.strategies` and `@settings(max_examples=100)`. Each test file is tagged with a comment: `# Feature: invoice-to-voucher, Property {N}: {property_text}`.
- Web framework: **FastAPI** with `Depends()` for auth, role checks, and client assignment enforcement — replaces Express middleware.
- Background worker: **asyncio polling loop** started via FastAPI `lifespan` in `app/main.py`; `process_extraction_job` in `app/services/extraction_worker.py`; polls `extraction_jobs` table every 2 seconds using `SELECT ... FOR UPDATE SKIP LOCKED`; retries up to 3 times (tracked in `retry_count` column); no Redis or separate worker process needed.
- Database ORM: **SQLAlchemy 2.x async** with **Alembic** migrations — replaces Knex/Prisma.
- PDF generation (backend): **Jinja2** renders the active `VoucherTemplate` HTML; **WeasyPrint** converts to PDF. Template stored in `VoucherTemplate` DB table, seeded from `app/templates/voucher_default.html`. Template version, `template_hash` (SHA-256 of rendered HTML), and `disclaimer_hash` (SHA-256 of disclaimer text) are recorded in `VoucherPDF` for auditability.
- File storage: **boto3** S3 client connecting to real AWS S3 in all environments. EvidenceFiles stored at `evidence/{request_id}/{order}_{filename}`; VoucherPDFs stored at `vouchers/{request_id}/{voucher_pdf_id}.pdf`. Use a dedicated development bucket. In AWS-hosted environments, attach an IAM role — never hardcode credentials. For the test suite, S3 calls are mocked with `moto`.
- Indonesian tax calculation: Use `app/services/tax_service.py` with Python `Decimal` for all tax arithmetic. NEVER use `float`. Hardcoded rates: PPH21=5%, PPH22=1.5%, PPH23=2%, PPH25=2%, PPN=11%, NO_TAX=0%. Rates are constants in code — not configurable at runtime. Grand total = sum(line_totals) + sum(tax_amounts).
- Bedrock provider: **boto3** `bedrock-runtime` client; model selected via `BEDROCK_MODEL_ID` env var; credentials from AWS credential provider chain.
- The `AuditEvent` and `FieldRevision` tables must have `INSERT`-only application grants and DDL triggers blocking `UPDATE` and `DELETE` — enforced at the database layer, not just the application layer.
- `ClientAssignment.is_active` is checked on every request via a direct async SQLAlchemy DB lookup. Do not cache assignment status.
- The `CALCULATION_TOLERANCE` value is loaded from the `CALCULATION_TOLERANCE` environment variable via `Pydantic Settings` and must be configurable without redeployment. Use Python `Decimal` (never `float`) for all financial arithmetic.
- The `AUTHORIZATION_DISCLAIMER` text is loaded from `Pydantic Settings`; its SHA-256 hash (`hashlib.sha256(disclaimer.encode()).hexdigest()`) is stored in each `VoucherPDF` record for auditability.
- All financial numeric values use `NUMERIC(18,4)` in PostgreSQL. Use Python `Decimal` for all arithmetic operations on financial values.
- Each task references specific requirements for traceability. All 22 requirements (including Req 21: Indonesian Tax Assignment and Req 22: Template-Based Voucher PDF) are covered.

---

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["2.1", "3.1", "3.2", "3.4"] },
    { "id": 1, "tasks": ["2.2", "2.3", "3.3", "3.5", "5.1", "10.1"] },
    { "id": 2, "tasks": ["5.2", "6.1", "10.2"] },
    { "id": 3, "tasks": ["6.2", "6.3", "7.1", "10.3", "10.4"] },
    { "id": 4, "tasks": ["7.2", "7.3", "8.1", "8.2", "8.3", "11.1"] },
    { "id": 5, "tasks": ["8.4", "8.5", "11.2", "11.3", "11.4", "12.1"] },
    { "id": 6, "tasks": ["12.2", "14.1", "17.1"] },
    { "id": 7, "tasks": ["14.2", "14.3", "15.1", "17.2", "18.1"] },
    { "id": 8, "tasks": ["15.2", "18.2", "18.3", "19.1", "22.1"] },
    { "id": 9, "tasks": ["19.2", "19.3", "19.4", "20.1", "50.1", "50.2", "22.2"] },
    { "id": 10, "tasks": ["20.2", "50.3", "50.4", "22.3", "22.4", "23.1", "25.1"] },
    { "id": 11, "tasks": ["50.5", "51.1", "51.2", "23.2", "23.3", "23.4", "25.2", "25.3", "26.1"] },
    { "id": 12, "tasks": ["23.5", "26.2", "26.3", "28.1", "28.2", "32.1"] },
    { "id": 13, "tasks": ["28.3", "29.1", "30.1", "32.2", "33.1"] },
    { "id": 14, "tasks": ["29.2", "29.3", "29.4", "30.2", "30.3", "30.4", "32.3", "32.4", "33.2", "36.1"] },
    { "id": 15, "tasks": ["33.3", "33.4", "34.1", "36.2", "37.1", "38.1", "40.1", "40.2"] },
    { "id": 16, "tasks": ["34.2", "34.3", "37.2", "40.3", "41.1", "41.2"] },
    { "id": 17, "tasks": ["41.3", "41.4", "42.1"] },
    { "id": 18, "tasks": ["42.2", "44.1"] },
    { "id": 19, "tasks": ["45.1"] },
    { "id": 20, "tasks": ["47.1", "47.2", "47.3", "47.4", "48.1", "48.2"] }
  ]
}
```


