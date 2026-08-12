"""Tool surface per deployment mode.

Registration happens at import time, so each mode is exercised in its own
process over stdio — the same path a client actually starts.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
LIVE_API_TOOLS = {
    "search_law", "get_paragraph", "get_paragraph_at", "get_statute",
    "get_law_outline", "lookup_bgbl", "get_amendment_timeline",
}

HANDSHAKE = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {},
        "clientInfo": {"name": "pytest", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
]


def _run(**env_overrides) -> subprocess.CompletedProcess:
    env = {**os.environ, "RIS_TRANSPORT": "stdio", **env_overrides}
    return subprocess.run(
        [sys.executable, "-m", "src.server"],
        input="\n".join(json.dumps(m) for m in HANDSHAKE) + "\n",
        capture_output=True, text=True, env=env, cwd=REPO_ROOT, timeout=60,
    )


def _tools(result: subprocess.CompletedProcess) -> set[str]:
    assert result.returncode == 0, result.stderr
    for line in result.stdout.splitlines():
        message = json.loads(line)
        if message.get("id") == 2:
            return {tool["name"] for tool in message["result"]["tools"]}
    raise AssertionError(f"no tools/list response in {result.stdout!r}")


def _seeded_db(tmp_path: Path) -> Path:
    """A real index file, built through the same code the crawler uses."""
    db = tmp_path / "seeded.db"
    script = tmp_path / "seed.py"
    script.write_text(
        "from src import index\n"
        "index.init_db()\n"
        "with index._connect() as conn:\n"
        "    index._upsert(conn, 'd1', 'ABGB', '§ 1', 'u', '1900-01-01', 'text')\n"
    )
    subprocess.run(
        [sys.executable, str(script)],
        check=True, env={**os.environ, "RIS_DB_PATH": str(db)},
        cwd=REPO_ROOT, timeout=60,
    )
    return db


def test_hosted_mode_drops_only_who_mentions(tmp_path):
    """RIS_INDEX=off is what the container runs: seven live-API tools."""
    assert _tools(_run(RIS_INDEX="off", RIS_DB_PATH=str(tmp_path / "absent.db"))) == LIVE_API_TOOLS


def test_auto_without_an_index_serves_the_hosted_surface(tmp_path):
    assert _tools(_run(RIS_INDEX="auto", RIS_DB_PATH=str(tmp_path / "absent.db"))) == LIVE_API_TOOLS


def test_auto_with_an_index_adds_who_mentions(tmp_path):
    db = _seeded_db(tmp_path)
    assert _tools(_run(RIS_INDEX="auto", RIS_DB_PATH=str(db))) == LIVE_API_TOOLS | {"who_mentions"}


def test_on_without_an_index_fails_fast(tmp_path):
    result = _run(RIS_INDEX="on", RIS_DB_PATH=str(tmp_path / "absent.db"))
    assert result.returncode != 0
    assert "RIS_INDEX=on but no index" in result.stderr


def test_invalid_mode_is_rejected():
    result = _run(RIS_INDEX="maybe")
    assert result.returncode != 0
    assert "RIS_INDEX must be one of" in result.stderr


@pytest.mark.parametrize("variable,value", [
    ("RIS_TRANSPORT", "carrier-pigeon"),
    ("RIS_LOG_LEVEL", "LOUD"),
])
def test_bad_config_fails_at_boot_not_on_first_request(variable, value):
    result = _run(**{variable: value})
    assert result.returncode != 0
    assert variable in result.stderr
