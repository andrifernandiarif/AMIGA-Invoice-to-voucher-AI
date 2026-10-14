"""Restore legacy upload evidence and move extracted drafts to manual review.

Revision ID: 0004
Revises: 0003
"""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    # Earlier uploads put every file key in REQUEST_CREATED but did not insert
    # evidence_files rows. Restore the original filenames/order without S3 writes.
    op.execute("""
        INSERT INTO evidence_files
            (evidence_id, request_id, file_key, original_filename, content_type,
             file_size_bytes, upload_order, uploaded_at)
        SELECT DISTINCT ON (a.request_id, item->>'file_key')
            gen_random_uuid(), a.request_id, item->>'file_key',
            COALESCE(NULLIF(item->>'filename', ''), NULLIF(item->>'original_filename', ''),
                     regexp_replace(item->>'file_key', '^.*/', '')),
            COALESCE(NULLIF(item->>'content_type', ''), 'application/pdf'),
            CASE WHEN jsonb_typeof(item->'file_size_bytes') = 'number'
                 THEN (item->>'file_size_bytes')::bigint ELSE NULL END,
            ordinal::integer, a.occurred_at
        FROM audit_events a
        CROSS JOIN LATERAL jsonb_array_elements(
            CASE WHEN jsonb_typeof(a.payload->'evidence_files') = 'array'
                 THEN a.payload->'evidence_files' ELSE '[]'::jsonb END
        ) WITH ORDINALITY AS evidence(item, ordinal)
        WHERE a.event_type = 'REQUEST_CREATED'
          AND NULLIF(item->>'file_key', '') IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM evidence_files e
                          WHERE e.request_id = a.request_id AND e.file_key = item->>'file_key')
        ORDER BY a.request_id, item->>'file_key', a.occurred_at
    """)
    # Preserve the primary uploaded file for still older requests without upload
    # metadata. Existing evidence rows always take precedence.
    op.execute("""
        INSERT INTO evidence_files
            (evidence_id, request_id, file_key, original_filename, content_type,
             file_size_bytes, upload_order, uploaded_at)
        SELECT gen_random_uuid(), r.request_id, r.invoice_file_key,
               regexp_replace(r.invoice_file_key, '^.*/', ''),
               CASE WHEN lower(r.invoice_file_key) LIKE '%.png' THEN 'image/png'
                    WHEN lower(r.invoice_file_key) LIKE '%.jpg'
                      OR lower(r.invoice_file_key) LIKE '%.jpeg' THEN 'image/jpeg'
                    ELSE 'application/pdf' END,
               NULL, 1, r.created_at
        FROM requests r
        WHERE r.invoice_file_key <> ''
          AND NOT EXISTS (SELECT 1 FROM evidence_files e WHERE e.request_id = r.request_id)
    """)
    # A draft must exist; real extraction failures without one stay in EXCEPTION.
    # Keep old flags/events in history, but retire flags on active manual-review requests.
    op.execute("""
        WITH restored AS (
            UPDATE requests r SET status = 'READY_FOR_REVIEW', flags = '{}', updated_at = now()
            WHERE r.status = 'EXCEPTION'
              AND EXISTS (SELECT 1 FROM drafts d WHERE d.request_id = r.request_id
                          AND d.extraction_status IN ('COMPLETE', 'PARTIAL'))
            RETURNING r.request_id
        )
        INSERT INTO audit_events (event_id, request_id, event_type, actor_id, payload, occurred_at)
        SELECT gen_random_uuid(), request_id, 'STATUS_CHANGED', NULL,
               '{"from_status":"EXCEPTION","to_status":"READY_FOR_REVIEW",
                 "reason":"Manual review workflow enabled; automated flags retired"}'::jsonb, now()
        FROM restored
    """)
    op.execute("UPDATE requests SET flags = '{}' WHERE status = 'READY_FOR_REVIEW'")


def downgrade():
    # Preserve restored evidence and append-only history when rolling back code.
    pass
