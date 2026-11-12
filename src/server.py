"""MCP server that serves Austrian federal law from RIS to language models."""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import date as Date
from typing import Annotated, Any, Literal

import anyio
from pydantic import BeforeValidator, Field
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config
from . import http_app
from . import ris_client as rc
from .content import html_to_markdown, extract_metadata_blocks, outline_version, parse_law_outline
from . import index as fts_index


# get_paragraph fetches each document's text separately, so this caps the upstream requests one call triggers.
MAX_TEXTS = 20

# Pages of 100 read per get_paragraph call. Larger laws are cut with a notice naming how many were not read.
MAX_UNIT_PAGES = 3
NUMBER_PARAM = (
    "Unit number with its suffix. Matches every container: ABGB 1 returns § 1, Art. 10 § 1 and Art. 18 § 1. "
    "Examples: § 1295 ABGB -> 1295; § 879a -> 879a; Art. 7 B-VG or Artikel VII -> 7; Anhang 1 -> 1; "
    "Art. 10 § 1 -> 1; § 1 Abs. 2 Z 3 -> 1. Abschnitt and Hauptstück are headings, use get_law_outline."
)
TO_PARAM = "Range end, inclusive of suffixes: 5 to 6 also returns § 5a and § 6a onward."
KIND_PARAM = "Needs number. Paragraph for §, Artikel for Art., Anlage for annexes, also when a law calls them Anhang."
KINDS = ("Paragraph", "Artikel", "Anlage")


def _kind(value: Any) -> Any:
    """Spells a kind the way RIS requires; RIS rejects Abschnitt.Typ in any other capitalisation."""
    if not isinstance(value, str):
        return value
    return {kind.casefold(): kind for kind in KINDS}.get(value.strip().casefold(), value.strip())

# CC BY 4.0 requires attribution when the data is passed on, and only the gazette wording is binding. The note
# travels with each response because the end user never sees the website.
SOURCE_CONSOLIDATED = (
    "\n---\n*Source: RIS, Bundeskanzleramt Österreich (CC BY 4.0). Consolidated text — "
    "no guarantee of accuracy, currency or completeness; only the wording published in the "
    "Bundesgesetzblatt (\"BGBl authentisch\") is legally binding. Not legal advice.*"
)
SOURCE_AUTHENTIC = (
    "\n---\n*Source: RIS, Bundeskanzleramt Österreich (CC BY 4.0), Bundesgesetzblatt "
    "authentisch. Not legal advice.*"
)


def _sourced(body: str, authentic: bool = False) -> str:
    """Appends the source, licence and bindingness note to a response built from RIS data."""
    return body + (SOURCE_AUTHENTIC if authentic else SOURCE_CONSOLIDATED)


def _expiry_note(meta: dict[str, Any]) -> str:
    expiry = meta["valid_until"] or meta["repealed"]
    return f"  valid until {expiry}" if expiry else ""


LAW_PARAM = "Name, short title or abbreviation, e.g. ABGB, Konsumentenschutzgesetz. Words of 3 or more characters match as prefixes."
PICK_PARAM = (
    "Gesetzesnummer copied from this tool's own list, nothing else. RIS reuses numbers, one number can mean "
    "multiple documents. A number from memory can therefore point to a different or repealed law. Leave pick "
    "empty and read the list instead."
)
DATE_PARAM = "YYYY-MM-DD; the law as in force that day."


async def _resolve(law: str, pick: str, date: str) -> rc.Resolution | str:
    """Returns the resolution, or the text to answer with when the name does not lead to one law."""
    try:
        return await rc.resolve_law(law, date, pick)
    except rc.LawLookupError as exc:
        return str(exc)


DATE_ERROR = "'{}' is not a date in YYYY-MM-DD format."


def _iso_date(text: str) -> str | None:
    """Normalises a YYYY-MM-DD date, empty meaning today, or returns None. RIS answers any other FassungVom with
    a SOAP fault."""
    if not text.strip():
        return rc.today()
    try:
        return Date.fromisoformat(text.strip()).isoformat()
    except ValueError:
        return None


def _transport_security() -> TransportSecuritySettings | None:
    """Host and Origin allowlist for public deployments. FastMCP turns this check on only for loopback hosts,
    so binding 0.0.0.0 would run without it."""
    if not config.PUBLIC_HOSTS:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=config.PUBLIC_HOSTS,
        allowed_origins=[f"https://{host}" for host in config.PUBLIC_HOSTS],
    )


