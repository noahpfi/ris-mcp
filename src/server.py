"""RIS MCP server, Austrian federal law for LLMs"""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Annotated, Any

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.requests import Request
from starlette.responses import JSONResponse

from . import config
from . import http_app
from . import ris_client as rc
from .content import html_to_markdown, extract_metadata_blocks, parse_law_outline
from . import index as fts_index


# get_paragraph fans out one upstream fetch per paragraph -> range = amplification lever, capped
MAX_RANGE = 20

_LEADING_DIGITS = re.compile(r"^\s*(\d+)")

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
    return f"  valid until {meta['valid_until']}" if meta["valid_until"] else ""


def _range_span(start: str, end: str) -> int | None:
    """None unless clean integer pair; suffixes like § 1295a left to per-result cap, not guessed"""
    if not end:
        return None
    lo, hi = _LEADING_DIGITS.match(start), _LEADING_DIGITS.match(end)
    if not lo or not hi:
        return None
    return int(hi.group(1)) - int(lo.group(1)) + 1


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


@mcp.tool()
async def search_law(
    query: Annotated[str, "Full-text search terms"],
    law: Annotated[str, "Optional law name or abbreviation to scope the search (e.g. ABGB, StGB)"] = "",
) -> str:
    """Search Austrian federal law (Bundesrecht) by keyword, optionally scoped to one statute.

    Note: the RIS API uses verbatim matching — use exact legal terms as they appear in statute
    text (e.g. "Kleinunternehmer" not "kleine Gewerbetreibende", "Rechnung" not "Faktura").
    Prefer get_law_outline to browse a known statute's table of contents.
    Only versions in force today are searched, most recently entered into force first.
    """
    refs, total = await rc.search_bundesrecht(
        suchworte=query, titel=law.strip(), fassung_vom=rc.today(), pro_seite="Fifty"
    )

    if not refs:
        return "No results in force today. Repealed wording is reachable via get_paragraph_at."

    refs = sorted(refs, key=lambda r: rc._meta_from_ref(r)["in_force_from"], reverse=True)

    lines = [f"Found {total} result(s) in force today, showing {len(refs)}, newest first.\n"]
    for ref in refs:
        meta = rc._meta_from_ref(ref)
        lines.append(f"**{meta['short_title']}** {meta['paragraph']}{_expiry_note(meta)}")
        lines.append(f"  Document: {meta['document_id']}")
        lines.append(f"  In force: {meta['in_force_from']}")
        lines.append(f"  URL: {meta['doc_url']}")
        lines.append("")

    return _sourced("\n".join(lines))


@mcp.tool()
async def get_paragraph(
    law: Annotated[str, "Law name or abbreviation, e.g. ABGB, StGB, UGB"],
    paragraph: Annotated[str, "Paragraph number, e.g. 1295"],
    to_paragraph: Annotated[str, f"End of range (optional), at most {MAX_RANGE} from the start"] = "",
) -> str:
    """Fetch one paragraph or a range of paragraphs from an Austrian statute."""
    span = _range_span(paragraph, to_paragraph)
    if span is not None and span > MAX_RANGE:
        return (
            f"Range §§ {paragraph}–{to_paragraph} covers {span} paragraphs; the limit is "
            f"{MAX_RANGE} per call. Narrow it, or use get_law_outline for an overview."
        )

    refs, _ = await rc.search_bundesrecht(
        titel=law.strip(),
        abschnitt_von=paragraph,
        abschnitt_bis=to_paragraph or paragraph,
        abschnitt_typ="Paragraph",
        fassung_vom=rc.today(),
        pro_seite="Fifty",
    )

    if not refs:
        return (
            f"No version of {law} § {paragraph} in force today. "
            "Repealed wording is reachable via get_paragraph_at."
        )

    # API may widen range past span check -> cap again, one call fans out to many fetches
    refs = refs[:MAX_RANGE]

    # sequential on purpose; concurrent fetch -> RIS throttling, 15-paragraph range > 60s via _get_html backoff
    parts: list[str] = []
    for ref in refs:
        meta = rc._meta_from_ref(ref)
        html = await rc.fetch_document_html(ref)
        text = html_to_markdown(html)
        parts.append(f"### {meta['short_title']} {meta['paragraph']}")
        parts.append(f"*{meta['kundmachung']}*")
        parts.append(f"*In force from: {meta['in_force_from']}{_expiry_note(meta)}*")
        parts.append(f"*Document: {meta['document_id']}*")
        parts.append("")
        parts.append(text)
        parts.append("")

    return _sourced("\n".join(parts))


