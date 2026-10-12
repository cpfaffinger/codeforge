"""Decode QR codes and 1D barcodes from images (zbar via pyzbar; DataMatrix via libdmtx when installed).

Real photos rarely decode on the first try: a 12 megapixel phone picture with a small, slightly
blurred code, uneven lighting, a screenshot of a screen, an inverted (white on black) code.
``decode_image`` therefore runs a ladder of cheap pre-processing passes and stops at the first
hit, within a time budget:

* size ladder - zbar is fastest and most reliable at ~1000-1600 px on the long edge, so large
  photos are tried downscaled first (also 4x faster than the original), small images upscaled
* contrast passes - autocontrast, local illumination flattening (gray minus blurred gray),
  unsharp masking, and an inverted copy
* a 45 degree rotated copy as a last resort for QR codes photographed diagonally

Every result carries the pass that found it (``found_by``) so the API can be tuned from the logs.
"""

from __future__ import annotations

import io
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from PIL import Image, ImageChops, ImageFilter, ImageOps

from app.config import settings
from app.engines.render import RenderError

log = logging.getLogger(__name__)

SUPPORTED = "PNG, JPEG, GIF, BMP, WEBP, TIFF"


def zbar_available() -> bool:
    try:
        import pyzbar.pyzbar  # noqa: F401

        return True
    except Exception:
        return False


def dmtx_available() -> bool:
    try:
        import pylibdmtx.pylibdmtx  # noqa: F401

        return True
    except Exception:
        return False


def _sniff(content: bytes) -> str | None:
    """Container format of well-known files we cannot decode, for a helpful error message."""
    head = content[:32]
    if len(head) >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"heif"):
            return "HEIC/HEIF"
        if brand in (b"avif", b"avis"):
            return "AVIF"
        return "MP4/MOV video"
    if head.startswith(b"%PDF"):
        return "PDF"
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        return "SVG"
    return None


def _open_image(content: bytes) -> Image.Image:
    if not content:
        raise RenderError("no image data received (empty upload)")
    if len(content) > settings.max_upload_bytes:
        raise RenderError(f"image is larger than {settings.max_upload_bytes // (1024 * 1024)} MB (limit CODEFORGE_MAX_UPLOAD_BYTES)")
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Image.DecompressionBombError:
        raise RenderError("image has too many pixels (limit CODEFORGE_MAX_IMAGE_PIXELS)") from None
    except Exception:
        kind = _sniff(content)
        if kind == "HEIC/HEIF":
            raise RenderError("HEIC/HEIF photos (iPhone default) cannot be read; export or share the photo as JPEG, or set the camera to 'Most compatible'") from None
        if kind:
            raise RenderError(f"{kind} files cannot be decoded; upload a raster image ({SUPPORTED})") from None
        raise RenderError(f"file is not a readable image ({SUPPORTED})") from None
    img = ImageOps.exif_transpose(img) or img
    return img


@dataclass
class DecodeResult:
    symbols: list[dict[str, Any]] = field(default_factory=list)
    width: int = 0
    height: int = 0
    passes: int = 0
    elapsed_ms: int = 0
    hints: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.symbols), "symbols": self.symbols, "width": self.width, "height": self.height,
            "passes": self.passes, "elapsed_ms": self.elapsed_ms, "hints": self.hints,
        }


def _flatten(gray: Image.Image, radius: int) -> Image.Image:
    """Remove slow illumination gradients: gray - blur(gray) + 128."""
    background = gray.filter(ImageFilter.GaussianBlur(radius))
    return ImageChops.subtract(gray, background, scale=1.0, offset=128)


