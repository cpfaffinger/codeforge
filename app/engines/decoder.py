"""Decode QR codes and barcodes from images.

Engines, in the order they are tried:

1. **zxing-cpp** (``zxingcpp``): QR Code, Micro QR, rMQR, Data Matrix, Aztec, PDF417, MaxiCode,
   EAN/UPC, Code 128/39/93, ITF, Codabar, GS1 DataBar - with its own rotation, inversion and
   multi-scale search. Finds several codes per image.
2. **zbar** (``pyzbar``) through a ladder of cheap pre-processing passes - the fallback for
   photos zxing-cpp gives up on: downscaled copies (zbar likes ~1000-1600 px), illumination
   flattening, autocontrast, sharpening, an inverted copy and a 45 degree rotation.
3. **libdmtx** (``pylibdmtx``) for Data Matrix when nothing else was found.

Every pass is bounded by ``CODEFORGE_DECODE_BUDGET`` seconds in total and every result carries
the engine and pass that found it (``found_by``), which is also counted for ``/stats``.
HEIC/HEIF photos are opened through pillow-heif when it is installed.
"""

from __future__ import annotations

import io
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterator

from PIL import Image, ImageChops, ImageFilter, ImageOps

from app.config import settings
from app.engines.render import RenderError

log = logging.getLogger(__name__)

try:  # HEIC/HEIF support (iPhone photos)
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIF_SUPPORTED = True
except Exception:  # pragma: no cover - optional dependency
    HEIF_SUPPORTED = False

SUPPORTED = "PNG, JPEG, GIF, BMP, WEBP, TIFF" + (", HEIC" if HEIF_SUPPORTED else "")

# decode statistics for /stats (per uvicorn worker)
STATS: Counter[str] = Counter()
_stats_lock = threading.Lock()


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


def zxing_available() -> bool:
    try:
        import zxingcpp  # noqa: F401

        return True
    except Exception:
        return False


def engines() -> dict[str, bool]:
    return {"zxingcpp": zxing_available(), "zbar": zbar_available(), "libdmtx": dmtx_available(), "heif": HEIF_SUPPORTED}


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
            raise RenderError("HEIC/HEIF photos (iPhone default) cannot be read on this server; export or share the photo as JPEG") from None
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


# ------------------------------------------------------------------ zxing-cpp
# zxing-cpp format names -> the zbar style names the API has always used
_ZXING_TYPES = {
    "QR Code": "QRCODE", "QR Code Model 1": "QRCODE", "QR Code Model 2": "QRCODE", "Micro QR Code": "MICROQR", "rMQR Code": "RMQR",
    "Data Matrix": "DATAMATRIX", "Aztec": "AZTEC", "Aztec Code": "AZTEC", "Aztec Rune": "AZTECRUNE", "PDF417": "PDF417",
    "Compact PDF417": "PDF417", "MicroPDF417": "MICROPDF417", "MaxiCode": "MAXICODE",
    "EAN-13": "EAN13", "EAN-8": "EAN8", "EAN-2": "EAN2", "EAN-5": "EAN5", "UPC-A": "UPCA", "UPC-E": "UPCE", "ISBN": "ISBN",
    "Code 128": "CODE128", "Code 39": "CODE39", "Code 39 Extended": "CODE39", "Code 39 Standard": "CODE39", "Code 32": "CODE32",
    "Pharmazentralnummer": "PZN", "Code 93": "CODE93", "ITF": "I25", "ITF-14": "ITF14", "Codabar": "CODABAR",
    "DataBar": "DATABAR", "DataBar Omni": "DATABAR", "DataBar Stacked": "DATABAR", "DataBar Stacked Omni": "DATABAR",
    "DataBar Limited": "DATABAR_LTD", "DataBar Expanded": "DATABAR_EXP", "DataBar Expanded Stacked": "DATABAR_EXP",
    "Telepen": "TELEPEN", "Telepen Alpha": "TELEPEN", "Telepen Numeric": "TELEPEN", "DX Film Edge": "DXFILMEDGE",
}


