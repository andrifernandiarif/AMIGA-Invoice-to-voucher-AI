from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database — NeonDB (postgresql+asyncpg with SSL)
    DATABASE_URL: str = "postgresql+asyncpg://amiga:amiga@localhost:5432/amiga_db"

    DB_CONNECT_TIMEOUT_SECONDS: float = Field(default=10, gt=0)
    DB_COMMAND_TIMEOUT_SECONDS: float = Field(default=30, gt=0)
    DB_POOL_RECYCLE_SECONDS: int = Field(default=240, ge=1)

    # Redis — optional, only needed if using RQ background worker
    REDIS_URL: str = "redis://localhost:6379/0"

    # File Storage — real AWS S3
    S3_BUCKET: str = "amiga-invoices-dev"
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_SESSION_TOKEN: str = ""
    AWS_REGION: str = "ap-southeast-1"

    # Auth
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRY_SECONDS: int = 3600

    # Extraction
    EXTRACTION_PROVIDER: Literal["mock", "bedrock"] = "mock"
    BEDROCK_MODEL_ID: str = "amazon.nova-lite-v1:0"

    # Retrieval / Telegram. Titan V2 supports 1024, 512 or 256; this schema uses 1024.
    TITAN_EMBED_MODEL_ID: str = "amazon.titan-embed-text-v2:0"
    TITAN_EMBED_REGION: str = ""  # empty uses AWS_REGION; override where Titan V2 is unavailable
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_USER_MAP: dict[str, str] = {}  # private chat ID -> system user UUID

    # Business rules
    CALCULATION_TOLERANCE: str = "0.01"
    AUTHORIZATION_DISCLAIMER: str = (
        "This voucher is prepared for client review purposes only. " "Payment authorization remains with the client."
    )

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
