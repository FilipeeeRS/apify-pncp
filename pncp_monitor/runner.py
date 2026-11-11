"""Crawl orchestration: streams -> pages -> normalize -> filter -> dedupe -> deliver -> remember.

Records come in the API's order (oldest publication first). With `incremental` on, a record is remembered only after
it has been pushed, and the state is saved after every page, so a run that stops halfway (result cap, the user's cost
limit, an API outage) resumes in the next run exactly where it stopped.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from typing import Any, Protocol

from .client import PAGE_SIZE, Page, PncpClient, PncpUnavailable
from .filters import RecordFilter
from .normalize import normalize
from .settings import MODE_PUBLISHED, Settings, build_streams
from .state import IncrementalState, state_key

log = logging.getLogger("apify.pncp_monitor.runner")

STOP_COMPLETED = "completed"
STOP_MAX_RESULTS = "max_results"
STOP_MAX_CHARGE = "max_charge"
STOP_MAX_PAGES = "max_api_pages"
STOP_UPSTREAM = "upstream_error"


class Sink(Protocol):
    """Where results and state go: the Apify platform in production, memory in tests."""

    async def push(self, items: list[dict[str, Any]]) -> bool:
        """Store the items; return True when the run's cost limit is now reached."""

    def remaining_budget(self) -> int | None:
        """How many more results the user's cost limit allows (None = unlimited)."""

    async def load_state(self, key: str) -> Any: ...

    async def save_state(self, key: str, value: dict[str, Any]) -> None: ...

    async def status(self, message: str) -> None: ...


@dataclass
class Summary:
    mode: str
    window: dict[str, str | None]
    incremental: bool
    streams: int
    streams_done: int = 0
    api_pages: int = 0
    api_requests: int = 0
    scanned: int = 0
    duplicates: int = 0
    filtered_out: int = 0
    already_delivered: int = 0
    invalid_records: int = 0
    delivered: int = 0
    stop_reason: str = STOP_COMPLETED
    error: str | None = None
    state_size_before: int = 0
    state_size_after: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "complete": self.stop_reason == STOP_COMPLETED}

    def status_line(self) -> str:
        line = f"{self.delivered} results delivered ({self.scanned} scanned, {self.filtered_out} filtered out"
        if self.incremental:
            line += f", {self.already_delivered} already delivered before"
        line += ")."
        notes = {
            STOP_MAX_RESULTS: " Stopped at maxResults.",
            STOP_MAX_CHARGE: " Stopped at your maximum cost per run.",
            STOP_MAX_PAGES: " Stopped at maxApiPages; narrow the filters or raise the limit.",
            STOP_UPSTREAM: f" The PNCP API failed: {self.error}",
        }
        line += notes.get(self.stop_reason, "")
        if self.delivered == 0 and self.stop_reason == STOP_COMPLETED:
            line += " Nothing new matched your filters in this window."
        return line


