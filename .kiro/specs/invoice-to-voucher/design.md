# Design Document: AMIGA Robot Admin — Invoice-to-Voucher Processing Platform

## Overview

AMIGA Robot Admin is an internal platform that transforms vendor invoice PDFs into structured, auditable voucher documents. The system orchestrates a multi-step workflow: upload → AI extraction → validation → Finance Reviewer review and correction → approval → voucher generation → client handover. Every action is governed by role-based and client-assignment-based authorization, and every change is recorded in an immutable append-only audit trail.

### Core Design Principles

- **Human-in-the-loop**: The AI extraction engine produces a draft labelled "AI-generated — not approved." No status can advance to `AMIGA_REVIEWED` without an explicit Finance Reviewer approval action.
- **Segregation of duties**: The user who uploads a Request cannot approve it.
- **Client data isolation**: Access to any Request requires both a valid role AND an active `Client_Assignment` for that client. These are checked independently on every operation.
- **Append-only audit**: The `Audit_Trail` is never mutated — only appended to. Revisions are stored in timestamp order.
- **Fail-closed authorization**: Any ambiguity in role or assignment resolves to denial with a descriptive error.

---

## Architecture

The system is deployed as a **modular monolith** — not microservices. The FastAPI application handles all HTTP traffic and runs the background extraction worker in the same process via an `asyncio` background task.

```mermaid
graph TB
    subgraph "Presentation Layer"
        UI[Web Application<br/>React + TypeScript + Vite]
    end

    subgraph "API Layer"
        FA[FastAPI Application<br/>Auth + Routes + Services]
    end

    subgraph "Background Processing"
        EW[Extraction Worker<br/>asyncio background task]
    end

    subgraph "Core Modules"
        RS[Request Service]
        US[User & Auth Service]
        AUS[Audit Service]
        VS[Voucher Service]
        TS[Telemetry Service]
        VAL[Validation Engine]
    end

    subgraph "AI / Extraction"
        EP[Extraction Provider Interface]
        MOCK[Mock Provider<br/>local/testing]
        BEDROCK[Bedrock Provider<br/>production]
    end

    subgraph "Storage"
        PDB[(PostgreSQL)]
        FS[AWS S3<br/>File Store]
        JQ[PostgreSQL<br/>Job Queue Table]
    end

    UI --> FA
    FA --> RS
    FA --> US
    FA --> VS
    FA --> TS
    RS --> AUS
    RS --> JQ
    JQ --> EW
    EW --> EP
    EP --> MOCK
    EP --> BEDROCK
    EW --> VAL
    EW --> PDB
    EW --> AUS
    VS --> FS
    VS --> PDB
    AUS --> PDB
    US --> PDB
    RS --> PDB
    TS --> PDB
```

### Tech Stack

**Backend:**
- Python 3.12+
- FastAPI for REST APIs
- Pydantic v2 for request validation, response schemas, and configuration
- SQLAlchemy 2.x with async support for database access
- Alembic for database migrations
- PostgreSQL as the primary relational database (NUMERIC(18,4) for all financial values)
- PostgreSQL job queue (via `SELECT FOR UPDATE SKIP LOCKED` polling) — no Redis required
- boto3 for AWS SDK integration (S3 + Bedrock)
- PyJWT for JWT authentication
- jinja2 for HTML template rendering (explicit dependency)
- weasyprint for HTML-to-PDF conversion
- pytest + pytest-asyncio for unit and integration testing
- Hypothesis for property-based testing
- Ruff for linting and formatting
- pyproject.toml for dependency management
**Frontend:**
- React + TypeScript + Vite
- react-pdf for invoice viewing

**Storage:**
- PostgreSQL (primary database)
- AWS S3 for file storage (EvidenceFiles and VoucherPDFs are both stored in S3, scoped by request_id; real S3 bucket used in all environments, including local development)
- PostgreSQL job queue table (`extraction_jobs`) — no Redis required

### Component Responsibilities

| Component | Responsibility |
|---|---|
| **Web Application** | React SPA; two-panel review workspace, queue dashboard, exception UX, voucher preview, audit timeline UI |
| **FastAPI Application** | JWT auth via PyJWT dependency, Pydantic v2 validation, request routing, all HTTP handlers |
| **Request Service** | Python module managing request lifecycle and state machine; creation, status transitions, flag management |
| **User & Auth Service** | Python module for user CRUD, roles, assignments, session management |
| **Audit Service** | Python module for append-only event writes and chronological reads |
| **Voucher Service** | Python module for preview + PDF generation (using WeasyPrint or reportlab) |
| **Telemetry Service** | Python module measuring durations, recording breach events, serving aggregated metrics |
| **Extraction Worker** | asyncio background task running inside FastAPI; polls `extraction_jobs` table every 2 s using `SELECT FOR UPDATE SKIP LOCKED`; calls ExtractionProvider |
| **Extraction Provider Interface** | Abstract base class (ABC) with `extract(pdf_bytes) -> ExtractionResult` |
| **Mock Provider** | Returns configurable fixture data; no AWS credentials required |
| **Bedrock Provider** | Calls Amazon Bedrock Runtime via boto3; selected model is configurable; credentials from AWS credential provider chain (IAM roles preferred; no hardcoded keys) |
| **Validation Engine** | Pure Python module for completeness, calculation, and duplicate validation |

### Project Layout

```
invoice_to_voucher/
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   │   └── versions/
│   ├── app/
│   │   ├── main.py              # FastAPI app factory
│   │   ├── config.py            # Pydantic Settings
│   │   ├── dependencies.py      # FastAPI dependencies (auth, db session)
│   │   ├── models/              # SQLAlchemy ORM models
│   │   ├── schemas/             # Pydantic v2 request/response schemas
│   │   ├── repositories/        # DB access layer
│   │   ├── services/            # Business logic (request, user, audit, voucher, telemetry)
│   │   ├── routes/              # FastAPI routers
│   │   ├── providers/           # ExtractionProvider ABC + mock + bedrock
│   │   ├── templates/
│   │   │   └── voucher_default.html   # default Jinja2 voucher template
│   │   └── validation/          # Completeness, calculation, duplicate validators
│   │   └── services/
│   │       └── extraction_worker.py  # asyncio polling loop + extract_invoice function
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   └── src/
```

### Environment Variables

