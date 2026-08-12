"""env config, single source of tunables for container + stdio; read once at import, validate() at boot via server.main()"""
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


# ~600MB index, only when enabled, never in image layer
DB_PATH = Path(os.environ.get("RIS_DB_PATH", "").strip() or _REPO_ROOT / "data" / "ris.db")

# who_mentions = only FTS-backed tool; hosted runs off, no volume/crawler/600MB; local built index -> auto
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

# Host allowlist for MCP endpoint; empty disables, ok for stdio/localhost, wrong for public tunnel
PUBLIC_HOSTS = _csv("RIS_PUBLIC_HOSTS")

# built landing page, same origin as /mcp -> one hostname; unset = MCP endpoint only
_static = os.environ.get("RIS_STATIC_DIR", "").strip()
STATIC_DIR = Path(_static) if _static else None

# cap concurrent data.bka.gv.at requests; only guard vs RIS blocking our IP, crawler + server alike
MAX_UPSTREAM = _int("RIS_MAX_UPSTREAM", 4)

# per-IP inbound bucket, RATE_LIMIT requests per RATE_WINDOW seconds; 0 disables
RATE_LIMIT = _int("RIS_RATE_LIMIT", 60, minimum=0)
RATE_WINDOW = _int("RIS_RATE_WINDOW", 60)
# bounds limiter memory; unique IPs = growth axis, unbounded map = memory leak
RATE_MAX_IPS = _int("RIS_RATE_MAX_IPS", 10_000)


def _validate_environment() -> None:
    """shape checks at import, before FastMCP builds settings -> error names env var, not pydantic internal field"""
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
    """filesystem checks, called from server.main()"""
    if INDEX_MODE == "on" and not DB_PATH.exists():
        raise ValueError(
            f"RIS_INDEX=on but no index at {DB_PATH}. Build it with "
            "`python -m src.index`, or set RIS_INDEX=off to run without who_mentions."
        )


_validate_environment()
