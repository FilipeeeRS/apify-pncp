"""Personal-data masking for the free-text fields of a contratação.

The PNCP is a public register of agencies and companies, but agencies sometimes type a person's CPF, RG or name into
free text: a CPF in a "justificativa", patients of TFD (out-of-town treatment) or beneficiaries of "aluguel social"
in the object. Identifiers are masked by label and by shape. Names are masked when they follow a high-precision
marker (paciente, acompanhante, beneficiário, Sr./Sra., TFD, aluguel social). Names elsewhere in free text cannot be
told apart from place and company names reliably, so the README promises no more than this.
"""

from __future__ import annotations

import re
import unicodedata

CPF_MASK = "[CPF omitido]"
RG_MASK = "[RG omitido]"
NAME_MASK = "[nome omitido]"

_DASH = "\\-\u2010\u2011\u2012\u2013\u2014"  # hyphen and the dashes word processors put in its place
# Labelled CPF: "CPF", "CPF/MF", "C.P.F.", optional "nº", then 11 digits with any of . - – space between the groups.
_CPF_LABELLED = re.compile(
    rf"(?i)(\bC\.?P\.?F\.?(?:\s*/\s*MF)?\s*(?:n[ºo°]\.?|n\.º|número|numero)?\s*[:{_DASH}]?\s*)"
    rf"(\d{{3}}[.\s]?\d{{3}}[.\s]?\d{{3}}[.\s{_DASH}]?\d{{2}})(?!\d)"
)
# Unlabelled: 000.000.000-00 is masked whatever its check digits say. A bare run of 11 digits is masked only when the
# CPF check digits are valid, because process numbers, RENAVAM and phone numbers also have 11 digits.
_CPF_FORMATTED = re.compile(rf"(?<![\d./{_DASH}])\d{{3}}\.\d{{3}}\.\d{{3}}[{_DASH}]\d{{2}}(?![\d/{_DASH}])")
_CPF_PLAIN = re.compile(rf"(?<![\d./{_DASH}])\d{{11}}(?![\d/{_DASH}]|\.\d)")
# RG / carteira de identidade: "RG", "R.G", "C.I.", "identidade", an optional issuer ("SSP/SP"), optional "nº", number.
_RG = re.compile(
    rf"(?i)(\b(?:R\.?G\.?|C\.I\.?|identidade)(?:\s*(?:SSP|SESP|SDS|SSPDS|SJS|IFP|PC|DETRAN|DGPC|PCII)[\s/{_DASH}]*[A-Z]{{2}})?"
    rf"\s*(?:n[ºo°]\.?|n\.º|número|numero)?\s*[:{_DASH}]?\s*)(\d[\d.{_DASH}]{{3,12}}[\dxX])(?![\d])"
)
_NAME_MARKER = re.compile(
    r"(?i)\b((?:paciente|acompanhante|benefici[aá]ri[oa]|sra?|tfd)(?![^\W\d_])\.?"
    r"|aluguel\s+social(?:\s+(?:para|a|ao|à|de|em\s+favor\s+d[eoa]s?)(?![^\W\d_]))?)"
    r"(\s*[:,\-\u2013\u2014]?\s*(?:(?:o|a)\s+)?(?:(?:sr|sra)\.?\s+)?)"
)
_TOKEN = re.compile(r"\s*([^\W\d_][^\W\d_'´`.\-]*)", re.UNICODE)
_PARTICLES = {"de", "da", "do", "dos", "das", "e", "d"}
# Words that end a name (or show there is none): connectors and the institutional words that follow such markers.
_STOP = {
    "para", "em", "no", "na", "nos", "nas", "com", "conforme", "referente", "que", "ao", "aos", "a", "o", "as", "os",
    "por", "sem", "sob", "pelo", "pela", "durante", "entre", "ate", "via", "mediante", "cpf", "rg", "cns", "sus",
    "portador", "portadora", "residente", "domiciliado", "domiciliada", "inscrito", "inscrita", "nome", "tfd", "um", "uma",
    "municipio", "municipal", "prefeito", "prefeita", "prefeitura", "secretario", "secretaria", "presidente", "hospital",
    "clinica", "estado", "fundo", "camara", "servico", "servicos", "tratamento", "transporte", "consulta", "exame",
    "exames", "cirurgia", "atendimento", "contratacao", "aquisicao", "pagamento", "locacao", "aluguel", "social",
    "beneficio", "beneficiario", "beneficiaria", "beneficiarios", "paciente", "pacientes", "acompanhante", "familia",
}


def is_valid_cpf(value: str) -> bool:
    digits = [int(char) for char in re.sub(r"\D", "", value)]
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    for size in (9, 10):
        total = sum(digit * weight for digit, weight in zip(digits[:size], range(size + 1, 1, -1)))
        if (total * 10 % 11) % 10 != digits[size]:
            return False
    return True


def mask_personal_data(text: str | None) -> str | None:
    """Replace CPF, RG and marked person names with a marker. CNPJ and other company data are left untouched."""
    if not text:
        return text
    text = _CPF_LABELLED.sub(lambda m: m.group(1) + CPF_MASK, text)
    text = _CPF_FORMATTED.sub(CPF_MASK, text)
    text = _CPF_PLAIN.sub(lambda m: CPF_MASK if is_valid_cpf(m.group()) else m.group(), text)
    text = _RG.sub(lambda m: m.group(1) + RG_MASK, text)
    return _mask_names(text)


def _plain(word: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", word.casefold()) if not unicodedata.combining(c))


def _mask_names(text: str) -> str:
    out, position = [], 0
    for marker in _NAME_MARKER.finditer(text):
        if marker.start() < position:
            continue
        start = end = marker.end()
        names, cursor = 0, start
        while True:
            token = _TOKEN.match(text, cursor)
            if not token:
                break
            word = token.group(1)
            plain = _plain(word)
            if plain in _PARTICLES and names:
                cursor = token.end()
                continue
            if plain in _STOP or not word[0].isupper():
                break
            names += 1
            cursor = end = token.end()
            if names == 6:
                break
        if names >= 2:  # one capitalised word is too often a place or an adjective in all-caps text
            out.append(text[position:start])
            out.append(NAME_MASK)
            position = end
    out.append(text[position:])
    return "".join(out)
