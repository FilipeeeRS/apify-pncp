import asyncio
from datetime import date

from pncp_monitor.client import Page, PncpUnavailable
from pncp_monitor.runner import Crawler
from pncp_monitor.settings import parse_settings

TODAY = date(2026, 11, 10)


def raw(n, modality=6, obj="Aquisição de material", published="2026-11-09T10:00:00", value=1000.0):
    return {
        "numeroControlePNCP": f"94436342000100-1-{n:06d}/2026",
        "modalidadeId": modality,
        "objetoCompra": obj,
        "dataPublicacaoPncp": published,
        "valorTotalEstimado": value,
        "orgaoEntidade": {"cnpj": "94436342000100", "razaoSocial": "MUNICIPIO DE MAQUINE", "esferaId": "M", "poderId": "N"},
        "unidadeOrgao": {"ufSigla": "RS", "codigoIbge": "4311775"},
    }


class FakeApi:
    """pages[(modality, page)] -> list of raw records; anything else is the empty 204 page."""

    def __init__(self, pages, page_size=2, fail_after=None):
        self.pages = pages
        self.page_size = page_size
        self.fail_after = fail_after
        self.calls = []
        self.requests = 0

    async def get_page(self, path, params):
        self.requests += 1
        self.calls.append((path, params["codigoModalidadeContratacao"], params["pagina"], params.get("dataInicial")))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise PncpUnavailable("HTTP 503 forever")
        modality = params["codigoModalidadeContratacao"]
        items = self.pages.get((modality, params["pagina"]), [])
        total_pages = max([p for (m, p) in self.pages if m == modality], default=0)
        return Page(items, params["pagina"], None, total_pages)


class MemorySink:
    def __init__(self, budget=None, state=None):
        self.items = []
        self.budget = budget
        self.states = dict(state or {})
        self.saves = 0

    async def push(self, items):
        self.items.extend(items)
        if self.budget is not None:
            self.budget -= len(items)
        return self.budget is not None and self.budget <= 0

    def remaining_budget(self):
        return self.budget

    async def load_state(self, key):
        return self.states.get(key)

    async def save_state(self, key, value):
        self.states[key] = value
        self.saves += 1

    async def status(self, message):
        pass


def crawl(raw_input, api, sink):
    settings = parse_settings(raw_input, TODAY)
    return asyncio.run(Crawler(settings, api, sink, today=TODAY, collected_at="2026-11-10T19:00:00Z").run())


PAGES = {(6, 1): [raw(1), raw(2)], (6, 2): [raw(3), raw(2)], (8, 1): [raw(10, modality=8)]}


def test_walks_every_page_of_every_modality_and_drops_page_shift_duplicates():
    api, sink = FakeApi(PAGES), MemorySink()
    summary = crawl({"modalities": ["6", "8"], "maxResults": 100}, api, sink)
    assert [r["id"][-11:-5] for r in sink.items] == ["000001", "000002", "000003", "000010"]
    assert summary.duplicates == 1 and summary.stop_reason == "completed" and summary.streams_done == 2
    assert api.calls[0] == ("/contratacoes/publicacao", 6, 1, "20261109")


def test_stops_at_max_results_without_reading_more_pages():
    api, sink = FakeApi(PAGES), MemorySink()
    summary = crawl({"modalities": ["6", "8"], "maxResults": 3}, api, sink)
    assert len(sink.items) == 3 and summary.stop_reason == "max_results"
    assert len(api.calls) == 2


def test_incremental_runs_deliver_only_new_records():
    sink = MemorySink()
    crawl({"modalities": ["6"], "incremental": True, "maxResults": 2}, FakeApi(PAGES), sink)
    assert [r["id"][-11:-5] for r in sink.items] == ["000001", "000002"]

    second = MemorySink(state=sink.states)
    summary = crawl({"modalities": ["6"], "incremental": True}, FakeApi(PAGES), second)
    assert [r["id"][-11:-5] for r in second.items] == ["000003"]  # resumes where the cap stopped the first run
    assert summary.already_delivered == 2

    third = MemorySink(state=second.states)
    assert crawl({"modalities": ["6"], "incremental": True}, FakeApi(PAGES), third).delivered == 0
    reset = MemorySink(state=second.states)
    assert crawl({"modalities": ["6"], "incremental": True, "resetState": True}, FakeApi(PAGES), reset).delivered == 3


def test_monitors_with_different_names_do_not_share_state():
    sink = MemorySink()
    crawl({"modalities": ["6"], "incremental": True, "monitorName": "a"}, FakeApi(PAGES), sink)
    other = MemorySink(state=sink.states)
    assert crawl({"modalities": ["6"], "incremental": True, "monitorName": "b"}, FakeApi(PAGES), other).delivered == 3


def test_respects_the_users_cost_limit():
    sink = MemorySink(budget=1)
    summary = crawl({"modalities": ["6"], "incremental": True}, FakeApi(PAGES), sink)
    assert len(sink.items) == 1 and summary.stop_reason == "max_charge"
    assert len(sink.states["STATE-published-default"]["ids"]["2026-11-09"]) == 1  # only what was really delivered


def test_keyword_and_value_filters():
    pages = {(6, 1): [raw(1, obj="Compra de NOTEBOOKS"), raw(2, obj="Compra de cadeiras"), raw(3, obj="Notebook", value=0)]}
    sink = MemorySink()
    summary = crawl({"modalities": ["6"], "keywords": ["notebook"], "minValue": 10}, FakeApi(pages), sink)
    assert [r["id"][-11:-5] for r in sink.items] == ["000001"] and sink.items[0]["matchedKeywords"] == ["notebook"]
    assert summary.filtered_out == 2


def test_page_budget_stops_the_run():
    summary = crawl({"modalities": ["6", "8"], "maxApiPages": 1, "maxResults": 100}, FakeApi(PAGES), MemorySink())
    assert summary.stop_reason == "max_api_pages" and summary.api_pages == 1


def test_an_api_outage_keeps_what_was_delivered():
    sink = MemorySink()
    summary = crawl({"modalities": ["6", "8"], "incremental": True, "maxResults": 100}, FakeApi(PAGES, fail_after=2), sink)
    assert summary.stop_reason == "upstream_error" and "503" in summary.error
    assert len(sink.items) == 3 and len(sink.states["STATE-published-default"]["ids"]["2026-11-09"]) == 3


def test_open_proposals_mode_uses_the_proposal_endpoint_and_prunes_closed_ones():
    closing = {"dataEncerramentoProposta": "2026-11-20T09:00:00"}
    pages = {(6, 1): [{**raw(1), **closing}]}
    old_state = {"STATE-openProposals-default": {"version": 1, "ids": {"2026-11-01": ["closed-one"]}}}
    api, sink = FakeApi(pages), MemorySink(state=old_state)
    crawl({"mode": "openProposals", "modalities": ["6"], "incremental": True}, api, sink)
    assert api.calls[0][0] == "/contratacoes/proposta"
    assert sink.states["STATE-openProposals-default"]["ids"] == {"2026-11-20": ["94436342000100-1-000001/2026"]}
