"""Apify entry point: input, storages and pay-per-event charging around the crawler."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from apify import Actor

from .client import PncpClient, PncpRejected, make_http_client
from .normalize import BRT
from .runner import STOP_UPSTREAM, Crawler, Summary
from .settings import InputError, Settings, parse_settings

STATE_STORE = "pncp-monitor-state"  # named store: it outlives the run, so schedules can remember what they delivered
DATASET_ITEM_EVENT = "apify-default-dataset-item"  # the synthetic pay-per-event event: one charge per dataset item


class ApifySink:
    def __init__(self, state_store: Any) -> None:
        self._store = state_store

    async def push(self, items: list[dict[str, Any]]) -> bool:
        result = await Actor.push_data(items)
        return result.event_charge_limit_reached

    def remaining_budget(self) -> int | None:
        return Actor.get_charging_manager().calculate_max_event_charge_count_within_limit(DATASET_ITEM_EVENT)

    async def load_state(self, key: str) -> Any:
        return await self._store.get_value(key) if self._store else None

    async def save_state(self, key: str, value: dict[str, Any]) -> None:
        if self._store:
            await self._store.set_value(key, value)

    async def status(self, message: str) -> None:
        await Actor.set_status_message(message)


async def main() -> None:
    async with Actor:
        now = datetime.now(BRT)
        try:
            settings = parse_settings(await Actor.get_input(), today=now.date())
        except InputError as exc:
            await _fail(f"Invalid input: {exc}")
            return
        Actor.log.info("Input: %s", _describe(settings))

        store = await Actor.open_key_value_store(name=STATE_STORE) if settings.incremental else None
        async with make_http_client() as http:
            crawler = Crawler(
                settings,
                PncpClient(http),
                ApifySink(store),
                today=now.date(),
                collected_at=datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            )
            try:
                summary = await crawler.run()
            except PncpRejected as exc:
                await _fail(str(exc))
                return

        await Actor.set_value("RUN_SUMMARY", summary.to_dict())
        await _finish(summary)


async def _finish(summary: Summary) -> None:
    message = summary.status_line()
    Actor.log.info(message)
    if summary.stop_reason == STOP_UPSTREAM:
        # Fail loudly so schedules alert the user. What was delivered stays in the dataset and in the state,
        # so the next run continues from here instead of starting over.
        await _fail(message)
        return
    Actor.status_message = message  # the SDK posts it as the terminal status message on exit


async def _fail(message: str) -> None:
    Actor.log.error(message)
    Actor.status_message = message
    Actor.exit_code = 1


def _describe(settings: Settings) -> str:
    parts = [f"mode={settings.mode}"]
    if settings.date_from:
        parts.append(f"published {settings.date_from}..{settings.date_to}")
    if settings.proposal_deadline_to:
        parts.append(f"proposals closing until {settings.proposal_deadline_to}")
    for name in ("modalities", "states", "municipalities", "cnpjs", "keywords", "exclude_keywords"):
        if getattr(settings, name):
            parts.append(f"{name}={list(getattr(settings, name))}")
    if settings.min_value is not None or settings.max_value is not None:
        parts.append(f"value {settings.min_value}..{settings.max_value}")
    parts.append(f"maxResults={settings.max_results}")
    if settings.incremental:
        parts.append(f"incremental monitor '{settings.monitor_key}'" + (" (state reset)" if settings.reset_state else ""))
    return ", ".join(parts)
