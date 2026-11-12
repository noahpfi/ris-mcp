"""How a law name is turned into the Titel sent to RIS, and names refused before any request."""
from __future__ import annotations

import pytest

from src import ris_client as rc


@pytest.mark.parametrize("name, sent", [
    ("EStG", "EStG*"),
    ("Einkommensteuer", "Einkommensteuer*"),
    ("Allgemeines  bürgerliches Gesetzbuch", "Allgemeines* bürgerliches* Gesetzbuch*"),
    ("KO", "KO"),
    ("B-VG", "B-VG*"),
    ("UStG*", "UStG*"),
])
def test_wildcards_go_on_words_ris_accepts_them_on(name, sent):
    assert rc._wildcarded(name) == sent


@pytest.mark.parametrize("abbreviation, short_title, name, rule", [
    ("EStG 1988", "Einkommensteuergesetz 1988", "estg 1988", ("abbreviation", False)),
    ("EStG 1988", "Einkommensteuergesetz 1988", "estg", ("abbreviation", True)),
    ("EStG 1988", "Einkommensteuergesetz 1988", "einkommensteuergesetz", ("short title", True)),
    ("UStG 1994", "Umsatzsteuergesetz 1994", "ustg 1972", None),
    ("", "Bausparen gemäß § 108 EStG 1988", "estg", None),
])
def test_exact_rule_names_the_field_and_whether_a_year_was_ignored(abbreviation, short_title, name, rule):
    assert rc._exact_rule(rc.Law("1", abbreviation, short_title, {}), name) == rule


async def test_a_number_as_name_points_to_pick(no_upstream):
    with pytest.raises(rc.LawLookupError, match="goes in pick"):
        await rc.resolve_law("10004570", "2026-01-01")
