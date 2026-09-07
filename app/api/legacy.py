"""Legacy-compatible routes.

* ``GET /qr?data=...``            - pjanczyk/qr-code-generator image endpoint (qr.<host>)
* ``GET /?bcid=...&text=...``     - padiazg/barcode-generator (bwip-js) image endpoint (barcode.<host>)
* ``GET /live``                   - old bwip-js demo page -> redirect to the playground

Both legacy front pages (``/``) are replaced by the playground; ``/`` with ``bcid``/``text``
query parameters still returns the barcode image so existing integrations keep working.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, RedirectResponse, Response

from app.config import settings
from app.engines.barcode import make_barcode
from app.engines.qr import make_qr
from app.engines.render import RenderError

router = APIRouter(tags=["legacy"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _no_cache_image(rendered) -> Response:
    return Response(content=rendered.content, media_type=rendered.mime, headers={"Cache-Control": "public, max-age=86400"})


@router.get(
    "/qr",
    summary="Legacy QR image (pjanczyk/qr-code-generator compatible)",
    response_class=Response,
    responses={200: {"content": {"image/png": {}, "image/svg+xml": {}}, "description": "QR code image"}, 400: {"description": "invalid parameters"}},
)
def legacy_qr(
    data: str = Query(..., description="Text to encode"),
    format: str | None = Query(None, description="PNG or SVG (case-insensitive); jpg/webp/pdf also accepted"),
    ec: str | None = Query(None, description="LOW, MEDIUM, QUARTILE, HIGH (or L/M/Q/H)"),
    version: str | None = Query(None, description="1-40, empty = automatic"),
    fg: str | None = Query(None, description="foreground colour #RRGGBB"),
    bg: str | None = Query(None, description="background colour #RRGGBB"),
    box: str | None = Query(None, description="pixels per module (default 10)"),
    border: str | None = Query(None, description="quiet zone in modules (default 4)"),
):
    try:
        rendered = make_qr(data, error=ec, version=version or None, scale=box or None, border=border or None, fg=fg, bg=bg, fmt=format)
    except RenderError as exc:
        return PlainTextResponse(f"QrCodeGenerator: {exc}", status_code=400)
    return _no_cache_image(rendered)


@router.get("/live", include_in_schema=False)
def legacy_live():
    return RedirectResponse("/?tab=barcode", status_code=302)


@router.get("/showcase", include_in_schema=False, summary="Gallery of every supported symbology")
def showcase():
    return FileResponse(STATIC_DIR / "showcase.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


@router.get("/scan", include_in_schema=False, summary="Live camera scanner")
def scan():
    return FileResponse(STATIC_DIR / "scan.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


@router.get(
    "/",
    summary="Playground, or legacy barcode image when bcid/text are given (bwip-js compatible)",
    response_class=Response,
    responses={
        200: {"content": {"text/html": {}, "image/png": {}, "image/jpeg": {}, "text/plain": {}}, "description": "playground HTML, or the barcode image"},
        400: {"description": "invalid barcode parameters (text/plain, 'BarcodeGenerator: ...')"},
        404: {"description": "bcid or text missing (text/plain, like the original service)"},
    },
)
def root(request: Request):
    params = dict(request.query_params)
    if "bcid" in params or "text" in params:
        return legacy_barcode(params)
    if not settings.enable_playground:
        return RedirectResponse("/docs", status_code=302)
    return FileResponse(STATIC_DIR / "index.html", media_type="text/html", headers={"Cache-Control": "no-cache"})


def legacy_barcode(params: dict[str, str]) -> Response:
    """Exact behaviour of padiazg/barcode-generator: query string = bwip-js options plus format/base64."""
    bcid = params.pop("bcid", None)
    text = params.pop("text", None)
    if not bcid or text is None or text == "":
        return PlainTextResponse("BarcodeGenerator: Missing bcid or text parameter", status_code=404)
    want_base64 = params.pop("base64", "false").strip().lower() == "true"
    fmt = params.pop("format", "png").strip().lower() or "png"
    # legacy defaults applied by the old service
    options: dict[str, str | bool] = dict(params)
    options.setdefault("backgroundcolor", "FFFFFF")
    options.setdefault("padding", "1")
    options.setdefault("paddingwidth", options.get("padding", "1"))
    options.setdefault("paddingheight", options.get("padding", "1"))
    options.setdefault("guardwhitespace", True)
    try:
        rendered = make_barcode(bcid, text, options, fmt=fmt)
    except RenderError as exc:
        return PlainTextResponse(f"BarcodeGenerator: {exc}", status_code=settings.legacy_error_status)
    if want_base64:
        return PlainTextResponse(rendered.base64, status_code=200)
    return _no_cache_image(rendered)
