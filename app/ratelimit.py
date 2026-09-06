"""Simple in-memory sliding-window rate limit per client IP (good enough for a single container)."""

from __future__ import annotations

import re
import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

_UNITS = {"second": 1, "sec": 1, "s": 1, "minute": 60, "min": 60, "m": 60, "hour": 3600, "h": 3600, "day": 86400, "d": 86400}
_SPEC_RE = re.compile(r"^\s*(\d+)\s*(?:/|per)\s*(\d*)\s*([a-zA-Z]+)\s*$")
EXEMPT_PREFIXES = ("/healthz", "/version", "/static/", "/docs", "/redoc", "/openapi.json", "/favicon.ico")


def parse_limit(spec: str | None) -> tuple[int, int] | None:
    """'120/minute' -> (120, 60); '10 per 5 minutes' -> (10, 300); '0' / '' -> None (disabled)."""
    if not spec or spec.strip() in ("0", "off", "none", "false"):
        return None
    m = _SPEC_RE.match(spec)
    if not m:
        raise ValueError(f"invalid rate limit '{spec}', use e.g. 120/minute")
    count, mult, unit = int(m.group(1)), int(m.group(2) or 1), m.group(3).lower().rstrip("s") or "s"
    unit = unit if unit in _UNITS else unit + "s"
    if unit not in _UNITS and unit[:-1] in _UNITS:
        unit = unit[:-1]
    if unit not in _UNITS:
        raise ValueError(f"unknown time unit in rate limit '{spec}'")
    return count, mult * _UNITS[unit]


class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def check(self, key: str) -> tuple[bool, int, int]:
        """Returns (allowed, remaining, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_sweep > self.window:
                self._sweep(now)
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False, 0, int(self.window - (now - q[0])) + 1
            q.append(now)
            return True, self.limit - len(q), 0

    def _sweep(self, now: float) -> None:
        self._last_sweep = now
        for key in [k for k, q in self._hits.items() if not q or now - q[-1] >= self.window]:
            del self._hits[key]


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
        real = request.headers.get("x-real-ip")
        if real:
            return real.strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, spec: str, trust_proxy: bool = True):
        super().__init__(app)
        parsed = parse_limit(spec)
        self.limiter = RateLimiter(*parsed) if parsed else None
        self.trust_proxy = trust_proxy

    async def dispatch(self, request: Request, call_next):
        if self.limiter is None or request.url.path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)
        allowed, remaining, retry = self.limiter.check(client_ip(request, self.trust_proxy))
        if not allowed:
            return JSONResponse(
                {"detail": "rate limit exceeded", "retry_after": retry},
                status_code=429,
                headers={"Retry-After": str(retry), "X-RateLimit-Limit": str(self.limiter.limit), "X-RateLimit-Remaining": "0"},
            )
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(self.limiter.limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        return response
