"""Client for the RIS OGD API v2.6 at https://data.bka.gv.at/ris/api/v2.6/ and the document pages it links to."""
from __future__ import annotations

import asyncio
import logging
import math
import re
import time
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

# Caps concurrent requests to RIS, since inbound public traffic is unbounded. A slot is held for one request
# and released while a retry waits.
_egress = asyncio.Semaphore(MAX_UPSTREAM)


class RisApiError(RuntimeError):
    """Raised when RIS fails a request. The message states what RIS answered and what the server already tried,
    because FastMCP relays it to the model as the tool result."""


# Statuses and transport errors RIS recovers from on its own. The backoff waits 1, 2 and 4 s between four tries.
RETRY_STATUS = frozenset({429, 503})
ATTEMPTS = 4


def _client() -> httpx.AsyncClient:
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
            timeout=30.0,
            follow_redirects=True,
        )
    return _http


def _ogd_error(resp: httpx.Response) -> str:
    """The message of an OGD Error element in a JSON body, or an empty string."""
    try:
        error = resp.json().get("OgdSearchResult", {}).get("Error", {})
    except ValueError:
        return ""
    return error.get("Message", "") if isinstance(error, dict) else ""


async def _fetch(url: str, params: dict[str, str] | None = None) -> httpx.Response:
    """GET with the retry policy shared by API and document requests.

    Throttling, unavailability and transport errors are retried with backoff. Any other error status raises
    at once with the status, reason and RIS's own error message. After the last try the error names the final
    outcome, the number of tries and the time spent.
    """
    started = time.monotonic()
    outcome, retry_after = "", ""
    for attempt in range(ATTEMPTS):
        if attempt:
            await asyncio.sleep(2 ** (attempt - 1))
        try:
            async with _egress:
                resp = await _client().get(url, params=params)
        except httpx.TransportError as exc:
            outcome, retry_after = f"gave no answer, {type(exc).__name__}", ""
            continue
        if resp.status_code in RETRY_STATUS:
            outcome, retry_after = f"answered {resp.status_code} {resp.reason_phrase}", resp.headers.get("Retry-After", "")
            continue
        if resp.is_error:
            logger.warning("RIS %s for %s", resp.status_code, resp.url)
            detail = _ogd_error(resp)
            raise RisApiError(f"RIS answered {resp.status_code} {resp.reason_phrase}." + (f" RIS message: {detail}" if detail else ""))
        return resp
    waited = time.monotonic() - started
    after = f" RIS sent Retry-After: {retry_after}." if retry_after else ""
    raise RisApiError(f"RIS {outcome} {ATTEMPTS} times; the server already retried with backoff over {waited:.0f} s.{after}")


async def _get_json(params: dict[str, str]) -> dict[str, Any]:
    key = str(sorted(params.items()))
    if key in _cache:
        return _cache[key]
    resp = await _fetch(BASE_URL, params)
    try:
        data = resp.json()
    except ValueError:
        raise RisApiError(
            f"RIS answered {resp.status_code} with a body that isn't JSON: {resp.text[:200]!r}. URL: {resp.url}"
        ) from None
    _cache[key] = data
    return data


async def _get_html(url: str) -> str:
    """Fetches a document page through the cache."""
    key = f"html:{url}"
    if key in _cache:
        return _cache[key]
    resp = await _fetch(url)
    _cache[key] = resp.text
    return resp.text


