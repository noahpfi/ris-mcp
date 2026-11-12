"""Parameter combinations get_paragraph answers before any request."""
from __future__ import annotations

import pytest

from src import server


@pytest.mark.parametrize("args", [{"to": "5"}, {"kind": "Artikel"}])
async def test_to_and_kind_without_number_are_refused_since_ris_ignores_them(no_upstream, args):
    assert "need number" in await server.get_paragraph(law="KSchG", **args)


async def test_invalid_date_is_rejected(no_upstream):
    assert "YYYY-MM-DD" in await server.get_paragraph(law="EStG", number="1", date="garbage")
