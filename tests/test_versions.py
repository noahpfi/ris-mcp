"""version selection, no network; RIS returns every historical version unless FassungVom set"""
from __future__ import annotations

from src import ris_client as rc


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