mcp = FastMCP(
    "ris-mcp",
    instructions=(
        "Independent open-source reader for Austria's public RIS OGD API. Not affiliated "
        "with, endorsed by or operated by the Austrian government, the Bundeskanzleramt or "
        "RIS. Documents are returned as RIS serves them; consolidated law can be outdated or "
        "incomplete and is not legally binding — only the Bundesgesetzblatt (\"BGBl "
        "authentisch\") is. Treat results as research material, never as legal advice, and "
        "keep the source note attached when quoting them."
    ),
    website_url="https://ris-mcp.noahpfister.com",
    dependencies=["httpx", "cachetools", "selectolax"],
    host=config.HOST,
    port=config.PORT,
    log_level=config.LOG_LEVEL,
    # Stateless HTTP needs no session affinity behind the tunnel. JSON responses avoid SSE, which the Cloudflare
    # proxy buffers.
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security(),
)


SEARCH_PAGE_SIZE = 50


@mcp.tool()
async def search_law(
    query: Annotated[str, Field(description="Full-text search terms")],
    law: Annotated[str, Field(description=LAW_PARAM)] = "",
    page: Annotated[int, Field(description=f"Result page, {SEARCH_PAGE_SIZE} hits each")] = 1,
    pick: Annotated[str, Field(description=PICK_PARAM)] = "",
    date: Annotated[str, Field(description=DATE_PARAM)] = "",
) -> str:
    """Search Austrian federal law (Bundesrecht) by keyword.

    Matching is verbatim, so use the statute's own terms: Kleinunternehmer, not kleine Gewerbetreibende.
    No relevance ranking; hits come newest entry into force first.
    """
    # Resolves the optional law to a Gesetzesnummer, then lets RIS sort all hits by entry into force and
    # return the requested page. RIS has no relevance ranking, and sorting one page locally would show an
    # arbitrary 50 hits in date order.
    if page < 1:
        return "page starts at 1."
    date = _iso_date(date)
    if date is None:
        return DATE_ERROR.format(date)
    header = ""
    scope: dict[str, str] = {}
    if law.strip():
        resolution = await _resolve(law, pick, date)
        if isinstance(resolution, str):
            return resolution
        header = rc.describe_resolution(resolution) + "\n"
        scope["gesetzesnummer"] = resolution.law.number

    refs, total = await rc.search_bundesrecht(
        suchworte=query,
        fassung_vom=date,
        sort_by="Inkrafttretensdatum",
        descending=True,
        seite=page,
        pro_seite="Fifty",
        **scope,
    )
    pages = math.ceil(total / SEARCH_PAGE_SIZE)
    if not refs:
        return f"RIS found 0 documents for '{query}' in force {rc._when(date)}."

    more = f" Next: page={page + 1}." if page < pages else ""
    lines = [f"{header}Found {total} result(s) in force {rc._when(date)}, newest in force first. Page {page} of {pages}.{more}\n"]
    for ref in refs:
        meta = rc._meta_from_ref(ref)
        lines.append(f"**{meta['short_title']}** {_label(ref)}{_expiry_note(meta)}")
        lines.append(f"  Document: {meta['document_id']}")
        lines.append(f"  In force: {meta['in_force_from']}")
        lines.append("")

    return _sourced("\n".join(lines))


