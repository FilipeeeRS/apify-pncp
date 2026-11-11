import pytest

from pncp_monitor.privacy import CPF_MASK, RG_MASK, is_valid_cpf, mask_personal_data


@pytest.mark.parametrize("cpf, valid", [("529.982.247-25", True), ("52998224725", True), ("529.982.247-26", False), ("111.111.111-11", False)])
def test_cpf_check_digits(cpf, valid):
    assert is_valid_cpf(cpf) is valid


def test_masks_formatted_cpf_even_with_wrong_check_digits():
    assert mask_personal_data("CPF 123.456.789-00 do contratado") == f"CPF {CPF_MASK} do contratado"


def test_masks_bare_cpf_only_when_check_digits_are_valid():
    text = "CPF 52998224725, emenda parlamentar nº 20269048000, RENAVAM 01406802031"
    assert mask_personal_data(text) == f"CPF {CPF_MASK}, emenda parlamentar nº 20269048000, RENAVAM 01406802031"


def test_masks_rg_and_keeps_the_label():
    masked = mask_personal_data("portador da identidade RG n° 842.995, inscrito no CPF sob nº. 013.360.111-06, residente")
    assert masked == f"portador da identidade RG n° {RG_MASK}, inscrito no CPF sob nº. {CPF_MASK}, residente"


@pytest.mark.parametrize("text", ["CNPJ 11.222.333/0001-81", "CNPJ 11222333000181", "Processo 024.00133567", "Lei 14.133/2021", "identidade visual 2026"])
def test_leaves_company_and_process_identifiers_alone(text):
    assert mask_personal_data(text) == text


def test_empty_values_pass_through():
    assert mask_personal_data(None) is None
    assert mask_personal_data("") == ""
