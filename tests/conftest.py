"""Point every module at a throwaway index before src is imported.

src.config reads the environment at import time, and src.index binds DB_PATH
from it, so this has to happen before the first `import src.*` anywhere in the
test session.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="ris-mcp-tests-"))
os.environ["RIS_DB_PATH"] = str(_TMP / "test.db")
# Force the full surface in-process; the index-off deployment is covered by
# test_modes.py, which starts real subprocesses.
os.environ.setdefault("RIS_INDEX", "on")
os.environ.setdefault("RIS_TRANSPORT", "streamable-http")
os.environ.setdefault("RIS_HOST", "127.0.0.1")
os.environ.setdefault("RIS_PUBLIC_HOSTS", "ris.test")
os.environ.setdefault("RIS_RATE_LIMIT", "5")
os.environ.setdefault("RIS_RATE_WINDOW", "60")
