from datetime import date

from pncp_monitor.filters import RecordFilter, fold
from pncp_monitor.settings import parse_settings


def make(**raw):
    return RecordFilter.from_settings(parse_settings(raw, date(2026, 11, 10)))


RECORD = {
    "object": "Aquisição de NOTEBOOKS e licenças de software para a Secretaria de Educação",
    "estimatedValue": 150000.0,
    "modalityCode": 6,
    "state": "SP",
    "cityIbgeCode": "3550308",
    "agencyCnpj": "94436342000100",
}


def test_fold():
    assert fold("  Licitação  ÓRGÃO ") == "licitacao orgao"
    assert fold("AR-CONDICIONADO\nSPLIT") == "ar condicionado split"


def test_keywords_match_at_word_start_across_hyphens():
    split = {**RECORD, "object": "Manutenção de AR-CONDICIONADO e realocação de mobiliário"}
    assert make(keywords=["ar condicionado"]).check(split)[0] is True
    assert make(excludeKeywords=["locação"]).check(split)[0] is True  # "realocação" is another word
    assert make(excludeKeywords=["realocação"]).check(split)[0] is False


def test_status_filter():
    assert make(statuses=["1"]).check({**RECORD, "statusCode": 4})[0] is False
    assert make(statuses=["1"]).check({**RECORD, "statusCode": 1})[0] is True


def test_any_keyword_is_enough_and_matches_are_reported_as_typed():
    assert make(keywords=["notebook", "impressora", "Licenças"]).check(RECORD) == (True, ["notebook", "Licenças"])
    assert make(keywords=["impressora"]).check(RECORD) == (False, [])


def test_exclude_keywords_win():
    assert make(keywords=["notebook"], excludeKeywords=["educacao"]).check(RECORD)[0] is False


def test_value_range_and_undisclosed_values():
    assert make(minValue=100000, maxValue=200000).check(RECORD)[0] is True
    assert make(minValue=200000).check(RECORD)[0] is False
    assert make(maxValue=100000).check(RECORD)[0] is False
    assert make(minValue=1).check({**RECORD, "estimatedValue": None})[0] is False
    assert make().check({**RECORD, "estimatedValue": None})[0] is True


def test_server_side_filters_are_rechecked():
    assert make(modalities=["8"]).check(RECORD)[0] is False
    assert make(states=["RJ"]).check(RECORD)[0] is False
    assert make(states=["RJ"], municipalityIbgeCodes=["3550308"]).check(RECORD)[0] is True
    assert make(agencyCnpjs=["11222333000181"]).check(RECORD)[0] is False
