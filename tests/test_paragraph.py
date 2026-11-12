"""Parameter combinations get_paragraph answers before any request."""
from __future__ import annotations

import pytest

from src import server


@pytest.mark.parametrize("args", [{"to": "5"}, {"kind": "Artikel"}])
async def test_to_and_kind_without_number_are_refused_since_ris_ignores_them(no_upstream, args):
    assert "need number" in await server.get_paragraph(law="KSchG", **args)


async def test_invalid_date_is_rejected(no_upstream):
    assert "YYYY-MM-DD" in await server.get_paragraph(law="EStG", number="1", date="garbage")


@pytest.mark.parametrize("kind", ["artikel", "ARTIKEL", " Artikel "])
async def test_kind_in_any_capitalisation_is_accepted(no_upstream, kind):
    result = await server.mcp.call_tool("get_paragraph", {"law": "KSchG", "kind": kind})
    assert "need number" in result[0][0].text


async def test_unknown_kind_is_rejected_with_the_valid_kinds(no_upstream):
    with pytest.raises(Exception, match="Paragraph"):
        await server.mcp.call_tool("get_paragraph", {"law": "KSchG", "number": "1", "kind": "Kapitel"})
