"""RIS MCP server — Austrian federal law for LLMs."""
from __future__ import annotations

import asyncio
import re
from typing import Annotated

from mcp.server.fastmcp import FastMCP

from . import ris_client as rc
from .content import html_to_markdown, extract_metadata_blocks, parse_law_outline
from . import index as fts_index

mcp = FastMCP("ris-mcp", dependencies=["httpx", "cachetools", "selectolax"])


@mcp.tool()
async def search_law(
    query: Annotated[str, "Full-text search terms"],
    law: Annotated[str, "Optional law name or abbreviation to scope the search (e.g. ABGB, StGB)"] = "",
) -> str:
    """Search Austrian federal law (Bundesrecht) by keyword, optionally scoped to one statute.

    Note: the RIS API uses literal matching without German stemming — prefer get_law_outline
    to discover the relevant paragraph when you know the statute.
    """
    refs, total = await rc.search_bundesrecht(suchworte=query, titel=law.strip(), pro_seite="Ten")

    if not refs:
        return "No results found."

    lines = [f"Found {total} result(s). Showing first {len(refs)}.\n"]
    for ref in refs:
        meta = rc._meta_from_ref(ref)
        repeal_note = f"  ⚠ REPEALED {meta['repealed']}" if meta["repealed"] else ""
        lines.append(f"**{meta['short_title']}** {meta['paragraph']}{repeal_note}")
        lines.append(f"  Document: {meta['document_id']}")
        lines.append(f"  In force: {meta['in_force_from']}")
        lines.append(f"  URL: {meta['doc_url']}")
        lines.append("")

    return "\n".join(lines)


@mcp.tool()
async def get_paragraph(
    law: Annotated[str, "Law name or abbreviation, e.g. ABGB, StGB, UGB"],
    paragraph: Annotated[str, "Paragraph number, e.g. 1295"],
    to_paragraph: Annotated[str, "End of range (optional), e.g. 1300"] = "",
) -> str:
    """Fetch one paragraph or a range of paragraphs from an Austrian statute."""
    refs, _ = await rc.search_bundesrecht(
        titel=law.strip(),
        abschnitt_von=paragraph,
        abschnitt_bis=to_paragraph or paragraph,
        abschnitt_typ="Paragraph",
        pro_seite="Fifty" if to_paragraph else "Ten",
    )

    if not refs:
        return f"No results for {law} § {paragraph}."

    live = [r for r in refs if not rc._meta_from_ref(r)["repealed"]]
    refs = live if live else refs

    parts: list[str] = []
    for ref in refs:
        meta = rc._meta_from_ref(ref)
        html = await rc.fetch_document_html(ref)
        text = html_to_markdown(html)
        repeal_note = f"  ⚠ repealed: {meta['repealed']}" if meta["repealed"] else ""
        parts.append(f"### {meta['short_title']} {meta['paragraph']}")
        parts.append(f"*{meta['kundmachung']}*")
        parts.append(f"*In force from: {meta['in_force_from']}{repeal_note}*")
        parts.append(f"*Document: {meta['document_id']}*")
        parts.append("")
        parts.append(text)
        parts.append("")

    return "\n".join(parts)


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

    return "\n".join([
        f"### {meta['short_title']} {meta['paragraph']} (as of {date})",
        f"*{meta['kundmachung']}*",
        f"*In force from: {meta['in_force_from']}*",
        f"*Document: {meta['document_id']}*",
        "",
        text,
    ])


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
        pro_seite="Ten",
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
        pro_seite="Ten",
        seite=1,
    )

    if refs_body:
        parts.append(f"*Statute has {total} total sections. Showing first {len(refs_body)}.*\n")
        htmls = await asyncio.gather(*[rc.fetch_document_html(r) for r in refs_body])
        for ref, html in zip(refs_body, htmls):
            meta = rc._meta_from_ref(ref)
            if meta["doc_type"] == "Paragraph" and meta["paragraph_number"] != "0":
                text = html_to_markdown(html)
                parts.append(f"### {meta['paragraph']}")
                parts.append(text)
                parts.append("")

    return "\n".join(parts) if parts else f"Statute '{name}' not found."


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
        pro_seite="Ten",
    )
    if not refs:
        refs, _ = await rc.search_bundesrecht(titel=titel, pro_seite="Ten")
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

    return f"# {meta['short_title']} — Table of Contents\n\n{outline}"


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

    return "\n".join(lines)


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
        pro_seite="Ten",
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

    return "\n".join(lines)


@mcp.tool()
async def who_mentions(
    reference: Annotated[str, "Citation string to search for, e.g. '§ 1295 ABGB' or 'Art. 7 B-VG'"],
    limit: Annotated[int, "Max results to return (default 20)"] = 20,
) -> str:
    """Full-text search the local RIS index for laws that mention a given citation.

    Requires the local index to be built first (run index.py crawl).
    """
    count = fts_index.doc_count()
    if count == 0:
        return (
            "Local index is empty. Build it first by running:\n"
            "  python3 -m src.index\n"
            "This crawls ~250k documents and takes 30-90 minutes."
        )

    results = fts_index.search_fts(reference, limit=limit)
    if not results:
        return f"No documents mention '{reference}' in the local index ({count} docs indexed)."

    lines = [f"Documents mentioning '{reference}' ({len(results)} results from {count} indexed):\n"]
    for r in results:
        lines.append(f"**{r['short_title']}** {r['paragraph']}")
        lines.append(f"  Document: {r['document_id']}")
        lines.append(f"  In force: {r['in_force_from']}")
        lines.append(f"  URL: {r['doc_url']}")
        lines.append("")

    return "\n".join(lines)


def main():
    mcp.run()


if __name__ == "__main__":
    main()
