import re
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import FieldRevision
from app.models.draft import Draft
from app.models.invoice_fields import HeaderFields, LineItem, PaymentDetails
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

    async def get_field_revisions(
        self, db: AsyncSession, request_id: uuid.UUID
    ) -> list[FieldRevision]:
        """Return all field revisions for a request, ordered by revised_at ascending."""
        stmt = (
            select(FieldRevision)
            .where(FieldRevision.request_id == request_id)
            .order_by(FieldRevision.revised_at.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def update_field_value(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        field_path: str,
        new_value: str,
    ) -> bool:
        """
        Atomically update a field value in HeaderFields, LineItem, or PaymentDetails.
        Returns True if the value changed, False if unchanged (same value).
        Raises ValueError if field_path is invalid.
        """
        # Parse field_path to determine which table and field to update
        # Format: "header.subtotal", "line_items[0].description", "payment.bank_account_number"
        parts = field_path.split(".", 1)
        if len(parts) != 2:
            raise ValueError(f"Invalid field_path: {field_path}")

        table_name, field_name = parts

        # Get draft_id for this request
        stmt = select(Draft).where(Draft.request_id == request_id)
        result = await db.execute(stmt)
        draft = result.scalar_one_or_none()
        if draft is None:
            raise ValueError(f"No draft found for request {request_id}")

        # Route to appropriate table and field
        if table_name == "header":
            result = await db.execute(
                select(HeaderFields).where(HeaderFields.draft_id == draft.draft_id)
            )
            current = result.scalar_one_or_none()
            if current and getattr(current, field_name) == new_value:
                return False
            update_stmt = (
                update(HeaderFields)
                .where(HeaderFields.draft_id == draft.draft_id)
                .values(**{field_name: new_value})
            )
        elif table_name == "line_items":
            match = re.match(r"(\d+)\.(.+)", field_name)
            if not match:
                raise ValueError(f"Invalid line_items field_path: {field_name}")
            index = int(match.group(1))
            field_name = match.group(2)

            stmt = select(LineItem).where(
                LineItem.draft_id == draft.draft_id,
                LineItem.sequence_number == index,
            )
            result = await db.execute(stmt)
            line_item = result.scalar_one_or_none()
            if line_item is None:
                raise ValueError(f"LineItem at index {index} not found in draft {draft.draft_id}")

            update_stmt = (
                update(LineItem)
                .where(LineItem.line_item_id == line_item.line_item_id)
                .values(**{field_name: new_value})
            )
        elif table_name == "payment":
            result = await db.execute(
                select(PaymentDetails).where(PaymentDetails.draft_id == draft.draft_id)
            )
            payment = result.scalar_one_or_none()
            if payment is None:
                raise ValueError(f"No payment details found for draft {draft.draft_id}")
            if getattr(payment, field_name) == new_value:
                return False
            update_stmt = (
                update(PaymentDetails)
                .where(PaymentDetails.payment_id == payment.payment_id)
                .values(**{field_name: new_value})
            )
        else:
            raise ValueError(f"Unknown table name: {table_name}")

        await db.execute(update_stmt)
        await db.flush()
        return True

    async def create_field_revision(
        self,
        db: AsyncSession,
        request_id: uuid.UUID,
        field_path: str,
        old_value: str | None,
        new_value: str | None,
        actor_id: uuid.UUID,
    ) -> None:
        """Insert a FieldRevision record."""
        now = datetime.now(timezone.utc)
        revision = FieldRevision(
            revision_id=uuid.uuid4(),
            request_id=request_id,
            field_path=field_path,
            old_value=old_value,
            new_value=new_value,
            actor_id=actor_id,
            revised_at=now,
        )
        db.add(revision)
        await db.flush()


request_repository = RequestRepository()
