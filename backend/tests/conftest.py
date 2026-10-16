"""
Shared test configuration for Phase 5 (Validation Engine) unit tests.

The production models use PostgreSQL-specific column types (ARRAY, JSONB).
When running against in-memory SQLite, those types must be swapped for
portable equivalents before the schema is created.

This conftest patches the relevant columns on the ORM models before any test
module imports them, then restores the originals after the session.
"""
import pytest
import sqlalchemy
from sqlalchemy import JSON, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

# ---------------------------------------------------------------------------
# Monkey-patch PG-only column types to SQLite-compatible equivalents
# ---------------------------------------------------------------------------
# We do this at import time so that every test module in this directory sees
# the patched models when they call create_all().
import app.models.request as _req_module
import app.models.audit as _audit_module

_original_flags_type = _req_module.InvoiceRequest.__table__.c["flags"].type
_original_payload_type = _audit_module.AuditEvent.__table__.c["payload"].type

_req_module.InvoiceRequest.__table__.c["flags"].type = JSON()
_audit_module.AuditEvent.__table__.c["payload"].type = JSON()

# SQLite does not support server_default of "{}" for JSON — remove it
_req_module.InvoiceRequest.__table__.c["flags"].server_default = None
_audit_module.AuditEvent.__table__.c["payload"].server_default = None
