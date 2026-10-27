"""Versioned JSON API: /api/v1/..."""

from __future__ import annotations

import base64
import io
import time
import zipfile
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response
from starlette.datastructures import UploadFile

from app.config import settings
from app.engines import barcode as barcode_engine
from app.engines import qr as qr_engine
from app.engines.decoder import FORMAT_GROUPS, decode_image_full, dmtx_available, known_types, parse_formats, zbar_available
from app.engines.render import ALL_FORMATS, RenderError, Rendered, label_sheet
from app.models import (
    BarcodeParams,
    BatchRequest,
    BatchResponse,
    DecodeBase64Request,
    DecodeBatchItem,
    DecodeBatchResponse,
    DecodeFormats,
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
    if not barcode_engine.is_image_format(format):
        # e.g. Aztec "format=full" is a BWIPP encoder option, not an output format
        options["format"] = format
        format = "png"
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
def symbologies(request: Request, q: str | None = Query(None, description="filter by id or description")):
    items = list(barcode_engine.symbologies().values())
    if q:
        needle = q.lower()
        items = [s for s in items if needle in s["id"] or needle in s["description"].lower()]
    # the list only changes with a new release / BWIPP version: let browsers revalidate cheaply
    from app import __version__
    from app.engines.bwipp_rules import bwipp_version

    etag = '"%s-%s-%s"' % (__version__, bwipp_version(), q or "")
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return JSONResponse(items, headers={"ETag": etag, "Cache-Control": "public, max-age=3600"})


@router.get("/symbologies/{bcid}", tags=["barcode"], summary="One symbology", response_model=Symbology)
def symbology(bcid: str):
    item = barcode_engine.symbologies().get(bcid.lower())
    if not item:
        raise HTTPException(status_code=404, detail=f"unknown symbology '{bcid}'")
    return item


@router.get("/formats", tags=["meta"], summary="Supported output formats")
def formats():
    return {"qr": ALL_FORMATS, "barcode": [f for f in ALL_FORMATS if f != "svg"]}


@router.get("/limits", tags=["meta"], summary="Server limits (upload size, data length, scale, batch items)")
def limits():
    return {
        "max_upload_bytes": settings.max_upload_bytes,
        "max_data_length": settings.max_data_length,
        "max_scale": settings.max_scale,
        "max_border": settings.max_border,
        "max_image_pixels": settings.max_image_pixels,
        "max_batch_items": settings.max_batch_items,
        "decode_budget_seconds": settings.decode_budget,
        "decode_batch_max_files": settings.decode_batch_max_files,
        "decode_batch_budget_seconds": settings.decode_batch_budget,
        "rate_limit": settings.rate_limit,
    }


# ----------------------------------------------------------------------- decode
DECODE_DESCRIPTION = """Reads every QR code / barcode in an image and returns the decoded text, symbol type and position.

Three ways to send the image:

* **multipart/form-data** with a field named `file` (HTML forms, `curl -F file=@photo.jpg`)
* the **raw image as the request body** with its `Content-Type` (`curl --data-binary @photo.jpg -H 'Content-Type: image/jpeg'`)
* **base64** JSON via `POST /api/v1/decode/base64`

Many images at once: `POST /api/v1/decode/batch`.

**Symbologies:** 2D codes (QR, Micro QR, rMQR, DataMatrix, Aztec, PDF417, MicroPDF417, MaxiCode) and 1D barcodes
(EAN-13/8, UPC-A/E, ISBN, Code 128/39/93, ITF/ITF-14, Codabar, GS1 DataBar, PZN, Telepen ...). Restrict the search with
`formats`: `qr`, `2d`, `1d`, single types (`ean13,code128`) or combinations (`qr,1d`); `GET /api/v1/decode/formats` lists them.
Other symbols in the image are ignored and do not end the search.

Photos are pre-processed in several passes (zxing-cpp with rotation/inversion/downscaling, sharpening, illumination
flattening, the zbar ladder, libdmtx) until something is found or the time budget runs out; `found_by` tells which pass
succeeded. Input formats: PNG, JPEG, GIF, BMP, WEBP, TIFF, HEIC/HEIF (iPhone).
"""


def _formats(spec: str | None) -> frozenset[str] | None:
    try:
        return parse_formats(spec)
    except RenderError as exc:
        raise _bad(exc)


def _decode_response(content: bytes, try_harder: bool, formats: frozenset[str] | None = None) -> dict[str, Any]:
    try:
        return decode_image_full(content, try_harder=try_harder, formats=formats).as_dict()
    except RenderError as exc:
        raise _bad(exc)


@router.post(
    "/decode",
    tags=["decode"],
    summary="Decode QR codes / barcodes from an uploaded image",
    description=DECODE_DESCRIPTION,
    response_model=DecodeResponse,
    openapi_extra={
        "requestBody": {
            "content": {
                "multipart/form-data": {"schema": {"type": "object", "properties": {"file": {"type": "string", "format": "binary"}}, "required": ["file"]}},
                "image/jpeg": {"schema": {"type": "string", "format": "binary"}},
                "image/png": {"schema": {"type": "string", "format": "binary"}},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            },
            "required": True,
        }
    },
)
async def decode_upload(
    request: Request,
    try_harder: bool = Query(True, description="run the pre-processing ladder (slower, finds more); false = one plain pass"),
    formats: str | None = Query(None, description="restrict to qr, 2d, 1d and/or types like ean13,code128 (default: all)"),
):
    _require_decoder()
    wanted = _formats(formats)
    content_type = request.headers.get("content-type", "")
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > settings.max_upload_bytes + 4096:
        raise HTTPException(status_code=413, detail=f"upload is larger than {settings.max_upload_bytes // (1024 * 1024)} MB (CODEFORGE_MAX_UPLOAD_BYTES)")
    if content_type.startswith(("multipart/form-data", "application/x-www-form-urlencoded")):
        form = await request.form()
        upload = form.get("file") or form.get("image") or next((v for v in form.values() if isinstance(v, UploadFile)), None)
        if not isinstance(upload, UploadFile):
            raise HTTPException(status_code=400, detail="multipart form needs a file field named 'file'")
        content = await upload.read()
    else:
        content = await request.body()
    # decoding is CPU bound: run it in the threadpool so the event loop keeps serving other requests
    return await run_in_threadpool(_decode_response, content, try_harder, wanted)


@router.post("/decode/base64", tags=["decode"], summary="Decode from a base64 encoded image", description="Same as `/decode`, image as base64 string or data URI in JSON.", response_model=DecodeResponse)
def decode_b64(body: DecodeBase64Request, try_harder: bool = Query(True), formats: str | None = Query(None, description="restrict to qr, 2d, 1d and/or types like ean13,code128 (default: all)")):
    _require_decoder()
    wanted = _formats(formats)
    payload = body.image.split(",", 1)[1] if body.image.startswith("data:") and "," in body.image else body.image
    try:
        content = base64.b64decode(payload, validate=False)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="image is not valid base64")
    return _decode_response(content, try_harder, wanted)


