"""Crawl orchestration: queries -> pages -> normalize -> filter -> dedupe -> deliver -> remember.

Order. The API returns oldest publications first. A one-off run reads each query newest first (page 1 tells how many
pages there are, then the last page down), so a small `maxResults` gets the latest records. Incremental runs keep the
API order, so a run cut short by `maxResults` or the user's cost limit is continued by the next run.

Safety. In incremental mode a record is remembered only after it is pushed, and the state is saved (merged with what
a parallel run may have saved) after every page. The ids delivered by this run are also kept as run progress, so a
run that the platform migrates or resurrects does not deliver and charge the same records twice. A query that keeps
failing is skipped and reported; two failed queries in a row mean the PNCP is down and the run stops.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any, Protocol

from .client import PAGE_SIZE, Page, PncpClient, PncpError, PncpRejected, PncpUnavailable
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
STOP_REJECTED = "rejected"
FAILED_STOPS = (STOP_UPSTREAM, STOP_REJECTED)
MAX_CONSECUTIVE_FAILURES = 2


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
    queries: int
    queries_done: int = 0
    failed_queries: list[str] = field(default_factory=list)
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

    @property
    def failed(self) -> bool:
        return self.stop_reason in FAILED_STOPS

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
            STOP_MAX_PAGES: " Stopped at maxApiPages; narrow the filters or raise the limit."
            + (" Incremental runs start reading from the first page again, so this cap must cover the whole window." if self.incremental else ""),
            STOP_UPSTREAM: f" The PNCP API failed: {self.error}",
            STOP_REJECTED: f" {self.error}",
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
        self.delivered_ids: list[str] = []
        self._already_this_run: set[str] = set()
        self.pages_left = settings.max_api_pages
        self.newest_first = not settings.incremental
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
        self.summary = Summary(mode=settings.mode, window=window, incremental=settings.incremental, queries=len(self.streams))

    # Run progress, for platform migrations: the ids this run already delivered.
    def progress(self) -> dict[str, Any]:
        return {"version": 1, "deliveredIds": list(self.delivered_ids)}

    def restore(self, progress: Any) -> None:
        if isinstance(progress, dict) and isinstance(progress.get("deliveredIds"), list):
            ids = [i for i in progress["deliveredIds"] if isinstance(i, str)]
            self.delivered_ids = ids
            self._already_this_run = set(ids)
            self.summary.delivered = len(ids)
            if ids:
                log.info("Resuming after a restart: %d results were already delivered by this run", len(ids))

    async def run(self) -> Summary:
        settings, summary = self.settings, self.summary
        if settings.incremental and not settings.reset_state:
            self.state = IncrementalState.from_json(await self.sink.load_state(self.key))
        summary.state_size_before = len(self.state)
        consecutive_failures = 0
        try:
            for index, stream in enumerate(self.streams, start=1):
                label = ", ".join(f"{key}={value}" for key, value in stream.items())
                try:
                    stop = await self._crawl_stream(index, label, stream)
                except PncpUnavailable as exc:
                    consecutive_failures += 1
                    summary.failed_queries.append(label)
                    summary.stop_reason, summary.error = STOP_UPSTREAM, str(exc)
                    log.error("Query %d/%d (%s) failed and was skipped: %s", index, len(self.streams), label, exc)
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        log.error("Two queries in a row failed: the PNCP looks down, stopping.")
                        break
                    continue
                consecutive_failures = 0
                summary.queries_done += 1
                if stop:
                    break
        except PncpRejected as exc:
            summary.stop_reason, summary.error = STOP_REJECTED, str(exc)
        except PncpError as exc:
            summary.stop_reason, summary.error = STOP_UPSTREAM, str(exc)
        finally:
            summary.api_requests = self.client.requests
            if summary.failed_queries and summary.stop_reason not in FAILED_STOPS:
                summary.stop_reason = STOP_UPSTREAM  # a cap was reached after a query had been skipped
            if summary.failed_queries:
                summary.error = f"{len(summary.failed_queries)} quer{'y' if len(summary.failed_queries) == 1 else 'ies'} failed ({'; '.join(summary.failed_queries)}). Last error: {summary.error}"
            if settings.incremental:
                await self._save_state()
            summary.state_size_after = len(self.state)
        return summary

    async def _crawl_stream(self, index: int, label: str, stream: dict[str, Any]) -> bool:
        """Read every page of one query. Return True when the whole run has to stop."""
        params = {**self.base_params, **stream}
        first = await self._fetch(params, 1)
        if first is None:
            return True
        log.info("Query %d/%d (%s): %s records", index, len(self.streams), label, first.total_records or 0)
        total = first.total_pages or (1 if first.items else 0)
        if not self.newest_first:
            page = first
            for number in range(1, total + 1):
                if number > 1:
                    page = await self._fetch(params, number)
                    if page is None:
                        return True
                if await self._handle_page(page, index, number):
                    return True
                if not page.items:
                    break
            return False
        for number in range(total, 1, -1):
            page = await self._fetch(params, number)
            if page is None:
                return True
            if await self._handle_page(page, index, number):
                return True
        return await self._handle_page(first, index, 1)

    async def _fetch(self, params: dict[str, Any], number: int) -> Page | None:
        if self.pages_left <= 0:
            self.summary.stop_reason = STOP_MAX_PAGES
            return None
        self.pages_left -= 1
        self.summary.api_pages += 1
        return await self.client.get_page(self.path, {**params, "pagina": number, "tamanhoPagina": PAGE_SIZE})

    async def _handle_page(self, page: Page, index: int, number: int) -> bool:
        settings, summary = self.settings, self.summary
        items = list(reversed(page.items)) if self.newest_first else page.items
        batch: list[dict[str, Any]] = []
        for raw in items:
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
            if record_id in self._already_this_run:  # delivered before a migration or restart of this same run
                continue
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
            self.delivered_ids.extend(record["id"] for record in batch)
            if settings.incremental:
                for record in batch:
                    self.state.add(record["id"], self._group(record))
                await self._save_state()
        if summary.api_pages % 5 == 1:
            await self.sink.status(f"Query {index}/{len(self.streams)}, page {number}: {summary.delivered} results so far")

        if summary.delivered >= settings.max_results:
            summary.stop_reason = STOP_MAX_RESULTS
            return True
        if limit_reached or (allowance is not None and allowance <= len(batch)):
            summary.stop_reason = STOP_MAX_CHARGE
            return True
        return False

    async def _save_state(self) -> None:
        """Merge with the stored state first, so two runs of the same monitor do not erase each other's ids."""
        stored = IncrementalState.from_json(await self.sink.load_state(self.key)) if not self.settings.reset_state else None
        if stored is not None:
            self.state.merge(stored)
        self.state.prune(self._keep_from())
        await self.sink.save_state(self.key, self.state.to_json(self.collected_at))

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
