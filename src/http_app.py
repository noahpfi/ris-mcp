"""rate limiting + uvicorn runner, kept out of server.py -> that stays tool surface only"""
from __future__ import annotations

import logging
import time

from cachetools import TTLCache
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from . import config
from . import ris_client as rc

logger = logging.getLogger(__name__)

HEALTH_PATH = "/health"


class RateLimitMiddleware:
    """per-IP bucket, pure ASGI, TTLCache vs IP-driven leak; only protected_path, landing page never throttled"""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limit: int,
        window: int,
        max_ips: int,
        protected_path: str,
    ) -> None:
        self.app = app
        self.limit = float(limit)
        self.window = float(window)
        self.refill_per_second = float(limit) / float(window) if limit else 0.0
        self.protected_path = protected_path.rstrip("/")
        # ttl = 2x window -> no eviction mid-throttle
        self._buckets: TTLCache = TTLCache(maxsize=max_ips, ttl=window * 2)

    def _is_protected(self, path: str) -> bool:
        stripped = path.rstrip("/")
        return stripped == self.protected_path or stripped.startswith(self.protected_path + "/")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.limit:
            await self.app(scope, receive, send)
            return
        if not self._is_protected(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        if self._allow(_client_ip(scope)):
            await self.app(scope, receive, send)
            return

        retry_after = str(int(self.window))
        response = JSONResponse(
            {
                "error": "rate_limited",
                "detail": f"Limit is {int(self.limit)} requests per {int(self.window)}s.",
            },
            status_code=429,
            headers={"Retry-After": retry_after},
        )
        await response(scope, receive, send)

    def _allow(self, ip: str) -> bool:
        now = time.monotonic()
        tokens, updated = self._buckets.get(ip, (self.limit, now))
        tokens = min(self.limit, tokens + (now - updated) * self.refill_per_second)
        if tokens < 1.0:
            self._buckets[ip] = (tokens, now)
            return False
        self._buckets[ip] = (tokens - 1.0, now)
        return True


def _client_ip(scope: Scope) -> str:
    """prefer CF-Connecting-IP; port unpublished -> tunnel only route, header unforgeable; socket peer locally"""
    headers = {key: value for key, value in scope.get("headers", [])}
    forwarded = headers.get(b"cf-connecting-ip") or headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def build_app(mcp: FastMCP) -> Starlette:
    app = mcp.streamable_http_app()

    if config.STATIC_DIR is not None:
        if not config.STATIC_DIR.is_dir():
            raise ValueError(
                f"RIS_STATIC_DIR points at {config.STATIC_DIR}, which is not a directory. "
                "Build the site with `npm run build` in website/, or unset the variable."
            )
        # appended last, catch-all; Starlette matches in order -> must not shadow /mcp or /health
        app.router.routes.append(
            Mount("/", app=StaticFiles(directory=config.STATIC_DIR, html=True), name="site")
        )

    app.add_middleware(
        RateLimitMiddleware,
        limit=config.RATE_LIMIT,
        window=config.RATE_WINDOW,
        max_ips=config.RATE_MAX_IPS,
        protected_path=mcp.settings.streamable_http_path,
    )
    return app


async def serve(mcp: FastMCP) -> None:
    """releases upstream client after shutdown"""
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            build_app(mcp),
            host=config.HOST,
            port=config.PORT,
            log_level=config.LOG_LEVEL.lower(),
            # no access log; query strings + client IPs = personal data, Cloudflare logs at edge anyway
            access_log=False,
        )
    )
    logger.info(
        "ris-mcp listening on %s:%d%s (rate limit %d/%ds, upstream cap %d)",
        config.HOST,
        config.PORT,
        mcp.settings.streamable_http_path,
        config.RATE_LIMIT,
        config.RATE_WINDOW,
        config.MAX_UPSTREAM,
    )
    try:
        await server.serve()
    finally:
        await rc.close()
