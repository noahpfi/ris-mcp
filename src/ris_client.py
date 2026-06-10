"""
HTTP wrapper for the RIS OGD API v2.6.
Base URL: https://data.bka.gv.at/ris/api/v2.6/
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from cachetools import TTLCache

BASE_URL = "https://data.bka.gv.at/ris/api/v2.6/Bundesrecht"
CONTENT_BASE = "https://www.ris.bka.gv.at"
USER_AGENT = "ris-mcp/0.1 (github.com/ris-mcp; legal research tool)"

_cache: TTLCache = TTLCache(maxsize=512, ttl=3600)
_http: httpx.AsyncClient | None = None


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
            resp = await _client().get(BASE_URL, params=params)
            if resp.status_code == 429:
                await asyncio.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            data = resp.json()
            _cache[key] = data
            return data
        except httpx.TimeoutException:
            if attempt == 2:
                raise
            await asyncio.sleep(1)

    raise RuntimeError("RIS API unreachable after retries")


async def _get_html(url: str) -> str:
    key = f"html:{url}"
    if key in _cache:
        return _cache[key]

    for attempt in range(5):
        try:
            resp = await _client().get(url)
            if resp.status_code in (429, 503):
                wait = 2 ** attempt
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            _cache[key] = resp.text
            return resp.text
        except httpx.TransportError:
            if attempt == 4:
                raise
            await asyncio.sleep(2 ** attempt)

    logging.getLogger(__name__).warning("Skipping %s after retries", url)
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


def _meta_from_ref(ref: dict[str, Any]) -> dict[str, Any]:
    meta = ref.get("Data", {}).get("Metadaten", {})
    technisch = meta.get("Technisch", {})
    allgemein = meta.get("Allgemein", {})
    bundesrecht = meta.get("Bundesrecht", {})
    brkons = bundesrecht.get("BrKons", {})

    return {
        "document_id": technisch.get("ID", ""),
        "doc_url": allgemein.get("DokumentUrl", ""),
        "geaendert": allgemein.get("Geaendert", ""),
        "short_title": bundesrecht.get("Kurztitel", ""),
        "abbreviation": brkons.get("Abkuerzung", bundesrecht.get("Abkuerzung", "")),
        "paragraph": brkons.get("ArtikelParagraphAnlage", ""),
        "paragraph_number": brkons.get("Paragraphnummer", ""),
        "kundmachung": brkons.get("Kundmachungsorgan", ""),
        "in_force_from": brkons.get("Inkrafttretensdatum", ""),
        "repealed": brkons.get("Ausserkrafttretensdatum", ""),
        "outline_url": brkons.get("GesamteRechtsvorschriftUrl", ""),
        "doc_type": brkons.get("Dokumenttyp", ""),
        "eli": bundesrecht.get("Eli", ""),
    }


async def search_bundesrecht(
    *,
    suchworte: str = "",
    titel: str = "",
    abschnitt_von: str = "",
    abschnitt_bis: str = "",
    abschnitt_typ: str = "Paragraph",
    fassung_vom: str = "",
    seite: int = 1,
    pro_seite: str = "Ten",
    dokument_nummer: str = "",
) -> tuple[list[dict[str, Any]], int]:
    params: dict[str, str] = {"Applikation": "BrKons"}
    if suchworte:
        params["Suchworte"] = suchworte
    if titel:
        params["Titel"] = titel
    if abschnitt_von:
        params["Abschnitt.Von"] = abschnitt_von
        params["Abschnitt.Bis"] = abschnitt_bis or abschnitt_von
        params["Abschnitt.Typ"] = abschnitt_typ
    if fassung_vom:
        params["FassungVom"] = fassung_vom
    if dokument_nummer:
        params["Dokumentnummer"] = dokument_nummer
    params["Seitennummer"] = str(seite)
    params["DokumenteProSeite"] = pro_seite

    data = await _get_json(params)
    refs, total = _extract_docs(data)
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


def best_law_match(refs: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """Pick the ref whose abbreviation or short title best matches query, preferring live laws."""
    import re as _re
    if not refs:
        return None
    live = [r for r in refs if not _meta_from_ref(r)["repealed"]]
    pool = live if live else refs
    q = query.strip().upper()
    # strip trailing year for fuzzy matching, e.g. "UStG" matches "UStG 1994"
    q_base = _re.sub(r'\s*\d{4}\s*$', '', q).strip()
    for ref in pool:
        m = _meta_from_ref(ref)
        if m["abbreviation"].upper() == q:
            return ref
    for ref in pool:
        m = _meta_from_ref(ref)
        abbr_base = _re.sub(r'\s*\d{4}\s*$', '', m["abbreviation"]).upper().strip()
        if abbr_base == q_base and q_base:
            return ref
    for ref in pool:
        m = _meta_from_ref(ref)
        if m["short_title"].upper() == q:
            return ref
    return pool[0]


async def fetch_document_html(ref: dict[str, Any]) -> str:
    url = _html_url_from_ref(ref)
    if not url:
        raise ValueError("No HTML URL found in document reference")
    return await _get_html(url)


async def close():
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()
