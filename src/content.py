"""HTML -> markdown conversion for RIS document content."""
from __future__ import annotations

import re
from selectolax.parser import HTMLParser


def html_to_markdown(html: str) -> str:
    tree = HTMLParser(html)

    # remove sr-only spans (screen reader duplicates)
    for node in tree.css("span.sr-only"):
        node.decompose()

    # find the Text contentBlock
    text_block = None
    for block in tree.css("div.contentBlock"):
        header = block.css_first("h1.Titel")
        if header and header.text(strip=True) == "Text":
            text_block = block
            break

    if text_block is None:
        # fallback: full body text
        body = tree.css_first("body")
        return _clean(body.text(separator="\n") if body else "")

    lines: list[str] = []
    _walk_block(text_block, lines)
    return "\n".join(lines).strip()


def _walk_block(node, lines: list[str]) -> None:
    tag = node.tag if node.tag else ""

    if tag in ("h2", "h3"):
        text = node.text(strip=True)
        if text:
            prefix = "##" if tag == "h2" else "###"
            lines.append(f"{prefix} {text}")
        return

    if tag == "li":
        lines.append(_node_text(node))
        return

    if tag == "p":
        t = _node_text(node)
        if t:
            lines.append(t)
        return

    child = node.child
    while child:
        _walk_block(child, lines)
        child = child.next


def _node_text(node) -> str:
    return _clean(node.text(separator=" "))


def _clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_law_outline(html: str) -> str:
    """Parse paragraph TOC from a GesamteRechtsvorschrift page."""
    tree = HTMLParser(html)
    lines: list[str] = []
    current_section: str | None = None
    last_printed_section: str | None = None

    for block in tree.css("div.contentBlock"):
        g1 = block.css_first("h4.UeberschrG1, h4.UeberschrG1-AfterG2")
        if g1:
            current_section = g1.text(strip=True)

        heading = block.css_first("h4.UeberschrPara")
        raw = block.text(separator=" ")
        m = re.search(r"§\s*(\d+)", raw)
        if heading and m:
            if current_section != last_printed_section:
                if lines:
                    lines.append("")
                lines.append(f"**{current_section or ''}**")
                last_printed_section = current_section
            lines.append(f"§ {int(m.group(1)):>4}  {heading.text(strip=True)}")

    return "\n".join(lines) if lines else ""


def extract_amendment_list(html: str) -> list[str]:
    """Pull the Änderung list from a preamble (§ 0) document."""
    tree = HTMLParser(html)
    for node in tree.css("span.sr-only"):
        node.decompose()

    for block in tree.css("div.contentBlock"):
        header = block.css_first("h1.Titel")
        if header and "nderung" in header.text():
            items = []
            for p in block.css("p"):
                t = p.text(strip=True)
                if t:
                    items.append(t)
            return items
    return []


def extract_metadata_blocks(html: str) -> dict[str, str]:
    """Extract all label->value pairs from contentBlock headers."""
    tree = HTMLParser(html)
    for node in tree.css("span.sr-only"):
        node.decompose()

    result: dict[str, str] = {}
    for block in tree.css("div.contentBlock"):
        header = block.css_first("h1.Titel")
        if not header:
            continue
        label = header.text(strip=True)
        texts = []
        for p in block.css("p"):
            t = p.text(strip=True)
            if t:
                texts.append(t)
        if texts:
            result[label] = "\n".join(texts)
    return result
