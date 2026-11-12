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
    """Lists the documents of a GeltendeFassung page, which holds a whole consolidated law, in page order.

    Each document sits in a documentContent container that opens with a screen-reader heading holding its
    label, such as § 5a or Art. 1, and may hold no content block at all. Section headings such as a Hauptstück
    become bold lines, the label and the other headings form one line. The visible label repeats the
    screen-reader one and is left out.
    """
    tree = HTMLParser(html)
    for node in tree.css("span.sr-only"):
        node.decompose()

    lines: list[str] = []
    for marker in tree.css("div.documentContent > h2.onlyScreenreader"):
        content = marker.parent
        before: list[str] = []
        headings: list[str] = []
        after: list[str] = []
        for node in content.css(", ".join(f"div.contentBlock h{level}" for level in range(1, 7))):
            classes = (node.attributes.get("class") or "").split()
            text = _clean(node.text(separator=" "))
            if not text or "GldSymbol" in classes:
                continue
            if any(cls.startswith("UeberschrG") for cls in classes):
                (after if headings else before).append(text)
            else:
                headings.append(text)
        lines.extend(f"**{text}**" for text in before)
        lines.append("  ".join([_clean(marker.text()), " / ".join(headings)]).strip())
        lines.extend(f"**{text}**" for text in after)
    return "\n".join(lines)


_VERSION = re.compile(r"Fassung vom \d{2}\.\d{2}\.\d{4}")


def outline_version(html: str) -> str:
    """The version RIS states in the page title, such as Fassung vom 12.11.2026, or an empty string."""
    title = HTMLParser(html).css_first("title")
    match = _VERSION.search(title.text()) if title else None
    return match.group(0) if match else ""


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
