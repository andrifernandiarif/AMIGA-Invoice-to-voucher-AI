import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.request import InvoiceRequest, RequestStatus


class RequestRepository:

    async def create_request(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        client_id: uuid.UUID,
        uploader_id: uuid.UUID,
        request_type: str,
        channel: str,
        request_date: date,
        invoice_file_key: str,
        invoice_page_count: int | None = None,
    ) -> InvoiceRequest:
        """Insert a new InvoiceRequest in QUEUED status."""
        now = datetime.now(timezone.utc)
        req = InvoiceRequest(
            request_id=request_id,
            client_id=client_id,
            uploader_id=uploader_id,
            request_type=request_type,
            channel=channel,
            request_date=request_date,
            status=RequestStatus.QUEUED,
            flags=[],
            invoice_file_key=invoice_file_key,
            invoice_page_count=invoice_page_count,
            created_at=now,
            updated_at=now,
        )
        db.add(req)
        await db.flush()
        return req

    async def get_by_id(
        self, db: AsyncSession, request_id: uuid.UUID
    ) -> InvoiceRequest | None:
        """Return the request with this ID, or None."""
        result = await db.execute(
            select(InvoiceRequest).where(InvoiceRequest.request_id == request_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(
        self,
        db: AsyncSession,
        client_ids: list[uuid.UUID],
        statuses: list[RequestStatus] | None = None,
    ) -> list[InvoiceRequest]:
        """
        Return requests belonging to any of the given client_ids,
        optionally filtered to specific statuses.
        """
        stmt = select(InvoiceRequest).where(InvoiceRequest.client_id.in_(client_ids))
        if statuses:
            stmt = stmt.where(InvoiceRequest.status.in_(statuses))
        stmt = stmt.order_by(InvoiceRequest.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        new_status: RequestStatus,
    ) -> InvoiceRequest:
        """Transition the request to a new status. Returns the updated request."""
        await db.execute(
            update(InvoiceRequest)
            .where(InvoiceRequest.request_id == request_id)
            .values(status=new_status, updated_at=datetime.now(timezone.utc))
        )
        await db.flush()
        req = await self.get_by_id(db, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found.")
        return req

    async def apply_flag(
        self, db: AsyncSession, request_id: uuid.UUID, flag: str
    ) -> None:
        """Append a flag string to the flags array if not already present."""
        req = await self.get_by_id(db, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found.")
        current = list(req.flags or [])
        if flag not in current:
            current.append(flag)
            await db.execute(
                update(InvoiceRequest)
                .where(InvoiceRequest.request_id == request_id)
                .values(flags=current, updated_at=datetime.now(timezone.utc))
            )
            await db.flush()

    async def remove_flag(
        self, db: AsyncSession, request_id: uuid.UUID, flag: str
    ) -> None:
        """Remove a flag string from the flags array if present."""
        req = await self.get_by_id(db, request_id)
        if req is None:
            raise ValueError(f"Request {request_id} not found.")
        current = [f for f in (req.flags or []) if f != flag]
        await db.execute(
            update(InvoiceRequest)
            .where(InvoiceRequest.request_id == request_id)
            .values(flags=current, updated_at=datetime.now(timezone.utc))
        )
        await db.flush()

    async def set_assigned_reviewer(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        reviewer_id: uuid.UUID | None,
    ) -> None:
        """Set or clear the assigned reviewer for a request."""
        await db.execute(
            update(InvoiceRequest)
            .where(InvoiceRequest.request_id == request_id)
            .values(
                assigned_reviewer=reviewer_id,
                updated_at=datetime.now(timezone.utc),
            )
        )
        await db.flush()


request_repository = RequestRepository()
