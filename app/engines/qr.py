"""QR code rendering with segno (pure Python, PNG/SVG/PDF/EPS, colours, all versions and error levels)."""

from __future__ import annotations

import io

import segno

from app.config import settings
from app.engines.render import (
    Rendered,
    RenderError,
    int_in_range,
    normalize_color,
    normalize_format,
    pil_to_bytes,
)

ERROR_LEVELS = {
    "L": "l", "LOW": "l",
    "M": "m", "MEDIUM": "m",
    "Q": "q", "QUARTILE": "q",
    "H": "h", "HIGH": "h",
}
DEFAULT_ERROR = "M"
DEFAULT_SCALE = 10      # pixels per module (legacy "box")
DEFAULT_BORDER = 4      # quiet zone in modules
DEFAULT_FG = "#000000"
DEFAULT_BG = "#ffffff"


def normalize_error(value: str | None) -> str:
    v = (value or DEFAULT_ERROR).strip().upper()
    if v not in ERROR_LEVELS:
        raise RenderError("error correction must be one of L, M, Q, H (or LOW, MEDIUM, QUARTILE, HIGH)")
    return v


def make_qr(
    data: str,
    *,
    error: str | None = None,
    version: int | str | None = None,
    scale: int | str | None = None,
    border: int | str | None = None,
    fg: str | None = None,
    bg: str | None = None,
    fmt: str | None = None,
    micro: bool = False,
    boost_error: bool = True,
) -> Rendered:
    """Render a QR code. Returns the encoded image bytes plus metadata."""
    if data is None or data == "":
        raise RenderError("data must not be empty")
    if len(data) > settings.max_data_length:
        raise RenderError(f"data is longer than {settings.max_data_length} characters")
    error_key = normalize_error(error)
    ver = int_in_range(version, "version", 1, 40, None)
    if micro and ver is not None and ver > 4:
        raise RenderError("micro QR codes only have versions 1 to 4")
    scl = int_in_range(scale, "scale", 1, settings.max_scale, DEFAULT_SCALE)
    brd = int_in_range(border, "border", 0, settings.max_border, DEFAULT_BORDER)
    dark = normalize_color(fg, DEFAULT_FG)
    light = normalize_color(bg, DEFAULT_BG)
    out_fmt = normalize_format(fmt, "png")

    try:
        qr = segno.make(
            data,
            error=ERROR_LEVELS[error_key],
            version=ver,
            micro=micro if micro else False,
            boost_error=boost_error,
        )
    except (ValueError, segno.DataOverflowError) as exc:  # type: ignore[attr-defined]
        raise RenderError(str(exc)) from None

    buf = io.BytesIO()
    if out_fmt == "svg":
        qr.save(buf, kind="svg", scale=scl, border=brd, dark=dark, light=light, xmldecl=True, svgns=True)
        return Rendered(buf.getvalue(), "image/svg+xml", "svg")
    if out_fmt == "pdf":
        qr.save(buf, kind="pdf", scale=scl, border=brd, dark=dark, light=light)
        return Rendered(buf.getvalue(), "application/pdf", "pdf")
    # raster: render PNG with segno, convert with Pillow when another raster format is wanted
    qr.save(buf, kind="png", scale=scl, border=brd, dark=dark, light=light)
    png = buf.getvalue()
    width = height = (qr.symbol_size(scale=scl, border=brd))[0]
    if out_fmt == "png":
        return Rendered(png, "image/png", "png", width, height)
    from PIL import Image

    with Image.open(io.BytesIO(png)) as img:
        return pil_to_bytes(img.convert("RGBA"), out_fmt)


def qr_info(data: str, *, error: str | None = None, version: int | str | None = None, micro: bool = False) -> dict:
    """Metadata about the symbol that would be generated (version, error level, module count)."""
    qr = segno.make(data, error=ERROR_LEVELS[normalize_error(error)], version=int_in_range(version, "version", 1, 40, None), micro=micro)
    return {
        "version": qr.version,
        "error": qr.error,
        "modules": qr.symbol_size(scale=1, border=0)[0],
        "mode": qr.mode,
        "is_micro": qr.is_micro,
    }
