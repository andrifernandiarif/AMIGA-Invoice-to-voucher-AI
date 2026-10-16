import uuid
from datetime import UTC, datetime

from sqlalchemy import exists, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.client_assignment import ClientAssignment
from app.models.user import User, UserRole


class DuplicateUsernameError(Exception):
    """Raised when a username already exists."""


class UserRepository:
    async def find_by_username(self, db: AsyncSession, username: str) -> User | None:
        """Return the User with this username, or None."""
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def find_by_id(self, db: AsyncSession, user_id: uuid.UUID) -> User | None:
        """Return the User with this ID, or None."""
        result = await db.execute(select(User).where(User.user_id == user_id))
        return result.scalar_one_or_none()

    async def create_user(
        self,
        db: AsyncSession,
        username: str,
        role: UserRole,
    ) -> User:
        """
        Insert a new User. Raises DuplicateUsernameError if username already taken.
        """
        now = datetime.now(UTC)
        user = User(
            user_id=uuid.uuid4(),
            username=username,
            role=role,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        db.add(user)
        try:
            await db.flush()  # detect constraint violation early
        except IntegrityError as exc:
            await db.rollback()
            raise DuplicateUsernameError(f"Username '{username}' is already taken.") from exc
        return user

    async def update_role(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        role: UserRole,
    ) -> User:
        """Update the role of an existing user. Returns the updated User."""
        now = datetime.now(UTC)
        await db.execute(update(User).where(User.user_id == user_id).values(role=role, updated_at=now))
        await db.flush()
        user = await self.find_by_id(db, user_id)
        if user is None:
            raise ValueError(f"User {user_id} not found.")
        return user

    async def add_assignment(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        client_id: uuid.UUID,
    ) -> ClientAssignment:
        """
        Create or reactivate a ClientAssignment.
        If a row exists but is_active=False, reactivate it.
        If no row exists, insert a new one.
        """
        result = await db.execute(
            select(ClientAssignment).where(
                ClientAssignment.user_id == user_id,
                ClientAssignment.client_id == client_id,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.is_active = True
            existing.revoked_at = None
            await db.flush()
            return existing

        assignment = ClientAssignment(
            assignment_id=uuid.uuid4(),
            user_id=user_id,
            client_id=client_id,
            is_active=True,
            assigned_at=datetime.now(UTC),
            revoked_at=None,
        )
        db.add(assignment)
        await db.flush()
        return assignment

    async def revoke_assignment(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        client_id: uuid.UUID,
    ) -> None:
        """Mark a ClientAssignment as inactive."""
        await db.execute(
            update(ClientAssignment)
            .where(
                ClientAssignment.user_id == user_id,
                ClientAssignment.client_id == client_id,
            )
            .values(is_active=False, revoked_at=datetime.now(UTC))
        )
        await db.flush()

    async def is_assignment_active(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        client_id: uuid.UUID,
    ) -> bool:
        """
        Return True iff a ClientAssignment exists with is_active=True
        for this (user_id, client_id) pair. No caching — always hits the DB.
        """
        assigned = exists().where(
            ClientAssignment.user_id == user_id,
            ClientAssignment.client_id == client_id,
            ClientAssignment.is_active.is_(True),
        )
        return bool(
            await db.scalar(
                select(
                    exists().where(
                        User.user_id == user_id,
                        User.is_active.is_(True),
                        or_(User.role == UserRole.SUPER_ADMIN, assigned),
                    )
                )
            )
        )


user_repository = UserRepository()
