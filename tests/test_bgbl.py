"""Gazette references that are answered before any request. Lookups are checked in test_live.py."""
from __future__ import annotations

from src import server


async def test_unreadable_reference(no_upstream):
    assert "Could not read" in await server.lookup_bgbl(reference="BGBl neu")
