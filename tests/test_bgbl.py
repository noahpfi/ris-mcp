"""Gazette lookup. A number/year exists once per Teil. live: "BGBl II Nr. 50/2023" returned BGBl I 50/2023,
and the entry text was not shown."""
from __future__ import annotations

import pytest

from src import ris_client as rc
from src import server

TEILE = {
    1: ("Gesetz", "Bundesgesetz, mit dem das KommAustria-Gesetz geändert wird"),
    2: ("Verordnung", "Verordnung über Pflegeleistungen"),
    3: ("Kundmachung", "Geltungsbereich des Übereinkommens"),
}
ROMAN = {1: "I", 2: "II", 3: "III"}


def _entry(teil: int) -> dict:
    typ, title = TEILE[teil]
    return {"Data": {
        "Metadaten": {
            "Technisch": {"ID": f"BGBLA_2023_{ROMAN[teil]}_50"},
            "Allgemein": {"DokumentUrl": f"https://example.invalid/{teil}"},
            "Bundesrecht": {"Kurztitel": title, "Titel": title, "BgblAuth": {
                "Bgblnummer": f"BGBl. {ROMAN[teil]} Nr. 50/2023", "Typ": typ, "Ausgabedatum": "2023-05-19"}},
        },
        "Dokumentliste": {"ContentReference": {"Urls": {"ContentUrl": [
            {"DataType": "Html", "Url": f"https://example.invalid/{teil}.html"},
            {"DataType": "Authentisch", "Url": f"https://example.invalid/{teil}.pdf"},
        ]}}},
    }}


@pytest.fixture
def gazette(monkeypatch):
    calls = []

    async def search(number, teile=(1, 2, 3)):
        calls.append((number, teile))
        refs = [_entry(t) for t in sorted(teile, reverse=True)]
        return refs, len(refs)

    async def fetch(ref):
        title = ref["Data"]["Metadaten"]["Bundesrecht"]["Titel"]
        return (f"<html><body><div class='contentBlock'><h1 class='Titel'>50. {title}</h1>"
                "<p class='NovAo1'>1. § 35 Abs. 1b wird eingefügt.<span class='sr-only'>Paragraph 35</span></p>"
                "</div></body></html>")

    monkeypatch.setattr(rc, "search_bgbl_auth", search)
    monkeypatch.setattr(rc, "fetch_document_html", fetch)
    return calls


@pytest.mark.parametrize("reference, teil", [
    ("BGBl I Nr. 50/2023", 1), ("BGBl. II Nr. 50/2023", 2), ("III 50/2023", 3), ("BGBl. III Nr.50 / 2023", 3),
])
async def test_teil_selects_its_own_entry(gazette, reference, teil):
    out = await server.lookup_bgbl(reference=reference)
    assert gazette == [("50/2023", (teil,))]
    assert f"## BGBl. {ROMAN[teil]} Nr. 50/2023" in out
    assert TEILE[teil][1] in out


async def test_missing_teil_lists_every_part_instead_of_picking_one(gazette):
    out = await server.lookup_bgbl(reference="50/2023")
    positions = [out.index(f"BGBl. {ROMAN[teil]} Nr. 50/2023 ({TEILE[teil][0]}") for teil in (1, 2, 3)]
    assert positions == sorted(positions)
    assert "CC BY 4.0" not in out


async def test_entry_text_is_returned_without_screen_reader_duplicates(gazette):
    out = await server.lookup_bgbl(reference="BGBl. I Nr. 50/2023")
    assert "### Text\n1. § 35 Abs. 1b wird eingefügt." in out
    assert "Paragraph 35" not in out
    assert "**Authentic PDF:** https://example.invalid/1.pdf" in out


async def test_long_text_is_cut_with_a_notice(gazette, monkeypatch):
    async def fetch(ref):
        return "<html><body><div class='contentBlock'><p>" + "x" * (server.BGBL_TEXT_LIMIT + 10) + "</p></div></body></html>"

    monkeypatch.setattr(rc, "fetch_document_html", fetch)
    out = await server.lookup_bgbl(reference="BGBl. I Nr. 50/2023")
    assert f"cut at {server.BGBL_TEXT_LIMIT} of {server.BGBL_TEXT_LIMIT + 10} characters" in out


@pytest.mark.parametrize("reference", ["BGBl. Nr. 60/1974", "I 99/2003"])
async def test_pre_2004_numbers_are_answered_without_a_query(gazette, reference):
    assert "starts in 2004" in await server.lookup_bgbl(reference=reference)
    assert not gazette


async def test_unreadable_reference(gazette):
    assert "Could not read" in await server.lookup_bgbl(reference="BGBl neu")
