"""Decode QR codes and 1D barcodes from images (zbar via pyzbar; DataMatrix via libdmtx when installed)."""

from __future__ import annotations

import io
from typing import Any

from PIL import Image, ImageOps

from app.config import settings
from app.engines.render import RenderError


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


def _open_image(content: bytes) -> Image.Image:
    if len(content) > settings.max_upload_bytes:
        raise RenderError(f"image is larger than {settings.max_upload_bytes} bytes")
    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except Image.DecompressionBombError:
        raise RenderError("image has too many pixels") from None
    except Exception:
        raise RenderError("file is not a readable image (PNG, JPEG, GIF, BMP, WEBP)") from None
    img = ImageOps.exif_transpose(img) or img
    return img


def decode_image(content: bytes, *, try_harder: bool = True) -> list[dict[str, Any]]:
    """Return every symbol found in the image: type, data, position."""
    img = _open_image(content)
    results: list[dict[str, Any]] = []
    gray = img.convert("L")

    if zbar_available():
        from pyzbar.pyzbar import decode as zbar_decode

        candidates = [gray]
        if try_harder:
            # a scaled-up copy helps zbar with small codes, an auto-contrast copy with photos
            w, h = gray.size
            if max(w, h) < 800:
                f = 800 // max(w, h) + 1
                candidates.append(gray.resize((w * f, h * f), Image.Resampling.NEAREST))
            candidates.append(ImageOps.autocontrast(gray))
        seen: set[tuple[str, bytes]] = set()
        for cand in candidates:
            for sym in zbar_decode(cand):
                key = (sym.type, sym.data)
                if key in seen:
                    continue
                seen.add(key)
                results.append(
                    {
                        "type": sym.type,
                        "data": _decode_bytes(sym.data),
                        "quality": getattr(sym, "quality", None),
                        "rect": {"left": sym.rect.left, "top": sym.rect.top, "width": sym.rect.width, "height": sym.rect.height},
                        "polygon": [{"x": p.x, "y": p.y} for p in sym.polygon],
                    }
                )
            if results and not try_harder:
                break

    if dmtx_available() and not any(r["type"] == "DATAMATRIX" for r in results):
        from pylibdmtx.pylibdmtx import decode as dmtx_decode

        for sym in dmtx_decode(gray, timeout=1500, max_count=10):
            results.append(
                {
                    "type": "DATAMATRIX",
                    "data": _decode_bytes(sym.data),
                    "quality": None,
                    "rect": {"left": sym.rect.left, "top": sym.rect.top, "width": sym.rect.width, "height": sym.rect.height},
                    "polygon": [],
                }
            )
    return results


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.hex()
