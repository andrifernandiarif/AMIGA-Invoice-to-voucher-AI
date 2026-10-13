"""
Phase 2 Checkpoint Tests
========================
Verifies that:
  1. JWT creation and decoding work correctly (valid, expired, tampered).
  2. `require_role` dependency gates protected routes — correct role passes,
     wrong role returns 403, missing token returns 401.
  3. `get_current_user` returns 401 for missing/invalid/expired tokens.
  4. User management routes (POST /users) enforce System Admin role.
  5. Pydantic validation catches missing required fields with 422.

These tests use mocked DB sessions so they run with zero external dependencies.
"""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import jwt
import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient

from app.auth import create_access_token, decode_token
from app.config import settings
from app.models.user import User, UserRole

# ─── Helpers ────────────────────────────────────────────────────────────────


def _make_user(role: UserRole = UserRole.SYSTEM_ADMIN) -> User:
    """Return a properly-constructed (non-persisted) User ORM object."""
    now = datetime.now(timezone.utc)
    return User(
        user_id=uuid.uuid4(),
        username=f"test_{role.value.lower()}_{uuid.uuid4().hex[:6]}",
        role=role,
        is_active=True,
        created_at=now,
        updated_at=now,
    )


def _token(user: User) -> str:
    return create_access_token(user.user_id, user.role.value)


def _make_mock_db(return_user: "User | None" = None):
    """Return an async generator that yields a mock AsyncSession."""
    async def mock_get_db():
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()
        mock_session.rollback = AsyncMock()
        mock_session.flush = AsyncMock()
        mock_session.add = MagicMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = return_user
        mock_session.execute = AsyncMock(return_value=mock_result)
        yield mock_session
    return mock_get_db


# ─── 1. JWT unit tests ───────────────────────────────────────────────────────


