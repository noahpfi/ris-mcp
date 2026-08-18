"""Source, licence and bindingness ride on the response, not just the website.

RIS publishes under CC BY 4.0 (attribution required wherever the data is passed
on) and states that only the gazette wording is binding. The person who acts on
an answer never sees ris-mcp.noahpfister.com — the model does see the tool
result, so the note has to be there. No network.
"""
from __future__ import annotations

import pytest

from src import ris_client as rc
from src import server


def _ref(**brkons):
    base = {
        "ArtikelParagraphAnlage": "§ 1295",
        "Kundmachungsorgan": "JGS Nr. 946/1811",
        "Inkrafttretensdatum": "1979-01-01",
        "Dokumenttyp": "Paragraph",
        "Paragraphnummer": "1295",
        "GesamteRechtsvorschriftUrl": "https://example.invalid/outline",
    }
    base.update(brkons)
    return {"Data": {"Metadaten": {
        "Technisch": {"ID": "NOR12345678"},
        "Allgemein": {"DokumentUrl": "https://example.invalid/doc"},
        "Bundesrecht": {"Kurztitel": "ABGB", "Abkuerzung": "ABGB", "BrKons": base},
    }}}


@pytest.fixture
def ris(monkeypatch):
    """Every upstream call answered locally with one plausible paragraph."""
    async def search(**kwargs):
        return [_ref()], 1

    async def fetch(ref):
        return "<html><body><div class='contentBlock'><h1 class='Titel'>Text</h1>" \
               "<p>Wer einem anderen einen Schaden zufügt...</p></div></body></html>"

    monkeypatch.setattr(rc, "search_bundesrecht", search)
    monkeypatch.setattr(rc, "search_bgbl_auth", search)
    monkeypatch.setattr(rc, "fetch_document_html", fetch)
    monkeypatch.setattr(rc, "_get_html", lambda url: _outline_html())


async def _outline_html():
    return "<html><body><div class='contentBlock'><h1 class='Titel'>Text</h1>" \
           "<p>§ 1295 Schadenersatz</p></div></body></html>"


def test_consolidated_note_names_source_licence_and_what_is_binding():
    note = server.SOURCE_CONSOLIDATED
    assert "RIS" in note and "Bundeskanzleramt" in note
    assert "CC BY 4.0" in note
    assert "BGBl authentisch" in note
    assert "Not legal advice" in note


def test_authentic_note_does_not_call_the_gazette_non_binding():
    """lookup_bgbl returns the authentic text; telling the model it is not binding is wrong."""
    assert "Consolidated" not in server.SOURCE_AUTHENTIC
    assert "CC BY 4.0" in server.SOURCE_AUTHENTIC


@pytest.mark.parametrize("call", [
    lambda: server.search_law(query="Schadenersatz"),
    lambda: server.get_paragraph(law="ABGB", paragraph="1295"),
    lambda: server.get_paragraph_at(law="ABGB", paragraph="1295", date="2020-01-01"),
    lambda: server.get_statute(name="ABGB"),
])
async def test_data_carrying_tools_end_with_the_source_note(ris, call):
    assert (await call()).endswith(server.SOURCE_CONSOLIDATED)


async def test_bgbl_lookup_is_marked_authentic_not_consolidated(ris):
    result = await server.lookup_bgbl(reference="50/2023")
    assert result.endswith(server.SOURCE_AUTHENTIC)


async def test_empty_results_carry_no_attribution(monkeypatch):
    """Nothing was passed on, so there is nothing to attribute or disclaim."""
    async def nothing(**kwargs):
        return [], 0

    monkeypatch.setattr(rc, "search_bundesrecht", nothing)
    monkeypatch.setattr(rc, "search_bgbl_auth", nothing)

    for result in (
        await server.search_law(query="zzz"),
        await server.get_paragraph(law="ABGB", paragraph="99999"),
        await server.get_paragraph_at(law="ABGB", paragraph="99999", date="2020-01-01"),
        await server.get_statute(name="Nichtgesetz"),
        await server.lookup_bgbl(reference="1/1900"),
    ):
        assert "CC BY 4.0" not in result


async def test_server_instructions_disclaim_affiliation_and_advice():
    """The client reads these once per session, before any tool result exists."""
    text = server.mcp.instructions
    assert "Not affiliated" in text
    assert "never as legal advice" in text
    assert "BGBl authentisch" in text
