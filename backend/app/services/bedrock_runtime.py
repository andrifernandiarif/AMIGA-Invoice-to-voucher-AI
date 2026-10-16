"""Lazy Bedrock client; importing services never calls AWS or credential discovery."""

from functools import lru_cache

import boto3
from botocore.config import Config

from app.config import settings


def create_client(region_name=None):
    return _client(
        region_name or settings.AWS_REGION,
        settings.AWS_ACCESS_KEY_ID,
        settings.AWS_SECRET_ACCESS_KEY,
        settings.AWS_SESSION_TOKEN,
    )


@lru_cache(maxsize=8)
def _client(region, access_key, secret_key, session_token):
    kwargs = {
        "region_name": region,
        "config": Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2, "mode": "standard"}),
    }
    if access_key and secret_key:
        kwargs.update(aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        if session_token:
            kwargs["aws_session_token"] = session_token
    return boto3.client("bedrock-runtime", **kwargs)
