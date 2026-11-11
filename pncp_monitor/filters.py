"""Filters the API cannot do for us: status, keywords and value range. Also a cheap safety net on the server-side filters."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from .settings import Settings


def fold(text: str | None) -> str:
    """Lower-case, accent-free words separated by single spaces: 'AR-CONDICIONADO, Licitação' -> 'ar condicionado licitacao'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^0-9a-z]+", " ", plain).split())


def _word_start(folded: str) -> re.Pattern[str]:
    """Match at the start of a word: 'notebook' finds 'notebooks', 'locacao' does not find 'realocacao'."""
    return re.compile(r"(?<![0-9a-z])" + re.escape(folded))


@dataclass(frozen=True)
class RecordFilter:
    keywords: tuple[tuple[str, re.Pattern[str]], ...]  # (as typed, pattern on folded text)
    exclude: tuple[re.Pattern[str], ...]
    min_value: float | None
    max_value: float | None
    statuses: frozenset[int]
    # The API filters these server-side (verified 2026-11-10). They are re-checked anyway, so a filter the portal
    # starts ignoring silently (a known failure mode of this API for other parameters) cannot leak foreign records.
    modalities: frozenset[int]
    states: frozenset[str]
    municipalities: frozenset[str]
    cnpjs: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> RecordFilter:
        return cls(
            keywords=tuple((word, _word_start(fold(word))) for word in settings.keywords if fold(word)),
            exclude=tuple(_word_start(fold(word)) for word in settings.exclude_keywords if fold(word)),
            min_value=settings.min_value,
            max_value=settings.max_value,
            statuses=frozenset(settings.statuses),
            modalities=frozenset(settings.modalities),
            states=frozenset(settings.states),
            municipalities=frozenset(settings.municipalities),
            cnpjs=frozenset(settings.cnpjs),
        )

    def check(self, record: dict[str, Any]) -> tuple[bool, list[str]]:
        """Return (kept, matched keywords). Keywords are matched against the object text, any one is enough."""
        if self.statuses and record.get("statusCode") not in self.statuses:
            return False, []
        if self.modalities and record.get("modalityCode") not in self.modalities:
            return False, []
        if self.municipalities:
            if record.get("cityIbgeCode") not in self.municipalities:
                return False, []
        elif self.states and record.get("state") not in self.states:
            return False, []
        if self.cnpjs and record.get("agencyCnpj") not in self.cnpjs:
            return False, []

        text = fold(record.get("object"))
        matched = [original for original, pattern in self.keywords if pattern.search(text)]
        if self.keywords and not matched:
            return False, []
        if any(pattern.search(text) for pattern in self.exclude):
            return False, []

        if self.min_value is not None or self.max_value is not None:
            value = record.get("estimatedValue")
            if value is None:  # no disclosed budget: cannot be said to be inside a range
                return False, []
            if self.min_value is not None and value < self.min_value:
                return False, []
            if self.max_value is not None and value > self.max_value:
                return False, []
        return True, matched
