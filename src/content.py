"""Converts RIS document pages to markdown and reads metadata and outlines from them."""
from __future__ import annotations

import re
from selectolax.parser import HTMLParser


def html_to_markdown(html: str) -> str:
    """Converts the wording of a RIS document page to markdown.

    Consolidated documents keep their wording in the contentBlock headed Text, next to metadata blocks.
    Gazette entries have no Text block, so every block is converted. A page without blocks falls back to its body text.
    """
    tree = HTMLParser(html)

    # Screen-reader spans repeat the visible text in spoken form.
    for node in tree.css("span.sr-only"):
        node.decompose()

    text_block = None
    for block in tree.css("div.contentBlock"):
        header = block.css_first("h1.Titel")
        if header and header.text(strip=True) == "Text":
            text_block = block
            break

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
    """Builds a table of contents from a GeltendeFassung page, which holds the whole consolidated law.

    Each contentBlock is one document. Its label, such as § 5a or Anl. 1, and its first heading form one line.
    Section headings that precede the label, such as a Hauptstück, become a bold line before it.
    Documents keep the page order.
    """
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
            # Section headings after the label belong to the inner structure of an annex, not to the law.
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
    """Classifies a heading by its RIS CSS class as label, heading, section or nothing."""
    for cls in (node.attributes.get("class") or "").split():
        if cls in _ROLES:
            return _ROLES[cls]
        if cls.startswith("UeberschrG"):
            return "section"
    return ""


def _outline_label(text: str) -> str:
    """Shortens a label to citation form: "Artikel 8a." becomes "Art. 8a" and "§ 1." becomes "§ 1"."""
    return re.sub(r"^Artikel\b", "Art.", text).rstrip(".").strip()


def extract_metadata_blocks(html: str) -> dict[str, str]:
    """Maps each contentBlock title, such as Kurztitel or Änderung, to its paragraphs joined by newlines."""
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
