"""version selection, no network; RIS returns every historical version unless FassungVom set"""
from __future__ import annotations

import pytest

from src import ris_client as rc
from src import server


def _ref(paragraph: str = "§ 6", in_force: str = "2026-01-01", expiry: str = "", title: str = "UStG 1994") -> dict:
    return {"Data": {"Metadaten": {
        "Technisch": {"ID": f"{title}-{paragraph}-{in_force}"},
        "Allgemein": {"DokumentUrl": "u"},
        "Bundesrecht": {"Kurztitel": title, "BrKons": {
            "ArtikelParagraphAnlage": paragraph, "Paragraphnummer": paragraph.lstrip("§ "),
            "Abkuerzung": title, "Inkrafttretensdatum": in_force, "Ausserkrafttretensdatum": expiry,
        }}}}}


def test_past_expiry_is_repealed(monkeypatch):
    monkeypatch.setattr(rc, "today", lambda: "2026-09-30")
    meta = rc._meta_from_ref(_ref(expiry="2024-07-31"))
    assert meta["repealed"] == "2024-07-31"
    assert meta["valid_until"] == ""


def test_future_expiry_is_still_live(monkeypatch):
    """§ 21 UStG 1994 carries expiry 2026-12-31 while in force; old check flagged it repealed"""
    monkeypatch.setattr(rc, "today", lambda: "2026-09-30")
    meta = rc._meta_from_ref(_ref(paragraph="§ 21", expiry="2026-12-31"))
    assert meta["repealed"] == ""
    assert meta["valid_until"] == "2026-12-31"


def test_expiry_today_is_still_live(monkeypatch):
    """expiry = last day in force"""
    monkeypatch.setattr(rc, "today", lambda: "2026-09-30")
    assert rc._meta_from_ref(_ref(expiry="2026-09-30"))["repealed"] == ""


def test_no_expiry_is_live():
    meta = rc._meta_from_ref(_ref())
    assert meta["repealed"] == "" and meta["valid_until"] == ""


def test_best_law_match_keeps_law_with_scheduled_expiry(monkeypatch):
    monkeypatch.setattr(rc, "today", lambda: "2026-09-30")
    old = _ref(expiry="1994-12-31", title="UStG 1972")
    scheduled = _ref(expiry="2026-12-31", title="UStG 1994")
    assert rc.best_law_match([old, scheduled], "UStG") is scheduled


LIVE_LOOKUPS = [
    ("search_law", {"query": "Kleinunternehmer", "law": "UStG"}),
    ("get_paragraph", {"law": "UStG", "paragraph": "6"}),
    ("get_statute", {"name": "UStG"}),
    ("get_law_outline", {"law": "UStG"}),
    ("get_amendment_timeline", {"law": "UStG"}),
]


@pytest.mark.parametrize("tool, args", LIVE_LOOKUPS)
async def test_live_lookups_ask_ris_for_versions_valid_today(monkeypatch, tool, args):
    """without FassungVom first 50 hits of UStG 'Kleinunternehmer' hold 0 live versions, live § 6 never fetched"""
    calls = []

    async def spy(**kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(rc, "search_bundesrecht", spy)
    monkeypatch.setattr(rc, "today", lambda: "2026-09-30")
    await getattr(server, tool)(**args)

    assert calls
    assert all(c.get("fassung_vom") == "2026-09-30" for c in calls)


async def test_get_paragraph_at_keeps_requested_date(monkeypatch):
    calls = []

    async def spy(**kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(rc, "search_bundesrecht", spy)
    await server.get_paragraph_at(law="ABGB", paragraph="879", date="2010-01-01")
    assert calls[0]["fassung_vom"] == "2010-01-01"


async def test_search_law_lists_newest_in_force_first_with_scheduled_expiry(monkeypatch):
    refs = [
        _ref(paragraph="§ 21", in_force="2025-01-01", expiry="2026-12-31"),
        _ref(paragraph="§ 6", in_force="2026-01-01"),
        _ref(paragraph="Art. 1", in_force="2023-07-22", title="UStG 1994 Anhang"),
    ]

    async def search(**kwargs):
        return refs, len(refs)

    monkeypatch.setattr(rc, "search_bundesrecht", search)
    monkeypatch.setattr(rc, "today", lambda: "2026-09-30")
    out = await server.search_law(query="Kleinunternehmer", law="UStG")

    assert out.index("§ 6") < out.index("§ 21") < out.index("Art. 1")
    assert "§ 21  valid until 2026-12-31" in out
    assert "REPEALED" not in out