@mcp.tool()
async def get_paragraph_at(
    law: Annotated[str, "Law name or abbreviation"],
    paragraph: Annotated[str, "Paragraph number"],
    date: Annotated[str, "Date in YYYY-MM-DD format — returns the version in force on that date"],
) -> str:
    """Fetch the historical version of a paragraph as it read on a given date."""
    refs, _ = await rc.search_bundesrecht(
        titel=law.strip(),
        abschnitt_von=paragraph,
        abschnitt_bis=paragraph,
        abschnitt_typ="Paragraph",
        fassung_vom=date,
        pro_seite="Ten",
    )

    if not refs:
        return f"No version of {law} § {paragraph} found for date {date}."

    ref = refs[0]
    meta = rc._meta_from_ref(ref)
    html = await rc.fetch_document_html(ref)
    text = html_to_markdown(html)

    return _sourced("\n".join([
        f"### {meta['short_title']} {meta['paragraph']} (as of {date})",
        f"*{meta['kundmachung']}*",
        f"*In force from: {meta['in_force_from']}*",
        f"*Document: {meta['document_id']}*",
        "",
        text,
    ]))


@mcp.tool()
async def get_statute(
    name: Annotated[str, "Law name or abbreviation, e.g. ABGB, GmbHG"],
) -> str:
    """Fetch the preamble and first page of paragraphs for a statute."""
    titel = name.strip()

    refs_pre, _ = await rc.search_bundesrecht(
        titel=titel,
        abschnitt_von="0",
        abschnitt_bis="0",
        abschnitt_typ="Paragraph",
        fassung_vom=rc.today(),
        pro_seite="Fifty",
    )

    parts: list[str] = []

    if refs_pre:
        ref = rc.best_law_match(refs_pre, titel)
        html = await rc.fetch_document_html(ref)
        blocks = extract_metadata_blocks(html)
        meta = rc._meta_from_ref(ref)
        parts.append(f"# {blocks.get('Kurztitel', meta['short_title'])}")
        parts.append("")
        for label in ("Kundmachungsorgan", "Typ", "Inkrafttretensdatum", "Abkürzung", "Index"):
            if label in blocks:
                parts.append(f"**{label}:** {blocks[label]}")
        if "Änderung" in blocks:
            amendments = blocks["Änderung"].split("\n")
            suffix = f" ... (+{len(amendments)-5} more)" if len(amendments) > 5 else ""
            parts.append(f"\n**Änderungen ({len(amendments)}):** {', '.join(amendments[:5])}{suffix}")
        parts.append("")

    refs_body, total = await rc.search_bundesrecht(
        titel=titel,
        fassung_vom=rc.today(),
        pro_seite="Ten",
        seite=1,
    )

    if refs_body:
        parts.append(f"*Statute has {total} sections in force. Showing first {len(refs_body)}.*\n")
        htmls = await asyncio.gather(*[rc.fetch_document_html(r) for r in refs_body])
        for ref, html in zip(refs_body, htmls):
            meta = rc._meta_from_ref(ref)
            if meta["doc_type"] == "Paragraph" and meta["paragraph_number"] != "0":
                text = html_to_markdown(html)
                parts.append(f"### {meta['paragraph']}")
                parts.append(text)
                parts.append("")

    if not parts:
        return f"Statute '{name}' not found."
    return _sourced("\n".join(parts))


