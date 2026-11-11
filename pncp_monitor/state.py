"""Incremental state: the ids already delivered, grouped by a date so old groups can be dropped."""

from __future__ import annotations

from typing import Any

from .settings import Settings

STATE_VERSION = 1


def state_key(settings: Settings) -> str:
    """One record per monitor and mode in the named key-value store (keys allow letters, digits and - _ . only)."""
    return f"STATE-{settings.mode}-{settings.monitor_key}"


class IncrementalState:
    def __init__(self) -> None:
        self._groups: dict[str, set[str]] = {}
        self._all: set[str] = set()

    def __len__(self) -> int:
        return len(self._all)

    def __contains__(self, item_id: object) -> bool:
        return item_id in self._all

    def add(self, item_id: str, group: str) -> None:
        self._groups.setdefault(group, set()).add(item_id)
        self._all.add(item_id)

    def prune(self, keep_from: str) -> None:
        """Forget groups dated before `keep_from` (ISO dates compare as text). Those records can no longer come back."""
        for group in [g for g in self._groups if g < keep_from]:
            self._all -= self._groups.pop(group)

    def to_json(self, updated_at: str) -> dict[str, Any]:
        return {
            "version": STATE_VERSION,
            "updatedAt": updated_at,
            "ids": {group: sorted(ids) for group, ids in sorted(self._groups.items())},
        }

    @classmethod
    def from_json(cls, data: Any) -> IncrementalState:
        """Lenient on purpose: anything unrecognised means "nothing delivered yet", never a crash."""
        state = cls()
        if not isinstance(data, dict) or data.get("version") != STATE_VERSION or not isinstance(data.get("ids"), dict):
            return state
        for group, ids in data["ids"].items():
            if isinstance(group, str) and isinstance(ids, list):
                for item_id in ids:
                    if isinstance(item_id, str):
                        state.add(item_id, group)
        return state
