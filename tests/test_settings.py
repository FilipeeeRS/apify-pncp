from datetime import date, timedelta

import pytest

from pncp_monitor.settings import InputError, build_streams, normalize_cnpj, parse_settings

TODAY = date(2026, 11, 10)


def test_defaults_are_small_and_always_have_data():
    s = parse_settings({}, TODAY)
    assert s.mode == "published"
    assert (s.date_from, s.date_to) == (date(2026, 11, 9), TODAY)  # last 1 day = yesterday and today
    assert s.max_results == 20 and not s.incremental and s.max_api_pages == 300
    assert s.modalities == (6, 8) and s.statuses == ()  # same defaults as the input schema
    assert len(build_streams(s)) == 2


def test_explicit_empty_modalities_mean_all_13():
    assert len(build_streams(parse_settings({"modalities": []}, TODAY))) == 13
    assert parse_settings({"modalities": None}, TODAY).modalities == (6, 8)


def test_explicit_window_wins_over_last_days():
    s = parse_settings({"dateFrom": "2026-11-01", "dateTo": "2026-11-05", "lastDays": 30}, TODAY)
    assert (s.date_from, s.date_to) == (date(2026, 11, 1), date(2026, 11, 5))


def test_open_proposals_mode_reaches_every_open_tender_and_only_active_ones():
    s = parse_settings({"mode": "openProposals"}, TODAY)
    assert s.date_from is None and s.proposal_deadline_to == TODAY + timedelta(days=3650)
    assert s.statuses == (1,)  # suspended or revoked tenders cannot be bid on
    assert parse_settings({"mode": "openProposals", "statuses": []}, TODAY).statuses == ()
    assert parse_settings({"statuses": ["1", "4"]}, TODAY).statuses == (1, 4)


def test_lists_are_cleaned_and_validated():
    s = parse_settings(
        {"modalities": ["6", 8, "6"], "states": ["sp", " RJ "], "agencyCnpjs": ["94.436.342/0001-00"], "keywords": [" software ", ""]},
        TODAY,
    )
    assert s.modalities == (6, 8) and s.states == ("SP", "RJ")
    assert s.cnpjs == ("94436342000100",) and s.keywords == ("software",)


def test_streams_cross_modalities_places_and_agencies():
    s = parse_settings({"modalities": ["6", "8"], "states": ["SP", "RJ"], "agencyCnpjs": ["94436342000100"]}, TODAY)
    streams = build_streams(s)
    assert len(streams) == 4
    assert streams[0] == {"codigoModalidadeContratacao": 6, "uf": "SP", "cnpj": "94436342000100"}


def test_municipalities_take_precedence_over_states():
    s = parse_settings({"modalities": ["6"], "states": ["SP"], "municipalityIbgeCodes": ["3550308"]}, TODAY)
    assert build_streams(s) == [{"codigoModalidadeContratacao": 6, "codigoMunicipioIbge": "3550308"}]


def test_alphanumeric_cnpj_is_accepted():
    assert normalize_cnpj("12.ABC.345/01DE-35") == "12ABC34501DE35"


@pytest.mark.parametrize(
    "raw, message",
    [
        ({"mode": "everything"}, "mode"),
        ({"dateFrom": "2026-11-09", "dateTo": "2026-11-01"}, "after"),
        ({"dateFrom": "09/11/2026"}, "YYYY-MM-DD"),
        ({"mode": "openProposals", "proposalDeadlineTo": "2026-01-01"}, "past"),
        ({"modalities": ["14"]}, "modality"),
        ({"states": ["XX"]}, "state"),
        ({"municipalityIbgeCodes": ["355030"]}, "IBGE"),
        ({"agencyCnpjs": ["00000000000000"]}, "CNPJ"),
        ({"agencyCnpjs": ["94436342000101"]}, "CNPJ"),
        ({"minValue": 10, "maxValue": 5}, "minValue"),
        ({"maxResults": 0}, "maxResults"),
        ({"statuses": ["9"]}, "status"),
        ({"modalities": [], "states": ["SP", "RJ", "MG", "BA", "PR", "RS", "SC", "GO", "PE", "CE", "PA", "AM", "ES", "MT", "MS", "DF"]}, "queries"),
    ],
)
def test_invalid_input_is_explained(raw, message):
    with pytest.raises(InputError, match=message):
        parse_settings(raw, TODAY)
