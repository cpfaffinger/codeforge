"""1D/2D barcode rendering with BWIPP (Barcode Writer in Pure PostScript) via treepoem + Ghostscript.

BWIPP is the same engine bwip-js uses, so symbology ids (``bcid``) and encoder options
(``includetext``, ``custinfoenc``, ``eclevel`` ...) are identical to the legacy barcode service.
Renderer options that bwip-js implemented in JavaScript (scale, rotate, padding ...) are
implemented here with Pillow.
"""

from __future__ import annotations

import functools
import json
import logging
import shutil
import threading
from collections import OrderedDict
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


@functools.lru_cache(maxsize=1)
def _bcid_index() -> dict[str, str]:
    """lowercase id -> canonical BWIPP id (a few encoders are camelCase, e.g. rationalizedCodabar)."""
    return {k.lower(): k for k in symbologies()}


def resolve_bcid(bcid: str) -> str:
    """Case-insensitive lookup of a symbology id; raises RenderError for unknown ids."""
    if not bcid or not bcid.strip():
        raise RenderError("bcid (symbology) is required")
    key = bcid.strip().lower()
    index = _bcid_index()
    if index and key not in index:
        raise RenderError(f"unknown symbology '{bcid}', see /api/v1/symbologies")
    return index.get(key, key)


def is_image_format(value: Any) -> bool:
    from app.engines.render import ALL_FORMATS

    return isinstance(value, str) and value.strip().lower().lstrip(".") in set(ALL_FORMATS) | {"jpeg"}


def normalize_options(raw: dict[str, Any]) -> tuple[dict[str, str | bool], dict[str, Any]]:
    """Split a flat option dict into (BWIPP encoder options, renderer options).

    Values "true"/"" become boolean flags, everything else stays a string.
    ``format`` is ambiguous: png/jpg/... selects the output image format, anything else
    (e.g. Aztec ``format=full``) is a BWIPP encoder option.
    """
    encoder: dict[str, str | bool] = {}
    render: dict[str, Any] = {}
    for key, value in raw.items():
        k = str(key).strip().lower()
        if not k:
            continue
        if k in RENDER_OPTIONS and not (k == "format" and not is_image_format(value)):
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
    bcid = resolve_bcid(bcid)
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

    cache_key = (bcid, text, tuple(sorted((k, str(v)) for k, v in encoder.items())), scale_x, scale_y, pad_w, pad_h, rotate.upper(), monochrome, out_fmt)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    image = _render(bcid, text, encoder, min(scale_x, scale_y))

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
    rendered = pil_to_bytes(image, out_fmt)
    _cache_put(cache_key, rendered)
    return rendered


# ----------------------------------------------------------------- rendering
def _render(bcid: str, text: str, encoder: dict[str, str | bool], scale: int) -> Image.Image:
    """Render with the persistent Ghostscript pool; fall back to treepoem (two gs starts per image)."""
    if settings.gs_workers > 0:
        from app.engines import gsworker

        try:
            pool = gsworker.get_pool(settings.gs_workers, settings.gs_timeout)
            return pool.render(bcid, text, encoder, scale)
        except gsworker.GsJobError as exc:
            raise RenderError(_clean_bwipp_error(str(exc))) from None
        except gsworker.GsWorkerUnavailable as exc:
            logging.getLogger(__name__).warning("ghostscript worker unavailable (%s), falling back to treepoem", exc)
        except Exception as exc:  # pragma: no cover - defensive
            logging.getLogger(__name__).exception("ghostscript worker failed (%s), falling back to treepoem", exc)

    import treepoem

    try:
        return treepoem.generate_barcode(bcid, text, encoder, scale=scale)
    except treepoem.TreepoemError as exc:
        raise RenderError(_clean_bwipp_error(str(exc))) from None
    except FileNotFoundError:
        raise RenderError("barcode rendering is unavailable: ghostscript (gs) is not installed") from None


# --------------------------------------------------------------------- cache
_cache: "OrderedDict[tuple, Rendered]" = OrderedDict()
_cache_bytes = 0
_cache_lock = threading.Lock()
CACHE_STATS = {"hits": 0, "misses": 0}


def _cache_get(key: tuple) -> Rendered | None:
    if settings.cache_max_bytes <= 0:
        return None
    with _cache_lock:
        item = _cache.get(key)
        if item is None:
            CACHE_STATS["misses"] += 1
            return None
        _cache.move_to_end(key)
        CACHE_STATS["hits"] += 1
        return item


def _cache_put(key: tuple, item: Rendered) -> None:
    global _cache_bytes
    if settings.cache_max_bytes <= 0 or len(item.content) > settings.cache_max_bytes // 4:
        return
    with _cache_lock:
        if key in _cache:
            _cache_bytes -= len(_cache[key].content)
        _cache[key] = item
        _cache_bytes += len(item.content)
        while _cache_bytes > settings.cache_max_bytes and _cache:
            _k, old = _cache.popitem(last=False)
            _cache_bytes -= len(old.content)


def cache_stats() -> dict[str, int]:
    with _cache_lock:
        return {"entries": len(_cache), "bytes": _cache_bytes, **CACHE_STATS}


def _clean_bwipp_error(message: str) -> str:
    """Turn Ghostscript/BWIPP stderr into a readable one-liner such as 'code128: BWIPP ERROR: bad data'."""
    lines = [ln.strip() for ln in message.splitlines() if ln.strip()]
    for ln in lines:
        if "BWIPP ERROR" in ln or "Error:" in ln:
            return ln.replace("Error: /", "").strip()
    return lines[0] if lines else "barcode rendering failed"
