"""Bounds a caller passes to get_paragraph, refused before any request when RIS could not read them."""
from __future__ import annotations

import pytest

from src import server


@pytest.mark.parametrize("number", ["§ 1295", "Art. 7", "fünf", "VII", ""])
async def test_bound_without_a_leading_number_is_refused_with_the_examples(no_upstream, number):
    out = await server.get_paragraph(law="ABGB", number=number)
    assert "is not a unit number" in out and "Examples:" in out


async def test_range_running_backwards_is_refused(no_upstream):
    assert "runs backwards" in await server.get_paragraph(law="KSchG", number="7", to="5")


async def test_invalid_date_is_rejected(no_upstream):
    assert "YYYY-MM-DD" in await server.get_paragraph(law="EStG", number="1", date="garbage")