class Crawler:
    def __init__(self, settings: Settings, client: PncpClient, sink: Sink, *, today: date, collected_at: str) -> None:
        self.settings = settings
        self.client = client
        self.sink = sink
        self.today = today
        self.collected_at = collected_at
        self.filter = RecordFilter.from_settings(settings)
        self.streams = build_streams(settings)
        self.key = state_key(settings)
        self.state = IncrementalState()
        self.seen: set[str] = set()
        self.pages_left = settings.max_api_pages
        if settings.mode == MODE_PUBLISHED:
            assert settings.date_from and settings.date_to
            self.path = "/contratacoes/publicacao"
            self.base_params = {"dataInicial": f"{settings.date_from:%Y%m%d}", "dataFinal": f"{settings.date_to:%Y%m%d}"}
            window = {"dateFrom": settings.date_from.isoformat(), "dateTo": settings.date_to.isoformat()}
        else:
            assert settings.proposal_deadline_to
            self.path = "/contratacoes/proposta"
            self.base_params = {"dataFinal": f"{settings.proposal_deadline_to:%Y%m%d}"}
            window = {"proposalDeadlineTo": settings.proposal_deadline_to.isoformat()}
        self.summary = Summary(mode=settings.mode, window=window, incremental=settings.incremental, streams=len(self.streams))

    async def run(self) -> Summary:
        settings, summary = self.settings, self.summary
        if settings.incremental and not settings.reset_state:
            self.state = IncrementalState.from_json(await self.sink.load_state(self.key))
        summary.state_size_before = len(self.state)
        try:
            for index, stream in enumerate(self.streams, start=1):
                stop = await self._crawl_stream(index, stream)
                if stop:
                    break
                summary.streams_done = index
        except PncpUnavailable as exc:
            summary.stop_reason = STOP_UPSTREAM
            summary.error = str(exc)
        finally:
            summary.api_requests = self.client.requests
            if settings.incremental:
                self.state.prune(self._keep_from())
                await self.sink.save_state(self.key, self.state.to_json(self.collected_at))
            summary.state_size_after = len(self.state)
        return summary

    async def _crawl_stream(self, index: int, stream: dict[str, Any]) -> bool:
        """Read every page of one query. Return True when the whole run has to stop."""
        params = {**self.base_params, **stream}
        label = ", ".join(f"{key}={value}" for key, value in stream.items())
        page_number = 1
        while True:
            if self.pages_left <= 0:
                self.summary.stop_reason = STOP_MAX_PAGES
                return True
            self.pages_left -= 1
            page = await self.client.get_page(self.path, {**params, "pagina": page_number, "tamanhoPagina": PAGE_SIZE})
            self.summary.api_pages += 1
            if page_number == 1:
                log.info("Query %d/%d (%s): %s records", index, len(self.streams), label, page.total_records or 0)
            if await self._handle_page(page):
                return True
            if self.summary.api_pages % 5 == 1:
                await self.sink.status(f"Query {index}/{len(self.streams)}, page {page_number}: {self.summary.delivered} results so far")
            if not page.items or (page.total_pages is not None and page_number >= page.total_pages):
                return False
            page_number += 1

    async def _handle_page(self, page: Page) -> bool:
        settings, summary = self.settings, self.summary
        batch: list[dict[str, Any]] = []
        for raw in page.items:
            summary.scanned += 1
            try:
                record = normalize(raw, self.collected_at)
            except Exception as exc:  # one malformed record must never cost the user the whole run
                summary.invalid_records += 1
                log.warning("Skipped a record that could not be read: %r", exc)
                continue
            record_id = record["id"]
            if not record_id:
                summary.invalid_records += 1
                continue
            if record_id in self.seen:  # pages shift when records are published mid-crawl
                summary.duplicates += 1
                continue
            self.seen.add(record_id)
            kept, matched = self.filter.check(record)
            if not kept:
                summary.filtered_out += 1
                continue
            if settings.incremental and record_id in self.state:
                summary.already_delivered += 1
                continue
            record["matchedKeywords"] = matched
            batch.append(record)

        allowance = self.sink.remaining_budget()
        room = settings.max_results - summary.delivered
        if allowance is not None:
            room = min(room, allowance)
        batch = batch[: max(room, 0)]
        limit_reached = False
        if batch:
            limit_reached = await self.sink.push(batch)
            summary.delivered += len(batch)
            if settings.incremental:
                for record in batch:
                    self.state.add(record["id"], self._group(record))
                await self.sink.save_state(self.key, self.state.to_json(self.collected_at))

        if summary.delivered >= settings.max_results:
            summary.stop_reason = STOP_MAX_RESULTS
            return True
        if limit_reached or (allowance is not None and allowance <= len(batch)):
            summary.stop_reason = STOP_MAX_CHARGE
            return True
        return False

    def _group(self, record: dict[str, Any]) -> str:
        """State is grouped by publication date (published mode) or proposal deadline (open mode) for pruning."""
        if self.settings.mode == MODE_PUBLISHED:
            return record.get("publishedDate") or "unknown"
        return (record.get("proposalsCloseAt") or record.get("publishedAt") or "unknown")[:10]

    def _keep_from(self) -> str:
        if self.settings.mode == MODE_PUBLISHED:
            assert self.settings.date_from
            retention = max(14, (self.today - self.settings.date_from).days + 7)
            return (self.today - timedelta(days=retention)).isoformat()
        # Open mode only returns proposals still open, so anything that closed before today cannot come back.
        return self.today.isoformat()
