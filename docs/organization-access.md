# AMIGA accounts, roles and acceptance walkthrough

Open http://localhost:5173 and sign in using a username **and password**. Roles are assigned to accounts; there is no role selector at sign-in. Sign out to test another role. Temporary passwords lead to a required password-change screen. After changing the password, sign in again using the new password.

## Ready-to-use local test accounts

Eight accounts have been provisioned. Their generated temporary passwords are in `backend/.env.demo-credentials` (local-only, mode 600, excluded from Git). Open that file locally; each tab-separated line contains username, temporary password, role and initial company. No existing account passwords were changed. Once changed, the printed temporary password stops working.

| Username | Role | What to try |
| --- | --- | --- |
| `demo_super_admin` | Super Admin | All companies and actions; account management, roles, client assignments, templates, self-approval |
| `demo_system_admin` | System Admin | Company and template management; assigned-client review/corrections, history, metrics; approval denied |
| `demo_user_admin` | User Admin | Create operational accounts, grant operational roles, activate/deactivate and reset temporary passwords; administrative accounts protected |
| `demo_service_lead` | Service Lead | Company A workload; manage A assignments, reviewer allocation and deadlines; history and metrics |
| `demo_finance` | Finance Reviewer | Company A review, correction, approval/rejection, generation and download; own uploads cannot be approved |
| `demo_client_pic` | Client PIC | Company A upload, refinement, taxes, submission, history and voucher download |
| `demo_intake` | Intake Support | Company A upload and progress tracking |
| `demo_other_client` | Client PIC | Company B only; Company A requests and evidence denied |

Super Admin has global client access. Other operational roles need active company assignments. User Admin manages accounts rather than invoice data. A professional role (job title) and external voucher approvers are separate from system roles.

## Installation, migration and startup

From the project root:

```sh
cd backend
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
```

On a fresh installation, create the first Super Admin with an interactively prompted password:

```sh
python -m scripts.manage_accounts bootstrap --username super_admin
```

Bootstrap refuses to modify an existing username or create another bootstrap account when an active Super Admin already exists. For this workspace, use the provisioned `demo_super_admin` instead. Existing historical accounts have no invented passwords: a Super Admin or authorized User Admin must set their temporary password through **People & assignments**.

To provision the development accounts on another installation, run explicitly:

```sh
umask 077
python -m scripts.manage_accounts seed-demo > .env.demo-credentials
```

The seed is idempotent: existing usernames, passwords and assignments are unchanged. Capture credentials on the first run; rerunning writes no passwords for existing users. Use a separate filename for later runs to preserve the first credential file. Demo seeding never runs at server startup.

Start the backend:

```sh
uvicorn app.main:app --reload --port 8000
```

In a second terminal, from the project root:

```sh
cd frontend
npm install
npm run dev
```

Configure PostgreSQL, S3 and AWS credentials in `backend/.env`. `EXTRACTION_PROVIDER=mock` replaces extraction only; S3 and PostgreSQL are still required. `EXTRACTION_PROVIDER=bedrock` uses the configured live model. PDF rendering needs the documented WeasyPrint native dependencies.

## Organization administration

- **Super Admin:** open **People & assignments** to create any role, edit roles, reset passwords, activate/deactivate accounts and grant/revoke company access. The last active Super Admin cannot be demoted or deactivated.
- **User Admin:** the same account controls apply only to operational roles. It cannot grant Super Admin, System Admin or User Admin, modify administrator accounts, or grant company assignments. A Service Lead or Super Admin assigns companies after account creation.
- **Service Lead:** select an operational account in **People & assignments**, then add/revoke a company assignment within the lead's own companies. Open a request to select its responsible Finance Reviewer and optional due date.
- A reviewer must be active and assigned to the request's company. Reviewer assignment indicates responsibility; other authorized reviewers can still review it. Revocations take effect on the next API request.
- Password reset/change and deactivation revoke existing sessions. Password reset requires a change at the next login. Account administration and assignment changes are audited without storing passwords in audit payloads.

## Acceptance walkthroughs

1. **Normal workflow:** use `demo_client_pic`, upload a valid PDF with purpose and external approver details, wait for extraction, review fields/taxes, and submit. Sign out, sign in as `demo_finance`, accept and generate the voucher. As Client PIC, preview/download it; download marks it `SENT_TO_CLIENT`.
2. **Warnings do not block:** before submitting, clear bank-account details, change header total to disagree with the item subtotal, or upload another invoice with the same vendor and invoice number in Company A. The request shows advisory warnings. Submit and approve normally as Finance Reviewer. No override permission or justification is required; approval preserves the unresolved issue snapshot in history. Missing/unrenderable item totals or technical rendering/storage failures still need correction/retry.
3. **Delegation:** as Super Admin, create an operational user with a temporary password. As User Admin, change that user's operational role; attempts to grant an administrative role fail. As Service Lead, assign the user to Company A, allocate `demo_finance` to a request and set a past due date. Verify dashboard reviewer/date/overdue filters.
4. **Client isolation:** copy a Company A request URL, sign out, then open it as `demo_other_client`. Request detail, history, evidence and voucher access are denied. Invoice chat retrieves only Company B context. Revoking a client assignment immediately removes access using the same session.
5. **Independent approval and Super Admin:** ordinary Finance Reviewers cannot approve their own uploads. Super Admin may upload, refine, submit and approve its own request, per the requested policy.
6. **History and PDF:** inspect the activity timeline for actor names, timestamps, issue changes, revisions, submission, approval, generation and download. New vouchers contain the client payment-authorization notice. Stored historical PDFs remain unchanged unless explicitly regenerated.
7. **Performance:** open **Performance** as Service Lead, System Admin or Super Admin. Targets are screen load ≤2 seconds, PDF generation ≤3 seconds, extraction ≤15 seconds for up to five pages. Metrics show actual measurements and breaches, refreshed every 60 seconds; these targets are not guarantees.

## Authentication details

Passwords use Argon2id. Passwords must be 12–128 characters. Sign-in uses a bounded per-process, per-peer sliding window: ten attempts per five minutes. With multiple application workers or instances, place a shared rate limiter at the ingress for fleet-wide limits. JWT permissions are checked against the current database account rather than stale token role claims. Old pre-migration sessions are revoked.

## Checks

```sh
cd backend
.venv/bin/python -m pytest -q
```

```sh
cd frontend
npm test
npm run build
```

Tests use isolated databases and mocked storage/model calls. Live smoke results and limitations are reported separately; they do not establish production capacity.

## Verification record — 15 October 2026

- Database upgraded to migration `0012`; running backend and frontend proxy both report `organization-v4`.
- All eight generated demo credentials passed live password login. Each account is still awaiting its first password change; verification did not consume or change its temporary password.
- Backend: **300 passed, 1 skipped**. The skipped PostgreSQL-vector test was also run separately against the configured database: **1 passed**, with synthetic writes rolled back.
- Frontend: **47 passed**; production TypeScript/Vite build passed. Remaining test output includes existing React `act` and deprecation warnings.
- A one-page synthetic three-item invoice took **3,395 ms** through the configured Bedrock provider and returned all three items.
- Native voucher PDF rendering took **318 ms**; rendering plus S3 upload took **550 ms**. Storage download matched the uploaded bytes, and the synthetic storage object was removed afterward. The authorization notice was checked in extracted PDF text and visually in the rendered page.
- These are single-sample service checks, not production capacity or end-to-end screen-load guarantees. Browser visual QA was unavailable because no browser surface was connected; UI behavior is covered by component tests.
