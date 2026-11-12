"""MCP server that serves Austrian federal law from RIS to language models."""
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
from .content import html_to_markdown, extract_metadata_blocks, outline_version, parse_law_outline
from . import index as fts_index


# get_paragraph fetches each document of a range separately, so a range lets one call trigger many upstream
# requests. The cap applies both to the numbers a range spans and to the documents it returns.
MAX_RANGE = 20

# Pages of 100 per range query. Twenty numbers with their suffix variants and transitional provisions stay far
# below 300 documents.
MAX_UNIT_PAGES = 3
CITATION_FORMS = "1295, § 879a, Art. 7, Anl. 1 or Art. 10 § 1"

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


LAW_PARAM = (
    "Name, short title or abbreviation of the law, e.g. ABGB, EStG, Konsumentenschutzgesetz. RIS matches whole "
    "words; the server adds * to words of 3 or more characters."
)
PICK_PARAM = (
    "Gesetzesnummer copied from this tool's own list, nothing else. RIS reuses numbers, one number can mean "
    "multiple documents. A number from memory can therefore point to a different or repealed law. Leave pick "
    "empty and read the list instead."
)
DATE_PARAM = "Date in YYYY-MM-DD format; the law is read as in force on that date. Empty means today."


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
    query: Annotated[str, "Full-text search terms"],
    law: Annotated[str, f"Optional, scopes the search to one law. {LAW_PARAM}"] = "",
    page: Annotated[int, f"Result page, {SEARCH_PAGE_SIZE} hits each"] = 1,
    pick: Annotated[str, PICK_PARAM] = "",
    date: Annotated[str, DATE_PARAM] = "",
) -> str:
    """Search Austrian federal law (Bundesrecht) by keyword, optionally scoped to one statute.

    Note: the RIS API uses verbatim matching — use exact legal terms as they appear in statute
    text (e.g. "Kleinunternehmer" not "kleine Gewerbetreibende", "Rechnung" not "Faktura").
    Prefer get_law_outline to browse a known statute's table of contents.
    Only versions in force on the date are searched, today by default. RIS has no relevance ranking; hits come most
    recently entered into force first, across all pages.
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


async def _units(law: str, pick: str, paragraph: str, to_paragraph: str, date: str) -> str:
    """Serves get_paragraph.

    1. Parse the citation and range, and refuse ranges wider than MAX_RANGE numbers before any request.
    2. Lead the law name to its Gesetzesnummer.
    3. Send one Abschnitt query: the number and suffix of each end, and the kind given, or Alle for a bare
       number. RIS compares numbers only, so the answer may hold more than the citation names.
    4. Split the answer without dropping anything. A single citation with a kind gets full text for the labels
       equal to it; a bare number or a range gets full text for everything inside its enclosing units. Every
       other document RIS returned is listed by label.
    """
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

    resolution = await _resolve(law, pick, date)
    if isinstance(resolution, str):
        return resolution

    refs, total = await rc.search_bundesrecht_pages(
        MAX_UNIT_PAGES,
        gesetzesnummer=resolution.law.number,
        abschnitt_von=start.last.query,
        abschnitt_bis=end.last.query,
        abschnitt_typ=start.abschnitt_typ,
        fassung_vom=date,
    )
    exact = start == end and not start.implicit
    named = [r for r in refs if (start.names if exact else start.contains)(rc._meta_from_ref(r)["paragraph"])]
    rest = [r for r in refs if not any(r is n for n in named)]
    wanted = f"{start}" if start == end else f"{start} to {end}"
    cut = f" RIS holds {total} documents for this query; the first {len(refs)} were read." if len(refs) < total else ""

    if not named:
        line = f"No {wanted} in {resolution.law.short_title} in force {rc._when(date)}."
        if refs:
            line += f" RIS returned for {wanted}: {_labels(refs)}."
        return line + cut

    shown, later = named[:MAX_RANGE], named[MAX_RANGE:]
    parts = [rc.describe_resolution(resolution), ""]
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
        first = rc._meta_from_ref(later[0])["paragraph"]
        parts.append(f"*{len(later)} more over the limit of {MAX_RANGE}: {_labels(later)}. Continue with paragraph={first}.*")
    if rest:
        parts.append(f"RIS also returned for {wanted}: {_labels(rest)}. Ask for one by its full label.")
    if cut:
        parts.append(cut.strip())

    return _sourced("\n".join(parts))


def _label(ref: dict[str, Any]) -> str:
    """The RIS label of a document, followed by RIS's Uebergangsrecht value where it sets one."""
    meta = rc._meta_from_ref(ref)
    return meta["paragraph"] + (f" [{meta['transitional']}]" if meta["transitional"] else "")


def _labels(refs: list[dict[str, Any]]) -> str:
    return ", ".join(_label(ref) for ref in refs)


@mcp.tool()
async def get_paragraph(
    law: Annotated[str, LAW_PARAM],
    paragraph: Annotated[str, f"Paragraph, article or annex, e.g. {CITATION_FORMS}. A bare number returns every kind RIS holds under it."],
    to_paragraph: Annotated[str, f"End of range (optional), at most {MAX_RANGE} numbers from the start"] = "",
    pick: Annotated[str, PICK_PARAM] = "",
    date: Annotated[str, DATE_PARAM] = "",
) -> str:
    """Fetch one paragraph, article or annex, or a range of them, from an Austrian law as in force on a date."""
    iso = _iso_date(date)
    if iso is None:
        return DATE_ERROR.format(date)
    return await _units(law, pick, paragraph, to_paragraph, iso)


@mcp.tool()
async def get_statute(
    name: Annotated[str, LAW_PARAM],
    pick: Annotated[str, PICK_PARAM] = "",
    date: Annotated[str, DATE_PARAM] = "",
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
    law: Annotated[str, LAW_PARAM],
    pick: Annotated[str, PICK_PARAM] = "",
    date: Annotated[str, DATE_PARAM] = "",
) -> str:
    """Return the table of contents of a law as RIS lists it: each document's label with its headings, and section headings.

    Use this to discover which paragraph covers a topic without relying on prior knowledge.
    """
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
_BGBL = re.compile(r"(?:\b(I{1,3})\b\.?\s*)?(?:Nr\.?\s*)?(\d+)\s*/\s*(\d{4})\b")


@mcp.tool()
async def lookup_bgbl(
    reference: Annotated[str, "BGBl reference with Teil, e.g. 'BGBl. I Nr. 50/2023', 'II 50/2023'; without Teil all three are listed"],
) -> str:
    """Look up an authentic Bundesgesetzblatt entry and return its metadata and text."""
    # Reads Teil, number and year. A number/year exists once per Teil, so without a Teil all three are
    # searched and several hits are listed for the caller to choose. One hit returns its metadata and its
    # text up to BGBL_TEXT_LIMIT.
    match = _BGBL.search(reference)
    if not match:
        return f"Could not read a BGBl number from '{reference}'. Use a form like 'BGBl. I Nr. 50/2023'."
    teil, number, year = match.group(1), match.group(2), int(match.group(3))

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
    law: Annotated[str, LAW_PARAM],
    pick: Annotated[str, PICK_PARAM] = "",
    date: Annotated[str, DATE_PARAM] = "",
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
    reference: Annotated[str, "Citation string to search for, e.g. '§ 1295 ABGB' or 'Art. 7 B-VG'"],
    limit: Annotated[int, "Max results to return (1-100, default 20)"] = 20,
) -> str:
    """Full-text search the local RIS index for laws that mention a given citation.

    Reverse citation lookup: finds provisions that cite the one you name.
    Backed by a locally built FTS index, so it is only registered where that
    index exists.
    """
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