async def _units(law: str, pick: str, number: str, to: str, kind: str, date: str) -> str:
    """Serves get_paragraph.

    Sends each Abschnitt parameter only when set, suffixes in lower case as RIS labels them. Returns full text for the first MAX_TEXTS documents RIS sent, in RIS order, and lists the
    rest by label.
    """
    number, to = number.strip().lower(), to.strip().lower()
    if (to or kind) and not number:
        return "to and kind need number; RIS ignores them without Abschnitt.Von."

    resolution = await _resolve(law, pick, date)
    if isinstance(resolution, str):
        return resolution

    refs, total = await rc.search_bundesrecht_pages(
        MAX_UNIT_PAGES,
        gesetzesnummer=resolution.law.number,
        abschnitt_von=number,
        abschnitt_bis=to,
        abschnitt_typ=kind,
        fassung_vom=date,
    )
    sent = [f"Abschnitt.{name}={value}" for name, value in (("Von", number), ("Bis", to), ("Typ", kind)) if value]
    asked = ", ".join(sent) or "the whole law"
    if not refs:
        return f"RIS returned no document of {resolution.law.short_title} in force {rc._when(date)} for {asked}."

    shown, later = refs[:MAX_TEXTS], refs[MAX_TEXTS:]
    parts = [rc.describe_resolution(resolution), "", f"*RIS returned {total} documents for {asked}.*", ""]
    as_of = "" if date == rc.today() else f" (as of {date})"
    # Fetched one at a time: concurrent fetches draw RIS throttling, and the backoff then pushes a
    # 15-paragraph range past 60 seconds.
    for ref in shown:
        meta = rc._meta_from_ref(ref)
        text = html_to_markdown(await rc.fetch_document_html(ref))
        parts.append(f"### {meta['short_title']} {_label(ref)}{as_of}")
        parts.append(f"*{meta['kundmachung']}*")
        parts.append(f"*In force from: {meta['in_force_from']}{_expiry_note(meta)}*")
        parts.append(f"*Document: {meta['document_id']}*")
        parts.append("")
        parts.append(text)
        parts.append(f"URL: {meta['doc_url']}")
        parts.append("")
    if later:
        parts.append(f"*Full text for the first {MAX_TEXTS}; ask for these by number: {_labels(later)}.*")
    if len(refs) < total:
        parts.append(f"*{total - len(refs)} more documents were not read; narrow with number and to.*")

    return _sourced("\n".join(parts))


def _label(ref: dict[str, Any]) -> str:
    """The RIS label of a document, followed by RIS's Uebergangsrecht value where it sets one."""
    meta = rc._meta_from_ref(ref)
    return meta["paragraph"] + (f" [{meta['transitional']}]" if meta["transitional"] else "")


def _labels(refs: list[dict[str, Any]]) -> str:
    return ", ".join(_label(ref) for ref in refs)


@mcp.tool()
async def get_paragraph(
    law: Annotated[str, Field(description=LAW_PARAM)],
    number: Annotated[str, Field(description=NUMBER_PARAM)] = "",
    to: Annotated[str, Field(description=TO_PARAM)] = "",
    kind: Annotated[
        Literal["", "Paragraph", "Artikel", "Anlage"], BeforeValidator(_kind), Field(description=KIND_PARAM)
    ] = "",
    pick: Annotated[str, Field(description=PICK_PARAM)] = "",
    date: Annotated[str, Field(description=DATE_PARAM)] = "",
) -> str:
    """Fetch paragraphs, articles or annexes of an Austrian law. Takes no citation text; convert it into number, to and kind."""
    iso = _iso_date(date)
    if iso is None:
        return DATE_ERROR.format(date)
    return await _units(law, pick, number, to, kind, iso)


@mcp.tool()
async def get_statute(
    name: Annotated[str, Field(description=LAW_PARAM)],
    pick: Annotated[str, Field(description=PICK_PARAM)] = "",
    date: Annotated[str, Field(description=DATE_PARAM)] = "",
) -> str:
    """Fetch the preamble and the first paragraphs or articles of a law as in force on a date."""
    iso = _iso_date(date)
    if iso is None:
        return DATE_ERROR.format(date)
    resolution = await _resolve(name, pick, iso)
    if isinstance(resolution, str):
        return resolution

    preamble = resolution.law.preamble
    blocks = extract_metadata_blocks(await rc.fetch_document_html(preamble))
    parts = [rc.describe_resolution(resolution), "", f"# {blocks.get('Kurztitel', resolution.law.short_title)}", ""]
    for label in ("Kundmachungsorgan", "Typ", "Inkrafttretensdatum", "Abkürzung", "Index"):
        if label in blocks:
            parts.append(f"**{label}:** {blocks[label]}")
    preamble_meta = rc._meta_from_ref(preamble)
    parts.extend(["", "**Änderungen:**", _amendments(preamble_meta), f"URL: {preamble_meta['doc_url']}", ""])

    # Without a sort column RIS returns document order: the § 0 preamble first, transitional provisions last.
    refs, total = await rc.search_bundesrecht(
        gesetzesnummer=resolution.law.number, fassung_vom=iso, pro_seite="Ten"
    )
    body = [r for r in refs if rc._meta_from_ref(r)["paragraph"] != "§ 0" and not rc._meta_from_ref(r)["transitional"]]
    if body:
        parts.append(f"*{total} documents in force {rc._when(iso)}. Showing the first {len(body)}; get_law_outline lists all.*\n")
        htmls = await asyncio.gather(*[rc.fetch_document_html(r) for r in body])
        for ref, html in zip(body, htmls):
            meta = rc._meta_from_ref(ref)
            parts.append(f"### {meta['paragraph']}")
            parts.append(html_to_markdown(html))
            parts.append(f"URL: {meta['doc_url']}")
            parts.append("")

    return _sourced("\n".join(parts))


