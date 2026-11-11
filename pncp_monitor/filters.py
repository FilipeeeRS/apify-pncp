"""Filters the API cannot do for us: keywords and value range. Also a cheap safety net on the server-side filters."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Any

from .settings import Settings


def fold(text: str | None) -> str:
    """Lower-case, accent-free, single-spaced text, so 'Licitação' matches 'LICITACAO'."""
    if not text:
        return ""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return " ".join("".join(char for char in decomposed if not unicodedata.combining(char)).split())


@dataclass(frozen=True)
class RecordFilter:
    keywords: tuple[tuple[str, str], ...]  # (as typed, folded)
    exclude: tuple[str, ...]
    min_value: float | None
    max_value: float | None
    # The API filters these server-side (verified 2026-11-10). They are re-checked anyway, so a filter the portal
    # starts ignoring silently (a known failure mode of this API for other parameters) cannot leak foreign records.
    modalities: frozenset[int]
    states: frozenset[str]
    municipalities: frozenset[str]
    cnpjs: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> RecordFilter:
        return cls(
            keywords=tuple((word, fold(word)) for word in settings.keywords if fold(word)),
            exclude=tuple(fold(word) for word in settings.exclude_keywords if fold(word)),
            min_value=settings.min_value,
            max_value=settings.max_value,
            modalities=frozenset(settings.modalities),
            states=frozenset(settings.states),
            municipalities=frozenset(settings.municipalities),
            cnpjs=frozenset(settings.cnpjs),
        )

    def check(self, record: dict[str, Any]) -> tuple[bool, list[str]]:
        """Return (kept, matched keywords). Keywords are matched against the object text, any one is enough."""
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
        matched = [original for original, folded in self.keywords if folded in text]
        if self.keywords and not matched:
            return False, []
        if any(word in text for word in self.exclude):
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
