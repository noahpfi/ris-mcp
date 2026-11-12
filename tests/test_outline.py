"""Outline built from GeltendeFassung markup. Every document is listed, including suffixed paragraphs such as
§ 879a, paragraphs without a heading, annexes and article titles."""
from __future__ import annotations

from src.content import parse_law_outline


def _block(*nodes: str) -> str:
    return "<div class='contentBlock'>" + "".join(nodes) + "</div>"


def _h(cls: str, text: str, tag: str = "h4") -> str:
    return f"<{tag} class='{cls} AlignCenter'>{text}</{tag}>"


def _symbol(text: str, spoken: str, tag: str = "h5") -> str:
    return f"<{tag} class='GldSymbol AlignJustify'><span aria-hidden='true'>{text}</span><span class='sr-only'>{spoken}</span></{tag}>"


PAGE = "<html><body>" + "".join([
    _block("<p>BGBl. Nr. 140/1979</p>"),
    _block(_h("UeberschrG1", "I. HAUPTSTÜCK<br/>Besondere Bestimmungen"), _h("UeberschrG1-AfterG2", "Abschnitt I"),
           _h("UeberschrPara", "Geltungsbereich"), _symbol("§ 1.", "Paragraph eins,")),
    _block(_symbol("§ 2.", "Paragraph 2,")),
    _block(_h("UeberschrPara", "Allgemeine Informationspflichten"), _symbol("§ 5a.", "Paragraph 5 a,")),
    _block(_h("UeberschrG1", "1. TEIL<br/>PERSÖNLICHE STEUERPFLICHT"), _symbol("§ 1.", "Paragraph eins,", tag="h4")),
    _block(_symbol("Artikel 8a.", "Artikel 8 a,")),
    _block(_h("UeberschrPara", "Aufzeichnungspflichten"), _h("UeberschrPara", "Allgemeines"), _symbol("§ 18a.", "Paragraph 18 a,")),
    _block(_h("Anlagenbez", "Anhang I"), _h("UeberschrPara", "Formular"), _h("UeberschrG2", "B. Land Burgenland")),
    _block(_h("UeberschrG1", "Artikel 4<br/>Umsetzungshinweis"), _h("UeberschrPara", "(Anm.: aus BGBl. I Nr. 175/2021)"),
           _symbol("§ 2.", "Paragraph 2,")),
]) + "</body></html>"


def test_every_document_is_listed_in_order_with_suffix_and_heading():
    entries = [line for line in parse_law_outline(PAGE).splitlines() if line and not line.startswith("**")]
    assert entries == [
        "§ 1  Geltungsbereich",
        "§ 2",
        "§ 5a  Allgemeine Informationspflichten",
        "§ 1",
        "Art. 8a",
        "§ 18a  Aufzeichnungspflichten",
        "Anhang I  Formular",
        "§ 2  (Anm.: aus BGBl. I Nr. 175/2021)",
    ]


def test_sections_precede_their_first_document_and_annex_structure_is_not_a_section():
    sections = [line for line in parse_law_outline(PAGE).splitlines() if line.startswith("**")]
    assert sections == [
        "**I. HAUPTSTÜCK Besondere Bestimmungen / Abschnitt I**",
        "**1. TEIL PERSÖNLICHE STEUERPFLICHT**",
        "**Artikel 4 Umsetzungshinweis**",
    ]


def test_screen_reader_duplicates_are_dropped():
    assert "Paragraph" not in parse_law_outline(PAGE)


def test_page_without_documents_is_empty():
    assert parse_law_outline("<html><body><div class='contentBlock'><p>x</p></div></body></html>") == ""
