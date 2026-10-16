"""Valid deterministic PDF bytes for mocked renderer/storage integration tests."""
from io import BytesIO
from reportlab.pdfgen.canvas import Canvas


def pdf_bytes():
    out = BytesIO()
    canvas = Canvas(out, invariant=True)
    canvas.drawString(50, 750, "Test voucher")
    canvas.save()
    return out.getvalue()
