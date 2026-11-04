"""search order; live: 'Schadenersatz' = 615 hits, page-local sort showed 50 'Abkommen …' treaties labelled newest first"""
from __future__ import annotations

import pytest

from src import server

from fake_ris import FakeRis, ref


@pytest.fixture
def hits(monkeypatch):
    docs = [ref(f"§ {n}", in_force=f"{1950 + n}-01-01", text="Schadenersatz") for n in range(1, 61)]
    return FakeRis(docs).install(monkeypatch)


async def test_newest_hit_overall_leads_page_one(hits):
    out = await server.search_law(query="Schadenersatz")
    assert hits.calls[-1]["sort_by"] == "Inkrafttretensdatum" and hits.calls[-1]["descending"]
    assert out.index("§ 60") < out.index("§ 59")
    assert "§ 10\n" not in out
    assert "Page 1 of 2. Next: page=2." in out


async def test_second_page_continues_the_same_order(hits):
    out = await server.search_law(query="Schadenersatz", page=2)
    assert "Page 2 of 2." in out and "Next" not in out
    assert out.index("§ 10") < out.index("§ 1\n")


async def test_page_past_the_end_names_the_last_page(hits):
    assert await server.search_law(query="Schadenersatz", page=3) == "Page 3 is past the last page, 2."


async def test_page_zero_is_refused_before_any_upstream_call(hits):
    assert "starts at 1" in await server.search_law(query="Schadenersatz", page=0)
    assert not hits.calls
