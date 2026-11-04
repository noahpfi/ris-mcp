"""RIS OGD API v2.6 client, base https://data.bka.gv.at/ris/api/v2.6/"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from cachetools import TTLCache

from .config import MAX_UPSTREAM

logger = logging.getLogger(__name__)

BASE_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
CONTENT_BASE = "https://www.ris.bka.gv.at"
USER_AGENT = "ris-mcp/0.1 (+https://github.com/noahpfi/ris-mcp; legal research tool)"

_VIENNA = ZoneInfo("Europe/Vienna")

_cache: TTLCache = TTLCache(maxsize=512, ttl=3600)
_http: httpx.AsyncClient | None = None

# caps RIS in-flight requests vs unbounded public inbound; held per request, not during backoff
_egress = asyncio.Semaphore(MAX_UPSTREAM)


class RisApiError(RuntimeError):
    """no URL/query/internals in message; FastMCP relays raised text -> would leak upstream query shape"""


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
    return _http


async def _get_json(params: dict[str, str]) -> dict[str, Any]:
    key = str(sorted(params.items()))
    if key in _cache:
        return _cache[key]

    for attempt in range(3):
        try:
            async with _egress:
                resp = await _client().get(BASE_URL, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            if resp.is_error:
                logger.warning("RIS API %s for params %s", resp.status_code, params)
                raise RisApiError(f"RIS API returned HTTP {resp.status_code}.")
            data = resp.json()
            _cache[key] = data
            return data
        except httpx.TimeoutException:
            if attempt == 2:
                raise RisApiError("RIS API timed out.") from None
            await asyncio.sleep(1)

    raise RisApiError("RIS API unreachable after retries.")


async def _get_html(url: str) -> str:
    key = f"html:{url}"
    if key in _cache:
        return _cache[key]

    for attempt in range(5):
        try:
            async with _egress:
                resp = await _client().get(url)
            if resp.status_code in (429, 503):
                wait = 2 ** attempt
                await asyncio.sleep(wait)
                continue
            if resp.is_error:
                logger.warning("RIS content %s for %s", resp.status_code, url)
                raise RisApiError(f"RIS returned HTTP {resp.status_code} for a document.")
            _cache[key] = resp.text
            return resp.text
        except httpx.TransportError:
            if attempt == 4:
                raise RisApiError("Could not reach RIS to fetch a document.") from None
            await asyncio.sleep(2 ** attempt)

    logger.warning("Skipping %s after retries", url)
    return ""


def _extract_docs(data: dict[str, Any]) -> list[dict[str, Any]]:
    result = data.get("OgdSearchResult", {})
    if "Error" in result:
        msg = result["Error"].get("Message", "Unknown RIS error")
        raise ValueError(f"RIS API error: {msg}")
    dr = result.get("OgdDocumentResults", {})
    hits_raw = dr.get("Hits", {})
    total = int(hits_raw.get("#text", 0)) if isinstance(hits_raw, dict) else int(hits_raw)
    refs = dr.get("OgdDocumentReference", [])
    if isinstance(refs, dict):
        refs = [refs]
    return refs, total


def _html_url_from_ref(ref: dict[str, Any]) -> str | None:
    try:
        cr = ref["Data"]["Dokumentliste"]["ContentReference"]
        if isinstance(cr, list):
            cr = cr[0]
        urls = cr["Urls"]["ContentUrl"]
        for u in urls:
            if u["DataType"] == "Html":
                return u["Url"]
    except (KeyError, TypeError, StopIteration):
        return None
    return None


def today() -> str:
    """ISO date in Vienna, RIS dates = Austrian calendar days, server clock may be UTC"""
    return datetime.now(_VIENNA).date().isoformat()


def _meta_from_ref(ref: dict[str, Any]) -> dict[str, Any]:
    meta = ref.get("Data", {}).get("Metadaten", {})
    technisch = meta.get("Technisch", {})
    allgemein = meta.get("Allgemein", {})
    bundesrecht = meta.get("Bundesrecht", {})
    brkons = bundesrecht.get("BrKons", {})
    # Ausserkrafttretensdatum = last day in force; future date = scheduled expiry, still live
    expiry = brkons.get("Ausserkrafttretensdatum", "")
    repealed = expiry if expiry and expiry < today() else ""

    return {
        "document_id": technisch.get("ID", ""),
        "doc_url": allgemein.get("DokumentUrl", ""),
        "short_title": bundesrecht.get("Kurztitel", ""),
        "abbreviation": brkons.get("Abkuerzung", bundesrecht.get("Abkuerzung", "")),
        "paragraph": brkons.get("ArtikelParagraphAnlage", ""),
        "paragraph_number": brkons.get("Paragraphnummer", ""),
        "kundmachung": brkons.get("Kundmachungsorgan", ""),
        "in_force_from": brkons.get("Inkrafttretensdatum", ""),
        "repealed": repealed,
        "valid_until": "" if repealed else expiry,
        "outline_url": brkons.get("GesamteRechtsvorschriftUrl", ""),
        "law_number": brkons.get("Gesetzesnummer", ""),
        "transitional": bool(brkons.get("Uebergangsrecht")),
        "doc_type": brkons.get("Dokumenttyp", ""),
        "eli": bundesrecht.get("Eli", ""),
    }


async def search_bundesrecht(
    *,
    suchworte: str = "",
    titel: str = "",
    gesetzesnummer: str = "",
    abschnitt_von: str = "",
    abschnitt_bis: str = "",
    abschnitt_typ: str = "Paragraph",
    fassung_vom: str = "",
    sort_by: str = "",
    descending: bool = False,
    seite: int = 1,
    pro_seite: str = "Ten",
) -> tuple[list[dict[str, Any]], int]:
    params: dict[str, str] = {"Applikation": "BrKons"}
    if suchworte:
        params["Suchworte"] = suchworte
    if titel:
        params["Titel"] = titel
    if gesetzesnummer:
        params["Gesetzesnummer"] = gesetzesnummer
    if abschnitt_von:
        params["Abschnitt.Von"] = abschnitt_von
        params["Abschnitt.Bis"] = abschnitt_bis or abschnitt_von
        params["Abschnitt.Typ"] = abschnitt_typ
    if fassung_vom:
        params["FassungVom"] = fassung_vom
    if sort_by:
        params["Sortierung.SortedByColumn"] = sort_by
        params["Sortierung.SortDirection"] = "Descending" if descending else "Ascending"
    params["Seitennummer"] = str(seite)
    params["DokumenteProSeite"] = pro_seite

    data = await _get_json(params)
    refs, total = _extract_docs(data)
    return refs, total


async def search_bundesrecht_pages(max_pages: int, **kwargs: Any) -> tuple[list[dict[str, Any]], int]:
    """every page up to max_pages at RIS max page size; caller compares len(refs) to total for truncation"""
    refs, total = await search_bundesrecht(seite=1, pro_seite="OneHundred", **kwargs)
    for page in range(2, min(math.ceil(total / 100), max_pages) + 1):
        more, _ = await search_bundesrecht(seite=page, pro_seite="OneHundred", **kwargs)
        refs.extend(more)
    return refs, total


async def search_bgbl_auth(
    *,
    bgbl_nummer: str = "",
    suchworte: str = "",
    titel: str = "",
    seite: int = 1,
    pro_seite: str = "Ten",
) -> tuple[list[dict[str, Any]], int]:
    params: dict[str, str] = {
        "Applikation": "BgblAuth",
        "Teil.SucheInTeil1": "true",
        "Teil.SucheInTeil2": "true",
        "Teil.SucheInTeil3": "true",
        "Seitennummer": str(seite),
        "DokumenteProSeite": pro_seite,
    }
    if bgbl_nummer:
        params["Bgblnummer"] = bgbl_nummer
    if suchworte:
        params["Suchworte"] = suchworte
    if titel:
        params["Titel"] = titel

    data = await _get_json(params)
    refs, total = _extract_docs(data)
    return refs, total


# one upstream call per page; B-VG = 63 preambles, widest real name seen -> 5 pages = 500 laws, past that name too vague
MAX_RESOLVE_PAGES = 5
AMBIGUITY_LISTED = 25
_GESETZESNUMMER = re.compile(r"\d{8}")
_TRAILING_YEAR = re.compile(r"\s+\d{4}$")


class LawLookupError(Exception):
    """message = tool answer, name unresolvable or ambiguous"""


@dataclass(frozen=True)
class Law:
    number: str
    abbreviation: str
    short_title: str
    preamble: dict[str, Any]


@dataclass(frozen=True)
class Resolution:
    law: Law
    related: tuple[Law, ...]


def _tidy(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _norm(text: str) -> str:
    return _tidy(text).casefold()


def _undated(text: str) -> str:
    return _TRAILING_YEAR.sub("", text)


async def resolve_law(name: str, date: str) -> Resolution:
    """name -> one Gesetzesnummer; Titel = substring search over titles + abbreviations, never queried directly"""
    query = _norm(name)
    if not query:
        raise LawLookupError("No law given.")
    by_number = bool(_GESETZESNUMMER.fullmatch(query))
    refs, total = await search_bundesrecht_pages(
        MAX_RESOLVE_PAGES,
        **({"gesetzesnummer": query} if by_number else {"titel": name.strip()}),
        abschnitt_von="0",
        abschnitt_typ="Paragraph",
        fassung_vom=date,
    )
    if len(refs) < total:
        raise LawLookupError(
            f"'{name}' matches {total} laws. Use the abbreviation, the full short title or the Gesetzesnummer."
        )

    laws: dict[str, Law] = {}
    for ref in refs:
        meta = _meta_from_ref(ref)
        if meta["paragraph"] == "§ 0" and meta["law_number"] not in laws:
            laws[meta["law_number"]] = Law(meta["law_number"], _tidy(meta["abbreviation"]), _tidy(meta["short_title"]), ref)
    if not laws:
        when = "today" if date == today() else f"on {date}"
        raise LawLookupError(f"No law matching '{name}' in force {when}.")
    candidates = list(laws.values())
    if by_number:
        return Resolution(candidates[0], ())

    # abbreviation before title, exact before omitted year; year only stripped law-side -> "UStG 1972" never hits UStG 1994
    # shared abbreviation -> main act = shortest title, rest related
    tiers = (
        lambda law: _norm(law.abbreviation) == query,
        lambda law: _undated(_norm(law.abbreviation)) == query,
        lambda law: _norm(law.short_title) == query,
        lambda law: _undated(_norm(law.short_title)) == query,
    )
    for matches in tiers:
        hits = sorted((law for law in candidates if matches(law)), key=lambda law: (len(law.short_title), law.number))
        if hits:
            return Resolution(hits[0], tuple(hits[1:]))
    if len(candidates) == 1:
        return Resolution(candidates[0], ())

    listing = "\n".join(f"- {_describe(law)}" for law in candidates[:AMBIGUITY_LISTED])
    if len(candidates) > AMBIGUITY_LISTED:
        listing += f"\n- … {len(candidates) - AMBIGUITY_LISTED} more"
    raise LawLookupError(f"'{name}' matches {len(candidates)} laws, name one by abbreviation or Gesetzesnummer:\n{listing}")


def _describe(law: Law) -> str:
    abbreviation = f"{law.abbreviation}, " if law.abbreviation else ""
    return f"{law.short_title} ({abbreviation}Gesetzesnummer {law.number})"


def describe_resolution(resolution: Resolution) -> str:
    """header naming resolved law, shared-abbreviation siblings reachable via their Gesetzesnummer"""
    lines = [f"*Law: {_describe(resolution.law)}*"]
    if resolution.related:
        others = "; ".join(_describe(law) for law in resolution.related)
        lines.append(f"*Same abbreviation, not included: {others}*")
    return "\n".join(lines)


async def fetch_document_html(ref: dict[str, Any]) -> str:
    url = _html_url_from_ref(ref)
    if not url:
        raise ValueError("No HTML URL found in document reference")
    return await _get_html(url)


async def close():
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()
