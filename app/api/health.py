"""Health and version endpoints (exempt from rate limiting)."""

from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import settings
from app.engines.barcode import ghostscript_available, symbologies
from app.engines.decoder import dmtx_available, zbar_available
from app.models import Health

router = APIRouter(tags=["meta"])


@router.get("/healthz", summary="Health check", response_model=Health)
def healthz():
    gs = ghostscript_available()
    return Health(
        status="ok" if gs else "degraded",
        version=__version__,
        ghostscript=gs,
        zbar=zbar_available(),
        libdmtx=dmtx_available(),
        symbologies=len(symbologies()),
        rate_limit=settings.rate_limit,
    )


@router.get("/version", summary="Version string")
def version():
    return {"name": "codeforge", "version": __version__}
