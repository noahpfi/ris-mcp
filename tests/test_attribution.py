"""Each response built from RIS data carries the CC BY 4.0 attribution and the note that only the gazette
wording is binding, because the end user never sees the website. The tools are checked in test_live.py."""
from __future__ import annotations

from src import server


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


async def test_server_instructions_disclaim_affiliation_and_advice():
    """Clients read the server instructions once per session, before any tool result."""
    text = server.mcp.instructions
    assert "Not affiliated" in text
    assert "never as legal advice" in text
    assert "BGBl authentisch" in text
