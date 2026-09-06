"""Versioned JSON API: /api/v1/..."""

from __future__ import annotations

import base64
import io
import zipfile
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from app.config import settings
from app.engines import barcode as barcode_engine
from app.engines import qr as qr_engine
from app.engines.decoder import decode_image, dmtx_available, zbar_available
from app.engines.render import ALL_FORMATS, RenderError, Rendered, label_sheet
from app.models import (
    BarcodeParams,
    BatchRequest,
    BatchResponse,
    DecodeBase64Request,
    DecodeResponse,
    QrParams,
    RenderedItem,
    Symbology,
)

router = APIRouter(prefix="/api/v1")

IMAGE_RESPONSES = {
    200: {"content": {"image/png": {}, "image/svg+xml": {}, "image/jpeg": {}, "application/pdf": {}, "application/json": {}}, "description": "the image, or JSON with base64 when output=base64"},
    400: {"description": "invalid parameters", "content": {"application/json": {"example": {"detail": "data must not be empty"}}}},
}


def _deliver(rendered: Rendered, output: str, download_name: str | None = None) -> Response:
    if output in ("base64", "json"):
        return JSONResponse(
            {"mime": rendered.mime, "format": rendered.format, "width": rendered.width, "height": rendered.height, "base64": rendered.base64, "data_uri": rendered.data_uri}
        )
    headers = {"Cache-Control": "public, max-age=86400"}
    if download_name:
        headers["Content-Disposition"] = f'attachment; filename="{download_name}.{rendered.format}"'
    return Response(rendered.content, media_type=rendered.mime, headers=headers)


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


# --------------------------------------------------------------------------- QR
@router.get("/qr", tags=["qr"], summary="Render a QR code", response_class=Response, responses=IMAGE_RESPONSES)
def qr_get(
    data: str = Query(..., description="Text or URL to encode"),
    error: str = Query("M", description="L, M, Q or H"),
    version: int | None = Query(None, ge=1, le=40),
    scale: int = Query(10, ge=1, description="pixels per module"),
    border: int = Query(4, ge=0, description="quiet zone in modules"),
    fg: str = Query("#000000"),
    bg: str = Query("#ffffff"),
    format: str = Query("png", description=", ".join(ALL_FORMATS)),
    micro: bool = Query(False),
    output: str = Query("image", description="image or base64 (JSON)"),
    download: bool = Query(False, description="send as attachment"),
):
    try:
        rendered = qr_engine.make_qr(data, error=error, version=version, scale=scale, border=border, fg=fg, bg=bg, fmt=format, micro=micro)
    except RenderError as exc:
        raise _bad(exc)
    return _deliver(rendered, output, "qrcode" if download else None)


@router.post("/qr", tags=["qr"], summary="Render a QR code (JSON body)", response_class=Response, responses=IMAGE_RESPONSES)
def qr_post(params: QrParams, output: str = Query("image", description="image or base64 (JSON)")):
    try:
        rendered = qr_engine.make_qr(
            params.data, error=params.error, version=params.version, scale=params.scale, border=params.border,
            fg=params.fg, bg=params.bg, fmt=params.format, micro=params.micro,
        )
    except RenderError as exc:
        raise _bad(exc)
    return _deliver(rendered, output)


@router.get("/qr/info", tags=["qr"], summary="Symbol metadata without rendering")
def qr_info(data: str = Query(...), error: str = Query("M"), version: int | None = Query(None, ge=1, le=40), micro: bool = Query(False)):
    try:
        return qr_engine.qr_info(data, error=error, version=version, micro=micro)
    except (RenderError, ValueError) as exc:
        raise _bad(exc)


# ---------------------------------------------------------------------- barcode
@router.get(
    "/barcode",
    tags=["barcode"],
    summary="Render a 1D/2D barcode (any BWIPP symbology)",
    description="Known query parameters are documented below; **every other query parameter is passed to BWIPP as an encoder option** "
    "(e.g. `includetext`, `textsize=12`, `eclevel=H`, `custinfoenc=character`, `barcolor=FF0000`).",
    response_class=Response,
    responses=IMAGE_RESPONSES,
)
def barcode_get(
    request: Request,
    bcid: str = Query(..., description="symbology id, see /api/v1/symbologies"),
    text: str = Query(..., description="data to encode"),
    scale: int = Query(2, ge=1),
    scaleX: int | None = Query(None, ge=1),
    scaleY: int | None = Query(None, ge=1),
    rotate: str = Query("N", description="N, R, L or I"),
    paddingwidth: int = Query(1, ge=0),
    paddingheight: int = Query(1, ge=0),
    format: str = Query("png", description="png, jpg, webp, gif, bmp, pdf"),
    output: str = Query("image", description="image or base64 (JSON)"),
    download: bool = Query(False),
):
    known = {"bcid", "text", "scale", "scaleX", "scaleY", "rotate", "paddingwidth", "paddingheight", "format", "output", "download"}
    options: dict[str, Any] = {k: v for k, v in request.query_params.items() if k not in known}
    options.update({"scale": scale, "scalex": scaleX, "scaley": scaleY, "rotate": rotate, "paddingwidth": paddingwidth, "paddingheight": paddingheight})
    try:
        rendered = barcode_engine.make_barcode(bcid, text, options, fmt=format)
    except RenderError as exc:
        raise _bad(exc)
    return _deliver(rendered, output, bcid if download else None)


