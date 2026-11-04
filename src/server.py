"""RIS MCP server, Austrian federal law for LLMs"""
from __future__ import annotations

import asyncio
import logging
import math
import re
from datetime import date as Date
from typing import Annotated, Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config
from . import http_app
from . import ris_client as rc
from . import sections
from .content import html_to_markdown, extract_metadata_blocks, parse_law_outline
from . import index as fts_index


# get_paragraph fans out one upstream fetch per paragraph -> range = amplification lever, capped
MAX_RANGE = 20

# range query pages; 20 numbers incl suffix variants + ÜR stay far below 300 docs
MAX_UNIT_PAGES = 3
CITATION_FORMS = "1295, § 879a, Art. 7, Anl. 1 or Art. 10 § 1"

# CC BY 4.0 requires attribution on pass-on; only gazette wording binding -> note on response, website reaches nobody
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
    """append source, licence, bindingness to RIS-data response"""
    return body + (SOURCE_AUTHENTIC if authentic else SOURCE_CONSOLIDATED)


def _expiry_note(meta: dict[str, Any]) -> str:
    expiry = meta["valid_until"] or meta["repealed"]
    return f"  valid until {expiry}" if expiry else ""


LAW_PARAM = "Law abbreviation, short title or 8-digit Gesetzesnummer, e.g. ABGB, EStG, 10002462"


async def _resolve(law: str, date: str) -> rc.Resolution | str:
    """resolution, or answer text when name unresolvable"""
    try:
        return await rc.resolve_law(law, date)
    except rc.LawLookupError as exc:
        return str(exc)


def _iso_date(text: str) -> str | None:
    """RIS rejects non-ISO FassungVom with raw SOAP fault -> validate before asking"""
    try:
        return Date.fromisoformat(text.strip()).isoformat()
    except ValueError:
        return None


def _transport_security() -> TransportSecuritySettings | None:
    """Host/Origin allowlist; FastMCP enables it only on loopback -> binding 0.0.0.0 silently drops check"""
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
    # stateless -> dumb tunnel, no affinity; json_response keeps off SSE, suits Cloudflare proxy buffering
    stateless_http=True,
    json_response=True,
    transport_security=_transport_security(),
)


SEARCH_PAGE_SIZE = 50


@mcp.tool()
async def search_law(
    query: Annotated[str, "Full-text search terms"],
    law: Annotated[str, f"Optional, scopes the search to one law. {LAW_PARAM}"] = "",
    page: Annotated[int, f"Result page, {SEARCH_PAGE_SIZE} hits each"] = 1,
) -> str:
    """Search Austrian federal law (Bundesrecht) by keyword, optionally scoped to one statute.

    Note: the RIS API uses verbatim matching — use exact legal terms as they appear in statute
    text (e.g. "Kleinunternehmer" not "kleine Gewerbetreibende", "Rechnung" not "Faktura").
    Prefer get_law_outline to browse a known statute's table of contents.
    Only versions in force today are searched. RIS has no relevance ranking; hits come most
    recently entered into force first, across all pages.
    """
    if page < 1:
        return "page starts at 1."
    header = ""
    scope: dict[str, str] = {}
    if law.strip():
        resolution = await _resolve(law, rc.today())
        if isinstance(resolution, str):
            return resolution
        header = rc.describe_resolution(resolution) + "\n"
        scope["gesetzesnummer"] = resolution.law.number

    # sorted by RIS over all hits; sorting one page locally = arbitrary 50 hits in date order
    refs, total = await rc.search_bundesrecht(
        suchworte=query,
        fassung_vom=rc.today(),
        sort_by="Inkrafttretensdatum",
        descending=True,
        seite=page,
        pro_seite="Fifty",
        **scope,
    )
    pages = math.ceil(total / SEARCH_PAGE_SIZE)
    if not refs:
        if total:
            return f"Page {page} is past the last page, {pages}."
        return "No results in force today. Repealed wording is reachable via get_paragraph_at."

    more = f" Next: page={page + 1}." if page < pages else ""
    lines = [f"{header}Found {total} result(s) in force today, newest in force first. Page {page} of {pages}.{more}\n"]
    for ref in refs:
        meta = rc._meta_from_ref(ref)
        lines.append(f"**{meta['short_title']}** {_label(ref)}{_expiry_note(meta)}")
        lines.append(f"  Document: {meta['document_id']}")
        lines.append(f"  In force: {meta['in_force_from']}")
        lines.append(f"  URL: {meta['doc_url']}")
        lines.append("")

    return _sourced("\n".join(lines))


