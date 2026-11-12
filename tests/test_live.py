"""Behaviour against the real RIS API. Run with `pytest -m live`; assertions check structure that holds while
the cited laws stay in force, not wording."""
from __future__ import annotations

import asyncio
import re

import pytest

from src import ris_client as rc
from src import server

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
async def fresh_client():
    """The shared httpx client binds to the event loop of its first request, and each test runs its own loop."""
    rc._egress = asyncio.Semaphore(rc.MAX_UPSTREAM)
    yield
    await rc.close()


def _headings(out: str) -> list[str]:
    return [line[4:] for line in out.splitlines() if line.startswith("### ")]


async def test_ris_error_message_is_passed_through():
    with pytest.raises(rc.RisApiError, match="mindestens 3 Zeichen"):
        await rc.search_bundesrecht(titel="KO*", abschnitt_von="0")


async def test_search_order_runs_newest_first_across_pages():
    first = await server.search_law(query="Schadenersatz")
    second = await server.search_law(query="Schadenersatz", page=2)
    dates = re.findall(r"In force: (\d{4}-\d{2}-\d{2})", first + second)
    assert len(dates) == 2 * server.SEARCH_PAGE_SIZE
    assert dates == sorted(dates, reverse=True)
    assert first.endswith(server.SOURCE_CONSOLIDATED)
    pages = int(re.search(r"Page 1 of (\d+)", first).group(1))
    with pytest.raises(rc.RisApiError, match="Seitennummer"):
        await server.search_law(query="Schadenersatz", page=pages + 1)


@pytest.mark.parametrize("teil", ["I", "II", "III"])
async def test_bgbl_teil_selects_its_own_entry(teil):
    out = await server.lookup_bgbl(reference=f"BGBl. {teil} Nr. 50/2023")
    assert out.startswith(f"## BGBl. {teil} Nr. 50/2023")
    assert "**Authentic PDF:**" in out and "### Text" in out
    assert out.endswith(server.SOURCE_AUTHENTIC)


async def test_bgbl_without_teil_lists_every_part():
    out = await server.lookup_bgbl(reference="50/2023")
    assert all(f"BGBl. {teil} Nr. 50/2023" in out for teil in ("I", "II", "III"))


async def test_bgbl_before_the_authentic_gazette_is_asked_and_explained():
    assert "starts in 2004" in await server.lookup_bgbl(reference="BGBl. Nr. 60/1974")


async def test_paragraph_comes_from_the_named_law_not_a_regulation_citing_it():
    out = await server.get_paragraph(law="EStG", paragraph="1")
    assert _headings(out)[0].startswith("Einkommensteuergesetz 1988 § 1")
    assert out.endswith(server.SOURCE_CONSOLIDATED)


async def test_historical_paragraph_uses_the_requested_date():
    out = await server.get_paragraph(law="EStG", paragraph="1", date="2020-01-01")
    assert _headings(out)[0] == "Einkommensteuergesetz 1988 § 1 (as of 2020-01-01)"


async def test_bare_number_reaches_articles():
    assert _headings(await server.get_paragraph(law="B-VG", paragraph="7"))[0] == "Bundes-Verfassungsgesetz Art. 7"


@pytest.mark.parametrize("tool, args", [
    ("get_statute", {"name": "KSchG"}),
    ("get_law_outline", {"law": "KSchG"}),
    ("get_amendment_timeline", {"law": "KSchG"}),
])
async def test_law_tools_name_the_law_and_carry_the_source_note(tool, args):
    out = await getattr(server, tool)(**args)
    assert "Gesetzesnummer 10002462" in out
    assert out.endswith(server.SOURCE_CONSOLIDATED)


async def test_single_exact_hit_is_used_and_every_other_hit_is_listed():
    out = await server.get_statute(name="EStG")
    assert "Law used: Einkommensteuergesetz 1988 (EStG 1988), Gesetzesnummer 10004570." in out
    assert "Only hit whose abbreviation equals 'EStG', ignoring a trailing year." in out
    assert "- Bausparen gemäß § 108 EStG 1988: 20004310" in out


async def test_shared_abbreviation_lists_instead_of_guessing():
    out = await server.get_statute(name="GOG")
    assert "3 named exactly 'GOG'" in out
    assert "(GOG): 10000009" in out and "(GOG): 10000576" in out and "(GOG 1945): 10000205" in out
    assert "Law used" not in out


async def test_pick_selects_from_the_list():
    out = await server.get_statute(name="GOG", pick="10000576")
    assert "Law used: Geschäftsordnungsgesetz 1975 (GOG), Gesetzesnummer 10000576. Picked by Gesetzesnummer 10000576." in out


async def test_pick_outside_the_hits_returns_the_list():
    out = await server.get_statute(name="Datenschutzgesetz", pick="10000009")
    assert "Gesetzesnummer 10000009 is not among them." in out
    assert "Law used" not in out


async def test_partial_word_is_found_through_the_wildcard():
    out = await server.get_statute(name="Einkommensteuer")
    assert "'Einkommensteuer*'" in out and ": 10004570" in out


async def test_unknown_name_says_what_was_sent():
    out = await server.get_statute(name="Nichtgesetzwort")
    assert "RIS found no law in force today for 'Nichtgesetzwort*'" in out


async def test_repealed_law_is_reached_with_its_date():
    """KO was replaced by IO; both carry Gesetzesnummer 10001736, and RIS names the old version by the new title."""
    assert "RIS found no law in force today for 'KO'" in await server.get_statute(name="KO")
    listed = await server.get_paragraph(law="KO", paragraph="1", date="2005-01-01")
    assert "- Insolvenzordnung (IO): 10001736" in listed
    out = await server.get_paragraph(law="KO", paragraph="1", date="2005-01-01", pick="10001736")
    assert _headings(out)[0] == "Insolvenzordnung § 1 (as of 2005-01-01)"
