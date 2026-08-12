"""Environment-driven configuration.

Single source for every tunable so the container and the local stdio run share
one set of names. Values are read once at import; validate() is called from
server.main() so a bad env fails fast at boot instead of on first request.
"""
from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent

TRANSPORTS = ("stdio", "streamable-http")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _csv(name: str) -> list[str]:
    return [part.strip() for part in os.environ.get(name, "").split(",") if part.strip()]


# Index location. Only relevant when the index is enabled; the file is ~600MB
# and never belongs in an image layer.
DB_PATH = Path(os.environ.get("RIS_DB_PATH", "").strip() or _REPO_ROOT / "data" / "ris.db")

# who_mentions is the one tool backed by a local FTS index. The hosted
# deployment runs without it ("off") so it needs no volume, no crawler and no
# 600MB of disk; a local checkout that has built the index gets it via "auto".
INDEX_MODES = ("auto", "on", "off")
INDEX_MODE = (os.environ.get("RIS_INDEX", "").strip() or "auto").lower()


def index_enabled() -> bool:
    if INDEX_MODE == "on":
        return True
    if INDEX_MODE == "off":
        return False
    return DB_PATH.exists()

TRANSPORT = os.environ.get("RIS_TRANSPORT", "").strip() or "stdio"
HOST = os.environ.get("RIS_HOST", "").strip() or "127.0.0.1"
PORT = _int("RIS_PORT", 8000)
LOG_LEVEL = (os.environ.get("RIS_LOG_LEVEL", "").strip() or "INFO").upper()

# Host header values accepted on the MCP endpoint. Empty list disables the
# check — correct for stdio and for localhost dev, wrong for a public tunnel.
PUBLIC_HOSTS = _csv("RIS_PUBLIC_HOSTS")

# Built landing page, served from the same origin as /mcp so the project needs
# one hostname rather than a static host plus an API host. Unset means the HTTP
# server carries the MCP endpoint alone.
_static = os.environ.get("RIS_STATIC_DIR", "").strip()
STATIC_DIR = Path(_static) if _static else None

# Process-wide cap on concurrent requests to data.bka.gv.at. This is the only
# thing standing between an unauthenticated public endpoint and our IP getting
# blocked by RIS, so it applies to crawler and server alike.
MAX_UPSTREAM = _int("RIS_MAX_UPSTREAM", 4)

# Per-IP inbound token bucket: RATE_LIMIT requests per RATE_WINDOW seconds.
# 0 disables it.
RATE_LIMIT = _int("RIS_RATE_LIMIT", 60, minimum=0)
RATE_WINDOW = _int("RIS_RATE_WINDOW", 60)
# Bounds the limiter's memory. Unique client IPs are the growth axis on a
# public endpoint; without a bound the bucket map is a slow memory leak.
RATE_MAX_IPS = _int("RIS_RATE_MAX_IPS", 10_000)


def _validate_environment() -> None:
    """Shape checks, run at import.

    Deliberately before anything else: FastMCP validates its own settings when
    the server object is constructed, and a pydantic literal_error naming an
    internal field is a worse answer than one naming the variable that is wrong.
    """
    if TRANSPORT not in TRANSPORTS:
        raise ValueError(f"RIS_TRANSPORT must be one of {TRANSPORTS}, got {TRANSPORT!r}")
    if LOG_LEVEL not in LOG_LEVELS:
        raise ValueError(f"RIS_LOG_LEVEL must be one of {LOG_LEVELS}, got {LOG_LEVEL!r}")
    if INDEX_MODE not in INDEX_MODES:
        raise ValueError(f"RIS_INDEX must be one of {INDEX_MODES}, got {INDEX_MODE!r}")
    if TRANSPORT == "streamable-http" and HOST not in ("127.0.0.1", "localhost", "::1"):
        if not PUBLIC_HOSTS:
            raise ValueError(
                "RIS_PUBLIC_HOSTS must list the hostnames this server is reached under "
                f"when binding {HOST}. Host-header validation is off without it."
            )


def validate_runtime() -> None:
    """Checks that depend on the filesystem. Called from server.main()."""
    if INDEX_MODE == "on" and not DB_PATH.exists():
        raise ValueError(
            f"RIS_INDEX=on but no index at {DB_PATH}. Build it with "
            "`python -m src.index`, or set RIS_INDEX=off to run without who_mentions."
        )


_validate_environment()
