from app.providers.base import ExtractionProvider
from app.providers.mock import MockExtractionProvider

__all__ = ["ExtractionProvider", "MockExtractionProvider"]

from app.providers.bedrock import BedrockExtractionProvider


def get_extraction_provider() -> ExtractionProvider:
    """Factory — returns the provider selected by EXTRACTION_PROVIDER env var."""
    from app.config import settings
    if settings.EXTRACTION_PROVIDER == "bedrock":
        return BedrockExtractionProvider()
    return MockExtractionProvider()
