import asyncio

import httpx
import pytest

from pncp_monitor import client as c


def make_client(clock, responses, calls=None):
    """responses: list of httpx.Response or exceptions, served in order."""
    queue = list(responses)

    def handler(request):
        if calls is not None:
            calls.append(request)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    http = httpx.AsyncClient(base_url=c.BASE_URL, transport=httpx.MockTransport(handler))
    return c.PncpClient(http, sleep=clock.sleep, monotonic=clock.monotonic, rng=lambda: 0.0)


def ok(items, total_pages=1):
    return httpx.Response(200, json={"data": items, "totalRegistros": len(items), "totalPaginas": total_pages, "numeroPagina": 1})


def get(client, page=1):
    return asyncio.run(client.get_page("/contratacoes/publicacao", {"dataInicial": "20261109", "codigoModalidadeContratacao": 6, "pagina": page}))


def test_builds_the_url_and_parses_the_page(clock):
    calls = []
    page = get(make_client(clock, [ok([{"numeroControlePNCP": "a"}], total_pages=3)], calls), page=2)
    assert str(calls[0].url).startswith("https://pncp.gov.br/api/consulta/v1/contratacoes/publicacao?")
    assert calls[0].url.params["pagina"] == "2" and calls[0].url.params["codigoModalidadeContratacao"] == "6"
    assert (page.number, page.total_pages, len(page.items)) == (2, 3, 1)


def test_204_is_an_empty_page_not_an_error(clock):
    assert get(make_client(clock, [httpx.Response(204)])).items == []


def test_requests_are_paced(clock):
    client = make_client(clock, [ok([]), ok([]), ok([])])
    for _ in range(3):
        get(client)
    assert clock.sleeps == [3.0, 3.0]


def test_rate_limit_html_page_is_retried_with_long_waits(clock):
    waf = httpx.Response(429, text="<html>Limite de requisições excedido</html>", headers={"Content-Type": "text/html"})
    client = make_client(clock, [waf, httpx.Response(200, text="<html>blocked</html>"), ok([{"x": 1}])])
    assert len(get(client).items) == 1
    assert [s for s in clock.sleeps if s >= 10] == [20.0, 40.0]
    assert client.requests == 3 and client.retries == 2


def test_retry_after_header_is_honoured(clock):
    client = make_client(clock, [httpx.Response(429, headers={"Retry-After": "120"}), ok([])])
    get(client)
    assert 120.0 in clock.sleeps


def test_server_errors_and_timeouts_are_retried(clock):
    client = make_client(clock, [httpx.Response(503), httpx.ReadTimeout("slow"), ok([])])
    get(client)
    assert [s for s in clock.sleeps if s >= 5] == [5.0, 15.0]


def test_gives_up_after_the_last_retry(clock):
    client = make_client(clock, [httpx.Response(500)] * 4)
    with pytest.raises(c.PncpUnavailable, match="HTTP 500"):
        get(client)
    assert client.requests == 4


@pytest.mark.parametrize("status", [400, 422])
def test_rejections_are_not_retried(clock, status):
    calls = []
    response = httpx.Response(status, json={"message": "CNPJ do Órgão/Entidade inválido."})
    with pytest.raises(c.PncpRejected, match="CNPJ do Órgão/Entidade inválido"):
        get(make_client(clock, [response], calls))
    assert len(calls) == 1
