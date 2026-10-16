# backend/app/services/telemetry_service.py
"""
Telemetry service for measuring durations, recording SLA breaches,
and serving aggregated metrics.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry import MetricType, TelemetryRecord


class TelemetryService:
    """Service for recording extraction duration and SLA breach events."""

    # SLA thresholds in milliseconds
    SLA_THRESHOLD_LOW_PAGES = 15_000  # ≤5 pages
    SLA_THRESHOLD_HIGH_PAGES = 30_000  # 6-20 pages

    @staticmethod
    def get_sla_threshold(page_count: int | None) -> int:
        """
        Determine SLA threshold based on page count.

        Args:
            page_count: Number of pages in the invoice

        Returns:
            SLA threshold in milliseconds (15_000 for ≤5 pages,
            30_000 for 6-20 pages, 30_000 for >20 pages)
        """
        if page_count is None or page_count <= 5:
            return TelemetryService.SLA_THRESHOLD_LOW_PAGES
        elif page_count <= 20:
            return TelemetryService.SLA_THRESHOLD_HIGH_PAGES
        else:
            return TelemetryService.SLA_THRESHOLD_HIGH_PAGES

    @classmethod
    async def record_extraction_duration(
        cls,
        db: AsyncSession,
        job_id: uuid.UUID,
        start_time: datetime,
        end_time: datetime,
        page_count: int | None,
    ) -> TelemetryRecord:
        """
        Record extraction duration telemetry and check for SLA breach.

        Args:
            db: AsyncSession for database operations
            job_id: Extraction job ID
            start_time: When extraction started (when job transitioned to PROCESSING)
            end_time: When extraction completed
            page_count: Number of pages in the invoice

        Returns:
            TelemetryRecord that was persisted
        """
        # Compute duration in milliseconds
        duration_ms = int((end_time - start_time).total_seconds() * 1000)

        # Determine SLA threshold based on page count
        threshold = cls.get_sla_threshold(page_count)

        # Check if SLA was breached
        is_breach = duration_ms > threshold

        # Create telemetry record
        record = TelemetryRecord(
            record_id=uuid.uuid4(),
            metric_type=MetricType.EXTRACTION_DURATION,
            subject_id=str(job_id),
            duration_ms=duration_ms,
            page_count=page_count,
            is_breach=is_breach,
            breach_threshold_ms=threshold,
            measured_at=end_time,
        )

        db.add(record)
        await db.flush()

        return record

    @classmethod
    async def record_breach_event(
        cls,
        db: AsyncSession,
        job_id: uuid.UUID,
        duration_ms: int,
        page_count: int | None,
        threshold_ms: int,
        measured_at: datetime,
    ) -> TelemetryRecord:
        """
        Record a breach event for an SLA threshold exceeded.

        Args:
            db: AsyncSession for database operations
            job_id: Extraction job ID
            duration_ms: Actual measured duration in milliseconds
            page_count: Number of pages in the invoice
            threshold_ms: SLA threshold that was exceeded
            measured_at: When the measurement was taken

        Returns:
            TelemetryRecord that was persisted
        """
        record = TelemetryRecord(
            record_id=uuid.uuid4(),
            metric_type=MetricType.EXTRACTION_DURATION,
            subject_id=str(job_id),
            duration_ms=duration_ms,
            page_count=page_count,
            is_breach=True,
            breach_threshold_ms=threshold_ms,
            measured_at=measured_at,
        )

        db.add(record)
        await db.flush()

        return record

    @classmethod
    async def record_document_timings(cls, db, request_id, timings):
        for index, timing in enumerate(timings):
            threshold = cls.get_sla_threshold(timing["page_count"])
            db.add(
                TelemetryRecord(
                    record_id=uuid.uuid4(),
                    metric_type=MetricType.EXTRACTION_DOCUMENT_DURATION,
                    subject_id=f"{request_id}/document/{index + 1}",
                    duration_ms=timing["duration_ms"],
                    page_count=timing["page_count"],
                    is_breach=timing["duration_ms"] > threshold,
                    breach_threshold_ms=threshold,
                    measured_at=datetime.now(UTC),
                )
            )
        await db.flush()