# ---- format groups for the ``formats`` filter (API ``?formats=1d``, ``qr,ean13`` ...)
QR_TYPES = frozenset({"QRCODE", "MICROQR", "RMQR"})
MATRIX_TYPES = QR_TYPES | {"DATAMATRIX", "AZTEC", "AZTECRUNE", "PDF417", "MICROPDF417", "MAXICODE", "SQCODE"}
LINEAR_TYPES = frozenset({
    "EAN13", "EAN8", "EAN2", "EAN5", "UPCA", "UPCE", "ISBN", "ISBN10", "ISBN13", "CODE128", "CODE39", "CODE32", "PZN", "CODE93",
    "I25", "ITF14", "CODABAR", "DATABAR", "DATABAR_LTD", "DATABAR_EXP", "TELEPEN", "DXFILMEDGE",
})
FORMAT_GROUPS = {"qr": QR_TYPES, "2d": MATRIX_TYPES, "1d": LINEAR_TYPES}
_GROUP_ALIASES = {"matrix": "2d", "linear": "1d", "barcode": "1d", "barcodes": "1d", "1d-barcodes": "1d", "qrcode": "qr"}
_TYPE_ALIASES = {"ITF": "I25", "INTERLEAVED2OF5": "I25", "MICROQRCODE": "MICROQR", "RMQRCODE": "RMQR", "DATABAREXPANDED": "DATABAR_EXP",
                 "DATABAREXP": "DATABAR_EXP", "DATABARLIMITED": "DATABAR_LTD", "DATABARLTD": "DATABAR_LTD", "AZTECCODE": "AZTEC"}


def known_types() -> list[str]:
    return sorted(MATRIX_TYPES | LINEAR_TYPES)


def parse_formats(spec: str | None) -> frozenset[str] | None:
    """``None``/``""``/``all`` -> no filter; otherwise a comma list of groups (qr, 2d, 1d) and/or types (EAN13, CODE128 ...)."""
    if spec is None or not spec.strip():
        return None
    out: set[str] = set()
    for raw in spec.split(","):
        token = raw.strip().lower()
        if not token:
            continue
        if token in ("all", "any", "*"):
            return None
        token = _GROUP_ALIASES.get(token, token)
        if token in FORMAT_GROUPS:
            out |= FORMAT_GROUPS[token]
            continue
        name = raw.strip().upper().replace("-", "").replace(" ", "")
        name = _TYPE_ALIASES.get(name, name)
        if name not in MATRIX_TYPES and name not in LINEAR_TYPES:
            raise RenderError(f"unknown format {raw.strip()!r}: use all, qr, 2d, 1d or a type out of {', '.join(known_types())}")
        out.add(name)
    return frozenset(out) or None


def _zxing_symbols(img: Image.Image, found_by: str, *, try_harder: bool, scale: float = 1.0, offset: tuple[int, int] = (0, 0)) -> list[dict[str, Any]]:
    import zxingcpp

    out: list[dict[str, Any]] = []
    for r in zxingcpp.read_barcodes(img, try_rotate=try_harder, try_downscale=try_harder, try_invert=try_harder):
        if not r.valid or not r.text and not r.bytes:
            continue
        fmt = str(r.format)
        pos = r.position
        pts = [(pos.top_left.x, pos.top_left.y), (pos.top_right.x, pos.top_right.y), (pos.bottom_right.x, pos.bottom_right.y), (pos.bottom_left.x, pos.bottom_left.y)]
        pts = [(round(x / scale) + offset[0], round(y / scale) + offset[1]) for x, y in pts]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        out.append(
            {
                "type": _ZXING_TYPES.get(fmt, fmt.upper().replace(" ", "").replace("-", "")),
                "data": r.text or _decode_bytes(r.bytes),
                "quality": None,
                "rect": {"left": min(xs), "top": min(ys), "width": max(xs) - min(xs), "height": max(ys) - min(ys)},
                "polygon": [{"x": x, "y": y} for x, y in pts],
                "engine": "zxingcpp",
                "found_by": found_by,
                "format": fmt,
                "ec_level": r.ec_level or None,
                "symbology_identifier": r.symbology_identifier or None,
            }
        )
    return out


# ----------------------------------------------------------------------- zbar
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


# ------------------------------------------------------------------- pipeline
def decode_image(content: bytes, *, try_harder: bool = True, budget: float | None = None, formats: frozenset[str] | None = None) -> list[dict[str, Any]]:
    """Return every symbol found in the image (compatibility wrapper around :func:`decode_image_full`)."""
    return decode_image_full(content, try_harder=try_harder, budget=budget, formats=formats).symbols


