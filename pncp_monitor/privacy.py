"""Personal-data masking for the free-text fields of a contratação.

The PNCP is a public register of agencies and companies, but agencies sometimes type a person's CPF or RG into free
text (a real example was found in a "justificativaPresencial" field while testing). Those identifiers are masked
before anything reaches the dataset. Names of people cannot be found reliably in free text, so none are extracted
and no supplier or person records are delivered: see "Privacy" in the README.
"""

from __future__ import annotations

import re

CPF_MASK = "[CPF omitido]"
RG_MASK = "[RG omitido]"

# 000.000.000-00 is masked whatever its check digits say. A bare run of 11 digits is masked only when the CPF
# check digits are valid, because process numbers, RENAVAM and phone numbers also have 11 digits.
_CPF_FORMATTED = re.compile(r"(?<![\d.\-/])\d{3}\.\d{3}\.\d{3}-\d{2}(?![\d\-/])")
_CPF_PLAIN = re.compile(r"(?<![\d.\-/])\d{11}(?![\d\-/]|\.\d)")
_RG = re.compile(r"(?i)\b(RG|R\.G\.|identidade)(\s*(?:n[ºo°]\.?|n\.º)?\s*[:\-]?\s*)(\d[\d.\-]{4,12}[\dxX]|\d{5,9})")


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
    """Replace CPF and RG numbers with a marker. Everything else, including CNPJ, is left untouched."""
    if not text:
        return text
    text = _CPF_FORMATTED.sub(CPF_MASK, text)
    text = _CPF_PLAIN.sub(lambda match: CPF_MASK if is_valid_cpf(match.group()) else match.group(), text)
    return _RG.sub(lambda match: f"{match.group(1)}{match.group(2)}{RG_MASK}", text)
