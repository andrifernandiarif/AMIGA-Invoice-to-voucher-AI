"""Normalize optional account signatures; never fetch user-supplied URLs."""

import base64
import binascii
import io
import warnings
from PIL import Image, UnidentifiedImageError
from fastapi import HTTPException


def normalize_signature(value):
    if not value:
        return None
    try:
        prefix, encoded = value.split(",", 1)
        if prefix not in ("data:image/png;base64", "data:image/jpeg;base64"):
            raise ValueError()
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError()
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format not in ("PNG", "JPEG") or image.width * image.height > 4_000_000:
                    raise ValueError()
                image.load()
                image = image.convert("RGBA")
                image.thumbnail((1000, 500))
                out = io.BytesIO()
                image.save(out, format="PNG")
        return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode()
    except (
        ValueError,
        binascii.Error,
        OSError,
        UnidentifiedImageError,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise HTTPException(422, "Upload a valid PNG or JPEG signature under 2 MB and 4 megapixels.")
