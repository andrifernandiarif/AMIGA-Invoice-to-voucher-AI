# Backend Startup Investigation

## Summary

The backend cannot start because of **one immediate crash** and **two secondary import errors** all introduced by the merge of PR #1 (`faryalhillan/afernandiarif`, commit `84348c4`). The server never gets past the import phase.

The primary crash is an `IndentationError` in `backend/app/repositories/request_repository.py` at line 154. Two async methods (`update_field_value` and `create_field_revision`) were placed **after** the module-level singleton `request_repository = RequestRepository()`, so they are indented as if they are class methods but are actually at module scope — Python rejects this as invalid syntax.

Additionally, `backend/app/routes/requests.py` (also changed in the merge) imports from two model files that do not exist, which would cause `ModuleNotFoundError` even if the IndentationError were fixed first.

---

## Evidence

### Error 1 (Root cause): `IndentationError` in `request_repository.py`

**File:** `backend/app/repositories/request_repository.py`  
**Confirmed by:** `python -m py_compile app/repositories/request_repository.py` → `IndentationError: unexpected indent (request_repository.py, line 154)`  
**Server import trace:**
```
app.main → app.routes.admin → app.dependencies →
app.repositories.request_repository → IndentationError line 154
```

**What went wrong:** The merge appended two new methods to `RequestRepository` — `update_field_value` (line 154) and `create_field_revision` (line 246) — **after** the module-level singleton at line 152:

```python
    async def get_field_revisions(self, ...) -> list[FieldRevision]:
        ...

request_repository = RequestRepository()   # ← singleton at module scope, line 152


    async def update_field_value(          # ← still indented as if inside class, line 154
        self,                              #    but class body already ended → IndentationError
```

The two new methods should appear **inside** the `RequestRepository` class body, **before** the singleton line. The merge from the `afernandiarif` branch incorrectly inserted them after it, breaking the class structure.

**Also broken as a result:** `update_field_value` uses `Draft`, `HeaderFields`, `LineItem`, and `PaymentDetails` but none of them are imported in the file's import block (lines 1–8). These would cause `NameError` at runtime even after the indentation is fixed.

---

### Error 2 (Secondary): Missing model files imported by `routes/requests.py`

**File:** `backend/app/routes/requests.py` (lines 17, 19)  
```python
from app.models.header_fields import HeaderFields   # line 17
from app.models.payment_details import PaymentDetails  # line 19
```

**Reality:** `HeaderFields` and `PaymentDetails` both live in `backend/app/models/invoice_fields.py`, not in separate `header_fields.py` / `payment_details.py` files. Those files do not exist:

```
ls: backend/app/models/header_fields.py: No such file or directory
ls: backend/app/models/payment_details.py: No such file or directory
```

Correct import (already used for `LineItem` on the very next line):
```python
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
```

---

### Error 3 (Minor bug, non-blocking): MIME type typo in `routes/requests.py`

**File:** `backend/app/routes/requests.py` line 41  
```python
ACCEPTED_TYPES = {"application/pdf", "image/png", "image.jpeg", "image/jpg"}
#                                                   ^^^^ dot instead of slash
```
`"image.jpeg"` should be `"image/jpeg"`. This does not prevent startup but silently rejects valid JPEG uploads.

---

## All Broken Files

| File | Issue | Severity |
|---|---|---|
| `backend/app/repositories/request_repository.py` | `IndentationError` at line 154: `update_field_value` and `create_field_revision` methods placed after module-level singleton, outside class body. Also missing imports for `Draft`, `HeaderFields`, `LineItem`, `PaymentDetails`. | **CRASH – prevents startup** |
| `backend/app/routes/requests.py` | Imports `from app.models.header_fields import HeaderFields` and `from app.models.payment_details import PaymentDetails` — neither file exists. Both classes are in `app.models.invoice_fields`. | **CRASH – prevents startup once Error 1 fixed** |
| `backend/app/routes/requests.py` | MIME type typo: `"image.jpeg"` instead of `"image/jpeg"`. | Bug – non-blocking |

---

## Conclusions

The bad merge (PR #1, commit `84348c4`, merged 2026-10-14) introduced code from `faryalhillan/afernandiarif` that:

1. Appended two new repository methods in the wrong place (after the singleton, outside the class).
2. Used non-existent module paths for two model imports that were correctly available under `invoice_fields`.
3. Introduced a MIME type typo.

---

## Recommended Fixes

### Fix 1 — `request_repository.py`: move methods inside the class and add imports

1. Delete `request_repository = RequestRepository()` from its current position (line 152).
2. Move `update_field_value` and `create_field_revision` inside the `RequestRepository` class, indented consistently with the existing methods (4 spaces).
3. Add the missing imports at the top of the file:
   ```python
   from app.models.draft import Draft
   from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
   ```
4. Place `request_repository = RequestRepository()` back at module scope **after** the class definition closes.

### Fix 2 — `routes/requests.py`: fix model imports

Replace lines 17–19:
```python
# Remove these two lines:
from app.models.header_fields import HeaderFields
from app.models.payment_details import PaymentDetails
```
With:
```python
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
```
(`LineItem` is already imported on line 18 from `invoice_fields`; consolidate all three into one import.)

### Fix 3 — `routes/requests.py`: fix MIME type typo

```python
# Line 41 — change:
ACCEPTED_TYPES = {"application/pdf", "image/png", "image.jpeg", "image/jpg"}
# To:
ACCEPTED_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/jpg"}
```

### Verification after fixes

```bash
cd backend
source .venv/bin/activate
python -m py_compile app/repositories/request_repository.py
python -m py_compile app/routes/requests.py
python -c "import app.main; print('import OK')"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```
