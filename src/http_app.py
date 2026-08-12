"""HTTP transport wiring: inbound rate limiting and the uvicorn runner.

Kept out of server.py so that module stays a description of the tool surface.
"""
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
    """Per-IP token bucket. Pure ASGI, no BaseHTTPMiddleware task group.

    The endpoint is public and unauthenticated by design, so address is the
    only per-client handle available. The bucket map is a TTLCache because
    unique client IPs are the growth axis here — an unbounded dict keyed by
    remote address is a memory leak that anyone can drive.

    Only `protected_path` is limited. The limiter exists to keep inbound load
    from becoming outbound load on the RIS API, and neither the landing page
    nor the health probe touches it — throttling a page view would be a
    self-inflicted outage on the one surface meant to attract users.
    """

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
        # ttl > window so a bucket is never evicted while still throttling.
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
    """Caller address, preferring what the tunnel reports.

    cloudflared sets CF-Connecting-IP. The container port is never published to
    the host, so the tunnel is the only route in and the header cannot be
    forged by an outside caller. Falls back to the socket peer for local runs.
    """
    headers = {key: value for key, value in scope.get("headers", [])}
    forwarded = headers.get(b"cf-connecting-ip") or headers.get(b"x-forwarded-for")
    if forwarded:
        return forwarded.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


def build_app(mcp: FastMCP) -> Starlette:
    """Streamable-HTTP ASGI app, rate limited, landing page mounted underneath."""
    app = mcp.streamable_http_app()

    if config.STATIC_DIR is not None:
        if not config.STATIC_DIR.is_dir():
            raise ValueError(
                f"RIS_STATIC_DIR points at {config.STATIC_DIR}, which is not a directory. "
                "Build the site with `npm run build` in website/, or unset the variable."
            )
        # Appended last so it cannot shadow /mcp or /health: Starlette matches
        # routes in order and this one matches everything.
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
    """Run the HTTP transport until shutdown, then release the upstream client."""
    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(
            build_app(mcp),
            host=config.HOST,
            port=config.PORT,
            log_level=config.LOG_LEVEL.lower(),
            # Query strings and client addresses in an access log are personal
            # data we have no reason to retain; Cloudflare already logs at the
            # edge. Errors and lifecycle events still go to stdout.
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
