"""in-memory BrKons with semantics observed live: Titel substring, numeric Abschnitt range, paging, sort"""
from __future__ import annotations

import re
from typing import Any

from src import ris_client as rc

_PAGE = {"Ten": 10, "Twenty": 20, "Fifty": 50, "OneHundred": 100}
_UNIT = re.compile(r"(§|Art\.|Anl\.)\s*(\d+)([a-z]*)")
_KIND = {"Paragraph": "§", "Artikel": "Art.", "Anlage": "Anl."}


def ref(
    label: str,
    *,
    law: str = "10001622",
    title: str = "Allgemeines bürgerliches Gesetzbuch",
    abbreviation: str = "ABGB",
    in_force: str = "2000-01-01",
    expiry: str = "",
    transitional: bool = False,
    text: str = "",
) -> dict[str, Any]:
    brkons = {
        "ArtikelParagraphAnlage": label,
        "Gesetzesnummer": law,
        "Abkuerzung": abbreviation,
        "Inkrafttretensdatum": in_force,
        "Ausserkrafttretensdatum": expiry,
        "Kundmachungsorgan": "BGBl. Nr. 1/2000",
        "Dokumenttyp": "Paragraph",
        "GesamteRechtsvorschriftUrl": f"https://example.invalid/outline/{law}",
    }
    if transitional:
        brkons["Uebergangsrecht"] = "ÜR"
    return {
        "_text": text or f"{label} {title}",
        "Data": {
            "Metadaten": {
                "Technisch": {"ID": f"{law}:{label}:{in_force}"},
                "Allgemein": {"DokumentUrl": f"https://example.invalid/{law}/{label}"},
                "Bundesrecht": {"Kurztitel": title, "BrKons": brkons},
            },
            "Dokumentliste": {"ContentReference": {"Urls": {"ContentUrl": [
                {"DataType": "Html", "Url": f"https://example.invalid/{law}/{label}.html"},
            ]}}},
        },
    }


def law(number: str, title: str, abbreviation: str, *labels: str, **kwargs: Any) -> list[dict[str, Any]]:
    """§ 0 preamble plus given units, document order"""
    return [ref(label, law=number, title=title, abbreviation=abbreviation, **kwargs) for label in ("§ 0", *labels)]


def _in_range(label: str, typ: str, lo: str, hi: str) -> bool:
    kind = _KIND[typ]
    units = [(k, int(n), s) for k, n, s in _UNIT.findall(label) if k == kind]
    if not units:
        return False
    _, number, suffix = units[-1]
    lo_m, hi_m = re.fullmatch(r"(\d+)([a-z]*)", lo), re.fullmatch(r"(\d+)([a-z]*)", hi)
    low = (int(lo_m.group(1)), lo_m.group(2))
    high = (int(hi_m.group(1)), hi_m.group(2) or "\uffff")
    return low <= (number, suffix) <= high


class FakeRis:
    def __init__(self, docs: list[dict[str, Any]]):
        self.docs = docs
        self.calls: list[dict[str, Any]] = []
        self.fetched: list[str] = []

    async def search_bundesrecht(self, **kw: Any) -> tuple[list[dict[str, Any]], int]:
        self.calls.append(kw)
        hits = []
        for doc in self.docs:
            meta = rc._meta_from_ref(doc)
            if kw.get("gesetzesnummer") and meta["law_number"] != kw["gesetzesnummer"]:
                continue
            titel = kw.get("titel", "").casefold()
            if titel and titel not in meta["short_title"].casefold() and titel not in meta["abbreviation"].casefold():
                continue
            if kw.get("suchworte") and kw["suchworte"].casefold() not in doc["_text"].casefold():
                continue
            if kw.get("abschnitt_von"):
                von = kw["abschnitt_von"]
                if not _in_range(meta["paragraph"], kw.get("abschnitt_typ", "Paragraph"), von, kw.get("abschnitt_bis") or von):
                    continue
            hits.append(doc)
        if kw.get("sort_by") == "Inkrafttretensdatum":
            hits.sort(key=lambda d: rc._meta_from_ref(d)["in_force_from"], reverse=kw.get("descending", False))
        size = _PAGE[kw.get("pro_seite", "Ten")]
        page = kw.get("seite", 1)
        return hits[(page - 1) * size:page * size], len(hits)

    async def fetch_document_html(self, doc: dict[str, Any]) -> str:
        self.fetched.append(rc._meta_from_ref(doc)["paragraph"])
        return (
            "<html><body><div class='contentBlock'><h1 class='Titel'>Text</h1>"
            f"<p>{doc['_text']}</p></div></body></html>"
        )

    def install(self, monkeypatch: Any) -> "FakeRis":
        monkeypatch.setattr(rc, "search_bundesrecht", self.search_bundesrecht)
        monkeypatch.setattr(rc, "fetch_document_html", self.fetch_document_html)
        return self
