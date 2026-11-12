"""Parses the citation a caller passes in, such as 5a, Art. 7, Anl. 1/ThNa or Art. 10 § 1, into the bounds of
one RIS Abschnitt query and the label it names. RIS labels themselves are compared as text, never parsed."""
from __future__ import annotations

import re
from dataclasses import dataclass

# Abschnitt.Typ per kind. A bare number is sent as Alle, so RIS returns every kind under it.
ABSCHNITT_TYP = {"§": "Paragraph", "Art.": "Artikel", "Anl.": "Anlage"}
ALL_KINDS = "Alle"

_KINDS = {"§": "§", "§§": "§", "art": "Art.", "artikel": "Art.", "anl": "Anl.", "anlage": "Anl."}
# One unit: an optional kind, the number, a suffix attached to it such as a or m1, and an annex part after a
# slash such as ThNa.
_TOKEN = re.compile(r"\s*(?:(§§?|artikel|art|anlage|anl)\.?\s*)?(\d+)([a-z][a-z0-9]*)?(?:/([a-z0-9]+))?\.?", re.IGNORECASE)


@dataclass(frozen=True)
class Unit:
    """One unit of a citation. kind is §, Art. or Anl., suffix the lowercase text after the number, sub the annex part."""

    kind: str
    number: int
    suffix: str
    sub: str = ""

    @property
    def key(self) -> tuple[str, int, str, str]:
        """Order used to check that a range runs forward."""
        return self.kind, self.number, self.suffix, self.sub.casefold()

    @property
    def query(self) -> str:
        """Value for an Abschnitt bound. RIS takes no annex part there; a bound on the number covers its sub-annexes."""
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

    @property
    def abschnitt_typ(self) -> str:
        return ALL_KINDS if self.implicit else ABSCHNITT_TYP[self.last.kind]

    def names(self, label: str) -> bool:
        """Whether a RIS label is this citation, compared as text without regard to spacing or case."""
        return _norm(label) == _norm(str(self))

    def contains(self, label: str) -> bool:
        """Whether a RIS label sits inside the enclosing units of this citation, such as Art. 2 § 5 in Art. 2."""
        return not self.outer or _norm(label).startswith(_norm(" ".join(map(str, self.outer))) + " ")

    def __str__(self) -> str:
        return " ".join(str(unit) for unit in self.units)


def _norm(text: str) -> str:
    return " ".join(text.replace("\xa0", " ").split()).casefold()


def parse(text: str) -> Citation | None:
    """Reads a citation such as "5a", "§ 365m1", "Art. 10 § 1" or "Anlage 1/ThNa".

    The text is consumed unit by unit, and anything that is not a unit makes the result None. Only the first
    unit may omit its kind; it is then marked implicit and stands for every kind RIS holds under the number.
    """
    text = text.strip()
    pos, raw = 0, []
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match or match.end() == pos:
            return None
        kind = match.group(1)
        raw.append((_KINDS[kind.casefold()] if kind else None, int(match.group(2)), (match.group(3) or "").casefold(), match.group(4) or ""))
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
