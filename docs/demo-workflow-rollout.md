# Demo workflow rollout — 16 October 2026

Migration `0014` has been applied to the configured PostgreSQL database. The active bundled template is `v5.0-corporate`; no User Admin accounts remain. Existing usernames/password hashes are preserved by migration, while affected session versions are incremented. Custom active templates and stored historical PDFs are preserved.

## Behavior

- `workflow_stage=REFINEMENT` belongs to Client PIC; `FINANCE_REVIEW` belongs to Finance. Either stage can have `status=EXCEPTION`. Technical failures without usable drafts have no review stage and remain retryable.
- Required header/payment/item fields, blank strings, arithmetic and same-client duplicate identities are validated after extraction and edits, tax updates, submission and approval. Numeric zero is valid. Both extraction workers use the same persistence/finalization rules. Single invoices use the edited identity; combined invoices use individual source snapshots. Combined arithmetic checks retain discrepancies from individual source invoices.
- Client PIC submits before Finance review. Finance may correct, approve or reject submitted exceptions; unresolved issues are included in approval history. Finance self-approval remains denied; Super Admin self-approval remains allowed.
- System Admin manages operational accounts, roles, client access, clients and templates. Finance correction/approval is denied. Service Lead allocates reviewers/deadlines within assigned clients, without granting client access. Deprecated `USER_ADMIN` remains in the database enum solely for historical compatibility and is rejected in role changes/account creation.
- Duplicate warnings identify the existing request and matched source invoice. Detection excludes rejected/current requests and other clients. Missing legacy source identity coverage is explicitly disclosed.
- Corporate vouchers use dynamic Indonesian sections, actual recorded details, separate extracted invoice versus calculated voucher amounts, conditional optional fields, external signature spaces and A4 pagination. A renderer-owned authorization notice is stamped on every page independently of custom template conditions. Configured notice language/text is preserved; the bundled default notice is Indonesian.

## Indexing and operation

Approval, voucher metadata and one coalescing `indexing_jobs` row commit together. Approval does not invoke embedding. Validation, tax refresh and rendering share one request-local invoice snapshot in the approval transaction. A background worker claims jobs briefly, releases its database session during model calls, then publishes atomically if the request/job revision still matches. It retries with backoff, recovers five-minute expired RUNNING leases, and records FAILED after five unsuccessful attempts. Model failures cannot undo approval. Authorized live retrieval remains available while indexing is pending.

The API starts extraction/indexing workers and warms the PDF renderer on startup. Restart a backend process that does not reload code automatically. An independent indexing worker can also run from `backend`:

```sh
.venv/bin/python -m scripts.run_indexing_worker
```

Multiple workers use PostgreSQL `SKIP LOCKED`. Redis is unnecessary. Operators can inspect `SELECT status, count(*) FROM indexing_jobs GROUP BY status`. After fixing a persistent provider outage, an operator can requeue failed jobs by setting their status to PENDING, attempts to zero and available_at to now(); live permissions are never read from indexed metadata.

Migration backfills work stages, carries existing validation issues into exception flags, and queues approved/delivered requests with missing or partial embeddings. It does not regenerate vouchers. The existing standalone embedding backfill command remains available.

## Verification

Backend: **363 passed, 1 skipped**. Frontend: **58 passed**. Production frontend build passes. Coverage includes flagged stage ownership and clearing, submission/correction/approval/rejection, missing-draft protection, role restrictions, single/combined/overlapping duplicates, normalization/client/rejection boundaries, equivalent extraction finalization, indexing outage/backoff/lease recovery/coalescing, no session during embedding, PDF notice enforcement, UI navigation without duplicate mounting, and rendered PDF pagination controls.

The live PostgreSQL check recovered an expired indexing lease and published four pgvector chunks. Approval left the index job PENDING without waiting for embedding. Temporary benchmark request data and audit events were rolled back; only benchmark-created QA objects were removed from S3.

Visual checks covered sparse data, long descriptions, multiple source invoices, multiple client approvers, a sample signature and an 11-page voucher. Every page retained its notice. Desktop/mobile review and Amiga AI were checked at 1440px and 390px, with no JavaScript errors or horizontal overflow after responsive layout settled. Evidence now uses a lazy PDF canvas viewer and readiness is measured after the first rendered page.

## Live measurements and remaining breach

These are limited local checks against the configured Bedrock/PostgreSQL/S3 services, not a production percentile study. Historical telemetry is retained; thresholds were not relaxed.

| Check | Observed | Target | Result |
| --- | ---: | ---: | --- |
| Freight sample, 1-page PDF | 3.021 s | ≤15 s/PDF | Pass |
| Fuel surcharge sample, 1-page PDF | 3.128 s | ≤15 s/PDF | Pass |
| Synthetic invoice, 2 pages / 20 lines | 9.523 s | ≤15 s/PDF | Pass |
| Queue API | 0.953 s | ≤2 s | Pass |
| Review data + evidence APIs | 1.762 s | ≤2 s | Pass |
| Latest production-browser queue navigation | 0.980 s | ≤2 s | Pass |
| Latest review component through visible evidence | 1.724 s | ≤2 s | Pass |
| Full automated queue-to-review action | **2.164 s** | **≤2 s** | **Remaining breach** |
| Approval → stored voucher | 2.628 s | ≤3 s | Pass |
| Approval → received PDF bytes | **3.512 s** | **≤3 s** | **Remaining delivery breach** |

Before the final read/navigation optimizations, browser samples reached 5.53 s for queue navigation and 4.70 s for review navigation. Queue queries now omit large submitter snapshots, detail reads are batched, tax choices/reviewer options travel with detail, and route transitions mount each page once. The remaining end-to-end review sample exceeds the strict target by 164 ms. A repeat approval reached 4.389 s before invoice reads were consolidated across validation, tax refresh and rendering; after consolidation the stored voucher was available at 2.628 s. Receiving its PDF bytes took 3.512 s, exceeding the stricter delivery target by 512 ms. Additional latency sampling is required before claiming sustained compliance.

Per-document extraction telemetry is recorded separately from request extraction duration. Review timing waits for visible evidence; voucher availability timing starts at the approval action. New metric types are `EXTRACTION_DOCUMENT_DURATION` and `VOUCHER_AVAILABILITY_DURATION`.
