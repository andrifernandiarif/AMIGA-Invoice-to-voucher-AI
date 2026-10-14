import asyncio
from functools import partial

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.config import settings


class StorageError(Exception):
    """Raised when an S3 operation fails."""


class S3FileStore:
    def __init__(self) -> None:
        credentials = {}
        if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
            credentials = {
                "aws_access_key_id": settings.AWS_ACCESS_KEY_ID,
                "aws_secret_access_key": settings.AWS_SECRET_ACCESS_KEY,
            }
            if settings.AWS_SESSION_TOKEN:
                credentials["aws_session_token"] = settings.AWS_SESSION_TOKEN
        self._client = boto3.client("s3", region_name=settings.AWS_REGION, **credentials)
        self.bucket = settings.S3_BUCKET

    async def upload(self, key: str, data: bytes, content_type: str) -> None:
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(
                None,
                partial(
                    self._client.put_object,
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType=content_type,
                ),
            )
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Failed to upload '{key}' to S3: {exc}") from exc

    async def download(self, key: str) -> bytes:
        loop = asyncio.get_event_loop()
        try:
            response = await loop.run_in_executor(
                None,
                partial(self._client.get_object, Bucket=self.bucket, Key=key),
            )
            return response["Body"].read()
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(f"Failed to download '{key}' from S3: {exc}") from exc


file_store = S3FileStore()
