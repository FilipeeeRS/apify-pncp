import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def api_page():
    """A real page shape: 5 records recorded from the public API on 2026-11-10, plus 1 synthetic record with fake PII."""
    return json.loads((FIXTURES / "contratacoes.json").read_text(encoding="utf-8"))


class FakeClock:
    """Stands in for time.monotonic/asyncio.sleep: sleeping just moves the clock, so tests never wait."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock():
    return FakeClock()