async def _units(law: str, paragraph: str, to_paragraph: str, date: str, historical: bool) -> str:
    """shared by get_paragraph + get_paragraph_at: parse citation, resolve law, select exact labels, render"""
    start = sections.parse(paragraph)
    end = sections.end_of_range(start, to_paragraph) if start and to_paragraph else start
    if start is None or end is None:
        return f"Cannot read '{paragraph}{' to ' + to_paragraph if to_paragraph else ''}' as a citation. Use forms like {CITATION_FORMS}."
    same_container = [u.key for u in end.outer] == [u.key for u in start.outer]
    if not same_container or end.last.kind != start.last.kind or end.last.key < start.last.key:
        return f"Range {start} to {end} does not run forward within one kind of unit."
    span = end.last.number - start.last.number + 1
    if span > MAX_RANGE:
        return (
            f"Range {start} to {end} covers {span} numbers; the limit is "
            f"{MAX_RANGE} per call. Narrow it, or use get_law_outline for an overview."
        )

    resolution = await _resolve(law, date)
    if isinstance(resolution, str):
        return resolution

    picked: list[dict[str, Any]] = []
    nearby: list[dict[str, Any]] = []
    truncated = False
    for kind in sections.IMPLICIT_KINDS if start.implicit else (start.last.kind,):
        first, last = (start.with_kind(kind), end.with_kind(kind)) if start.implicit else (start, end)
        lo, hi = first.units[0], last.units[0]
        refs, total = await rc.search_bundesrecht_pages(
            MAX_UNIT_PAGES,
            gesetzesnummer=resolution.law.number,
            abschnitt_von=lo.query,
            abschnitt_bis=hi.query,
            abschnitt_typ=sections.ABSCHNITT_TYP[lo.kind],
            fassung_vom=date,
        )
        picked = sections.select(refs, first, last)
        nearby = nearby or refs
        truncated = len(refs) < total
        if picked:
            start, end = first, last
            break

    when = f"on {date}" if historical else "today"
    wanted = f"{start}" if start == end else f"{start} to {end}"
    if not picked:
        lines = [f"No {wanted} in {resolution.law.short_title} in force {when}."]
        if nearby:
            labels = ", ".join(_label(r) for r in nearby[:MAX_RANGE])
            lines.append(f"RIS holds near it: {labels}.")
        if not historical:
            lines.append("Repealed wording is reachable via get_paragraph_at.")
        return "\n".join(lines)

    parts = [rc.describe_resolution(resolution), ""]
    if len(picked) > MAX_RANGE:
        parts.append(f"*{len(picked)} documents match {wanted}; showing the first {MAX_RANGE}, continue after {_label(picked[MAX_RANGE - 1])}.*\n")
        picked = picked[:MAX_RANGE]
    if truncated:
        parts.append(f"*RIS returned more than {MAX_UNIT_PAGES * 100} documents for this range; narrow it to see all.*\n")

    as_of = f" (as of {date})" if historical else ""
    # sequential on purpose; concurrent fetch -> RIS throttling, 15-paragraph range > 60s via _get_html backoff
    for ref in picked:
        meta = rc._meta_from_ref(ref)
        text = html_to_markdown(await rc.fetch_document_html(ref))
        parts.append(f"### {meta['short_title']} {_label(ref)}{as_of}")
        parts.append(f"*{meta['kundmachung']}*")
        parts.append(f"*In force from: {meta['in_force_from']}{_expiry_note(meta)}*")
        parts.append(f"*Document: {meta['document_id']}*")
        parts.append("")
        parts.append(text)
        parts.append("")

    return _sourced("\n".join(parts))


def _label(ref: dict[str, Any]) -> str:
    meta = rc._meta_from_ref(ref)
    return meta["paragraph"] + (" (transitional)" if meta["transitional"] else "")


@mcp.tool()
async def get_paragraph(
    law: Annotated[str, LAW_PARAM],
    paragraph: Annotated[str, f"Paragraph, article or annex, e.g. {CITATION_FORMS}. A bare number tries §, then Art., then Anl."],
    to_paragraph: Annotated[str, f"End of range (optional), at most {MAX_RANGE} numbers from the start"] = "",
) -> str:
    """Fetch the version in force today of one paragraph, article or annex, or a range of them, from an Austrian statute."""
    return await _units(law, paragraph, to_paragraph, rc.today(), historical=False)


@mcp.tool()
async def get_paragraph_at(
    law: Annotated[str, LAW_PARAM],
    paragraph: Annotated[str, f"Paragraph, article or annex, e.g. {CITATION_FORMS}"],
    date: Annotated[str, "Date in YYYY-MM-DD format — returns the version in force on that date"],
) -> str:
    """Fetch the historical version of a paragraph, article or annex as it read on a given date."""
    iso = _iso_date(date)
    if iso is None:
        return f"'{date}' is not a date in YYYY-MM-DD format."
    return await _units(law, paragraph, "", iso, historical=True)


