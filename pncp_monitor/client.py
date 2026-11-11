"""Async client for the PNCP consultation API (https://pncp.gov.br/api/consulta, no authentication).

Measured against the live API on 2026-11-10; these facts shape everything below:
  * `tamanhoPagina` accepts 10 to 50. The PDF manual says up to 500, but the API answers 400 above 50.
  * No results is HTTP 204 with an empty body, not an empty list. A page past the end is also 204.
  * A WAF answers 429 with an HTML page after a burst: about 7 calls in 10 s at one call per 1.5 s, and the block
    lasted more than 30 s. 30 calls in a row at one call per 2.5 s went through. Hence one call per 3 s.
  * Responses take 0.1 s to 30+ s, and a heavy query can end in HTTP 500 after about a minute.
  * 400/422 carry a JSON `message` (invalid CNPJ, date in the past...) and are not worth retrying.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

log = logging.getLogger("apify.pncp_monitor.client")

BASE_URL = "https://pncp.gov.br/api/consulta/v1"
PAGE_SIZE = 50
USER_AGENT = "pncp-monitor-apify-actor/0.1"
MIN_INTERVAL = 3.0  # seconds between the starts of two requests
RATE_LIMIT_WAITS = (20.0, 40.0, 60.0, 90.0)  # after 429 or a WAF page: about 3.5 min in total
SERVER_WAITS = (5.0, 15.0, 45.0)  # after 5xx, timeouts and connection errors
MAX_RETRY_AFTER = 300.0


class PncpError(RuntimeError):
    pass


class PncpRejected(PncpError):
    """400/422: the request itself is wrong (bad CNPJ, past date...). Retrying would not help."""


class PncpUnavailable(PncpError):
    """The API kept failing after every retry."""


@dataclass(frozen=True)
class Page:
    items: list[dict[str, Any]]
    number: int
    total_records: int | None = None
    total_pages: int | None = None


def make_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=BASE_URL,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=httpx.Timeout(90.0, connect=20.0),
    )


class PncpClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        min_interval: float = MIN_INTERVAL,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self._http = http
        self._min_interval = min_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._rng = rng
        self._next_slot = float("-inf")
        self.requests = 0
        self.retries = 0

    async def get_page(self, path: str, params: Mapping[str, Any]) -> Page:
        number = int(params.get("pagina", 1))
        attempts = {"rate": 0, "server": 0}
        while True:
            await self._pace()
            self.requests += 1
            retry_after: float | None = None
            try:
                response = await self._http.get(path, params=dict(params))
            except httpx.TransportError as exc:  # timeouts, resets, DNS
                kind, reason = "server", f"{type(exc).__name__}: {exc}"
            else:
                status = response.status_code
                if status == 204:
                    return Page([], number, 0, 0)
                if status == 200:
                    page = _parse_page(response, number)
                    if page is not None:
                        return page
                    kind, reason = "rate", "HTTP 200 without the expected JSON (WAF page?)"
                elif status == 429:
                    kind, reason = "rate", "HTTP 429 rate limit"
                elif status >= 500:
                    kind, reason = "server", f"HTTP {status}"
                elif status in (400, 422):
                    raise PncpRejected(f"The PNCP rejected the query (HTTP {status}): {_error_message(response)}")
                else:
                    raise PncpError(f"Unexpected HTTP {status} from the PNCP for {path}")
                retry_after = _retry_after(response)

            waits = RATE_LIMIT_WAITS if kind == "rate" else SERVER_WAITS
            attempt = attempts[kind]
            if attempt >= len(waits):
                raise PncpUnavailable(f"The PNCP API kept failing ({reason}) after {attempt + 1} attempts on {path}.")
            attempts[kind] += 1
            self.retries += 1
            wait = waits[attempt] * (1 + 0.25 * self._rng())
            if retry_after:
                wait = max(wait, min(retry_after, MAX_RETRY_AFTER))
            log.warning("PNCP %s on %s page %d; retrying in %.0f s", reason, path, number, wait)
            await self._sleep(wait)

    async def _pace(self) -> None:
        now = self._monotonic()
        start = max(now, self._next_slot)
        self._next_slot = start + self._min_interval
        if start > now:
            await self._sleep(start - now)


def _parse_page(response: httpx.Response, number: int) -> Page | None:
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict) or not isinstance(body.get("data"), list):
        return None
    return Page(
        items=[item for item in body["data"] if isinstance(item, dict)],
        number=number,
        total_records=_int_or_none(body.get("totalRegistros")),
        total_pages=_int_or_none(body.get("totalPaginas")),
    )


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict) and body.get("message"):
            return str(body["message"])
    except ValueError:
        pass
    return response.text[:200]


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return float(response.headers.get("Retry-After", ""))
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
