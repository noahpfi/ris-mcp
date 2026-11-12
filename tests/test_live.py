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


def _shown(out: str, title: str) -> list[str]:
    """Labels that got full text, read from the headings the tool writes."""
    return [h[len(title) + 1:] for h in _headings(out) if h.startswith(title + " ")]


async def test_single_citation_gets_its_own_text_and_lists_the_rest():
    out = await server.get_paragraph(law="ABGB", paragraph="§ 1")
    assert _shown(out, "Allgemeines bürgerliches Gesetzbuch") == ["§ 1"]
    assert "RIS also returned for § 1: Art. 10 § 1 [ÜR], Art. 18 § 1 [ÜR]." in out
    assert "\nURL: https://" in out


async def test_suffixed_neighbours_are_listed_not_dropped():
    out = await server.get_paragraph(law="KSchG", paragraph="§ 5")
    assert _shown(out, "Konsumentenschutzgesetz") == ["§ 5"]
    assert "§ 5a" in out.split("RIS also returned for § 5:")[1]


async def test_label_with_letter_and_digit_suffix_is_reachable():
    out = await server.get_paragraph(law="GewO", pick="10007517", paragraph="§ 365m1")
    assert _shown(out, "Gewerbeordnung 1994") == ["§ 365m1"]


async def test_bare_number_returns_every_kind():
    out = await server.get_paragraph(law="KSchG", paragraph="1")
    shown = _shown(out, "Konsumentenschutzgesetz")
    assert "§ 1" in shown and "Anl. 1" in shown and any(label.startswith("Art. 1 [") for label in shown)


async def test_range_inside_an_article():
    out = await server.get_paragraph(law="DSG", paragraph="Art. 2 § 4", to_paragraph="6")
    assert _shown(out, "Datenschutzgesetz") == ["Art. 2 § 4", "Art. 2 § 5", "Art. 2 § 6"]


async def test_range_returns_what_ris_holds_between_the_numbers():
    out = await server.get_paragraph(law="KSchG", paragraph="5", to_paragraph="6")
    shown = _shown(out, "Konsumentenschutzgesetz")
    assert shown[0] == "§ 5" and "§ 5a" in shown and "§ 6" in shown


async def test_miss_lists_everything_ris_returned():
    """DSG keeps its paragraphs inside Art. 1 and Art. 2."""
    out = await server.get_paragraph(law="DSG", paragraph="§ 1")
    assert out.startswith("No § 1 in Datenschutzgesetz in force today. RIS returned for § 1: Art. 1 § 1")


async def test_miss_with_nothing_returned_says_so():
    assert await server.get_paragraph(law="KSchG", paragraph="§ 99") == "No § 99 in Konsumentenschutzgesetz in force today."


async def _api_labels(number: str, date: str) -> list[str]:
    refs, _ = await rc.search_bundesrecht_pages(30, gesetzesnummer=number, fassung_vom=date)
    return [rc._meta_from_ref(ref)["paragraph"] for ref in refs]


def _outline_labels(out: str) -> list[str]:
    body = out.split("full text per unit via get_paragraph.*\n\n", 1)[1].split("\n\nURL: ", 1)[0]
    return [line.split("  ")[0] for line in body.splitlines() if not line.startswith("**")]


@pytest.mark.parametrize("law, pick, number", [
    ("KSchG", "", "10002462"),
    ("Seeschiffahrtsorganisation", "10011477", "10011477"),
    ("Lehrpläne allgemeinbildende", "10008568", "10008568"),
])
async def test_outline_lists_every_document_ris_holds_in_order(law, pick, number):
    """Treaties carry no visible label markup, and some documents have no content block."""
    out = await server.get_law_outline(law=law, pick=pick)
    assert _outline_labels(out) == await _api_labels(number, rc.today())
    assert re.search(r"\*Fassung vom \d{2}\.\d{2}\.\d{4}\. RIS holds \d+ documents", out)
    assert "\nURL: https://" in out and out.endswith(server.SOURCE_CONSOLIDATED)


async def test_outline_follows_the_date():
    out = await server.get_law_outline(law="KSchG", date="2010-01-01")
    assert "*Fassung vom 01.01.2010." in out
    assert _outline_labels(out) == await _api_labels("10002462", "2010-01-01")