def _variants(gray: Image.Image, try_harder: bool) -> Iterator[tuple[str, Image.Image, float]]:
    """(name, image, scale factor relative to the original) in the order they should be tried."""
    w, h = gray.size
    longest = max(w, h)
    sizes: list[tuple[str, Image.Image, float]] = []
    if longest < 700:
        f = 800 // longest + 1
        sizes.append((f"up{f}x", gray.resize((w * f, h * f), Image.Resampling.NEAREST), float(f)))
    for target in (1600, 1000, 640):
        if longest > target * 1.2:
            f = target / longest
            sizes.append((f"scale{target}", gray.resize((max(1, round(w * f)), max(1, round(h * f))), Image.Resampling.LANCZOS), f))
    if longest <= 2400:
        sizes.insert(0 if longest >= 700 else 1, ("orig", gray, 1.0))
    else:
        sizes.append(("orig", gray, 1.0))
    modes = ["plain", "flat", "autocontrast", "sharpen", "inverted"] if try_harder else ["plain"]
    for mode in modes:
        for name, g, f in sizes:
            if mode == "plain":
                yield f"{name}/plain", g, f
            elif mode == "flat":
                yield f"{name}/flat", ImageOps.autocontrast(_flatten(g, max(8, min(g.size) // 40)), cutoff=1), f
            elif mode == "autocontrast":
                yield f"{name}/autocontrast", ImageOps.autocontrast(g, cutoff=2), f
            elif mode == "sharpen":
                yield f"{name}/sharpen", g.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2)), f
            elif mode == "inverted":
                yield f"{name}/inverted", ImageOps.invert(g), f
    if try_harder:
        name, g, f = sizes[0]
        yield f"{name}/rotated45", g.rotate(45, resample=Image.Resampling.BILINEAR, expand=True, fillcolor=255), -1.0


def _zbar_symbols(image: Image.Image, scale: float, found_by: str) -> list[dict[str, Any]]:
    from pyzbar.pyzbar import decode as zbar_decode

    out: list[dict[str, Any]] = []
    for sym in zbar_decode(image):
        if not sym.data:
            continue
        geom = scale > 0
        inv = (1.0 / scale) if geom else 1.0
        out.append(
            {
                "type": sym.type,
                "data": _decode_bytes(sym.data),
                "quality": getattr(sym, "quality", None),
                "rect": {"left": round(sym.rect.left * inv), "top": round(sym.rect.top * inv), "width": round(sym.rect.width * inv), "height": round(sym.rect.height * inv)} if geom else {},
                "polygon": [{"x": round(p.x * inv), "y": round(p.y * inv)} for p in sym.polygon] if geom else [],
                "engine": "zbar",
                "found_by": found_by,
            }
        )
    return out


def _dmtx_symbols(gray: Image.Image, timeout_ms: int) -> list[dict[str, Any]]:
    from pylibdmtx.pylibdmtx import decode as dmtx_decode

    w, h = gray.size
    longest = max(w, h)
    f = 1.0
    if longest > 1200:  # libdmtx is slow on big images and happy with ~1000 px
        f = 1200 / longest
        gray = gray.resize((max(1, round(w * f)), max(1, round(h * f))), Image.Resampling.LANCZOS)
    inv = 1.0 / f
    out: list[dict[str, Any]] = []
    for sym in dmtx_decode(gray, timeout=timeout_ms, max_count=10):
        out.append(
            {
                "type": "DATAMATRIX",
                "data": _decode_bytes(sym.data),
                "quality": None,
                "rect": {"left": round(sym.rect.left * inv), "top": round(sym.rect.top * inv), "width": round(sym.rect.width * inv), "height": round(sym.rect.height * inv)},
                "polygon": [],
                "engine": "libdmtx",
                "found_by": "scale1200/plain" if f != 1.0 else "orig/plain",
            }
        )
    return out


def decode_image(content: bytes, *, try_harder: bool = True, budget: float | None = None) -> list[dict[str, Any]]:
    """Return every symbol found in the image (compatibility wrapper around :func:`decode_image_full`)."""
    return decode_image_full(content, try_harder=try_harder, budget=budget).symbols


def decode_image_full(content: bytes, *, try_harder: bool = True, budget: float | None = None) -> DecodeResult:
    """Decode with the pre-processing ladder; stops at the first pass that finds something."""
    t0 = time.monotonic()
    budget = settings.decode_budget if budget is None else budget
    img = _open_image(content)
    result = DecodeResult(width=img.width, height=img.height)
    gray = img.convert("L")
    have_zbar, have_dmtx = zbar_available(), dmtx_available()
    seen: set[tuple[str, str]] = set()

    def add(symbols: list[dict[str, Any]]) -> None:
        for s in symbols:
            key = (s["type"], s["data"])
            if key not in seen:
                seen.add(key)
                result.symbols.append(s)

    timed_out = False
    if have_zbar:
        for name, variant, scale in _variants(gray, try_harder):
            if result.passes and time.monotonic() - t0 > budget:
                timed_out = True
                break
            result.passes += 1
            try:
                found = _zbar_symbols(variant, scale, name)
            except Exception as exc:  # pragma: no cover - zbar internal assertion
                log.warning("zbar failed on pass %s: %s", name, exc)
                found = []
            if found:
                add(found)
                break

    # DataMatrix is not covered by zbar; only spend the (slow) libdmtx time when nothing else was found
    if have_dmtx and not result.symbols:
        remaining = budget - (time.monotonic() - t0)
        if remaining > 0.3:
            result.passes += 1
            try:
                add(_dmtx_symbols(gray, int(min(remaining, 3.0) * 1000)))
            except Exception as exc:  # pragma: no cover
                log.warning("libdmtx failed: %s", exc)

    result.elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not result.symbols:
        result.hints = _hints(img, have_zbar, have_dmtx, timed_out)
    log.info("decode: %dx%d %d symbol(s) in %d ms after %d pass(es)%s", img.width, img.height, len(result.symbols), result.elapsed_ms, result.passes,
             " - " + ", ".join(f"{s['type']} via {s['found_by']}" for s in result.symbols) if result.symbols else "")
    return result


def _hints(img: Image.Image, have_zbar: bool, have_dmtx: bool, timed_out: bool) -> list[str]:
    hints = []
    longest = max(img.size)
    if longest < 200:
        hints.append("the image is very small; a QR code needs at least ~3 pixels per module to be readable")
    if longest > 3000:
        hints.append("large photo: make sure the code fills a good part of the frame and is in focus")
    hints.append("readable symbologies: QR Code, EAN/UPC, Code 128/39/93, ITF, Codabar, GS1 DataBar, PDF417" + (", DataMatrix" if have_dmtx else "") + " (not Micro QR, Aztec or MaxiCode)")
    hints.append("try a sharper, evenly lit photo taken straight on, with some white space around the code")
    if timed_out:
        hints.append("the time budget for pre-processing passes ran out (CODEFORGE_DECODE_BUDGET)")
    if not have_zbar:
        hints.append("zbar is not installed on this server")
    return hints


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.hex()
