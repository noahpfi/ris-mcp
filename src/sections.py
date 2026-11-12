"""Parses citations in the label shapes RIS uses, such as § 5a, Art. 7, Anl. 1, Anl. 1/lF and Art. 10 § 1,
and picks the documents a citation names out of a RIS result."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from . import ris_client as rc

ABSCHNITT_TYP = {"§": "Paragraph", "Art.": "Artikel", "Anl.": "Anlage"}
# A bare number is tried as each kind in this order, because laws numbered by article have no § at all.
IMPLICIT_KINDS = ("§", "Art.", "Anl.")

_KINDS = {"§": "§", "§§": "§", "art": "Art.", "artikel": "Art.", "anl": "Anl.", "anlage": "Anl."}
# One unit: an optional kind, the number, a letter suffix of up to two letters and an optional annex part
# after a slash, which RIS writes in mixed case as in Anl. 1/ThNa.
_TOKEN = re.compile(r"\s*(?:(§§?|artikel|art|anlage|anl)\.?\s*)?(\d+)\s*([a-z]{0,2})(?![a-z])(?:/([a-z0-9]+))?\.?", re.IGNORECASE)


@dataclass(frozen=True)
class Unit:
    """One addressable unit. kind is §, Art. or Anl., suffix is the lowercase letter suffix and sub the annex part."""

    kind: str
    number: int
    suffix: str
    sub: str = ""

    @property
    def key(self) -> tuple[str, int, str, str]:
        """Sort key in the order RIS lists units. A sub-annex sorts after its annex: Anl. 1, Anl. 1/e, Anl. 2."""
        return self.kind, self.number, self.suffix, self.sub.casefold()

    @property
    def query(self) -> str:
        """Value for an Abschnitt bound. RIS takes no annex part there; a range over the number also returns its sub-annexes."""
        return f"{self.number}{self.suffix}"

    def __str__(self) -> str:
        return f"{self.kind} {self.number}{self.suffix}" + (f"/{self.sub}" if self.sub else "")


@dataclass(frozen=True)
class Citation:
    """A path of units such as Art. 10 § 1. implicit is true when the first unit was given as a bare number."""

    units: tuple[Unit, ...]
    implicit: bool

    @property
    def last(self) -> Unit:
        return self.units[-1]

    @property
    def outer(self) -> tuple[Unit, ...]:
        """The enclosing units, such as Art. 10 in Art. 10 § 1."""
        return self.units[:-1]

    def with_kind(self, kind: str) -> Citation:
        """The last unit alone, read as the given kind. Used to try a bare number as §, Art. and Anl."""
        return Citation((Unit(kind, self.last.number, self.last.suffix, self.last.sub),), implicit=False)

    def __str__(self) -> str:
        return " ".join(str(unit) for unit in self.units)


def parse(text: str) -> Citation | None:
    """Reads a citation such as "5a", "Art. 10 § 1" or "Anlage 1/ThNa".

    The text is consumed unit by unit, and anything that is not a unit makes the result None.
    Only the first unit may omit its kind; it then defaults to § and the citation is marked implicit.
    """
    text = text.strip()
    pos, raw = 0, []
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            return None
        kind = match.group(1)
        raw.append((_KINDS[kind.casefold()] if kind else None, int(match.group(2)), match.group(3).casefold(), match.group(4) or ""))
        pos = match.end()
    if not raw or any(unit[0] is None for unit in raw[1:]):
        return None
    implicit = raw[0][0] is None
    units = tuple(Unit(kind or "§", number, suffix, sub) for kind, number, suffix, sub in raw)
    return Citation(units, implicit)


def end_of_range(start: Citation, text: str) -> Citation | None:
    """Reads the end of a range relative to its start.

    A bare number takes the kind and the enclosing units of the start, so "Art. 14" to "15" means
    Art. 14 to Art. 15 and "Art. 4 § 2" to "3" means Art. 4 § 2 to Art. 4 § 3. Any other end is read as written.
    """
    end = parse(text)
    if end is None:
        return None
    if end.implicit and len(end.units) == 1:
        return Citation((*start.outer, Unit(start.last.kind, end.last.number, end.last.suffix, end.last.sub)), start.implicit)
    return end


def select(refs: list[dict[str, Any]], start: Citation, end: Citation) -> list[dict[str, Any]]:
    """Keeps the documents whose label lies between start and end.

    RIS filters Abschnitt ranges by number only, so a query for § 5 also returns § 5a to § 5d.
    Labels are compared in natural order instead: § 5 to § 6 includes § 5a and excludes § 6a.
    A citation with enclosing units must match them exactly, transitional provisions included.
    Without them, transitional provisions are dropped, because ÜR articles reuse § numbers: ABGB § 1
    must not return Art. 10 § 1. Other nested labels stay, so DSG § 1 finds Art. 1 § 1.
    """
    picked = []
    for ref in refs:
        info = rc._meta_from_ref(ref)
        label = parse(info["paragraph"])
        if label is None or label.last.kind != start.last.kind:
            continue
        if not start.last.key <= label.last.key <= end.last.key:
            continue
        if start.outer:
            if [unit.key for unit in label.outer] != [unit.key for unit in start.outer]:
                continue
        elif info["transitional"]:
            continue
        picked.append(ref)
    return picked
