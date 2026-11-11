import asyncio
from datetime import date

from pncp_monitor.client import Page, PncpRejected, PncpUnavailable
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
        "situacaoCompraId": 1,
        "orgaoEntidade": {"cnpj": "94436342000100", "razaoSocial": "MUNICIPIO DE MAQUINE", "esferaId": "M", "poderId": "N"},
        "unidadeOrgao": {"ufSigla": "RS", "codigoIbge": "4311775"},
    }


class FakeApi:
    """pages[(modality, page)] -> list of raw records; anything else is the empty 204 page."""

    def __init__(self, pages, page_size=2, fail_after=None, fail_modalities=(), reject=False):
        self.pages = pages
        self.page_size = page_size
        self.fail_after = fail_after
        self.fail_modalities = set(fail_modalities)
        self.reject = reject
        self.calls = []
        self.requests = 0

    async def get_page(self, path, params):
        self.requests += 1
        self.calls.append((path, params["codigoModalidadeContratacao"], params["pagina"], params.get("dataInicial")))
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise PncpUnavailable("HTTP 503 forever")
        modality = params["codigoModalidadeContratacao"]
        if modality in self.fail_modalities:
            raise PncpUnavailable("HTTP 500 after 4 attempts")
        if self.reject:
            raise PncpRejected("The PNCP rejected the query (HTTP 422): CNPJ do Órgão/Entidade inválido.")
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


def ids(sink):
    return [r["id"][-11:-5] for r in sink.items]


def test_one_off_runs_read_newest_first_and_drop_page_shift_duplicates():
    api, sink = FakeApi(PAGES), MemorySink()
    summary = crawl({"modalities": ["6", "8"], "maxResults": 100}, api, sink)
    assert ids(sink) == ["000002", "000003", "000001", "000010"]  # last page first, each page reversed
    assert summary.duplicates == 1 and summary.stop_reason == "completed" and summary.queries_done == 2
    assert [c[1:3] for c in api.calls] == [(6, 1), (6, 2), (8, 1)]
    assert api.calls[0] == ("/contratacoes/publicacao", 6, 1, "20261109")


def test_small_max_results_gets_the_latest_records():
    api, sink = FakeApi(PAGES), MemorySink()
    summary = crawl({"modalities": ["6", "8"], "maxResults": 1}, api, sink)
    assert ids(sink) == ["000002"] and summary.stop_reason == "max_results" and len(api.calls) == 2


def test_incremental_runs_keep_the_api_order():
    sink = MemorySink()
    crawl({"modalities": ["6", "8"], "incremental": True, "maxResults": 100}, FakeApi(PAGES), sink)
    assert ids(sink) == ["000001", "000002", "000003", "000010"]


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
    assert summary.stop_reason == "upstream_error" and summary.failed and "503" in summary.error
    assert len(sink.items) == 3 and len(sink.states["STATE-published-default"]["ids"]["2026-11-09"]) == 3


def test_a_failing_query_is_skipped_and_the_next_ones_still_run():
    pages = {**PAGES, (9, 1): [raw(20, modality=9)]}
    sink = MemorySink()
    summary = crawl({"modalities": ["6", "8", "9"], "maxResults": 100}, FakeApi(pages, fail_modalities={8}), sink)
    assert "000020" in ids(sink) and summary.queries_done == 2
    assert summary.failed_queries == ["codigoModalidadeContratacao=8"] and summary.failed and "1 query failed" in summary.error


def test_two_failures_in_a_row_stop_the_run():
    api = FakeApi(PAGES, fail_modalities={6, 8})
    summary = crawl({"modalities": ["6", "8", "9"], "maxResults": 100}, api, MemorySink())
    assert summary.failed_queries == ["codigoModalidadeContratacao=6", "codigoModalidadeContratacao=8"]
    assert all(c[1] != 9 for c in api.calls)


def test_a_rejected_query_stops_the_run_with_its_message():
    summary = crawl({"modalities": ["6"]}, FakeApi(PAGES, reject=True), MemorySink())
    assert summary.stop_reason == "rejected" and summary.failed and "CNPJ" in summary.status_line()


def test_suspended_tenders_are_left_out_of_open_proposals():
    closing = {"dataEncerramentoProposta": "2026-11-20T09:00:00"}
    pages = {(6, 1): [{**raw(1), **closing}, {**raw(2), **closing, "situacaoCompraId": 4}]}
    sink = MemorySink()
    crawl({"mode": "openProposals", "modalities": ["6"]}, FakeApi(pages), sink)
    assert ids(sink) == ["000001"]


def test_parallel_runs_of_one_monitor_do_not_erase_each_other():
    sink = MemorySink(state={"STATE-published-default": {"version": 1, "ids": {"2026-11-09": ["from-another-run"]}}})
    original, calls = sink.load_state, []

    async def load_state(key):  # the read at start sees nothing; later reads see the other run's save
        calls.append(key)
        return None if len(calls) == 1 else await original(key)

    sink.load_state = load_state
    crawl({"modalities": ["6"], "incremental": True}, FakeApi(PAGES), sink)
    assert "from-another-run" in sink.states["STATE-published-default"]["ids"]["2026-11-09"]


def test_a_migrated_run_does_not_deliver_twice():
    settings = parse_settings({"modalities": ["6"], "maxResults": 100}, TODAY)
    sink = MemorySink()
    crawler = Crawler(settings, FakeApi(PAGES), sink, today=TODAY, collected_at="2026-11-10T19:00:00Z")
    crawler.restore({"version": 1, "deliveredIds": ["94436342000100-1-000002/2026"]})
    summary = asyncio.run(crawler.run())
    assert ids(sink) == ["000003", "000001"] and summary.delivered == 3
    assert crawler.progress()["deliveredIds"][0] == "94436342000100-1-000002/2026"


def test_open_proposals_mode_uses_the_proposal_endpoint_and_prunes_closed_ones():
    closing = {"dataEncerramentoProposta": "2026-11-20T09:00:00"}
    pages = {(6, 1): [{**raw(1), **closing}]}
    old_state = {"STATE-openProposals-default": {"version": 1, "ids": {"2026-11-01": ["closed-one"]}}}
    api, sink = FakeApi(pages), MemorySink(state=old_state)
    crawl({"mode": "openProposals", "modalities": ["6"], "incremental": True}, api, sink)
    assert api.calls[0][0] == "/contratacoes/proposta"
    assert sink.states["STATE-openProposals-default"]["ids"] == {"2026-11-20": ["94436342000100-1-000001/2026"]}
