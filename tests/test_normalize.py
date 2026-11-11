from pncp_monitor.normalize import normalize, portal_url, to_iso, to_url
from pncp_monitor.privacy import CPF_MASK, RG_MASK

COLLECTED = "2026-11-10T19:00:00Z"


def by_id(page, record_id):
    return next(r for r in page["data"] if r["numeroControlePNCP"] == record_id)


def test_maps_a_real_record_to_the_flat_schema(api_page):
    record = normalize(by_id(api_page, "94436342000100-1-000058/2026"), COLLECTED)
    assert record["url"] == "https://pncp.gov.br/app/editais/94436342000100/2026/58"
    assert record["modalityCode"] == 6 and record["modality"] == "Pregão - Eletrônico"
    assert record["status"] == "Divulgada no PNCP" and record["isPriceRegistry"] is True
    assert record["estimatedValue"] == 32799.96 and record["approvedValue"] == 10200.0
    assert record["publishedAt"] == "2026-11-01T02:14:33-03:00" and record["publishedDate"] == "2026-11-01"
    assert record["proposalsCloseAt"] == "2026-07-17T09:00:00-03:00"
    assert record["agencyCnpj"] == "94436342000100" and record["agencySphere"] == "Municipal"
    assert record["agencyBranch"] is None  # poderId "N"
    assert (record["state"], record["cityIbgeCode"]) == ("RS", "4311775")
    assert record["legalBasis"] == "Lei 14.133/2021, Art. 28, I"
    assert record["additionalInfo"] is None  # empty string upstream
    assert record["matchedKeywords"] == [] and record["collectedAt"] == COLLECTED


def test_zero_value_means_not_disclosed(api_page):
    assert normalize(by_id(api_page, "01613072000177-1-000067/2026"), COLLECTED)["estimatedValue"] is None


def test_direct_contracting_has_no_proposal_window(api_page):
    record = normalize(by_id(api_page, "92402510000103-1-000167/2026"), COLLECTED)
    assert record["proposalsOpenAt"] is None and record["proposalsCloseAt"] is None


def test_federal_agency_and_budget_sources(api_page):
    record = normalize(by_id(api_page, "33839275000172-1-000060/2026"), COLLECTED)
    assert record["agencySphere"] == "Federal" and record["budgetSources"]


def test_personal_data_is_masked_but_company_data_kept(api_page):
    text = normalize(by_id(api_page, "92402510000103-1-000999/2026"), COLLECTED)["object"]
    assert "529.982.247-25" not in text and "52998224725" not in text and text.count(CPF_MASK) == 2
    assert RG_MASK in text and "11.222.333/0001-81" in text and "01406802031" in text


def test_every_record_has_the_same_keys(api_page):
    keys = {tuple(normalize(raw, COLLECTED)) for raw in api_page["data"]}
    assert len(keys) == 1


def test_missing_nested_objects_do_not_crash():
    record = normalize({"numeroControlePNCP": "x"}, COLLECTED)
    assert record["id"] == "x" and record["url"] is None and record["agencyName"] is None


def test_helpers():
    assert portal_url(None, "94436342000100", 2026, "58") == "https://pncp.gov.br/app/editais/94436342000100/2026/58"
    assert to_iso("2026-11-01T02:14:33.5") == "2026-11-01T02:14:33-03:00"
    assert to_iso("not a date") is None


def test_links_typed_by_hand_are_cleaned():
    assert to_url("https://bll.org.br/x?y=1") == "https://bll.org.br/x?y=1"
    assert to_url("contratosgov.com.br/edital/123") == "https://contratosgov.com.br/edital/123"
    assert to_url("www.portal.gov.br") == "https://www.portal.gov.br"
    assert to_url("Link Não Informado") is None
    assert to_url("") is None and to_url(None) is None


def test_free_text_whitespace_is_collapsed():
    record = normalize({"numeroControlePNCP": "x", "objetoCompra": "Compra de\r\n  cadeiras   ", "unidadeOrgao": {"nomeUnidade": "SEC.\nSAUDE"}}, COLLECTED)
    assert record["object"] == "Compra de cadeiras" and record["unitName"] == "SEC. SAUDE"
