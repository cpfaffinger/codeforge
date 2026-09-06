"""codeforge - FastAPI application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api import health, legacy, v1
from app.config import settings
from app.engines.render import RenderError
from app.ratelimit import RateLimitMiddleware

DESCRIPTION = """
**codeforge** renders QR codes and more than 100 1D/2D barcode symbologies (EAN, UPC, Code 128,
GS1, DataMatrix, PDF417, Aztec, Australia Post, USPS ...) and decodes them from images.

* `/api/v1/qr` - QR codes as PNG, SVG, JPEG, WEBP or PDF
* `/api/v1/barcode` - every BWIPP symbology, encoder options are passed through
* `/api/v1/decode` - read codes from an uploaded image
* `/api/v1/batch` - many codes at once as JSON, ZIP or a PDF label sheet
* `/qr` and `/?bcid=` - drop-in replacements for the legacy QR and bwip-js barcode services
* `/` - interactive playground
"""

TAGS = [
    {"name": "qr", "description": "QR codes (segno)."},
    {"name": "barcode", "description": "1D/2D barcodes rendered by BWIPP (same engine as bwip-js), symbology ids are BWIPP encoder names."},
    {"name": "decode", "description": "Read QR codes and barcodes from images (zbar)."},
    {"name": "batch", "description": "Bulk rendering and PDF label sheets."},
    {"name": "legacy", "description": "Compatibility with the replaced services."},
    {"name": "meta", "description": "Health, version, formats."},
]

STATIC_DIR = Path(__file__).resolve().parent / "static"


def create_app() -> FastAPI:
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = FastAPI(
        title="codeforge",
        version=__version__,
        description=DESCRIPTION,
        openapi_tags=TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        license_info={"name": "MIT", "url": "https://opensource.org/licenses/MIT"},
        contact={"name": "codeforge on GitHub", "url": "https://github.com/cpfaffinger/codeforge"},
    )

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["*"],
        )
    app.add_middleware(RateLimitMiddleware, spec=settings.rate_limit, trust_proxy=settings.trust_proxy)

    @app.exception_handler(RenderError)
    async def render_error(_request: Request, exc: RenderError):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    app.include_router(health.router)
    app.include_router(v1.router)
    app.include_router(legacy.router)  # last: it owns "/"

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon():
        from fastapi.responses import FileResponse

        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    return app


app = create_app()
