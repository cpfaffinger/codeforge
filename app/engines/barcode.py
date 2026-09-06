"""1D/2D barcode rendering with BWIPP (Barcode Writer in Pure PostScript) via treepoem + Ghostscript.

BWIPP is the same engine bwip-js uses, so symbology ids (``bcid``) and encoder options
(``includetext``, ``custinfoenc``, ``eclevel`` ...) are identical to the legacy barcode service.
Renderer options that bwip-js implemented in JavaScript (scale, rotate, padding ...) are
implemented here with Pillow.
"""

from __future__ import annotations

import functools
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image

from app.config import settings
from app.engines.render import (
    Rendered,
    RenderError,
    apply_padding,
    apply_rotation,
    color_to_bwipp,
    int_in_range,
    normalize_format,
    pil_to_bytes,
)

# options handled by the renderer (bwip-js "renderer options"), not passed to BWIPP
RENDER_OPTIONS = {
    "scale", "scalex", "scaley", "rotate", "padding", "paddingwidth", "paddingheight",
    "paddingleft", "paddingright", "paddingtop", "paddingbottom", "monochrome", "sizelimit",
    "format", "base64", "bcid", "text",
}
DEFAULT_SCALE = 2
DEFAULT_BACKGROUND = "FFFFFF"

_DATA_FILE = Path(__file__).resolve().parent.parent / "data" / "symbologies.json"


def ghostscript_available() -> bool:
    return shutil.which("gs") is not None


@functools.lru_cache(maxsize=1)
def symbologies() -> dict[str, dict[str, Any]]:
    """All symbology ids supported by the vendored BWIPP, merged with descriptions and examples."""
    described: dict[str, dict[str, Any]] = {}
    if _DATA_FILE.exists():
        described = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
    result: dict[str, dict[str, Any]] = {}
    try:
        import treepoem

        for code, meta in treepoem.barcode_types.items():
            d = described.get(code, {})
            result[code] = {
                "id": code,
                "description": d.get("description") or meta.description,
                "example": d.get("example", ""),
                "example_options": d.get("example_options", ""),
            }
    except ImportError:  # treepoem missing (e.g. local dev without ghostscript)
        for code, d in described.items():
            result[code] = {"id": code, **d}
    return dict(sorted(result.items()))


def normalize_options(raw: dict[str, Any]) -> tuple[dict[str, str | bool], dict[str, Any]]:
    """Split a flat option dict into (BWIPP encoder options, renderer options).

    Values "true"/"" become boolean flags, everything else stays a string.
    """
    encoder: dict[str, str | bool] = {}
    render: dict[str, Any] = {}
    for key, value in raw.items():
        k = str(key).strip().lower()
        if not k:
            continue
        if k in RENDER_OPTIONS:
            render[k] = value
            continue
        if isinstance(value, bool):
            if value:
                encoder[k] = True
            continue
        v = "" if value is None else str(value).strip()
        if v == "" or v.lower() == "true":
            encoder[k] = True
        elif v.lower() == "false":
            continue
        else:
            encoder[k] = v
    return encoder, render


def parse_option_string(text: str | None) -> dict[str, Any]:
    """Parse bwip-js style option strings: 'includetext textsize=12 guardwhitespace'."""
    result: dict[str, Any] = {}
    if not text:
        return result
    for token in text.replace("\n", " ").split():
        if "=" in token:
            k, v = token.split("=", 1)
            result[k] = v
        else:
            result[token] = True
    return result


def make_barcode(
    bcid: str,
    text: str,
    options: dict[str, Any] | None = None,
    *,
    fmt: str | None = None,
) -> Rendered:
    """Render a barcode. ``options`` may contain BWIPP encoder options and renderer options."""
    if not bcid:
        raise RenderError("bcid (symbology) is required")
    if text is None or text == "":
        raise RenderError("text is required")
    if len(text) > settings.max_data_length:
        raise RenderError(f"text is longer than {settings.max_data_length} characters")
    bcid = bcid.strip().lower()
    known = symbologies()
    if known and bcid not in known:
        raise RenderError(f"unknown symbology '{bcid}', see /api/v1/symbologies")
    if not ghostscript_available():
        raise RenderError("barcode rendering is unavailable: ghostscript (gs) is not installed")

    encoder, render = normalize_options(options or {})
    out_fmt = normalize_format(fmt or render.get("format"), "png")
    if out_fmt == "svg":
        raise RenderError("SVG output is only available for QR codes (/api/v1/qr)")

    # colours: BWIPP wants RRGGBB without '#'
    if "backgroundcolor" in encoder:
        encoder["backgroundcolor"] = color_to_bwipp(str(encoder["backgroundcolor"]), DEFAULT_BACKGROUND)
    else:
        encoder["backgroundcolor"] = DEFAULT_BACKGROUND
    for key in ("barcolor", "textcolor", "bordercolor"):
        if key in encoder and isinstance(encoder[key], str):
            encoder[key] = color_to_bwipp(encoder[key], "000000")

    scale = int_in_range(render.get("scale"), "scale", 1, settings.max_scale, DEFAULT_SCALE)
    scale_x = int_in_range(render.get("scalex"), "scaleX", 1, settings.max_scale, scale)
    scale_y = int_in_range(render.get("scaley"), "scaleY", 1, settings.max_scale, scale_x)
    pad = int_in_range(render.get("padding"), "padding", 0, settings.max_border, 1)
    pad_w = int_in_range(render.get("paddingwidth"), "paddingwidth", 0, settings.max_border, pad)
    pad_h = int_in_range(render.get("paddingheight"), "paddingheight", 0, settings.max_border, pad)
    rotate = str(render.get("rotate") or "N")
    monochrome = str(render.get("monochrome", "false")).lower() in ("true", "1", "")

    import treepoem

    try:
        image = treepoem.generate_barcode(bcid, text, encoder, scale=min(scale_x, scale_y))
    except treepoem.TreepoemError as exc:
        raise RenderError(_clean_bwipp_error(str(exc))) from None
    except FileNotFoundError:
        raise RenderError("barcode rendering is unavailable: ghostscript (gs) is not installed") from None

    base = min(scale_x, scale_y)
    if scale_x != scale_y:
        image = image.resize(
            (max(1, image.width * scale_x // base), max(1, image.height * scale_y // base)),
            Image.Resampling.NEAREST,
        )
    if image.width * image.height > settings.max_image_pixels:
        raise RenderError("resulting image is too large, reduce scale")
    image = image.convert("RGBA" if not monochrome else "1")
    bg = "#" + encoder["backgroundcolor"][:6]
    image = apply_rotation(image, rotate)
    image = apply_padding(image, pad_w * max(scale_x, 1), pad_h * max(scale_y, 1), bg)
    if monochrome:
        image = image.convert("1")
    return pil_to_bytes(image, out_fmt)


def _clean_bwipp_error(message: str) -> str:
    """Turn Ghostscript/BWIPP stderr into a readable one-liner such as 'code128: BWIPP ERROR: bad data'."""
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    for ln in lines:
        if "BWIPP ERROR" in ln or "Error:" in ln:
            return ln.replace("Error: /", "").strip()
    return lines[0] if lines else "barcode rendering failed"
