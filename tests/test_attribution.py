"""Each response built from RIS data carries the CC BY 4.0 attribution and the note that only the gazette
wording is binding, because the end user never sees the website. No network."""
from __future__ import annotations

import pytest

from src import ris_client as rc
from src import server

from fake_ris import FakeRis, law


@pytest.fixture
def ris(monkeypatch):
    """Answers every upstream call from local data."""
    fake = FakeRis(law("10001622", "Allgemeines bürgerliches Gesetzbuch", "ABGB", "§ 1295", text="Schadenersatz"))
    fake.install(monkeypatch)

    async def search_bgbl(number, teile=(1, 2, 3)):
        return fake.docs[:1], 1

    monkeypatch.setattr(rc, "search_bgbl_auth", search_bgbl)
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
    """lookup_bgbl returns authentic gazette text, so its note must not call it non-binding."""
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
    """A response that passes on no RIS data has nothing to attribute or disclaim."""
    async def nothing(*args, **kwargs):
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
    """Clients read the server instructions once per session, before any tool result."""
    text = server.mcp.instructions
    assert "Not affiliated" in text
    assert "never as legal advice" in text
    assert "BGBl authentisch" in text