```
# backend/.env (local development only — do not commit secrets)

# Database — install PostgreSQL 16+ natively (e.g. brew install postgresql@16)
DATABASE_URL=postgresql+asyncpg://amiga:amiga@localhost:5432/amiga_db

# File Storage — real AWS S3 (used in all environments; no local emulator)
S3_BUCKET=bedrock-kb-student1-1791858842
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=your-secret-access-key
AWS_REGION=ap-southeast-1

# Auth
JWT_SECRET=change-me-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRY_SECONDS=3600

# Extraction
EXTRACTION_PROVIDER=mock               # "mock" for tests, "bedrock" for live extraction
BEDROCK_MODEL_ID=anthropic.claude-3-5-sonnet-20241022-v2:0

# Business rules
CALCULATION_TOLERANCE=0.01
AUTHORIZATION_DISCLAIMER=This voucher is prepared for client review purposes only. Payment authorization remains with the client.
```

**Local development setup**: Install PostgreSQL 16+ natively on your machine (e.g. via Homebrew: `brew install postgresql@16`). Create the `amiga_db` database and start it before running the API. No Redis is needed — the extraction job queue runs inside PostgreSQL. AWS S3 credentials must point to a real bucket — use a dedicated development bucket (e.g. `bedrock-kb-student1-1791858842`) to keep dev and production data separate.

**In AWS-hosted environments**: Do NOT set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`. Attach an IAM role to the instance or task instead — boto3 resolves credentials from the standard AWS credential provider chain automatically.

---

## Components and Interfaces

### API Layer — Authorization Middleware

Every incoming request is evaluated against two independent conditions before reaching any service:

1. **Role check**: The authenticated user's role must be permitted for the requested operation.
2. **Client Assignment check**: The user must hold an active `Client_Assignment` for the client associated with the target Request (or client entity). Both must pass; failure of either returns `403 Forbidden` with no client data in the response body.

```python
# FastAPI dependency — applied to all protected routes
async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    payload = jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])
    user = await db.get(User, payload["user_id"])
    if not user or not user.is_active:
        raise HTTPException(status_code=401)
    return user