def decode_image_full(content: bytes, *, try_harder: bool = True, budget: float | None = None, formats: frozenset[str] | None = None) -> DecodeResult:
    """Decode with every engine; stops at the first pass that finds something (of the wanted ``formats``)."""
    t0 = time.monotonic()
    budget = settings.decode_budget if budget is None else budget
    img = _open_image(content)
    result = DecodeResult(width=img.width, height=img.height)
    gray = img.convert("L")
    have_zxing, have_zbar, have_dmtx = zxing_available(), zbar_available(), dmtx_available()
    seen: set[tuple[str, str]] = set()
    skipped: set[str] = set()

    def wanted(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # symbols of other formats do not end the search: the wanted code may need a later pass
        if formats is None:
            return symbols
        skipped.update(s["type"] for s in symbols if s["type"] not in formats)
        return [s for s in symbols if s["type"] in formats]

    def add(symbols: list[dict[str, Any]]) -> None:
        for s in symbols:
            key = (s["type"], s["data"])
            if key not in seen:
                seen.add(key)
                result.symbols.append(s)

    def over_budget() -> bool:
        return result.passes > 0 and time.monotonic() - t0 > budget

    timed_out = False
    if have_zxing:
        # zxing-cpp searches rotated, inverted and downscaled copies itself; a sharpened copy helps with blurry photos
        passes: list[tuple[str, Image.Image]] = [("zxing/plain", gray)]
        if try_harder:
            passes.append(("zxing/sharpen", gray.filter(ImageFilter.UnsharpMask(radius=2, percent=180, threshold=2))))
            passes.append(("zxing/flat", ImageOps.autocontrast(_flatten(gray, max(8, min(gray.size) // 40)), cutoff=1)))
        for name, variant in passes:
            if over_budget():
                timed_out = True
                break
            result.passes += 1
            try:
                found = wanted(_zxing_symbols(variant, name, try_harder=try_harder))
            except Exception as exc:  # pragma: no cover
                log.warning("zxingcpp failed on %s: %s", name, exc)
                found = []
            if found:
                add(found)
                break

    if have_zbar and not result.symbols and not timed_out:
        for name, variant, scale in _variants(gray, try_harder):
            if over_budget():
                timed_out = True
                break
            result.passes += 1
            try:
                found = wanted(_zbar_symbols(variant, scale, "zbar/" + name))
            except Exception as exc:  # pragma: no cover - zbar internal assertion
                log.warning("zbar failed on pass %s: %s", name, exc)
                found = []
            if found:
                add(found)
                break

    # DataMatrix fallback: only spend the (slow) libdmtx time when nothing else was found
    if have_dmtx and not result.symbols and (formats is None or "DATAMATRIX" in formats):
        remaining = budget - (time.monotonic() - t0)
        if remaining > 0.3:
            result.passes += 1
            try:
                add(_dmtx_symbols(gray, int(min(remaining, 3.0) * 1000)))
            except Exception as exc:  # pragma: no cover
                log.warning("libdmtx failed: %s", exc)

    result.elapsed_ms = int((time.monotonic() - t0) * 1000)
    if not result.symbols:
        result.hints = _hints(img, have_zxing or have_zbar, have_zxing or have_dmtx, timed_out)
        if skipped:
            result.hints.insert(0, f"found {', '.join(sorted(skipped))}, which the formats filter excludes - decode without formats= to get it")
    with _stats_lock:
        STATS["images"] += 1
        STATS["symbols"] += len(result.symbols)
        STATS["found" if result.symbols else "nothing_found"] += 1
        for s in result.symbols:
            STATS["pass:" + s["found_by"]] += 1
            STATS["type:" + s["type"]] += 1
        if timed_out:
            STATS["timed_out"] += 1
    log.info("decode: %dx%d %d symbol(s) in %d ms after %d pass(es)%s", img.width, img.height, len(result.symbols), result.elapsed_ms, result.passes,
             " - " + ", ".join(f"{s['type']} via {s['found_by']}" for s in result.symbols) if result.symbols else "")
    return result


def decode_stats() -> dict[str, Any]:
    with _stats_lock:
        return {"engines": engines(), **dict(sorted(STATS.items()))}


def _hints(img: Image.Image, have_1d: bool, have_2d: bool, timed_out: bool) -> list[str]:
    hints = []
    longest = max(img.size)
    if longest < 200:
        hints.append("the image is very small; a QR code needs at least ~3 pixels per module to be readable")
    if longest > 3000:
        hints.append("large photo: make sure the code fills a good part of the frame and is in focus")
    if zxing_available():
        hints.append("readable symbologies: QR Code, Micro QR, rMQR, DataMatrix, Aztec, PDF417, MaxiCode, EAN/UPC, Code 128/39/93, ITF, Codabar, GS1 DataBar")
    else:
        hints.append("readable symbologies: QR Code, EAN/UPC, Code 128/39/93, ITF, Codabar, GS1 DataBar, PDF417" + (", DataMatrix" if dmtx_available() else ""))
    hints.append("try a sharper, evenly lit photo taken straight on, with some white space around the code")
    if timed_out:
        hints.append("the time budget for pre-processing passes ran out (CODEFORGE_DECODE_BUDGET)")
    if not have_1d:
        hints.append("no decoder library is installed on this server")
    return hints


def _decode_bytes(b: bytes) -> str:
    for enc in ("utf-8", "latin-1"):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            continue
    return b.hex()