@router.get("/decode/formats", tags=["decode"], summary="Symbol types and groups for the formats filter", response_model=DecodeFormats)
def decode_formats():
    return {"groups": {k: sorted(v) for k, v in FORMAT_GROUPS.items()}, "types": known_types()}


def _decode_batch(files: list[tuple[str | None, bytes]], try_harder: bool, wanted: frozenset[str] | None) -> dict[str, Any]:
    t0 = time.monotonic()
    items: list[dict[str, Any]] = []
    for idx, (name, content) in enumerate(files):
        remaining = settings.decode_batch_budget - (time.monotonic() - t0)
        if remaining < 0.5:
            items.append(DecodeBatchItem(index=idx, filename=name, size=len(content), ok=False, error="batch time budget exhausted - send this file again").model_dump())
            continue
        try:
            r = decode_image_full(content, try_harder=try_harder, budget=min(settings.decode_budget, remaining), formats=wanted).as_dict()
            items.append(DecodeBatchItem(index=idx, filename=name, size=len(content), ok=True, **r).model_dump())
        except RenderError as exc:
            items.append(DecodeBatchItem(index=idx, filename=name, size=len(content), ok=False, error=str(exc)).model_dump())
    return DecodeBatchResponse(
        files=len(items),
        decoded=sum(1 for i in items if i["ok"]),
        with_codes=sum(1 for i in items if i["count"]),
        symbols=sum(i["count"] for i in items),
        elapsed_ms=int((time.monotonic() - t0) * 1000),
        items=items,
    ).model_dump()


@router.post(
    "/decode/batch",
    tags=["decode"],
    summary="Decode many images in one request",
    description="multipart/form-data with one or more files (any field name, e.g. `curl -F file=@a.jpg -F file=@b.png`). "
    "Every file gets its own result in `items`, in upload order; unreadable files are reported with `ok: false` and do not fail "
    "the batch. Limits: `decode_batch_max_files` files, `max_upload_bytes` per file and a total time budget "
    "(`decode_batch_budget_seconds`, see `/api/v1/limits`); files after the budget are returned with an error to resend them.",
    response_model=DecodeBatchResponse,
    openapi_extra={
        "requestBody": {
            "content": {"multipart/form-data": {"schema": {"type": "object", "properties": {"file": {"type": "array", "items": {"type": "string", "format": "binary"}}}, "required": ["file"]}}},
            "required": True,
        }
    },
)
async def decode_batch(
    request: Request,
    try_harder: bool = Query(True, description="run the pre-processing ladder per image"),
    formats: str | None = Query(None, description="restrict to qr, 2d, 1d and/or types like ean13,code128 (default: all)"),
):
    _require_decoder()
    wanted = _formats(formats)
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise HTTPException(status_code=400, detail="send the images as multipart/form-data (curl -F file=@a.jpg -F file=@b.jpg)")
    max_total = settings.max_upload_bytes * settings.decode_batch_max_files
    length = request.headers.get("content-length")
    if length and length.isdigit() and int(length) > max_total + 65536:
        raise HTTPException(status_code=413, detail=f"batch is larger than {max_total // (1024 * 1024)} MB")
    form = await request.form(max_files=settings.decode_batch_max_files + 1)
    uploads = [v for _, v in form.multi_items() if isinstance(v, UploadFile)]
    if not uploads:
        raise HTTPException(status_code=400, detail="no files in the request (multipart fields with a filename)")
    if len(uploads) > settings.decode_batch_max_files:
        raise HTTPException(status_code=400, detail=f"at most {settings.decode_batch_max_files} files per batch")
    files: list[tuple[str | None, bytes]] = []
    for up in uploads:
        content = await up.read()
        if len(content) > settings.max_upload_bytes:
            raise HTTPException(status_code=413, detail=f"{up.filename}: larger than {settings.max_upload_bytes // (1024 * 1024)} MB")
        files.append((up.filename, content))
    return await run_in_threadpool(_decode_batch, files, try_harder, wanted)


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