@router.post("/barcode", tags=["barcode"], summary="Render a barcode (JSON body)", response_class=Response, responses=IMAGE_RESPONSES)
def barcode_post(params: BarcodeParams, output: str = Query("image", description="image or base64 (JSON)")):
    try:
        rendered = barcode_engine.make_barcode(params.bcid, params.text, params.options, fmt=params.format)
    except RenderError as exc:
        raise _bad(exc)
    return _deliver(rendered, output)


@router.get("/symbologies", tags=["barcode"], summary="List all supported symbologies", response_model=list[Symbology])
def symbologies(q: str | None = Query(None, description="filter by id or description")):
    items = list(barcode_engine.symbologies().values())
    if q:
        needle = q.lower()
        items = [s for s in items if needle in s["id"] or needle in s["description"].lower()]
    return items


@router.get("/symbologies/{bcid}", tags=["barcode"], summary="One symbology", response_model=Symbology)
def symbology(bcid: str):
    item = barcode_engine.symbologies().get(bcid.lower())
    if not item:
        raise HTTPException(status_code=404, detail=f"unknown symbology '{bcid}'")
    return item


@router.get("/formats", tags=["meta"], summary="Supported output formats")
def formats():
    return {"qr": ALL_FORMATS, "barcode": [f for f in ALL_FORMATS if f != "svg"]}


# ----------------------------------------------------------------------- decode
@router.post("/decode", tags=["decode"], summary="Decode QR codes / barcodes from an uploaded image", response_model=DecodeResponse)
async def decode_upload(file: UploadFile = File(..., description="PNG, JPEG, GIF, BMP or WEBP"), try_harder: bool = Query(True)):
    _require_decoder()
    content = await file.read()
    try:
        symbols = decode_image(content, try_harder=try_harder)
    except RenderError as exc:
        raise _bad(exc)
    return {"count": len(symbols), "symbols": symbols}


@router.post("/decode/base64", tags=["decode"], summary="Decode from a base64 encoded image", response_model=DecodeResponse)
def decode_b64(body: DecodeBase64Request, try_harder: bool = Query(True)):
    _require_decoder()
    payload = body.image.split(",", 1)[1] if body.image.startswith("data:") and "," in body.image else body.image
    try:
        content = base64.b64decode(payload, validate=False)
        symbols = decode_image(content, try_harder=try_harder)
    except (RenderError, ValueError) as exc:
        raise _bad(exc)
    return {"count": len(symbols), "symbols": symbols}


def _require_decoder() -> None:
    if not settings.enable_decoder:
        raise HTTPException(status_code=404, detail="decoder is disabled")
    if not (zbar_available() or dmtx_available()):
        raise HTTPException(status_code=503, detail="decoder unavailable: libzbar is not installed")


# ------------------------------------------------------------------------ batch
@router.post(
    "/batch",
    tags=["batch"],
    summary="Render many codes at once (JSON with base64, ZIP or PDF label sheet)",
    response_class=Response,
    responses={200: {"content": {"application/json": {}, "application/zip": {}, "application/pdf": {}}}},
)
def batch(req: BatchRequest = Body(...)):
    if not settings.enable_batch:
        raise HTTPException(status_code=404, detail="batch is disabled")
    if len(req.items) > settings.max_batch_items:
        raise HTTPException(status_code=400, detail=f"at most {settings.max_batch_items} items per batch")
    fmt = "png" if req.output == "pdf" else req.format
    results: list[RenderedItem] = []
    rendered_ok: list[tuple[int, Rendered, str]] = []
    for idx, item in enumerate(req.items):
        try:
            if item.type == "qr":
                if not item.data:
                    raise RenderError("data is required for qr items")
                r = qr_engine.make_qr(item.data, error=item.error, version=item.version, scale=item.scale, border=item.border, fg=item.fg, bg=item.bg, fmt=fmt, micro=item.micro)
                caption = item.caption if item.caption is not None else item.data
            else:
                if not item.bcid or not item.text:
                    raise RenderError("bcid and text are required for barcode items")
                r = barcode_engine.make_barcode(item.bcid, item.text, item.options, fmt=fmt)
                caption = item.caption if item.caption is not None else item.text
            rendered_ok.append((idx, r, caption))
            results.append(RenderedItem(index=idx, ok=True, mime=r.mime, format=r.format, width=r.width, height=r.height, base64=r.base64 if req.output == "json" else None, data_uri=r.data_uri if req.output == "json" else None))
        except RenderError as exc:
            results.append(RenderedItem(index=idx, ok=False, error=str(exc)))

    if req.output == "json":
        return JSONResponse(BatchResponse(count=len(results), succeeded=len(rendered_ok), failed=len(results) - len(rendered_ok), items=results).model_dump())

    if req.output == "zip":
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for idx, r, _caption in rendered_ok:
                zf.writestr(f"code-{idx + 1:04d}.{r.format}", r.content)
            errors = [f"{it.index + 1}: {it.error}" for it in results if not it.ok]
            if errors:
                zf.writestr("errors.txt", "\n".join(errors) + "\n")
        return Response(buf.getvalue(), media_type="application/zip", headers={"Content-Disposition": 'attachment; filename="codes.zip"'})

    # pdf label sheet
    if not rendered_ok:
        raise HTTPException(status_code=400, detail="no item could be rendered: " + "; ".join(it.error or "" for it in results))
    try:
        pdf = label_sheet(
            [(r.content, caption) for _idx, r, caption in rendered_ok],
            page=req.sheet.page, columns=req.sheet.columns, rows=req.sheet.rows, margin_mm=req.sheet.margin_mm,
            gap_mm=req.sheet.gap_mm, captions=req.sheet.captions, title=req.sheet.title,
        )
    except RenderError as exc:
        raise _bad(exc)
    return Response(pdf, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="codes.pdf"'})
