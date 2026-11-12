"""Parsing of the citation a caller passes in, and the answers given before any request to RIS."""
from __future__ import annotations

import pytest

from src import sections
from src import server


@pytest.mark.parametrize("text, expected", [
    ("1295", "§ 1295"),
    ("§ 879a", "§ 879a"),
    ("§879A", "§ 879a"),
    ("§§ 5", "§ 5"),
    ("Art. 7", "Art. 7"),
    ("Artikel 15a", "Art. 15a"),
    ("art 7", "Art. 7"),
    ("Anl. 1", "Anl. 1"),
    ("Anlage 2", "Anl. 2"),
    ("Art. 10 § 1", "Art. 10 § 1"),
    ("Anl. 1/ThNa", "Anl. 1/ThNa"),
    ("Anlage 2/m1", "Anl. 2/m1"),
    ("§ 1.", "§ 1"),
])
def test_citation_forms_parse_to_ris_labels(text, expected):
    assert str(sections.parse(text)) == expected


@pytest.mark.parametrize("text", ["", "abc", "1295 ABGB", "Art. 10 1", "§ IV"])
def test_non_citations_are_rejected(text):
    assert sections.parse(text) is None


def test_bare_number_is_implicit_explicit_kind_is_not():
    assert sections.parse("7").implicit
    assert not sections.parse("§ 7").implicit


def test_bare_range_end_inherits_kind_and_container():
    start = sections.parse("Art. 2 § 4")
    assert str(sections.end_of_range(start, "6")) == "Art. 2 § 6"


async def test_unreadable_citation_names_accepted_forms(no_upstream):
    assert server.CITATION_FORMS in await server.get_paragraph(law="KSchG", paragraph="fünf")


async def test_reversed_range_is_refused(no_upstream):
    assert "does not run forward" in await server.get_paragraph(law="KSchG", paragraph="7", to_paragraph="5")


async def test_invalid_date_is_rejected(no_upstream):
    assert "YYYY-MM-DD" in await server.get_paragraph_at(law="EStG", paragraph="1", date="garbage")
