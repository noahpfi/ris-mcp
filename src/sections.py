"""citations in RIS label shapes: § 5a, Art. 7, Anl. 1, Art. 10 § 1"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import ris_client as rc

ABSCHNITT_TYP = {"§": "Paragraph", "Art.": "Artikel", "Anl.": "Anlage"}
# bare number tries kinds in this order; laws numbered by article have no § at all
IMPLICIT_KINDS = ("§", "Art.", "Anl.")

_KINDS = {"§": "§", "§§": "§", "art": "Art.", "artikel": "Art.", "anl": "Anl.", "anlage": "Anl."}
_TOKEN = re.compile(r"\s*(?:(§§?|artikel|art|anlage|anl)\.?\s*)?(\d+)\s*([a-z]{0,2})(?![a-z])\.?", re.IGNORECASE)


@dataclass(frozen=True)
class Unit:
    kind: str
    number: int
    suffix: str

    @property
    def key(self) -> tuple[int, str]:
        return self.number, self.suffix

    def __str__(self) -> str:
        return f"{self.kind} {self.number}{self.suffix}"


@dataclass(frozen=True)
class Citation:
    units: tuple[Unit, ...]
    implicit: bool

    @property
    def last(self) -> Unit:
        return self.units[-1]

    @property
    def outer(self) -> tuple[Unit, ...]:
        return self.units[:-1]

    def with_kind(self, kind: str) -> Citation:
        return Citation((Unit(kind, self.last.number, self.last.suffix),), implicit=False)

    def __str__(self) -> str:
        return " ".join(str(unit) for unit in self.units)


def parse(text: str) -> Citation | None:
    """None unless whole text = units; only first unit may omit its kind"""
    text = text.strip()
    pos, raw = 0, []
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            return None
        kind = match.group(1)
        raw.append((_KINDS[kind.casefold()] if kind else None, int(match.group(2)), match.group(3).casefold()))
        pos = match.end()
    if not raw or any(kind is None for kind, _, _ in raw[1:]):
        return None
    implicit = raw[0][0] is None
    units = tuple(Unit(kind or "§", number, suffix) for kind, number, suffix in raw)
    return Citation(units, implicit)


def end_of_range(start: Citation, text: str) -> Citation | None:
    """bare end inherits start kind and container: Art. 14 to 15 = Art. 14 to Art. 15"""
    end = parse(text)
    if end is None:
        return None
    if end.implicit and len(end.units) == 1:
        return Citation((*start.outer, Unit(start.last.kind, end.last.number, end.last.suffix)), start.implicit)
    return end


def select(refs: list[dict[str, Any]], start: Citation, end: Citation) -> list[dict[str, Any]]:
    """labels in start..end by natural order, 5..6 skips 6a; explicit container = exact incl ÜR, else ÜR dropped -> ABGB § 1 skips Art. 10 § 1, DSG § 1 finds Art. 1 § 1"""
    picked = []
    for ref in refs:
        info = rc._meta_from_ref(ref)
        label = parse(info["paragraph"])
        if label is None or label.last.kind != start.last.kind:
            continue
        if not start.last.key <= label.last.key <= end.last.key:
            continue
        if start.outer:
            if label.outer != start.outer:
                continue
        elif info["transitional"]:
            continue
        picked.append(ref)
    return picked