async def require_client_assignment(request_id: UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    # Direct DB lookup every request — no caching
    client_id = await resolve_client_id(request_id, db)
    assignment = await db.execute(
        select(ClientAssignment).where(
            ClientAssignment.user_id == current_user.user_id,
            ClientAssignment.client_id == client_id,
            ClientAssignment.is_active == True
        )
    )
    if not assignment.scalar_one_or_none():
        raise HTTPException(status_code=403, detail="You are not authorized to access Requests for this client.")
```

Assignment revocation is checked on every request — no caching of assignment status beyond a single request's scope (satisfies Requirements 18.3 and 18.4).

### Request Service — State Machine Interface

```
POST   /requests                          → create request (upload)
GET    /requests                          → list (filtered by role + assignments)
GET    /requests/{id}                     → get request detail
PUT    /requests/{id}/fields              → update draft field(s)
POST   /requests/{id}/approve             → Finance Reviewer approval
POST   /requests/{id}/reject             → Finance Reviewer rejection
POST   /requests/{id}/dismiss-duplicate  → dismiss POSSIBLE_DUPLICATE flag
POST   /requests/{id}/reallocate         → Service Lead reallocation
```

### Voucher Service Interface

```
GET    /requests/{id}/voucher-preview    → render in-system voucher preview
POST   /requests/{id}/voucher-pdf        → generate Voucher PDF (initiates generation)
GET    /requests/{id}/voucher-pdf        → download Voucher PDF (triggers SENT_TO_CLIENT)
```

### User & Auth Service Interface

```
POST   /users                            → System Admin creates user
GET    /users/{id}                       → get user
PATCH  /users/{id}/role                  → assign/modify role
POST   /users/{id}/assignments           → add Client_Assignment
DELETE /users/{id}/assignments/{client}  → remove Client_Assignment
```

### Telemetry Service Interface

```
GET    /telemetry/metrics                → aggregated p50/p90/p99 + breach stats
GET    /telemetry/breaches               → breach event log
```

---

## Data Models

### Request

```
Request {
  request_id        UUID (PK, immutable)           -- system-generated
  client_id         UUID (FK → Client)
  uploader_id       UUID (FK → User)
  assigned_reviewer UUID (FK → User, nullable)
  request_type      ENUM('standard', 'urgent', ...) -- from metadata
  channel           VARCHAR(100)
  request_date      DATE
  status            ENUM(QUEUED, PROCESSING, READY_FOR_REVIEW,
                         EXCEPTION, AMIGA_REVIEWED,
                         SENT_TO_CLIENT, REJECTED)
  flags             TEXT[]                          -- MISSING_EVIDENCE, POSSIBLE_DUPLICATE
  invoice_file_key  VARCHAR (FK → stored file)
  invoice_page_count INT
  created_at        TIMESTAMPTZ
  updated_at        TIMESTAMPTZ
}
```

### Draft

```
Draft {
  draft_id          UUID (PK)
  request_id        UUID (FK → Request, unique — one draft per request)
  ai_label_visible  BOOLEAN DEFAULT TRUE           -- always true; UI enforces the label
  extraction_status ENUM(PENDING, COMPLETE, PARTIAL, FAILED)
  extracted_at      TIMESTAMPTZ
}
```

### EvidenceFile

```
EvidenceFile {
  evidence_id       UUID (PK, default uuid4)
  request_id        UUID (FK → Request)
  file_key          VARCHAR(500)            -- S3 key
  original_filename VARCHAR(500)
  content_type      VARCHAR(100)            -- application/pdf, image/png, image/jpeg
  file_size_bytes   BIGINT
  upload_order      INTEGER                 -- 1-based, preserves submission order
  uploaded_at       TIMESTAMPTZ
}
```

### HeaderFields

```
HeaderFields {
  header_id         UUID (PK)
  draft_id          UUID (FK → Draft)
  vendor_name       VARCHAR(500)
  invoice_number    VARCHAR(200)
  invoice_date      DATE
  currency          CHAR(3)                        -- ISO 4217
  subtotal          NUMERIC(18,4)
  total_amount      NUMERIC(18,4)
  tax_amount        NUMERIC(18,4)
  fee_amount        NUMERIC(18,4)
  rounding_adj      NUMERIC(18,4)
}
```

### LineItem

```
LineItem {
  line_item_id      UUID (PK)
  draft_id          UUID (FK → Draft)
  sequence_number   INT                            -- preserves original order
  description       TEXT
  quantity          NUMERIC(18,4)
  unit              VARCHAR(50)
  unit_price        NUMERIC(18,4)
  line_total        NUMERIC(18,4)
  extraction_status ENUM(COMPLETE, PARTIAL)        -- PARTIAL when any field missing
}
```

### TaxAssignment

```
TaxAssignment {
  tax_assignment_id UUID (PK, default uuid4)
  line_item_id      UUID (FK → LineItem, unique — one assignment per item)
  tax_type          ENUM(PPH21, PPH22, PPH23, PPH25, PPN, NO_TAX)
  tax_rate          NUMERIC(5,4)            -- hardcoded: 0.05, 0.015, 0.02, 0.02, 0.11, 0.00
  tax_amount        NUMERIC(18,4)           -- computed: line_total × tax_rate, using Decimal arithmetic
  assigned_at       TIMESTAMPTZ
  assigned_by       UUID (FK → User)
}
```

**Hardcoded Indonesian tax rates:**

| Tax Type | Rate |
|---|---|
| PPH21 | 5% (0.0500) |
| PPH22 | 1.5% (0.0150) |
| PPH23 | 2% (0.0200) |
| PPH25 | 2% (0.0200) |
| PPN | 11% (0.1100) |
| NO_TAX | 0% (0.0000) |

Tax amounts MUST be computed using Python `Decimal`, never `float`.

### PaymentDetails

```
PaymentDetails {
  payment_id        UUID (PK)
  draft_id          UUID (FK → Draft)
  bank_account_number VARCHAR(100)
  bank_name         VARCHAR(300)
  payment_reference  VARCHAR(300)
}
```

### FieldRevision

```
FieldRevision {
  revision_id       UUID (PK)
  request_id        UUID (FK → Request)
  field_path        VARCHAR(200)                   -- e.g. "header.vendor_name", "line_items[2].unit_price"
  old_value         TEXT
  new_value         TEXT
  actor_id          UUID (FK → User)
  revised_at        TIMESTAMPTZ
}
```

### AuditEvent

```
AuditEvent {
  event_id          UUID (PK)
  request_id        UUID (FK → Request, nullable for system-level events)
  event_type        ENUM(REQUEST_CREATED, STATUS_CHANGED, FIELD_REVISED,
                         APPROVED, REJECTED, DUPLICATE_DISMISSED,
                         REALLOCATED, VOUCHER_GENERATED, VOUCHER_DOWNLOADED,
                         UNAUTHORIZED_APPROVAL_ATTEMPT, ...)
  actor_id          UUID (FK → User, nullable for system events)
  payload           JSONB                          -- event-specific detail
  occurred_at       TIMESTAMPTZ
}
```

`AuditEvent` rows are never updated or deleted. The table has no `UPDATE` or `DELETE` grants for any application role.

### User

```
User {
  user_id           UUID (PK)
  username          VARCHAR(200) UNIQUE
  role              ENUM(CLIENT_PIC, INTAKE_SUPPORT, FINANCE_REVIEWER,
                         SERVICE_LEAD, SYSTEM_ADMIN)
  is_active         BOOLEAN
  created_at        TIMESTAMPTZ
  updated_at        TIMESTAMPTZ
}
```

### ClientAssignment

```
ClientAssignment {
  assignment_id     UUID (PK)
  user_id           UUID (FK → User)
  client_id         UUID (FK → Client)
  is_active         BOOLEAN
  assigned_at       TIMESTAMPTZ
  revoked_at        TIMESTAMPTZ (nullable)
  UNIQUE (user_id, client_id)
}
```

### Client

```
Client {
  client_id         UUID (PK)
  client_name       VARCHAR(300)
  is_active         BOOLEAN
}
```

### VoucherPDF

```
VoucherPDF {
  voucher_pdf_id       UUID (PK)
  request_id           UUID (FK → Request)
  file_key             VARCHAR(500)           -- S3 key
  generated_at         TIMESTAMPTZ
  generated_by         UUID (FK → User)
  template_version     VARCHAR(100)           -- version identifier of the template used
  disclaimer_hash      CHAR(64)               -- SHA-256 of Authorization_Disclaimer text
  template_hash        CHAR(64)               -- SHA-256 of rendered HTML before PDF conversion
}
```

### VoucherTemplate

```
VoucherTemplate {
  template_id      UUID (PK)
  version          VARCHAR(100)              -- e.g. "v1.0", "v1.1"
  template_content TEXT                      -- Jinja2 HTML template with {{placeholders}}
  is_active        BOOLEAN DEFAULT FALSE     -- exactly one active template at a time
  created_by       UUID (FK → User)
  created_at       TIMESTAMPTZ
}
```

**Required placeholders that every template must contain:**
`{{vendor_name}}`, `{{invoice_number}}`, `{{invoice_date}}`, `{{line_items_table}}`, `{{tax_summary_table}}`, `{{subtotal}}`, `{{total_tax}}`, `{{grand_total}}`, `{{bank_account_number}}`, `{{bank_name}}`, `{{payment_reference}}`, `{{authorization_disclaimer}}`

### TelemetryRecord

```
TelemetryRecord {
  record_id         UUID (PK)
  metric_type       ENUM(EXTRACTION_DURATION, PAGE_LOAD_DURATION, PDF_GENERATION_DURATION)
  subject_id        VARCHAR                        -- job_id or page identifier
  duration_ms       BIGINT
  page_count        INT (nullable)                 -- for extraction metrics
  is_breach         BOOLEAN
  breach_threshold_ms BIGINT
  measured_at       TIMESTAMPTZ
}
```

### Entity Relationship Diagram

```mermaid
erDiagram
    Client ||--o{ ClientAssignment : "assigned via"
    User ||--o{ ClientAssignment : "holds"
    Client ||--o{ Request : "has"
    User ||--o{ Request : "uploads"
    Request ||--o{ EvidenceFile : "has evidence"
    Request ||--|| Draft : "has"
    Draft ||--|| HeaderFields : "contains"
    Draft ||--o{ LineItem : "contains"
    LineItem ||--o| TaxAssignment : "has tax"
    Draft ||--|| PaymentDetails : "contains"
    Request ||--o{ FieldRevision : "has"
    Request ||--o{ AuditEvent : "logged in"
    Request ||--o| VoucherPDF : "produces"
    Request ||--o| VoucherTemplate : "uses template version"
    User ||--o{ AuditEvent : "actor"
    User ||--o{ FieldRevision : "actor"
```

---

## Workflow State Machine

### Status Transition Diagram

```mermaid
stateDiagram-v2
    [*] --> QUEUED : Request created (upload)
    QUEUED --> PROCESSING : Extraction job dispatched
    PROCESSING --> READY_FOR_REVIEW : Extraction complete, no flags
    PROCESSING --> EXCEPTION : Extraction complete, MISSING_EVIDENCE or calc discrepancy flag
    READY_FOR_REVIEW --> EXCEPTION : Flag applied during review (e.g. duplicate match found)
    READY_FOR_REVIEW --> AMIGA_REVIEWED : Finance Reviewer approves (SOD check passes)
    READY_FOR_REVIEW --> REJECTED : Finance Reviewer rejects (with reason)
    EXCEPTION --> READY_FOR_REVIEW : All flags resolved by Finance Reviewer
    EXCEPTION --> REJECTED : Finance Reviewer rejects (with reason)
    AMIGA_REVIEWED --> SENT_TO_CLIENT : Client PIC downloads Voucher PDF
    REJECTED --> [*] : Terminal state
    SENT_TO_CLIENT --> [*] : Terminal state
```

### Transition Guards

| Transition | Trigger | Guard Conditions |
|---|---|---|
| `QUEUED → PROCESSING` | Extraction queue picks up job | None (automatic) |
| `PROCESSING → READY_FOR_REVIEW` | Extraction worker signals extraction complete | No flags raised |
| `PROCESSING → EXCEPTION` | Extraction worker signals extraction complete | At least one flag raised (MISSING_EVIDENCE or calculation discrepancy) |
| `READY_FOR_REVIEW → AMIGA_REVIEWED` | `POST /requests/{id}/approve` | Actor role = FINANCE_REVIEWER; actor ≠ uploader; no active EXCEPTION flags |
| `READY_FOR_REVIEW → REJECTED` | `POST /requests/{id}/reject` | Actor role = FINANCE_REVIEWER; actor ≠ uploader; rejection reason 1–500 chars present |
| `EXCEPTION → READY_FOR_REVIEW` | Finance Reviewer resolves all flags | All flags cleared |
| `EXCEPTION → REJECTED` | `POST /requests/{id}/reject` | Actor role = FINANCE_REVIEWER; rejection reason present |
| `AMIGA_REVIEWED → SENT_TO_CLIENT` | `GET /requests/{id}/voucher-pdf` (download) | Actor holds active Client_Assignment for the request's client; request status = AMIGA_REVIEWED |

---

## AI Extraction and Validation Pipeline

### Extraction Job Lifecycle

```mermaid
sequenceDiagram
    participant RS as Request Service
    participant JQ as PostgreSQL Job Queue
    participant EW as Extraction Worker (asyncio)
    participant EP as ExtractionProvider
    participant PDB as Database
    participant AUS as Audit Service

    RS->>JQ: INSERT ExtractionJob row (status=PENDING)
    EW->>JQ: Poll every 2s — SELECT FOR UPDATE SKIP LOCKED
    JQ-->>EW: Deliver PENDING job
    EW->>PDB: Set Request status = PROCESSING
    EW->>AUS: Log STATUS_CHANGED event (QUEUED → PROCESSING)
    EW->>EP: extract(evidence_files, request_id)
    EP-->>EW: ExtractionResult (HeaderFields, LineItems, PaymentDetails)
    EW->>PDB: Write Draft, HeaderFields, LineItems, PaymentDetails
    EW->>EW: Run validation rules (completeness, calculation, duplicate)
    EW->>PDB: Apply flags, set status (READY_FOR_REVIEW or EXCEPTION)
    EW->>JQ: UPDATE ExtractionJob status = DONE
    EW->>AUS: Log STATUS_CHANGED + any FLAG_APPLIED events
```

### Validation Rules

**Completeness Validation (Requirement 6)**

```
required_payment_fields = [bank_account_number, bank_name, payment_reference]
missing = [f for f in required_payment_fields if draft.payment_details[f] is None or empty]
if missing:
    apply_flag(MISSING_EVIDENCE)
    set_status(EXCEPTION)
```

Any Line_Item with one or more unextractable fields also sets MISSING_EVIDENCE (Requirement 5.3).

**Calculation Validation (Requirement 7)**

```
tolerance = system_config.calculation_tolerance

# Check 1: line items vs subtotal
computed_subtotal = sum(line_item.line_total for line_item in line_items)
if abs(computed_subtotal - header.subtotal) > tolerance:
    flag_field("subtotal", computed=computed_subtotal, extracted=header.subtotal)
    apply_exception()

# Check 2: total composition
computed_total = header.subtotal + header.tax_amount + header.fee_amount + header.rounding_adj
if abs(computed_total - header.total_amount) > tolerance:
    flag_field("total_amount", computed=computed_total, extracted=header.total_amount)
    apply_exception()
```

**Duplicate Detection (Requirement 8)**

```sql
SELECT request_id, status, invoice_date, total_amount
FROM Request r
JOIN Draft d ON d.request_id = r.request_id
JOIN HeaderFields h ON h.draft_id = d.draft_id
WHERE LOWER(h.vendor_name) = LOWER(:vendor_name)
  AND LOWER(h.invoice_number) = LOWER(:invoice_number)
  AND r.status NOT IN ('REJECTED', 'VOIDED')
  AND r.request_id != :new_request_id
```

If any rows are returned, apply `POSSIBLE_DUPLICATE` flag and store matching `request_id` references for display in the Exception UX.

### Extraction Performance SLA

| PDF Page Count | Extraction Deadline | Breach Threshold |
|---|---|---|
| 1–5 pages | 15 seconds | 15,000 ms |
| 6–20 pages | 30 seconds | 30,000 ms |

The Telemetry Service measures from the moment the job transitions to PROCESSING to the moment extraction output is written to storage. A breach event is recorded if the measured duration exceeds the threshold.

---

## Bedrock Extraction Provider

### Extraction Provider Interface

```python
from abc import ABC, abstractmethod
from app.schemas.extraction import ExtractionResult

class ExtractionProvider(ABC):
    @abstractmethod
    async def extract(self, evidence_files: list[tuple[bytes, str]], request_id: UUID) -> ExtractionResult:
        """Extract invoice fields from one or more evidence files. Returns structured ExtractionResult."""
        ...
```

`ExtractionResult` is a Pydantic v2 model containing optional `HeaderFields`, `list[LineItem]`, and optional `PaymentDetails`. Missing fields are represented as `None`, not invented values.

### Mock Provider

Returns configurable fixture data from a JSON file or environment variable. No AWS credentials required. Used for local development and all automated tests by default.

### Bedrock Provider

```python
import boto3
from app.providers.base import ExtractionProvider

class BedrockExtractionProvider(ExtractionProvider):
    def __init__(self):
        # Credentials from AWS credential provider chain — never hardcoded
        self.client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)
        self.model_id = settings.BEDROCK_MODEL_ID  # configurable, e.g. "anthropic.claude-3-5-sonnet-20241022-v2:0"

    async def extract(self, evidence_files: list[tuple[bytes, str]], request_id: UUID) -> ExtractionResult:
        """
        Extract invoice fields from one or more evidence files.

        Args:
            evidence_files: List of (file_bytes, content_type) tuples — PDF, PNG, or JPEG.
            request_id: Used for idempotency checks.

        Pipeline:
        1. Encode each file for the Bedrock model:
           - PDFs: use document block (base64 encoded) if model supports it; otherwise convert to PNG
           - Images (PNG/JPEG): use image block (base64 encoded)
        2. Build a single multi-content message with the Extraction_Prompt as the system prompt
           and all encoded files as user message content blocks.
        3. Call bedrock-runtime InvokeModel / Converse API.
        4. Parse the model response JSON against the ExtractionResult Pydantic schema.
        5. Return ExtractionResult with None for any field the model could not extract.
        """
```

### ExtractionResult Schema

```python
class ExtractionResult(BaseModel):
    header: HeaderFieldsSchema | None
    line_items: list[LineItemSchema]
    payment_details: PaymentDetailsSchema | None
    extraction_prompt_version: str        # recorded in audit trail
    raw_response: str | None              # model's raw JSON for debugging
```

**Extraction_Prompt**: Stored in `VoucherTemplate`-adjacent config or as a system-level setting. Returns JSON conforming to the ExtractionResult schema. System_Admin-configurable without code deployment.

**Model selection notes:**
- The Bedrock model is selected via `BEDROCK_MODEL_ID` environment variable — it is NOT hardcoded.
- Not all Bedrock models support PDF input or structured JSON output. Verify the selected model's capabilities before deployment.
- For PoC, Claude 3.x or Amazon Titan models with document understanding are candidate options, but final selection requires compatibility verification.
- The provider interface isolates model-specific behavior so the model can be swapped without changing business logic.

### Provider Selection

```python
# backend/app/config.py
class Settings(BaseModel):
    EXTRACTION_PROVIDER: Literal["mock", "bedrock"] = "mock"  # default: mock
    BEDROCK_MODEL_ID: str = "anthropic.claude-3-5-sonnet-20241022-v2:0"
    AWS_REGION: str = "ap-southeast-1"
```

Switch from mock to Bedrock by setting `EXTRACTION_PROVIDER=bedrock` and providing AWS credentials via the standard credential chain.

---

## Background Worker Design

### PostgreSQL Job Queue Architecture

The extraction worker runs as an **asyncio background task inside the FastAPI process** — no separate worker process or Redis is needed. This is the simplest approach for a PoC.

```
FastAPI app  →  INSERT ExtractionJob (PENDING)  →  asyncio polling loop (every 2 s)  →  SELECT FOR UPDATE SKIP LOCKED  →  calls ExtractionProvider  →  runs validation  →  updates DB  →  logs audit events
```

### ExtractionJob Table

```
ExtractionJob {
  job_id            UUID (PK, default uuid4)
  request_id        UUID (FK → Request)
  status            ENUM(PENDING, IN_PROGRESS, DONE, FAILED) DEFAULT PENDING
  retry_count       INT DEFAULT 0
  created_at        TIMESTAMPTZ
  started_at        TIMESTAMPTZ (nullable)
  completed_at      TIMESTAMPTZ (nullable)
  error_detail      TEXT (nullable)
}
```

### Polling Loop

The loop is started via FastAPI's `lifespan` context manager in `app/main.py`:

```python
async def extraction_polling_loop(db_session_factory):
    while True:
        async with db_session_factory() as db:
            job = await db.execute(
                select(ExtractionJob)
                .where(ExtractionJob.status == "PENDING")
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            job = job.scalar_one_or_none()
            if job:
                await process_extraction_job(job, db)
        await asyncio.sleep(2)
```

**Retry and failure behavior:**
- On job failure, `retry_count` is incremented and status is reset to `PENDING`
- After 3 failures (`retry_count >= 3`), status is set to `FAILED`; Request status transitions to `EXCEPTION` with a `PROCESSING_FAILED` detail in the AuditEvent payload

**Idempotency:**
- Before creating a Draft, the worker checks if a Draft already exists for the `request_id` (unique constraint on `Draft.request_id`)
- If a Draft already exists, the worker skips creation and proceeds to validation — no duplicate Draft or AuditEvent is created

---

## Authorization Model

### Role × Operation Matrix

| Operation | CLIENT_PIC | INTAKE_SUPPORT | FINANCE_REVIEWER | SERVICE_LEAD | SYSTEM_ADMIN |
|---|:---:|:---:|:---:|:---:|:---:|
| Upload invoice | ✓ | ✓ (delegated) | — | — | — |
| View queue | ✓ (limited) | — | ✓ (limited) | ✓ (all) | — |
| Open review workspace | — | — | ✓ | — | — |
| Edit draft fields | — | — | ✓ | — | — |
| Approve Request | — | — | ✓ | — | — |
| Reject Request | — | — | ✓ | — | — |
| Dismiss POSSIBLE_DUPLICATE | — | — | ✓ | — | — |
| Reallocate Request | — | — | — | ✓ | — |
| View Audit Trail | — | — | ✓ | ✓ | ✓ |
| Download Voucher PDF | ✓ | — | — | — | — |
| Create/modify users | — | — | — | — | ✓ |
| View telemetry dashboard | — | — | — | ✓ | ✓ |

### Segregation of Duties Enforcement

The `uploader_id` is stored on the `Request` at creation time. On every approval or rejection action, the service layer performs:

```
if request.uploader_id == acting_user.user_id:
    raise SelfApprovalError("Self-approval is not permitted.")
```

This check is performed in the service layer (not only at the database layer) so that the error message is returned clearly to the UI.

### Client Assignment Enforcement

`ClientAssignment.is_active` is checked on every data access operation. The check is performed in the authorization middleware with a direct database lookup — no cache is used for assignment status — ensuring that a revocation applied by a System Admin is effective on the very next request from the affected user (Requirements 18.3 and 18.4).

---

## Audit Trail Design

### Append-Only Guarantee

- The `AuditEvent` table is created without `UPDATE` or `DELETE` privileges for any application database role.
- The `FieldRevision` table follows the same constraint.
- Both tables have `INSERT`-only access from the Audit Service.
- Database-level row-level triggers raise an error if an `UPDATE` or `DELETE` is attempted (defense-in-depth).

### Events Logged

| Event Type | Trigger | Payload Fields |
|---|---|---|
| `REQUEST_CREATED` | Upload | `request_id`, `client_id`, `uploader_id`, `upload_timestamp` |
| `STATUS_CHANGED` | Any status transition | `from_status`, `to_status`, `actor_id` (or `system` for automatic transitions) |
| `FLAG_APPLIED` | Validation flag set | `flag_type`, `details` |
| `FLAG_DISMISSED` | Finance Reviewer dismisses duplicate | `flag_type`, `actor_id` |
| `FIELD_REVISED` | Draft field edited | References `FieldRevision.revision_id` |
| `APPROVED` | Finance Reviewer approves | `actor_id` (approver) |
| `REJECTED` | Finance Reviewer rejects | `actor_id`, `rejection_reason` |
| `REALLOCATED` | Service Lead reallocates | `actor_id`, `previous_reviewer_id`, `new_reviewer_id` |
| `VOUCHER_GENERATED` | PDF generation completes | `actor_id`, `voucher_pdf_id` |
| `VOUCHER_DOWNLOADED` | Client PIC downloads PDF | `actor_id`, `download_timestamp` |
| `UNAUTHORIZED_APPROVAL_ATTEMPT` | Robot Admin / System Admin attempts approval | `actor_id`, `actor_role` |
| `USER_MODIFIED` | System Admin changes user role/assignment | Stored in system-level audit log (not per-Request) |

### Retention

The primary database retains all `AuditEvent` and `FieldRevision` rows for a minimum of 7 years (Requirement 17.1). Rows older than 7 years may be archived to cold storage but must remain queryable within 30 seconds of a retrieval request.

---

## Voucher PDF Generation

### Voucher PDF Generation — Jinja2 + WeasyPrint

**Technology choice: Jinja2 + WeasyPrint**
- **Jinja2** renders the HTML `VoucherTemplate` by substituting `{{placeholder}}` values with approved Draft fields, Tax_Assignment summaries, and configuration values.
- **WeasyPrint** converts the rendered HTML to a PDF with CSS-driven layout.
- The template is stored in the database (`VoucherTemplate` table) and loaded at generation time — no hard-coded layout in application code.

**Generation pipeline:**

```python
async def generate_voucher_pdf(request_id: UUID, actor_id: UUID, db: AsyncSession) -> VoucherPDF:
    # 1. Load the active VoucherTemplate
    template_record = await load_active_template(db)

    # 2. Assemble template context from approved Draft
    context = build_template_context(draft, tax_assignments, settings)
    # context includes: vendor_name, invoice_number, invoice_date,
    #   line_items_table (list of dicts with tax_type and tax_amount per row),
    #   tax_summary_table (per-type subtotals),
    #   subtotal, total_tax, grand_total, bank_*, authorization_disclaimer

    # 3. Render HTML via Jinja2
    jinja_env = Environment(loader=BaseLoader())
    template = jinja_env.from_string(template_record.template_content)
    rendered_html = template.render(context)

    # 4. Convert to PDF via WeasyPrint
    pdf_bytes = HTML(string=rendered_html).write_pdf()

    # 5. Upload PDF to S3, persist VoucherPDF record with template_version,
    #    disclaimer_hash (SHA-256 of disclaimer text),
    #    template_hash (SHA-256 of rendered_html)
```

**Grand total calculation:**
```python
subtotal = sum(Decimal(str(item.line_total)) for item in line_items)
total_tax = sum(Decimal(str(ta.tax_amount)) for ta in tax_assignments)
grand_total = subtotal + total_tax
```

**SLA**: Voucher PDF generation must complete within 5 seconds of the request (updated from 3s to account for Jinja2+WeasyPrint render time).

### Generation Sequence

```mermaid
sequenceDiagram
    participant CP as Client PIC
    participant VS as Voucher Service
    participant FS as File Store
    participant AUS as Audit Service

    CP->>VS: POST /requests/{id}/voucher-pdf
    VS->>VS: Check status = AMIGA_REVIEWED
    VS->>VS: Assemble voucher data (approved Header + LineItems + PaymentDetails)
    VS->>VS: Inject Authorization_Disclaimer
    VS->>VS: Render to PDF (headless renderer)
    VS->>FS: Store PDF file → file_key
    VS->>VS: Persist VoucherPDF record (file_key, generated_by, generated_at)
    VS->>AUS: Log VOUCHER_GENERATED event
    VS-->>CP: 200 OK (voucher_pdf_id, download_url)
```

Generation must complete within 5 seconds of the request (Requirement 15.1). If generation fails, no `VOUCHER_GENERATED` event is recorded and no `SENT_TO_CLIENT` transition occurs (Requirement 15.5).

### Authorization_Disclaimer

The current Authorization_Disclaimer text is stored in system configuration (not hard-coded). A SHA-256 hash of the disclaimer text used is stored in the `VoucherPDF` record (`disclaimer_hash`) so that auditors can verify which version of the disclaimer was included in any given voucher.

### Download and SENT_TO_CLIENT Transition

```mermaid
sequenceDiagram
    participant CP as Client PIC
    participant VS as Voucher Service
    participant RS as Request Service
    participant AUS as Audit Service

    CP->>VS: GET /requests/{id}/voucher-pdf
    VS->>VS: Verify actor holds active Client_Assignment for request's client
    VS->>VS: Verify request.status = AMIGA_REVIEWED
    VS->>FS: Retrieve file
    VS->>RS: Trigger SENT_TO_CLIENT transition (idempotent check: no prior SENT_TO_CLIENT event)
    RS->>AUS: Log VOUCHER_DOWNLOADED + STATUS_CHANGED (AMIGA_REVIEWED → SENT_TO_CLIENT)
    VS-->>CP: Binary PDF stream
```

The idempotency check ensures exactly one `SENT_TO_CLIENT` event is recorded even if the download endpoint is called more than once (Requirement 16.1).

---

## Performance and Telemetry Design

### Measurement Points

| Metric | Start Point | End Point | SLA |
|---|---|---|---|
| AI extraction (≤5 pages) | Job transitions to PROCESSING | Extraction output written to DB | 15 s |
| AI extraction (6–20 pages) | Job transitions to PROCESSING | Extraction output written to DB | 30 s |
| Voucher PDF generation | Generation request received | File available for download | 5 s |
| Queue dashboard page load | Navigation request sent | Page fully interactive | 2 s |
| Review workspace page load | Navigation request sent | Both panels interactive | 2 s |
| Audit Trail render | Request to view audit trail | Results displayed | 3 s |
| Voucher preview generation | Status transitions to AMIGA_REVIEWED | Preview renderable | 3 s |

### Breach Event Schema

```json
{
  "breach_id": "uuid",
  "metric_type": "EXTRACTION_DURATION | PDF_GENERATION_DURATION | PAGE_LOAD_DURATION",
  "subject_id": "job_id or page_identifier",
  "measured_duration_ms": 16203,
  "threshold_ms": 15000,
  "page_count": 4,
  "occurred_at": "2025-01-15T10:32:14.000Z"
}
```

### Telemetry Dashboard

The dashboard is served by the Telemetry Service and refreshed at intervals no greater than 60 seconds (Requirement 20.8). It displays per metric type:

- p50, p90, p99 durations
- Total measurement count
- Breach count
- Breach rate (breach_count / total_count × 100)

Aggregations are pre-computed on ingestion into the time-series store and stored as rolling windows to ensure the dashboard responds within 60 seconds without scanning the full telemetry table.

---

## Error Handling

All errors are raised as `fastapi.HTTPException` with the appropriate `status_code` and `detail` string, or returned via Pydantic validation error responses (`422 Unprocessable Entity` for schema violations).

### Upload Errors (Requirement 1)

| Condition | HTTP Status | Response |
|---|---|---|
| File is not PDF | `415 Unsupported Media Type` | `{ "error": "Unsupported file format: {received_format}. Only PDF files are accepted." }` |
| File exceeds 20 MB | `413 Content Too Large` | `{ "error": "File size exceeds the 20 MB limit." }` |
| Missing metadata field(s) | `422 Unprocessable Entity` | `{ "error": "Required fields missing: {field_names}" }` |
| Intake_Support lacks assignment | `403 Forbidden` | `{ "error": "You are not authorized to submit invoices for the selected client." }` |

### Authorization Errors (Requirements 12, 18, 19)

| Condition | HTTP Status | Response |
|---|---|---|
| Self-approval attempt | `403 Forbidden` | `{ "error": "Self-approval is not permitted." }` |
| Role lacks approval authority | `403 Forbidden` | `{ "error": "You do not have authority to approve Requests." }` |
| Approval while EXCEPTION status | `409 Conflict` | `{ "error": "Unresolved exceptions must be addressed before this Request can be approved." }` |
| No Client_Assignment for client | `403 Forbidden` | `{ "error": "You are not authorized to access Requests for this client." }` (no client data included) |

### Persistence Errors (Requirement 11.5)

If the Audit Service fails to persist a `FieldRevision` after a field modification, the Request Service rolls back the field update and returns:

```json
{ "error": "The change could not be saved. Please try again." }
```

The field value is left unchanged in the database.

### Request ID Generation Failure (Requirement 2.3)

If 5 UUID generation attempts all result in collisions (astronomically unlikely in practice but required by spec), the system returns:

```json
{ "error": "Request creation failed: ID generation failed. Please retry." }
```

---

## Testing Strategy

### Unit Testing

Unit tests cover specific examples, edge cases, and error conditions in isolation using **pytest** and **pytest-asyncio** for async tests:

- **Validation rules**: specific calculation discrepancy examples (exact tolerance boundary, just-over, just-under), MISSING_EVIDENCE flag with exactly 0, 1, or 2 missing fields
- **State machine transitions**: valid transitions succeed; invalid transitions (e.g. REJECTED → PROCESSING) return errors
- **Authorization checks**: self-approval rejected, System_Admin approval rejected, inactive assignment denied
- **Request ID generation**: collision retry logic (up to 5 retries, then failure)
- **Rejection reason validation**: 0 chars rejected, 1 char accepted, 500 chars accepted, 501 chars rejected
- **Tax calculation**: for each tax type, verify `Decimal(line_total) × rate == expected_tax_amount`; verify grand_total = subtotal + total_tax
- **Template rendering**: verify all required placeholders are substituted; verify missing values produce empty string, not error
- **Multi-file extraction**: mock Bedrock returning a result from 3 evidence files combined

### Property-Based Testing

The feature contains several components amenable to property-based testing. See the Correctness Properties section below. Property tests are implemented using **Hypothesis** (`hypothesis.strategies`) with the `@given` decorator. Each property test runs a minimum of 100 examples.

Each property test is tagged with a comment referencing the design property:

```
# Feature: invoice-to-voucher, Property {number}: {property_text}
```

### Integration Testing

Integration tests with 1–3 representative examples cover, using **pytest** with a local PostgreSQL instance (started natively). No Redis is needed. AWS S3 calls are mocked with the `moto` library — no real S3 bucket is required for the test suite:

- End-to-end upload → extraction → review → approve → PDF download flow
- Telemetry breach events are written when SLA thresholds are exceeded
- Audit Trail contains correct events after full workflow execution
- Client isolation: user with assignment for Client A cannot access Client B's Requests

### Performance Testing

Load tests using **locust** or **pytest-benchmark** verify that:
- Queue dashboard renders within 2 seconds with 500 Requests in the result set
- Review workspace loads within 2 seconds
- Voucher PDF generation completes within 3 seconds

---

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

PBT is applicable to this feature because the core processing logic involves pure validation functions (calculation checking, duplicate detection, flag application), access control predicates (role × assignment checks), and data transformation pipelines (field extraction, audit event generation) — all of which have universal behaviors that must hold across a wide input space. **Hypothesis** is used for property-based testing in Python. Each property test runs a minimum of 100 examples.

Each property test is tagged with its feature name and property number.

---

### Property 1: Valid Uploads Create Queued Requests with Uploader Identity in Audit Trail

*For any* valid upload submitted by a Client_PIC or Intake_Support with a valid Client_Assignment and all required metadata fields present, the AMIGA_System SHALL create a Request with status QUEUED and the Audit_Trail SHALL contain a REQUEST_CREATED event with the uploader's account identifier and a non-null UTC timestamp.

**Validates: Requirements 1.1, 1.4, 1.5**

---

### Property 2: Non-PDF and Oversized Files Are Rejected; Error Names the Received Format

*For any* file submission where the file is not in PDF format, the AMIGA_System SHALL reject the upload and the error response SHALL name the received file format and state that only PDF files are accepted. *For any* PDF submission exceeding 20 MB, the AMIGA_System SHALL reject the upload with a file size error.

**Validates: Requirements 1.2, 1.3**

---

### Property 3: Missing Metadata Fields Are Identified by Name in the Rejection Error

*For any* non-empty subset of required metadata fields (client name, request type, channel, request date) that are omitted from an upload submission, the AMIGA_System SHALL reject the submission and the error response SHALL identify each missing field by name.

**Validates: Requirements 1.7, 19.7**

---

### Property 4: All Request IDs Are Unique Across the System

*For any* batch of N concurrently or sequentially created Requests, all N Request_IDs SHALL be distinct — no two Requests in the system share the same Request_ID at any point in time.

**Validates: Requirements 2.1**

---

### Property 5: Queue Dashboard Returns Only Authorized Requests Filtered by Role

*For any* user with a specific role and a specific set of active Client_Assignments, the queue dashboard SHALL return only Requests belonging to that user's assigned clients (no Requests from other clients) AND only Requests in the statuses permitted for that role:
- Finance_Reviewer: {READY_FOR_REVIEW, EXCEPTION, AMIGA_REVIEWED}
- Client_PIC: {AMIGA_REVIEWED, SENT_TO_CLIENT}
- Service_Lead: all statuses

Each returned Request SHALL include at minimum the Request_ID, client name, vendor name, current status, and submission timestamp.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.6**

---

### Property 6: MISSING_EVIDENCE Flag and EXCEPTION Status Applied for Any Missing Required Field

*For any* extraction result in which one or more required fields are absent — whether Header_Fields, Payment_Details fields (bank account number, bank name, payment reference), or Line_Item fields (description, quantity, unit, unit price, line total) — the AMIGA_System SHALL apply the MISSING_EVIDENCE flag AND set the Request status to EXCEPTION. The exception UX SHALL list each missing required Payment_Details field by name.

**Validates: Requirements 4.6, 5.3, 6.1, 6.2, 6.3**

---

### Property 7: Calculation Discrepancies Beyond Tolerance Are Flagged with Computed and Extracted Values

*For any* extracted invoice where:
- |sum(line_item.line_total) − header.subtotal| > Calculation_Tolerance, OR
- |header.subtotal + header.tax + header.fees + header.rounding_adj − header.total_amount| > Calculation_Tolerance,

the AMIGA_System SHALL flag the discrepant field, set the Request status to EXCEPTION, and the exception UX SHALL display the field name, the computed value, and the extracted value for each discrepant field. Conversely, for any extraction where all differences are within tolerance, no calculation flag SHALL be applied.

**Validates: Requirements 7.1, 7.2, 7.3**

---

### Property 8: Duplicate Detection Is Case-Insensitive and Excludes Rejected/Voided Requests

*For any* new Request, the AMIGA_System SHALL apply the POSSIBLE_DUPLICATE flag if and only if there exists at least one existing Request whose vendor name matches (case-insensitively) AND invoice number matches (case-insensitively), AND whose status is not REJECTED or VOIDED. The exception UX SHALL display the Request_ID, status, invoice date, and invoice amount of each matching Request.

**Validates: Requirements 8.1, 8.2, 8.3**

---

### Property 9: Exception UX Displays All Active Flags with Per-Flag Instructions

*For any* Request in EXCEPTION status carrying any non-empty combination of flags (MISSING_EVIDENCE, POSSIBLE_DUPLICATE, calculation discrepancy), the exception UX SHALL display all active flags and SHALL include the specific next-action instruction for each flag type present.

**Validates: Requirements 9.1, 9.3**

---

### Property 10: AI-Generated Draft Label Is Always Visible After Extraction

*For any* Request that has completed extraction (whether full or partial), the Draft SHALL carry a persistent, visible label reading "AI-generated draft — not approved" and the Request status SHALL be either READY_FOR_REVIEW or EXCEPTION — never AMIGA_REVIEWED without an explicit Finance_Reviewer approval action.

**Validates: Requirements 4.2, 4.5**

---

### Property 11: Field Revisions Are Complete, Ordered, and Accessible Only to Authorized Roles

*For any* field modification in the review workspace where the new value differs from the current value, the AMIGA_System SHALL record a FieldRevision containing the field identifier, old value, new value, the actor's account identifier, and the UTC timestamp. The complete Revision history for a Request SHALL be returned in non-decreasing timestamp order and SHALL be accessible only to users with the Finance_Reviewer, Service_Lead, or System_Admin role.

**Validates: Requirements 11.1, 11.2, 11.4, 17.2, 17.3**

---

### Property 12: Finance Reviewer Approval Enforces Segregation of Duties and Role Restriction

*For any* approval action:
- If the acting user's role is not Finance_Reviewer → SHALL be rejected
- If the acting user's user_id equals the Request's uploader_id → SHALL be rejected with a self-approval error
- If the Request's status is EXCEPTION → SHALL be rejected

*For any* approval action where all three conditions are satisfied (Finance_Reviewer role, not the uploader, Request not in EXCEPTION), the Request SHALL transition to AMIGA_REVIEWED and the Audit_Trail SHALL contain an APPROVED event with the approver's account identifier and a UTC timestamp.

**Validates: Requirements 12.1, 12.2, 12.3, 12.5, 4.5**

---

### Property 13: Rejection Enforces Segregation of Duties and Reason Length Constraint

*For any* rejection action:
- If the rejection reason length is 0 characters or exceeds 500 characters → SHALL be rejected
- If the acting user's user_id equals the Request's uploader_id → SHALL be rejected with a self-rejection error

*For any* valid rejection (Finance_Reviewer role, not the uploader, reason length ∈ [1, 500]), the Request SHALL transition to REJECTED and the Audit_Trail SHALL contain a REJECTED event with the rejection reason, the Finance_Reviewer's account identifier, and a UTC timestamp. The rejection reason SHALL be visible to any Client_PIC holding a valid Client_Assignment for the Request's client.

**Validates: Requirements 13.1, 13.2, 13.3, 13.4**

---

### Property 14: Authorization_Disclaimer Appears in All Voucher Outputs

*For any* Request in AMIGA_REVIEWED status, both the in-system voucher preview and the generated Voucher PDF SHALL contain the Authorization_Disclaimer text. The generated Voucher PDF SHALL also include all approved Header_Fields, Line_Items, and Payment_Details.

**Validates: Requirements 14.2, 15.2, 15.4**

---

### Property 15: PDF Generation and Download Are Gated on AMIGA_REVIEWED Status

*For any* Request whose status is not AMIGA_REVIEWED, both Voucher PDF generation requests and Voucher PDF download requests SHALL be denied with an error. *For any* successful download by a Client_PIC with an active Client_Assignment, regardless of the number of download attempts, exactly one SENT_TO_CLIENT event SHALL appear in the Audit_Trail and the Request status SHALL be SENT_TO_CLIENT. *For any* Client_PIC without an active Client_Assignment for the Request's client, download SHALL be denied with no client data in the response.

**Validates: Requirements 15.3, 16.1, 16.2, 16.3, 16.4**

---

### Property 16: Client Isolation: Access Requires Both Valid Role and Active Client_Assignment

*For any* data operation (read, list, create, update, delete, export) on a client's Request:
- If the acting user has a valid role but no active Client_Assignment for that client → SHALL be denied, response SHALL contain no client data from the inaccessible client
- If the acting user has an active Client_Assignment but no valid role → SHALL be denied
- If the acting user's Client_Assignment is revoked, their very next request for that client's data SHALL be denied without requiring re-authentication

**Validates: Requirements 18.1, 18.2, 18.3, 18.4**

---

### Property 17: Telemetry Breach Events Are Recorded When SLA Thresholds Are Exceeded; Aggregated Metrics Are Consistent

*For any* measured operation whose duration exceeds its defined threshold (15 s for ≤5-page extraction, 30 s for 6–20-page extraction, 5 s for PDF generation, 3 s for page load), the AMIGA_System SHALL record a breach event containing the subject identifier, duration in milliseconds, applicable page count (for extraction), and UTC timestamp. For any set of telemetry records of the same metric type, the aggregated dashboard SHALL satisfy: p50 ≤ p90 ≤ p99, and breach_rate = (breach_count ÷ total_count) × 100.

**Validates: Requirements 20.1, 20.3, 20.4, 20.5, 20.6, 20.7, 20.8**