class TestJWT:
    def test_valid_token_roundtrip(self):
        """create_access_token → decode_token returns correct claims."""
        user = _make_user(UserRole.FINANCE_REVIEWER)
        token = create_access_token(user.user_id, user.role.value)
        payload = decode_token(token)
        assert payload["sub"] == str(user.user_id)
        assert payload["role"] == "FINANCE_REVIEWER"

    def test_expired_token_raises_401(self):
        """An already-expired token raises HTTPException(401)."""
        from fastapi import HTTPException

        past = datetime.now(timezone.utc) - timedelta(seconds=1)
        payload = {"sub": str(uuid.uuid4()), "role": "SYSTEM_ADMIN", "exp": past}
        expired_token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            decode_token(expired_token)
        assert exc_info.value.status_code == 401
        assert "expired" in exc_info.value.detail.lower()

    def test_tampered_signature_raises_401(self):
        """A token signed with a different secret raises HTTPException(401)."""
        from fastapi import HTTPException

        user = _make_user()
        payload = {
            "sub": str(user.user_id),
            "role": user.role.value,
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        bad_token = jwt.encode(payload, "wrong-secret", algorithm="HS256")

        with pytest.raises(HTTPException) as exc_info:
            decode_token(bad_token)
        assert exc_info.value.status_code == 401

    def test_missing_role_claim_raises_401(self):
        """A token without 'role' claim raises HTTPException(401)."""
        from fastapi import HTTPException

        payload = {
            "sub": str(uuid.uuid4()),
            # 'role' intentionally omitted
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        incomplete_token = jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

        with pytest.raises(HTTPException) as exc_info:
            decode_token(incomplete_token)
        assert exc_info.value.status_code == 401

    def test_garbage_string_raises_401(self):
        """A garbage string raises HTTPException(401)."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            decode_token("this.is.garbage")
        assert exc_info.value.status_code == 401

    def test_token_carries_both_claims(self):
        """Token payload contains both 'sub' and 'role'."""
        user_id = uuid.uuid4()
        token = create_access_token(user_id, "SERVICE_LEAD")
        payload = decode_token(token)
        assert "sub" in payload
        assert "role" in payload
        assert "exp" in payload


# ─── 2. require_role dependency tests ────────────────────────────────────────


def _make_test_app_with_role_guard(*allowed_roles: str) -> FastAPI:
    """
    Build a minimal FastAPI app with one GET /protected endpoint protected
    by require_role(*allowed_roles). get_current_user is NOT overridden here —
    callers must set dependency_overrides on the returned app.
    """
    from app.dependencies import require_role

    app = FastAPI()

    @app.get("/protected")
    async def protected(user: User = Depends(require_role(*allowed_roles))):
        return {"user_id": str(user.user_id), "role": user.role.value}

    return app


class TestRequireRole:
    def test_correct_role_passes(self):
        """A user with an allowed role gets through with 200."""
        user = _make_user(UserRole.SYSTEM_ADMIN)
        app = _make_test_app_with_role_guard("SYSTEM_ADMIN")

        from app.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda: user

        with TestClient(app) as client:
            resp = client.get("/protected", headers={"Authorization": "Bearer dummy"})
        assert resp.status_code == 200
        assert resp.json()["role"] == "SYSTEM_ADMIN"

    def test_wrong_role_returns_403(self):
        """A user without the required role gets 403."""
        user = _make_user(UserRole.FINANCE_REVIEWER)
        app = _make_test_app_with_role_guard("SYSTEM_ADMIN")

        from app.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda: user

        with TestClient(app) as client:
            resp = client.get("/protected", headers={"Authorization": "Bearer dummy"})
        assert resp.status_code == 403
        assert "authority" in resp.json()["detail"].lower()

    def test_multiple_allowed_roles_both_pass(self):
        """Either allowed role passes."""
        for role in (UserRole.FINANCE_REVIEWER, UserRole.SERVICE_LEAD):
            user = _make_user(role)
            app = _make_test_app_with_role_guard("FINANCE_REVIEWER", "SERVICE_LEAD")

            from app.dependencies import get_current_user
            app.dependency_overrides[get_current_user] = lambda u=user: u

            with TestClient(app) as client:
                resp = client.get("/protected", headers={"Authorization": "Bearer dummy"})
            assert resp.status_code == 200, f"Expected 200 for role {role.value}"

    def test_missing_auth_header_returns_401(self):
        """No Authorization header returns 401 from the OAuth2 bearer scheme."""
        app = _make_test_app_with_role_guard("SYSTEM_ADMIN")
        # No dependency_overrides — let OAuth2PasswordBearer enforce the header
        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/protected")
        assert resp.status_code == 401

    def test_403_detail_exact_message(self):
        """The 403 detail must match the exact spec wording."""
        user = _make_user(UserRole.INTAKE_SUPPORT)
        app = _make_test_app_with_role_guard("SYSTEM_ADMIN")

        from app.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda: user

        with TestClient(app) as client:
            resp = client.get("/protected", headers={"Authorization": "Bearer dummy"})

        assert resp.status_code == 403
        assert resp.json()["detail"] == "You do not have authority to perform this action."


# ─── 3. get_current_user dependency tests ────────────────────────────────────


class TestGetCurrentUser:
    """
    Test get_current_user by building a minimal app where get_db is overridden
    to return a mock session, so we can control what the DB returns.
    """

    def _build_app(self, db_user: "User | None") -> FastAPI:
        from app.dependencies import get_current_user, get_db

        mini_app = FastAPI()
        mini_app.dependency_overrides[get_db] = _make_mock_db(db_user)

        @mini_app.get("/me")
        async def me(user: User = Depends(get_current_user)):
            return {"user_id": str(user.user_id)}

        return mini_app

    def test_valid_token_returns_user(self):
        """Valid JWT + existing active user → 200."""
        user = _make_user(UserRole.SERVICE_LEAD)
        token = _token(user)
        app = self._build_app(user)

        with TestClient(app) as client:
            resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == str(user.user_id)

    def test_invalid_token_returns_401(self):
        """Invalid token → 401 (decode_token raises before DB is queried)."""
        app = self._build_app(None)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/me", headers={"Authorization": "Bearer not.a.real.token"})
        assert resp.status_code == 401

    def test_user_not_found_in_db_returns_401(self):
        """Valid JWT but user not in DB → 401."""
        user = _make_user()
        token = _token(user)
        app = self._build_app(None)  # DB returns None for any query

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_inactive_user_returns_401(self):
        """Valid JWT but user.is_active = False → 401."""
        user = _make_user()
        user.is_active = False
        token = _token(user)
        app = self._build_app(user)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401

    def test_missing_bearer_token_returns_401(self):
        """No Authorization header at all → 401."""
        app = self._build_app(None)

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.get("/me")
        assert resp.status_code == 401


# ─── 4. POST /users route — role enforcement and input validation ─────────────


class TestCreateUserRoute:
    """Test the real POST /users route with DB and current_user overridden."""

    def _build_users_app(self, acting_user: User) -> FastAPI:
        from app.dependencies import get_db, get_current_user
        from app.routes.users import router

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = _make_mock_db(None)
        app.dependency_overrides[get_current_user] = lambda: acting_user

        return app

    def test_non_admin_cannot_create_user_gets_403(self):
        """Finance Reviewer POSTing to /users gets 403."""
        app = self._build_users_app(_make_user(UserRole.FINANCE_REVIEWER))

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/users",
                json={
                    "username": "newuser",
                    "role": "INTAKE_SUPPORT",
                    "assignments": [{"client_id": str(uuid.uuid4())}],
                },
                headers={"Authorization": "Bearer dummy"},
            )
        assert resp.status_code == 403

    def test_intake_support_cannot_create_user_gets_403(self):
        """Intake Support POSTing to /users gets 403."""
        app = self._build_users_app(_make_user(UserRole.INTAKE_SUPPORT))

        with TestClient(app, raise_server_exceptions=False) as client:
            resp = client.post(
                "/users",
                json={
                    "username": "hacker",
                    "role": "SYSTEM_ADMIN",
                    "assignments": [{"client_id": str(uuid.uuid4())}],
                },
                headers={"Authorization": "Bearer dummy"},
            )
        assert resp.status_code == 403

    def test_missing_username_returns_422(self):
        """Missing 'username' field returns 422 with field named in error."""
        app = self._build_users_app(_make_user(UserRole.SYSTEM_ADMIN))

        with TestClient(app) as client:
            resp = client.post(
                "/users",
                json={
                    # username missing
                    "role": "INTAKE_SUPPORT",
                    "assignments": [{"client_id": str(uuid.uuid4())}],
                },
                headers={"Authorization": "Bearer dummy"},
            )
        assert resp.status_code == 422
        body = resp.json()
        field_names = [e["loc"][-1] for e in body.get("detail", [])]
        assert "username" in field_names

    def test_missing_role_returns_422(self):
        """Missing 'role' field returns 422."""
        app = self._build_users_app(_make_user(UserRole.SYSTEM_ADMIN))

        with TestClient(app) as client:
            resp = client.post(
                "/users",
                json={
                    "username": "someone",
                    # role missing
                    "assignments": [{"client_id": str(uuid.uuid4())}],
                },
                headers={"Authorization": "Bearer dummy"},
            )
        assert resp.status_code == 422

    def test_empty_assignments_returns_422(self):
        """Empty assignments list returns 422 (min_length=1)."""
        app = self._build_users_app(_make_user(UserRole.SYSTEM_ADMIN))

        with TestClient(app) as client:
            resp = client.post(
                "/users",
                json={
                    "username": "someone",
                    "role": "INTAKE_SUPPORT",
                    "assignments": [],
                },
                headers={"Authorization": "Bearer dummy"},
            )
        assert resp.status_code == 422

    def test_invalid_role_value_returns_422(self):
        """An unrecognized role enum value returns 422."""
        app = self._build_users_app(_make_user(UserRole.SYSTEM_ADMIN))

        with TestClient(app) as client:
            resp = client.post(
                "/users",
                json={
                    "username": "someone",
                    "role": "SUPER_HACKER",
                    "assignments": [{"client_id": str(uuid.uuid4())}],
                },
                headers={"Authorization": "Bearer dummy"},
            )
        assert resp.status_code == 422


# ─── 5. All five roles recognized by require_role ─────────────────────────────


class TestAllRolesRecognized:
    @pytest.mark.parametrize("role", list(UserRole))
    def test_each_role_passes_its_own_guard(self, role: UserRole):
        """Each role passes a require_role check for that exact role."""
        user = _make_user(role)
        app = _make_test_app_with_role_guard(role.value)

        from app.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda u=user: u

        with TestClient(app) as client:
            resp = client.get("/protected", headers={"Authorization": "Bearer dummy"})
        assert resp.status_code == 200

    @pytest.mark.parametrize("role", list(UserRole))
    def test_each_role_is_denied_for_wrong_guard(self, role: UserRole):
        """Each role is denied when a different role is required."""
        user = _make_user(role)
        other = UserRole.SYSTEM_ADMIN if role != UserRole.SYSTEM_ADMIN else UserRole.CLIENT_PIC
        app = _make_test_app_with_role_guard(other.value)

        from app.dependencies import get_current_user
        app.dependency_overrides[get_current_user] = lambda u=user: u

        with TestClient(app) as client:
            resp = client.get("/protected", headers={"Authorization": "Bearer dummy"})
        assert resp.status_code == 403