def _extract_docs(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Returns the document references and the total hit count. RIS sends a single hit as an object, not a list."""
    result = data.get("OgdSearchResult", {})
    if "Error" in result:
        raise RisApiError(f"RIS answered with an error: {result['Error'].get('Message', result['Error'])}")
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
    """Today's ISO date in Vienna. RIS dates are Austrian calendar days, and the server clock may run on UTC."""
    return datetime.now(_VIENNA).date().isoformat()


def _meta_from_ref(ref: dict[str, Any]) -> dict[str, Any]:
    """Flattens the metadata of a consolidated-law document reference into the fields the tools use."""
    meta = ref.get("Data", {}).get("Metadaten", {})
    technisch = meta.get("Technisch", {})
    allgemein = meta.get("Allgemein", {})
    bundesrecht = meta.get("Bundesrecht", {})
    brkons = bundesrecht.get("BrKons", {})
    # Ausserkrafttretensdatum is the last day in force. A future date is a scheduled expiry of a version still in force.
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
        "amendments": brkons.get("Aenderung", ""),
        "in_force_from": brkons.get("Inkrafttretensdatum", ""),
        "repealed": repealed,
        "valid_until": "" if repealed else expiry,
        "outline_url": brkons.get("GesamteRechtsvorschriftUrl", ""),
        "law_number": brkons.get("Gesetzesnummer", ""),
        "transitional": brkons.get("Uebergangsrecht", ""),
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
    """Collects up to max_pages pages of 100 hits, the largest page RIS serves.

    Returns the hits and the total RIS reports. When fewer hits than the total come back, the result was cut,
    and the caller decides how to report that.
    """
    refs, total = await search_bundesrecht(seite=1, pro_seite="OneHundred", **kwargs)
    for page in range(2, min(math.ceil(total / 100), max_pages) + 1):
        more, _ = await search_bundesrecht(seite=page, pro_seite="OneHundred", **kwargs)
        refs.extend(more)
    return refs, total


async def search_bgbl_auth(bgbl_nummer: str, teile: tuple[int, ...] = (1, 2, 3)) -> tuple[list[dict[str, Any]], int]:
    """Searches the authentic gazette in the given Teile. A number such as 50/2023 exists once per Teil,
    so a search over all three can return up to three entries."""
    params = {"Applikation": "BgblAuth", "Bgblnummer": bgbl_nummer, "Seitennummer": "1", "DokumenteProSeite": "Ten"}
    for teil in teile:
        params[f"Teil.SucheInTeil{teil}"] = "true"
    data = await _get_json(params)
    return _extract_docs(data)


def bgbl_meta(ref: dict[str, Any]) -> dict[str, str]:
    """Flattens the metadata of a gazette entry, including the link to its authentic PDF."""
    meta = ref.get("Data", {}).get("Metadaten", {})
    bundesrecht = meta.get("Bundesrecht", {})
    auth = bundesrecht.get("BgblAuth", {})
    urls = ref.get("Data", {}).get("Dokumentliste", {}).get("ContentReference", {}).get("Urls", {}).get("ContentUrl", [])
    return {
        "id": meta.get("Technisch", {}).get("ID", ""),
        "number": auth.get("Bgblnummer", ""),
        "type": auth.get("Typ", ""),
        "issued": auth.get("Ausgabedatum", ""),
        "short_title": bundesrecht.get("Kurztitel", ""),
        "title": bundesrecht.get("Titel", ""),
        "url": meta.get("Allgemein", {}).get("DokumentUrl", ""),
        "pdf": next((u["Url"] for u in urls if u.get("DataType") == "Authentisch"), ""),
    }


# Each page is one upstream call. B-VG is the widest real name seen and matches 64 preambles; five pages hold
# 500 laws, and a name matching more is too vague to list.
MAX_RESOLVE_PAGES = 5
# RIS refuses a wildcard with fewer than 3 characters next to it.
WILDCARD_MIN = 3
_TRAILING_YEAR = re.compile(r"\s+\d{4}$")


class LawLookupError(Exception):
    """Raised when a law name does not lead to one law. The message is the answer the tool returns."""


@dataclass(frozen=True)
class Law:
    number: str
    abbreviation: str
    short_title: str
    preamble: dict[str, Any]


@dataclass(frozen=True)
class Resolution:
    """The law used, the reason code gives for it, the Titel sent to RIS and every other law RIS returned."""

    law: Law
    reason: str
    sent: str
    others: tuple[Law, ...]


def _tidy(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split())


def _norm(text: str) -> str:
    return _tidy(text).casefold()


def _undated(text: str) -> str:
    return _TRAILING_YEAR.sub("", text)


def _wildcarded(name: str) -> str:
    """Appends * to each word long enough for RIS to accept it, so partial words such as Einkommensteuer match."""
    return " ".join(
        word if word.endswith("*") or len(word) < WILDCARD_MIN else f"{word}*" for word in _tidy(name).split()
    )


def _exact_rule(law: Law, query: str) -> tuple[str, bool] | None:
    """The field by which law carries the name query and whether a trailing year was ignored, or None.
    Stricter matches come first."""
    for undated in (False, True):
        for field, value in (("abbreviation", law.abbreviation), ("short title", law.short_title)):
            if value and (_undated(_norm(value)) if undated else _norm(value)) == query:
                return field, undated
    return None


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" + ("" if n == 1 else "s")


def _when(date: str) -> str:
    return "today" if date == today() else f"on {date}"


async def resolve_law(name: str, date: str, pick: str = "") -> Resolution:
    """Leads a law name to one law in force on date, by letting RIS search for it.

    1. Send the name to RIS as Titel, with wildcards on long words, restricted to § 0 preambles in force on
       date. Titel is a whole-word search over title, short title and abbreviation, and every law has exactly
       one preamble, so each hit is one law.
    2. A pick names one of these hits by Gesetzesnummer. A number that is not among them leads nowhere, so a
       stale number from memory gets the current list back.
    3. Without a pick, a law is used only when it is the single hit whose abbreviation or short title equals
       the name, a trailing year aside. Any other outcome lists every hit for the caller to pick from.
    """
    query = _norm(name)
    if not query:
        raise LawLookupError("No law given.")
    if query.isdigit():
        raise LawLookupError(f"'{name}' is a number. Pass the law's name or abbreviation; a Gesetzesnummer goes in pick.")
    sent = _wildcarded(name)
    refs, total = await search_bundesrecht_pages(
        MAX_RESOLVE_PAGES, titel=sent, abschnitt_von="0", abschnitt_typ="Paragraph", fassung_vom=date
    )
    when = _when(date)
    if len(refs) < total:
        raise LawLookupError(
            f"RIS returned {_count(total, 'law')} in force {when} for '{sent}', more than the {len(refs)} this tool lists. "
            "Use more of the law's name."
        )

    laws: dict[str, Law] = {}
    for ref in refs:
        meta = _meta_from_ref(ref)
        laws.setdefault(meta["law_number"], Law(meta["law_number"], _tidy(meta["abbreviation"]), _tidy(meta["short_title"]), ref))
    if not laws:
        raise LawLookupError(
            f"RIS found no law in force {when} for '{sent}'. RIS matches whole words of title, short title and abbreviation."
        )

    rules = {number: _exact_rule(law, query) for number, law in laws.items()}
    ordered = sorted(laws.values(), key=lambda law: (not rules[law.number], law.short_title, law.number))
    pick = pick.strip()
    if pick in laws:
        return Resolution(laws[pick], f"Picked by Gesetzesnummer {pick}.", sent, ())
    exact = [law for law in ordered if rules[law.number]]
    if pick or len(exact) != 1:
        head = f"Gesetzesnummer {pick} is not among them. " if pick else ""
        listing = "\n".join(f"- {_describe(law)}" for law in ordered)
        raise LawLookupError(
            f"RIS returned {_count(len(laws), 'law')} in force {when} for '{sent}'; {len(exact)} named exactly '{_tidy(name)}'. "
            f"{head}Call again with pick=<Gesetzesnummer>:\n{listing}"
        )
    chosen = exact[0]
    field, undated = rules[chosen.number]
    reason = f"Only hit whose {field} equals '{_tidy(name)}'" + (", ignoring a trailing year." if undated else ".")
    return Resolution(chosen, reason, sent, tuple(law for law in ordered if law is not chosen))


def _describe(law: Law) -> str:
    abbreviation = f" ({law.abbreviation})" if law.abbreviation else ""
    return f"{law.short_title}{abbreviation}: {law.number}"


def describe_resolution(resolution: Resolution) -> str:
    """Header naming the law used, the reason and every other hit, so the caller can see a wrong choice."""
    law = resolution.law
    abbreviation = f" ({law.abbreviation})" if law.abbreviation else ""
    lines = [f"*Law used: {law.short_title}{abbreviation}, Gesetzesnummer {law.number}. {resolution.reason}*"]
    if resolution.others:
        lines.append(
            f"*RIS returned {_count(len(resolution.others), 'other law')} for '{resolution.sent}'; "
            "to use one, call again with pick=<Gesetzesnummer>:*"
        )
        lines.extend(f"- {_describe(other)}" for other in resolution.others)
    return "\n".join(lines)


async def fetch_document_html(ref: dict[str, Any]) -> str:
    url = _html_url_from_ref(ref)
    if not url:
        meta = _meta_from_ref(ref)
        raise RisApiError(f"RIS lists no HTML version for {meta['paragraph'] or meta['document_id']}. Document: {meta['doc_url']}")
    return await _get_html(url)


async def close():
    global _http
    if _http and not _http.is_closed:
        await _http.aclose()