@mcp.tool()
async def get_law_outline(
    law: Annotated[str, Field(description=LAW_PARAM)],
    pick: Annotated[str, Field(description=PICK_PARAM)] = "",
    date: Annotated[str, Field(description=DATE_PARAM)] = "",
) -> str:
    """Table of contents of a law: each document's label with its headings, and section headings."""
    iso = _iso_date(date)
    if iso is None:
        return DATE_ERROR.format(date)
    resolution = await _resolve(law, pick, iso)
    if isinstance(resolution, str):
        return resolution

    meta = rc._meta_from_ref(resolution.law.preamble)
    if not meta["outline_url"]:
        return f"RIS gives no consolidated version URL for {meta['short_title']}."
    separator = "&" if "?" in meta["outline_url"] else "?"
    outline_url = f"{meta['outline_url']}{separator}FassungVom={iso}"

    html, (_, total) = await asyncio.gather(
        rc._get_html(outline_url),
        rc.search_bundesrecht(gesetzesnummer=resolution.law.number, fassung_vom=iso),
    )
    version = outline_version(html) or f"Fetched on {rc.today()}; RIS states no version date for this page"
    parts = [
        rc.describe_resolution(resolution),
        "",
        f"# {meta['short_title']}, table of contents",
        f"*{version}. RIS holds {total} documents for this law in force {rc._when(iso)}; full text per unit via get_paragraph.*",
        "",
        parse_law_outline(html),
        "",
        f"URL: {outline_url}",
    ]
    return _sourced("\n".join(parts))


# Characters of gazette text per answer, picked by feel. Long amending acts exceed 100k characters, and the URL
# carries the full entry.
BGBL_TEXT_LIMIT = 6000
# The authentic gazette in BgblAuth starts in 2004. Older issues are scanned PDFs outside this API.
BGBL_AUTH_FROM = 2004
# Optional Teil as I, II or III, optional "Nr.", then number/year.
_BGBL = re.compile(r"(?:\b(I{1,3})\b\.?\s*)?(?:Nr\.?\s*)?(\d+)\s*/\s*(\d{4})\b", re.IGNORECASE)


@mcp.tool()
async def lookup_bgbl(
    reference: Annotated[str, Field(description="BGBl reference with Teil, e.g. 'BGBl. I Nr. 50/2023', 'II 50/2023'; without Teil all three are listed")],
) -> str:
    """Look up an authentic Bundesgesetzblatt entry and return its metadata and text."""
    # Reads Teil, number and year. A number/year exists once per Teil, so without a Teil all three are
    # searched and several hits are listed for the caller to choose. One hit returns its metadata and its
    # text up to BGBL_TEXT_LIMIT.
    match = _BGBL.search(reference)
    if not match:
        return f"Could not read a BGBl number from '{reference}'. Use a form like 'BGBl. I Nr. 50/2023'."
    teil, number, year = (match.group(1) or "").upper(), match.group(2), int(match.group(3))

    teile = (len(teil),) if teil else (1, 2, 3)
    refs, _ = await rc.search_bgbl_auth(f"{number}/{year}", teile)
    wanted = f"BGBl. {teil} Nr. {number}/{year}" if teil else f"BGBl. Nr. {number}/{year}"
    if not refs:
        older = f" The authentic gazette in RIS starts in {BGBL_AUTH_FROM}." if year < BGBL_AUTH_FROM else ""
        return f"RIS found no {wanted} in the authentic gazette.{older}"

    if len(refs) > 1:
        lines = [f"{wanted} exists in {len(refs)} parts of the gazette. Ask again with the Teil:"]
        for meta in sorted((rc.bgbl_meta(ref) for ref in refs), key=lambda m: m["number"]):
            lines.append(f"- {meta['number']} ({meta['type']}, {meta['issued']}): {meta['short_title'] or meta['title']}")
        return "\n".join(lines)

    meta = rc.bgbl_meta(refs[0])
    text = html_to_markdown(await rc.fetch_document_html(refs[0]))
    lines = [
        f"## {meta['number']}",
        f"**Titel:** {meta['title']}",
        f"**Typ:** {meta['type']}",
        f"**Ausgabedatum:** {meta['issued']}",
        f"**ID:** {meta['id']}",
        f"**URL:** {meta['url']}",
    ]
    if meta["pdf"]:
        lines.append(f"**Authentic PDF:** {meta['pdf']}")
    if text:
        cut = len(text) > BGBL_TEXT_LIMIT
        lines.append(f"\n### Text\n{text[:BGBL_TEXT_LIMIT]}")
        if cut:
            lines.append(f"\n*Text cut at {BGBL_TEXT_LIMIT} of {len(text)} characters; the URL has the full entry.*")

    return _sourced("\n".join(lines), authentic=True)


