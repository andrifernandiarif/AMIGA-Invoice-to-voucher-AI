# backend/app/providers/base.py
from abc import ABC, abstractmethod
from uuid import UUID

from app.schemas.extraction import ExtractionResult


class ExtractionProvider(ABC):
    """
    Abstract base class for invoice extraction providers.
    All evidence files for a Request are passed together so the model
    can cross-reference across multiple documents.
    """

    @abstractmethod
    async def extract(
        self,
        evidence_files: list[tuple[bytes, str]],  # (file_bytes, content_type)
        request_id: UUID,
    ) -> ExtractionResult:
        """
        Extract invoice fields from one or more evidence files.

        Args:
            evidence_files: List of (file_bytes, content_type) tuples.
                            content_type is one of: application/pdf, image/png,
                            image/jpeg, image/jpg
            request_id: Used for idempotency checks and audit logging.

        Returns:
            ExtractionResult with None for any field the model could not extract.
            The provider MUST NOT fabricate values.
        """
        ...