@mcp.tool()
async def get_statute(
    name: Annotated[str, LAW_PARAM],
) -> str:
    """Fetch the preamble and the first live paragraphs or articles of a statute."""
    resolution = await _resolve(name, rc.today())
    if isinstance(resolution, str):
        return resolution

    preamble = resolution.law.preamble
    blocks = extract_metadata_blocks(await rc.fetch_document_html(preamble))
    parts = [rc.describe_resolution(resolution), "", f"# {blocks.get('Kurztitel', resolution.law.short_title)}", ""]
    for label in ("Kundmachungsorgan", "Typ", "Inkrafttretensdatum", "Abkürzung", "Index"):
        if label in blocks:
            parts.append(f"**{label}:** {blocks[label]}")
    if "Änderung" in blocks:
        amendments = blocks["Änderung"].split("\n")
        suffix = f" ... (+{len(amendments)-5} more)" if len(amendments) > 5 else ""
        parts.append(f"\n**Änderungen ({len(amendments)}):** {', '.join(amendments[:5])}{suffix}")
    parts.append("")

    # default order = document order, § 0 first, transitional provisions last
    refs, total = await rc.search_bundesrecht(
        gesetzesnummer=resolution.law.number, fassung_vom=rc.today(), pro_seite="Ten"
    )
    body = [r for r in refs if rc._meta_from_ref(r)["paragraph"] != "§ 0" and not rc._meta_from_ref(r)["transitional"]]
    if body:
        parts.append(f"*{total} documents in force. Showing the first {len(body)}; get_law_outline lists all.*\n")
        htmls = await asyncio.gather(*[rc.fetch_document_html(r) for r in body])
        for ref, html in zip(body, htmls):
            parts.append(f"### {rc._meta_from_ref(ref)['paragraph']}")
            parts.append(html_to_markdown(html))
            parts.append("")

    return _sourced("\n".join(parts))


@mcp.tool()
async def get_law_outline(
    law: Annotated[str, LAW_PARAM],
) -> str:
    """Return full table of contents for a statute: § numbers with their headings, grouped by section.

    Use this to discover which paragraph covers a topic without relying on prior knowledge.
    """
    resolution = await _resolve(law, rc.today())
    if isinstance(resolution, str):
        return resolution

    meta = rc._meta_from_ref(resolution.law.preamble)
    outline_url = meta["outline_url"]
    if not outline_url:
        return f"No outline URL for '{law}'."

    html = await rc._get_html(outline_url)
    outline = parse_law_outline(html)
    if not outline:
        return f"Could not parse outline for '{law}'."

    return _sourced(f"{rc.describe_resolution(resolution)}\n\n# {meta['short_title']} — Table of Contents\n\n{outline}")


# authentic gazette text per call, picked by feel; long amending acts run past 100k chars, link carries the rest
BGBL_TEXT_LIMIT = 6000
# BgblAuth starts 2004; older gazettes = scanned PDFs outside this API
BGBL_AUTH_FROM = 2004
_BGBL = re.compile(r"(?:\b(I{1,3})\b\.?\s*)?(?:Nr\.?\s*)?(\d+)\s*/\s*(\d{4})\b")


@mcp.tool()
async def lookup_bgbl(
    reference: Annotated[str, "BGBl reference with Teil, e.g. 'BGBl. I Nr. 50/2023', 'II 50/2023'; without Teil all three are listed"],
) -> str:
    """Look up an authentic Bundesgesetzblatt entry and return its metadata and text."""
    match = _BGBL.search(reference)
    if not match:
        return f"Could not read a BGBl number from '{reference}'. Use a form like 'BGBl. I Nr. 50/2023'."
    teil, number, year = match.group(1), match.group(2), int(match.group(3))
    if year < BGBL_AUTH_FROM:
        return f"BGBl {number}/{year} predates the authentic electronic gazette, which starts in {BGBL_AUTH_FROM}."

    teile = (len(teil),) if teil else (1, 2, 3)
    refs, _ = await rc.search_bgbl_auth(f"{number}/{year}", teile)
    wanted = f"BGBl. {teil} Nr. {number}/{year}" if teil else f"BGBl. Nr. {number}/{year}"
    if not refs:
        return f"{wanted} not found."

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
    law: Annotated[str, LAW_PARAM],
) -> str:
    """Return an ordered list of every BGBl amendment that touched a statute."""
    resolution = await _resolve(law, rc.today())
    if isinstance(resolution, str):
        return resolution

    blocks = extract_metadata_blocks(await rc.fetch_document_html(resolution.law.preamble))
    short_title = blocks.get("Kurztitel", resolution.law.short_title)
    amendments_raw = blocks.get("Änderung", "")

    if not amendments_raw:
        return f"No amendment list found for {law}."

    amendments = [a.strip() for a in amendments_raw.split("\n") if a.strip()]
    lines = [rc.describe_resolution(resolution), "", f"## Amendment timeline: {short_title}", f"*{len(amendments)} amendments total*", ""]
    for i, a in enumerate(amendments, 1):
        lines.append(f"{i}. {a}")

    return _sourced("\n".join(lines))


async def who_mentions(
    reference: Annotated[str, "Citation string to search for, e.g. '§ 1295 ABGB' or 'Art. 7 B-VG'"],
    limit: Annotated[int, "Max results to return (1-100, default 20)"] = 20,
) -> str:
    """Full-text search the local RIS index for laws that mention a given citation.

    Reverse citation lookup: finds provisions that cite the one you name.
    Backed by a locally built FTS index, so it is only registered where that
    index exists.
    """
    # negative LIMIT = unbounded in SQLite -> clamp, else -1 returns whole match set
    limit = max(1, min(limit, 100))

    # SQLite blocks, FTS5 full scan slow -> off event loop or every request stalls
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


# registered last, conditionally; hosted runs index-free, hidden tool beats always-unavailable one
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
