from datetime import date

from pncp_monitor.settings import parse_settings
from pncp_monitor.state import IncrementalState, state_key


def test_round_trip_and_prune():
    state = IncrementalState()
    state.add("old", "2026-10-01")
    state.add("new", "2026-11-09")
    loaded = IncrementalState.from_json(state.to_json("2026-11-10T00:00:00Z"))
    assert "old" in loaded and "new" in loaded and len(loaded) == 2
    loaded.prune("2026-11-01")
    assert "old" not in loaded and "new" in loaded


def test_garbage_means_empty_state():
    for data in (None, "x", {"version": 99, "ids": {}}, {"version": 1, "ids": {"2026-11-09": "not a list"}}):
        assert len(IncrementalState.from_json(data)) == 0


def test_key_is_per_mode_and_monitor():
    settings = parse_settings({"mode": "openProposals", "monitorName": "TI em São Paulo!"}, date(2026, 11, 10))
    assert state_key(settings) == "STATE-openProposals-ti-em-s-o-paulo"
