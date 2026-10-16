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


def add_authorization_footer(pdf_bytes: bytes, notice: str) -> bytes:
    """Stamp the mandatory notice after rendering, outside template control."""
    from io import BytesIO

    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.utils import simpleSplit
    from reportlab.pdfgen.canvas import Canvas

    reader, writer = PdfReader(BytesIO(pdf_bytes)), PdfWriter()
    for number, page in enumerate(reader.pages, start=1):
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        buffer = BytesIO()
        canvas = Canvas(buffer, pagesize=(width, height))
        canvas.setFillColorRGB(1, 1, 1)
        canvas.rect(0, 0, width, 64, fill=1, stroke=0)
        canvas.setStrokeColorRGB(0.72, 0.81, 0.81)
        canvas.line(51, 63, width - 51, 63)
        canvas.setFillColorRGB(0.26, 0.36, 0.38)
        lines = simpleSplit(notice, "Helvetica", 7, width - 102)
        if len(lines) > 4:
            raise ValueError("Authorization notice exceeds the reserved footer space.")
        canvas.setFont("Helvetica", 7)
        for index, line in enumerate(lines):
            canvas.drawString(51, 50 - index * 9, line)
        canvas.setFont("Helvetica", 6.5)
        canvas.drawRightString(width - 51, 12, f"AMIGA  |  {number} / {len(reader.pages)}")
        canvas.save()
        page.merge_page(PdfReader(BytesIO(buffer.getvalue())).pages[0])
        writer.add_page(page)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()
