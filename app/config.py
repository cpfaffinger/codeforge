"""Runtime configuration, read from environment variables (prefix CODEFORGE_) or a .env file."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEFORGE_", env_file=".env", extra="ignore")

    # server
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 2
    log_level: str = "info"
    base_url: str = Field("", description="Public base URL used in generated links, e.g. https://qr.example.com. Empty = relative links.")
    cors_origins: str = Field("*", description="Comma separated list of allowed CORS origins, empty to disable CORS headers.")

    # abuse protection
    rate_limit: str = Field("300/minute", description="Requests per client IP and uvicorn worker process (the counter is in-memory per process, so the effective limit is this value times CODEFORGE_WORKERS), e.g. 300/minute, 2000/hour, 0 = disabled. The showcase alone loads ~110 images.")
    trust_proxy: bool = Field(True, description="Use X-Forwarded-For / X-Real-IP to identify the client (behind nginx).")
    max_data_length: int = Field(4000, description="Maximum length of the encoded text.")
    max_scale: int = Field(30, description="Maximum scale / box size.")
    max_border: int = Field(50, description="Maximum border / padding in modules or pixels.")
    max_image_pixels: int = Field(30_000_000, description="Maximum pixels of a generated or uploaded image.")
    max_batch_items: int = Field(200, description="Maximum items in one batch request.")
    max_upload_bytes: int = Field(16 * 1024 * 1024, description="Maximum upload size for the decoder (phone photos are 3-12 MB).")
    decode_budget: float = Field(8.0, description="Seconds the decoder may spend on pre-processing passes of one image.")
    decode_batch_max_files: int = Field(20, description="Maximum images in one /api/v1/decode/batch request.")
    decode_batch_budget: float = Field(60.0, description="Seconds one decode batch may take in total (stay below proxy timeouts, Cloudflare: 100 s).")

    # barcode engine
    gs_workers: int = Field(2, description="Persistent Ghostscript processes per uvicorn worker (0 = start gs per image via treepoem, slow).")
    gs_timeout: float = Field(15.0, description="Seconds a single Ghostscript job may take before the worker is restarted.")
    cache_max_bytes: int = Field(64 * 1024 * 1024, description="In-memory cache for rendered barcodes per uvicorn worker, 0 = off.")

    # features
    enable_decoder: bool = True
    enable_batch: bool = True
    enable_playground: bool = True

    # legacy behaviour
    legacy_error_status: int = Field(400, description="HTTP status for invalid legacy barcode requests (the original service used 500).")


settings = Settings()
