"""Lazy Bedrock client; importing services never calls AWS or credential discovery."""

import boto3
from botocore.config import Config
from app.config import settings


def create_client(region_name=None):
    kwargs = {
        "region_name": region_name or settings.AWS_REGION,
        "config": Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2, "mode": "standard"}),
    }
    if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
        kwargs.update(
            aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        if settings.AWS_SESSION_TOKEN:
            kwargs["aws_session_token"] = settings.AWS_SESSION_TOKEN
    return boto3.client("bedrock-runtime", **kwargs)
