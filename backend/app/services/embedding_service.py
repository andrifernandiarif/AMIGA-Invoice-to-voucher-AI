"""Titan V2 InvokeModel wrapper; boto3 runs outside the asyncio event loop."""

import asyncio
import json
import math
from app.config import settings
from app.models.chunk_embedding import EMBEDDING_DIMENSIONS
from app.services.bedrock_runtime import create_client


def validate_vector(vector):
    if len(vector) != EMBEDDING_DIMENSIONS or any(isinstance(v, bool) or not math.isfinite(float(v)) for v in vector):
        raise ValueError(f"Expected {EMBEDDING_DIMENSIONS} finite embedding values.")
    result = [float(v) for v in vector]
    if not any(result):
        raise ValueError("Cosine embeddings must not be zero vectors.")
    return result


def _embed(text: str) -> list[float]:
    if not text.strip() or len(text) > 50000:
        raise ValueError("Embedding input must contain 1–50,000 characters.")
    response = create_client(settings.TITAN_EMBED_REGION or None).invoke_model(
        modelId=settings.TITAN_EMBED_MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps({"inputText": text, "dimensions": EMBEDDING_DIMENSIONS, "normalize": True}),
    )
    body = response["body"]
    try:
        data = json.loads(body.read())
    finally:
        body.close()
    return validate_vector(data["embedding"])


async def embed(text: str) -> list[float]:
    return await asyncio.to_thread(_embed, text)


async def embed_chunks(chunks: list[str]) -> list[list[float]]:
    # Titan V2 has no synchronous native batch embedding operation.
    return [await embed(chunk) for chunk in chunks]
