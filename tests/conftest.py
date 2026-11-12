"""set throwaway index env before any import src.*; src.config reads env at import, src.index binds DB_PATH"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ris-mcp-tests-"))
os.environ["RIS_DB_PATH"] = str(_TMP / "test.db")
# force full surface in-process; index-off covered by test_modes.py subprocesses
os.environ.setdefault("RIS_INDEX", "on")
os.environ.setdefault("RIS_TRANSPORT", "streamable-http")
os.environ.setdefault("RIS_HOST", "127.0.0.1")
os.environ.setdefault("RIS_PUBLIC_HOSTS", "ris.test")
os.environ.setdefault("RIS_RATE_LIMIT", "5")
os.environ.setdefault("RIS_RATE_WINDOW", "60")


import pytest  # noqa: E402


@pytest.fixture
def no_upstream(monkeypatch):
    """Fails the test on any request to RIS, for checks that must answer before calling out."""
    from src import ris_client as rc

    async def refuse(*args, **kwargs):
        raise AssertionError("request reached RIS")

    monkeypatch.setattr(rc, "_fetch", refuse)