@mcp.tool()
async def get_law_outline(
    law: Annotated[str, "Law name or abbreviation, e.g. ABGB, StGB, UGB"],
) -> str:
    """Return full table of contents for a statute: § numbers with their headings, grouped by section.

    Use this to discover which paragraph covers a topic without relying on prior knowledge.
    """
    titel = law.strip()
    refs, _ = await rc.search_bundesrecht(
        titel=titel,
        abschnitt_von="0",
        abschnitt_bis="0",
        abschnitt_typ="Paragraph",
        fassung_vom=rc.today(),
        pro_seite="Fifty",
    )
    if not refs:
        refs, _ = await rc.search_bundesrecht(titel=titel, fassung_vom=rc.today(), pro_seite="Ten")
    if not refs:
        return f"Law '{law}' not found."

    ref = rc.best_law_match(refs, titel)
    meta = rc._meta_from_ref(ref)
    outline_url = meta["outline_url"]
    if not outline_url:
        return f"No outline URL for '{law}'."

    html = await rc._get_html(outline_url)
    outline = parse_law_outline(html)
    if not outline:
        return f"Could not parse outline for '{law}'."

    return _sourced(f"# {meta['short_title']} — Table of Contents\n\n{outline}")


@mcp.tool()
async def lookup_bgbl(
    reference: Annotated[str, "BGBl reference, e.g. 'BGBl I Nr. 50/2023' or '50/2023'"],
) -> str:
    """Look up an authentic Bundesgesetzblatt entry and return its title and amended laws."""
    m = re.search(r"(\d+/\d+)", reference)
    if not m:
        return f"Could not parse BGBl number from '{reference}'. Use format like '50/2023'."

    bgbl_num = m.group(1)
    refs, _ = await rc.search_bgbl_auth(bgbl_nummer=bgbl_num)

    if not refs:
        return f"BGBl Nr. {bgbl_num} not found. Note: BgblAuth only covers entries from 2004 onwards."

    ref = refs[0]
    meta_raw = ref.get("Data", {}).get("Metadaten", {})
    bundesrecht = meta_raw.get("Bundesrecht", {})
    technisch = meta_raw.get("Technisch", {})
    allgemein = meta_raw.get("Allgemein", {})

    html = await rc.fetch_document_html(ref)
    blocks = extract_metadata_blocks(html)

    lines = [
        f"## BGBl {bgbl_num}",
        f"**ID:** {technisch.get('ID', '')}",
        f"**URL:** {allgemein.get('DokumentUrl', '')}",
        f"**Titel:** {bundesrecht.get('Titel', '')}",
        "",
    ]

    for label in ("Kundmachungsorgan", "Typ", "Inkrafttretensdatum"):
        if label in blocks:
            lines.append(f"**{label}:** {blocks[label]}")

    if "Text" in blocks:
        lines.append(f"\n### Content\n{blocks['Text'][:2000]}")

    return _sourced("\n".join(lines), authentic=True)


@mcp.tool()
async def get_amendment_timeline(
    law: Annotated[str, "Law name or abbreviation, e.g. ABGB, StGB"],
) -> str:
    """Return an ordered list of every BGBl amendment that touched a statute."""
    titel = law.strip()

    refs, _ = await rc.search_bundesrecht(
        titel=titel,
        abschnitt_von="0",
        abschnitt_bis="0",
        abschnitt_typ="Paragraph",
        fassung_vom=rc.today(),
        pro_seite="Fifty",
    )

    if not refs:
        return f"Statute '{law}' not found."

    ref = rc.best_law_match(refs, titel)
    html = await rc.fetch_document_html(ref)
    blocks = extract_metadata_blocks(html)

    short_title = blocks.get("Kurztitel", law)
    amendments_raw = blocks.get("Änderung", "")

    if not amendments_raw:
        return f"No amendment list found for {law}."

    amendments = [a.strip() for a in amendments_raw.split("\n") if a.strip()]
    lines = [f"## Amendment timeline: {short_title}", f"*{len(amendments)} amendments total*", ""]
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