@mcp.tool()
async def get_amendment_timeline(
    law: Annotated[str, Field(description=LAW_PARAM)],
    pick: Annotated[str, Field(description=PICK_PARAM)] = "",
    date: Annotated[str, Field(description=DATE_PARAM)] = "",
) -> str:
    """Return the amendments RIS lists for a law, as of a date, in RIS's order."""
    iso = _iso_date(date)
    if iso is None:
        return DATE_ERROR.format(date)
    resolution = await _resolve(law, pick, iso)
    if isinstance(resolution, str):
        return resolution

    meta = rc._meta_from_ref(resolution.law.preamble)
    lines = [rc.describe_resolution(resolution), "", f"## Amendments: {meta['short_title']}", "", _amendments(meta), "", f"URL: {meta['doc_url']}"]
    return _sourced("\n".join(lines))


def _amendments(meta: dict[str, Any]) -> str:
    """The Aenderung field of a preamble as RIS gives it, one line per line."""
    lines = [line.strip() for line in meta["amendments"].splitlines() if line.strip()]
    return "\n".join(lines) or f"RIS lists no amendments; original publication {meta['kundmachung']}."


async def who_mentions(
    reference: Annotated[str, Field(description="Citation string to search for, e.g. '§ 1295 ABGB' or 'Art. 7 B-VG'")],
    limit: Annotated[int, Field(description="At most 100")] = 20,
) -> str:
    """Reverse citation lookup: provisions that cite the one named."""
    # SQLite treats a negative LIMIT as unbounded, so -1 would return the whole match set.
    limit = max(1, min(limit, 100))

    # SQLite calls block and an FTS5 scan can be slow, so they run in a thread to keep other requests moving.
    count = await asyncio.to_thread(fts_index.doc_count)
    if count == 0:
        return (
            "Local index is empty. Build it first by running:\n"
            "  python3 -m src.index\n"
            "That crawls ~441k documents at roughly 9/s, so plan for an overnight run."
        )

    if not fts_index.fts_query(reference):
        return f"'{reference}' holds no searchable term."

    results = await asyncio.to_thread(fts_index.search_fts, reference, limit)
    if not results:
        return f"No documents mention '{reference}' in the local index ({count} docs indexed)."

    lines = [f"Documents mentioning '{reference}' ({len(results)} results from {count} indexed):\n"]
    for r in results:
        lines.append(f"**{r['short_title']}** {r['paragraph']}")
        lines.append(f"  Document: {r['document_id']}")
        lines.append(f"  In force: {r['in_force_from']}")
        lines.append(f"  URL: {r['doc_url']}")
        lines.append("")

    return _sourced("\n".join(lines))


# Hosted instances run without the index, and hiding the tool there beats offering one that always fails.
if config.index_enabled():
    mcp.add_tool(who_mentions)


@mcp.custom_route(http_app.HEALTH_PATH, methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Liveness probe, plus which tool surface this instance is serving.

    An empty or stale index is reported, not failed: the other seven tools hit
    the RIS API directly and work without it.
    """
    payload: dict[str, Any] = {
        "status": "ok",
        "index": config.index_enabled(),
        "tools": len(await mcp.list_tools()),
    }
    if config.index_enabled():
        payload["docs"] = await asyncio.to_thread(fts_index.doc_count)
        payload["last_crawl"] = await asyncio.to_thread(fts_index.last_crawl)
    return JSONResponse(payload)


async def _serve_stdio() -> None:
    try:
        await mcp.run_stdio_async()
    finally:
        await rc.close()


def main() -> None:
    config.validate_runtime()
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if config.TRANSPORT == "streamable-http":
        anyio.run(http_app.serve, mcp)
    else:
        anyio.run(_serve_stdio)


if __name__ == "__main__":
    main()
