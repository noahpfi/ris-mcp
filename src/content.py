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

    # BgblAuth entries = one titled block, no Text block -> walk every block
    blocks = [text_block] if text_block is not None else tree.css("div.contentBlock")
    if not blocks:
        body = tree.css_first("body")
        return _clean(body.text(separator="\n") if body else "")

    lines: list[str] = []
    for block in blocks:
        _walk_block(block, lines)
    return "\n".join(lines).strip()


def _walk_block(node, lines: list[str]) -> None:
    tag = node.tag if node.tag else ""

    if tag in ("h2", "h3"):
        text = _clean(node.text(separator=" "))
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
    """one line per document of a GeltendeFassung page, sections as bold lines, document order kept"""
    tree = HTMLParser(html)
    for node in tree.css("span.sr-only"):
        node.decompose()

    lines: list[str] = []
    for block in tree.css("div.contentBlock"):
        sections: list[str] = []
        label = heading = ""
        for node in block.css("h1, h2, h3, h4, h5, h6"):
            kind = _outline_role(node)
            text = _clean(node.text(separator=" "))
            if not kind or not text:
                continue
            # G headings after label = annex inner structure, not law sections
            if kind == "section" and not label:
                sections.append(text)
            elif kind == "label" and not label:
                label = _outline_label(text)
            elif kind == "heading" and not heading:
                heading = text
        if sections:
            lines.extend(["", f"**{' / '.join(sections)}**"])
        entry = f"{label}  {heading}".strip()
        if entry:
            lines.append(entry)
    return "\n".join(lines).strip()


_ROLES = {"GldSymbol": "label", "Anlagenbez": "label", "UeberschrPara": "heading", "UeberschrArt": "heading"}


def _outline_role(node) -> str:
    for cls in (node.attributes.get("class") or "").split():
        if cls in _ROLES:
            return _ROLES[cls]
        if cls.startswith("UeberschrG"):
            return "section"
    return ""


def _outline_label(text: str) -> str:
    """'Artikel 8a.' -> 'Art. 8a', '§ 1.' -> '§ 1'"""
    return re.sub(r"^Artikel\b", "Art.", text).rstrip(".").strip()


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
