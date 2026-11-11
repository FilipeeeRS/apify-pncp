import pytest

from pncp_monitor.privacy import CPF_MASK, NAME_MASK, RG_MASK, is_valid_cpf, mask_personal_data


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


@pytest.mark.parametrize(
    "text",
    [
        "CPF: 111.444.777\u201335",
        "CPF/MF nº 111444777-35",
        "CPF 111.444.777.35",
        "CPF nº 111 444 777 35",
        "CPF-529.982.247-25",
    ],
)
def test_labelled_cpf_in_any_typing(text):
    masked = mask_personal_data(text)
    assert masked.endswith(CPF_MASK) and not any(ch.isdigit() for ch in masked)


@pytest.mark.parametrize("text", ["RG SSP/SP 12.345.678-9", "R.G 1234567", "C.I. 1234567", "RG nº 12.345.678-X"])
def test_labelled_rg(text):
    masked = mask_personal_data(text)
    assert masked.endswith(RG_MASK) and "345" not in masked and "1234567" not in masked


@pytest.mark.parametrize(
    "text, expected",
    [
        ("TRANSPORTE DO PACIENTE JOAO DA SILVA SANTOS PARA TRATAMENTO", f"TRANSPORTE DO PACIENTE {NAME_MASK} PARA TRATAMENTO"),
        ("Aluguel social para MARIA APARECIDA DE SOUZA, conforme lei", f"Aluguel social para {NAME_MASK}, conforme lei"),
        ("Sr. José Pereira, residente", f"Sr. {NAME_MASK}, residente"),
        ("TFD - Pedro Alves", f"TFD - {NAME_MASK}"),
        ("acompanhante: Carla Mendes e paciente Luiz Souza", f"acompanhante: {NAME_MASK} e paciente {NAME_MASK}"),
    ],
)
def test_names_after_markers_are_masked(text, expected):
    assert mask_personal_data(text) == expected


@pytest.mark.parametrize(
    "text",
    ["PACIENTE COM CANCER", "pacientes oncológicos do município", "REGISTRO DE PREÇOS SRP PARA AQUISIÇÃO", "beneficiários do programa"],
)
def test_markers_without_a_name_are_left_alone(text):
    assert mask_personal_data(text) == text


def test_empty_values_pass_through():
    assert mask_personal_data(None) is None
    assert mask_personal_data("") == ""
