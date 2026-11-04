"""law name -> one Gesetzesnummer; RIS Titel is substring search, regulations citing a law share its abbreviation in their title"""
from __future__ import annotations

import pytest

from src import ris_client as rc
from src import server

from fake_ris import FakeRis, law, ref

ESTG = ("10004570", "Einkommensteuergesetz 1988", "EStG 1988")
BAUSPAR = ("20004403", "Bausparen gemäß § 108 EStG 1988", "")
USTG = ("10004873", "Umsatzsteuergesetz 1994", "UStG 1994")
USTG_ANNEX = ("10004929", "Umsatzsteuergesetz 1994 – Anhang (Binnenmarkt)", "UStG 1994")


@pytest.fixture
def statutes(monkeypatch):
    return FakeRis(
        law(*BAUSPAR, "§ 1", text="Bausparprämie")
        + law(*ESTG, "§ 1", text="Persönliche Steuerpflicht")
        + law(*USTG_ANNEX, "Art. 1")
        + law(*USTG, "§ 6")
    ).install(monkeypatch)


@pytest.mark.parametrize("name", ["EStG", "EStG 1988", "estg", "Einkommensteuergesetz", "Einkommensteuergesetz 1988", "10004570"])
async def test_name_forms_resolve_to_the_statute_not_a_regulation_citing_it(statutes, name):
    resolution = await rc.resolve_law(name, "2026-11-01")
    assert resolution.law.number == ESTG[0]


async def test_shared_abbreviation_picks_main_act_and_names_the_sibling(statutes):
    resolution = await rc.resolve_law("UStG", "2026-11-01")
    assert resolution.law.number == USTG[0]
    assert [sibling.number for sibling in resolution.related] == [USTG_ANNEX[0]]
    assert USTG_ANNEX[0] in rc.describe_resolution(resolution)


async def test_explicit_year_is_never_stripped_from_the_query(monkeypatch):
    """only UStG 1994 in force; 'UStG 1972' must not silently become it"""
    FakeRis(law(*USTG, "§ 6") + law("20000001", "Verordnung zum UStG 1972 und UStG 1994", "", "§ 1")).install(monkeypatch)
    resolution = await rc.resolve_law("UStG 1972", "2026-11-01")
    assert resolution.law.number != USTG[0]


async def test_vague_name_lists_candidates_instead_of_guessing(statutes):
    with pytest.raises(rc.LawLookupError) as excinfo:
        await rc.resolve_law("1988", "2026-11-01")
    assert ESTG[0] in str(excinfo.value) and BAUSPAR[0] in str(excinfo.value)


async def test_unknown_name_reports_the_date(statutes):
    with pytest.raises(rc.LawLookupError, match="on 1990-01-01"):
        await rc.resolve_law("Nichtgesetz", "1990-01-01")


async def test_candidates_beyond_the_first_page_are_considered(monkeypatch):
    """B-VG = 63 preambles; RIS order puts no guarantee on the statute being on page 1"""
    docs = [ref("§ 0", law=f"2{i:07d}", title=f"Vereinbarung {i} gemäß Art. 15a B-VG", abbreviation="") for i in range(150)]
    docs += law("10000138", "Bundes-Verfassungsgesetz", "B-VG", "Art. 7")
    FakeRis(docs).install(monkeypatch)
    resolution = await rc.resolve_law("B-VG", "2026-11-01")
    assert resolution.law.number == "10000138"


async def test_name_matching_more_than_the_page_cap_is_refused(monkeypatch):
    limit = rc.MAX_RESOLVE_PAGES * 100
    FakeRis([ref("§ 0", law=f"2{i:07d}", title=f"Verordnung {i}", abbreviation="") for i in range(limit + 1)]).install(monkeypatch)
    with pytest.raises(rc.LawLookupError, match=f"matches {limit + 1} laws"):
        await rc.resolve_law("Verordnung", "2026-11-01")


async def test_historical_lookup_resolves_with_the_requested_date(statutes):
    await server.get_paragraph_at(law="EStG", paragraph="1", date="2020-01-01")
    assert all(call["fassung_vom"] == "2020-01-01" for call in statutes.calls)


@pytest.mark.parametrize("tool", ["get_paragraph", "get_paragraph_at"])
async def test_paragraph_tools_return_the_named_law_only(statutes, tool):
    """live: get_paragraph_at(EStG, 1) returned § 1 of the Bausparen regulation"""
    args = {"law": "EStG", "paragraph": "1"} | ({"date": "2020-01-01"} if tool == "get_paragraph_at" else {})
    out = await getattr(server, tool)(**args)
    assert "Persönliche Steuerpflicht" in out
    assert "Bausparprämie" not in out


async def test_invalid_date_is_rejected_before_any_upstream_call(statutes):
    out = await server.get_paragraph_at(law="EStG", paragraph="1", date="garbage")
    assert "YYYY-MM-DD" in out
    assert not statutes.calls


@pytest.mark.parametrize("tool, arg", [
    ("get_paragraph", {"law": "Nichtgesetz", "paragraph": "1"}),
    ("get_statute", {"name": "Nichtgesetz"}),
    ("get_law_outline", {"law": "Nichtgesetz"}),
    ("get_amendment_timeline", {"law": "Nichtgesetz"}),
    ("search_law", {"query": "x", "law": "Nichtgesetz"}),
])
async def test_every_law_tool_answers_unresolvable_names_in_text(statutes, tool, arg):
    out = await getattr(server, tool)(**arg)
    assert "No law matching 'Nichtgesetz'" in out


async def test_scoped_search_filters_by_gesetzesnummer_not_title(statutes):
    await server.search_law(query="Steuerpflicht", law="EStG")
    search = statutes.calls[-1]
    assert search["gesetzesnummer"] == ESTG[0]
    assert "titel" not in search
