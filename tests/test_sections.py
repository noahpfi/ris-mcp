"""citation addressing; RIS Abschnitt range is numeric, § 5 alone also returns 5a-5d, transitional articles reuse § numbers"""
from __future__ import annotations

import pytest

from src import sections
from src import server

from fake_ris import FakeRis, law, ref

KSCHG = ("10002462", "Konsumentenschutzgesetz", "KSchG")
BVG = ("10000138", "Bundes-Verfassungsgesetz", "B-VG")
DSG = ("10001597", "Datenschutzgesetz", "DSG")


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


@pytest.fixture
def kschg(monkeypatch):
    docs = law(*KSCHG, "§ 1", "§ 5", "§ 5a", "§ 5b", "§ 6", "§ 6a", "§ 7")
    docs.append(ref("Art. 4 § 2", law=KSCHG[0], title=KSCHG[1], abbreviation=KSCHG[2], transitional=True))
    return FakeRis(docs).install(monkeypatch)


def _shown(out: str) -> list[str]:
    return [line.split("Konsumentenschutzgesetz ", 1)[1] for line in out.splitlines() if line.startswith("### Konsumentenschutzgesetz")]


@pytest.mark.parametrize("start, end, expected", [
    ("5", "", ["§ 5"]),
    ("5a", "", ["§ 5a"]),
    ("5", "6", ["§ 5", "§ 5a", "§ 5b", "§ 6"]),
    ("5a", "6a", ["§ 5a", "§ 5b", "§ 6", "§ 6a"]),
    ("§ 2", "§ 6", ["§ 5", "§ 5a", "§ 5b", "§ 6"]),
])
async def test_ranges_follow_natural_order_with_exact_ends(kschg, start, end, expected):
    """live: get_paragraph(KSchG, 5) returned §§ 5, 5a-5d"""
    assert _shown(await server.get_paragraph(law="KSchG", paragraph=start, to_paragraph=end)) == expected


async def test_transitional_provision_is_dropped_unless_named(kschg):
    assert _shown(await server.get_paragraph(law="KSchG", paragraph="2")) == []
    assert _shown(await server.get_paragraph(law="KSchG", paragraph="Art. 4 § 2")) == ["Art. 4 § 2 (transitional)"]


async def test_miss_lists_what_ris_holds_near_it(kschg):
    out = await server.get_paragraph(law="KSchG", paragraph="2")
    assert "No § 2 in Konsumentenschutzgesetz in force today." in out
    assert "Art. 4 § 2 (transitional)" in out
    assert "get_paragraph_at" in out


async def test_unreadable_citation_names_accepted_forms(kschg):
    out = await server.get_paragraph(law="KSchG", paragraph="fünf")
    assert server.CITATION_FORMS in out
    assert not kschg.calls


async def test_reversed_range_is_refused(kschg):
    out = await server.get_paragraph(law="KSchG", paragraph="7", to_paragraph="5")
    assert "does not run forward" in out
    assert not kschg.calls


async def test_bare_number_falls_back_to_articles(monkeypatch):
    """live: get_paragraph(B-VG, 7) = 'No version in force', Art. 7 only reachable as Artikel"""
    FakeRis(law(*BVG, "Art. 7", "Art. 14", "Art. 14a", "Art. 15", "Art. 15a", "Anl. 1")).install(monkeypatch)
    assert "### Bundes-Verfassungsgesetz Art. 7" in await server.get_paragraph(law="B-VG", paragraph="7")
    out = await server.get_paragraph(law="B-VG", paragraph="Art. 14", to_paragraph="15")
    assert [line.split("Verfassungsgesetz ", 1)[1] for line in out.splitlines() if line.startswith("### Bundes")] == ["Art. 14", "Art. 14a", "Art. 15"]
    assert "### Bundes-Verfassungsgesetz Anl. 1" in await server.get_paragraph(law="B-VG", paragraph="Anlage 1")


async def test_paragraph_inside_a_non_transitional_article_is_found(monkeypatch):
    """DSG keeps its §§ inside Art. 1 / Art. 2 without the ÜR flag"""
    FakeRis(law(*DSG, "Art. 1 § 1", "Art. 2 § 4", "Art. 2 § 5")).install(monkeypatch)
    out = await server.get_paragraph(law="DSG", paragraph="1", to_paragraph="4")
    assert "### Datenschutzgesetz Art. 1 § 1" in out and "### Datenschutzgesetz Art. 2 § 4" in out
    assert "Art. 2 § 5" not in out


async def test_historical_miss_does_not_point_to_itself(kschg):
    out = await server.get_paragraph_at(law="KSchG", paragraph="99", date="2010-01-01")
    assert "in force on 2010-01-01" in out
    assert "get_paragraph_at" not in out
