"""Lazy HTML PDF rendering with native-library discovery on Homebrew macOS."""

import importlib
import os
import sys
from pathlib import Path


def render_html_pdf(html: str, renderer=None) -> bytes:
    if renderer is None or not hasattr(renderer, "HTML"):
        if sys.platform == "darwin":
            # ctypes uses this path to locate Homebrew Pango/GLib libraries.
            paths = [p for p in ("/opt/homebrew/lib", "/usr/local/lib") if Path(p, "libgobject-2.0.dylib").exists()]
            configured = os.environ.get("DYLD_FALLBACK_LIBRARY_PATH", "")
            if paths:
                os.environ["DYLD_FALLBACK_LIBRARY_PATH"] = ":".join([configured, *paths]).strip(":")
        renderer = importlib.import_module("weasyprint")
    return renderer.HTML(string=html).write_pdf()
