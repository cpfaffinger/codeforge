"""Shared image helpers: format conversion, colours, base64, PDF label sheets."""

from __future__ import annotations

import base64
import io
import re
from dataclasses import dataclass

from PIL import Image, ImageOps

from app.config import settings

Image.MAX_IMAGE_PIXELS = settings.max_image_pixels


class RenderError(ValueError):
    """Invalid parameters or a rendering failure that is the client's fault."""


@dataclass
class Rendered:
    content: bytes
    mime: str
    format: str
    width: int | None = None
    height: int | None = None

    @property
    def base64(self) -> str:
        return base64.b64encode(self.content).decode("ascii")

    @property
    def data_uri(self) -> str:
        return f"data:{self.mime};base64,{self.base64}"


# output format -> (Pillow format, mime type)
RASTER_FORMATS: dict[str, tuple[str, str]] = {
    "png": ("PNG", "image/png"),
    "jpg": ("JPEG", "image/jpeg"),
    "jpeg": ("JPEG", "image/jpeg"),
    "webp": ("WEBP", "image/webp"),
    "gif": ("GIF", "image/gif"),
    "bmp": ("BMP", "image/bmp"),
    "pdf": ("PDF", "application/pdf"),
}
VECTOR_FORMATS = {"svg": "image/svg+xml"}
ALL_FORMATS = sorted(set(RASTER_FORMATS) | set(VECTOR_FORMATS))

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8}|[0-9a-fA-F]{3})$")
_NAME_RE = re.compile(r"^[a-zA-Z]{3,30}$")


def normalize_format(fmt: str | None, default: str = "png") -> str:
    f = (fmt or default).strip().lower().lstrip(".")
    if f == "jpeg":
        f = "jpg"
    if f not in RASTER_FORMATS and f not in VECTOR_FORMATS:
        raise RenderError(f"unsupported format '{fmt}', use one of: {', '.join(ALL_FORMATS)}")
    return f


def normalize_color(value: str | None, default: str) -> str:
    """Accept #RRGGBB, RRGGBB, #RGB, #RRGGBBAA or CSS colour names; return a value Pillow/segno understand."""
    if value is None or value == "":
        return default
    v = value.strip()
    if _HEX_RE.match(v):
        return v if v.startswith("#") else f"#{v}"
    if _NAME_RE.match(v):
        return v.lower()
    raise RenderError(f"invalid colour '{value}', use #RRGGBB or a colour name")


def color_to_bwipp(value: str | None, default: str) -> str:
    """BWIPP wants colours as hex without '#': RRGGBB, RRGGBBAA or CMYK."""
    v = normalize_color(value, default)
    if _HEX_RE.match(v):
        v = v.lstrip("#")
        if len(v) == 3:
            v = "".join(c * 2 for c in v)
        return v.upper()
    # colour name -> hex via Pillow
    from PIL import ImageColor

    r, g, b = ImageColor.getrgb(v)[:3]
    return f"{r:02X}{g:02X}{b:02X}"


def int_in_range(value, name: str, lo: int, hi: int, default: int | None = None) -> int | None:
    if value is None or value == "":
        return default
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise RenderError(f"{name} must be an integer") from None
    if n < lo or n > hi:
        raise RenderError(f"{name} must be between {lo} and {hi}")
    return n


def pil_to_bytes(image: Image.Image, fmt: str) -> Rendered:
    """Encode a Pillow image in one of RASTER_FORMATS."""
    fmt = normalize_format(fmt)
    if fmt in VECTOR_FORMATS:
        raise RenderError("SVG output is only available for QR codes")
    pil_fmt, mime = RASTER_FORMATS[fmt]
    buf = io.BytesIO()
    img = image
    if pil_fmt in ("JPEG", "PDF", "BMP") and img.mode not in ("RGB", "L"):
        # flatten transparency onto white
        bg = Image.new("RGB", img.size, "white")
        if img.mode in ("RGBA", "LA"):
            bg.paste(img, mask=img.getchannel("A"))
        else:
            bg.paste(img.convert("RGB"))
        img = bg
    save_kwargs = {}
    if pil_fmt == "JPEG":
        save_kwargs = {"quality": 90, "optimize": True}
    elif pil_fmt == "PNG":
        save_kwargs = {"optimize": True}
    elif pil_fmt == "PDF":
        save_kwargs = {"resolution": 150.0}
    img.save(buf, pil_fmt, **save_kwargs)
    return Rendered(buf.getvalue(), mime, fmt, image.width, image.height)


def apply_rotation(image: Image.Image, rotate: str | None) -> Image.Image:
    """bwip-js rotate: N (none), R (90 clockwise), L (90 counter-clockwise), I (180)."""
    r = (rotate or "N").strip().upper()
    if r in ("", "N"):
        return image
    if r == "R":
        return image.transpose(Image.Transpose.ROTATE_270)
    if r == "L":
        return image.transpose(Image.Transpose.ROTATE_90)
    if r == "I":
        return image.transpose(Image.Transpose.ROTATE_180)
    raise RenderError("rotate must be one of N, R, L, I")


def apply_padding(image: Image.Image, width: int, height: int, fill: str) -> Image.Image:
    if width <= 0 and height <= 0:
        return image
    return ImageOps.expand(image, border=(max(width, 0), max(height, 0)), fill=fill)


def label_sheet(items: list[tuple[bytes, str | None]], *, page: str = "A4", columns: int = 3, rows: int = 8,
                margin_mm: float = 10.0, gap_mm: float = 4.0, captions: bool = True, title: str | None = None) -> bytes:
    """Lay out PNG images on a PDF label sheet (reportlab). Items are (png_bytes, caption)."""
    from reportlab.lib.pagesizes import A4, LETTER, landscape
    from reportlab.lib.units import mm
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfgen import canvas

    sizes = {"A4": A4, "LETTER": LETTER, "A4-LANDSCAPE": landscape(A4), "LETTER-LANDSCAPE": landscape(LETTER)}
    try:
        page_size = sizes[page.upper()]
    except KeyError:
        raise RenderError(f"page must be one of {', '.join(sizes)}") from None
    columns = max(1, min(columns, 20))
    rows = max(1, min(rows, 40))
    pw, ph = page_size
    margin = margin_mm * mm
    gap = gap_mm * mm
    cell_w = (pw - 2 * margin - (columns - 1) * gap) / columns
    cell_h = (ph - 2 * margin - (rows - 1) * gap) / rows
    caption_h = 10 if captions else 0

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=page_size)
    c.setTitle(title or "codeforge label sheet")
    c.setAuthor("codeforge")
    per_page = columns * rows
    for idx, (png, caption) in enumerate(items):
        if idx and idx % per_page == 0:
            c.showPage()
        pos = idx % per_page
        col, row = pos % columns, pos // columns
        x0 = margin + col * (cell_w + gap)
        y0 = ph - margin - (row + 1) * cell_h - row * gap
        img = ImageReader(io.BytesIO(png))
        iw, ih = img.getSize()
        avail_w, avail_h = cell_w, cell_h - caption_h
        scale = min(avail_w / iw, avail_h / ih)
        dw, dh = iw * scale, ih * scale
        c.drawImage(img, x0 + (cell_w - dw) / 2, y0 + caption_h + (avail_h - dh) / 2, dw, dh, mask="auto")
        if captions and caption:
            c.setFont("Helvetica", 7)
            text = caption if len(caption) <= 60 else caption[:57] + "..."
            c.drawCentredString(x0 + cell_w / 2, y0 + 2, text)
    c.save()
    return buf.getvalue()
