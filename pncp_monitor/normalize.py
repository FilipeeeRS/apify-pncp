"""Raw PNCP record (`RecuperarCompraPublicacaoDTO`) -> the Actor's flat, stable output record."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

from .privacy import mask_personal_data
from .settings import MODALITIES

# The PNCP sends Brasília local time without an offset (its own error payloads say -03:00, and Brazil has had no
# daylight saving time since 2019). The offset is made explicit so spreadsheets and databases read it right.
BRT = timezone(timedelta(hours=-3))
PORTAL_URL = "https://pncp.gov.br/app/editais/{cnpj}/{year}/{sequence}"
SPHERES = {"F": "Federal", "E": "Estadual", "M": "Municipal", "D": "Distrital"}
BRANCHES = {"E": "Executivo", "L": "Legislativo", "J": "Judiciário"}
# numeroControlePNCP = <CNPJ of the agency>-1-<sequence, zero padded>/<year>, e.g. 94436342000100-1-000058/2026
_CONTROL_NUMBER = re.compile(r"^([0-9A-Za-z]{14})-\d+-0*(\d+)/(\d{4})$")


def normalize(raw: Mapping[str, Any], collected_at: str) -> dict[str, Any]:
    agency = raw.get("orgaoEntidade") or {}
    unit = raw.get("unidadeOrgao") or {}
    legal = raw.get("amparoLegal") or {}
    control_number = _text(raw.get("numeroControlePNCP"))
    published = _text(raw.get("dataPublicacaoPncp"))
    modality_code = _int(raw.get("modalidadeId"))
    return {
        "id": control_number,
        "url": portal_url(control_number, agency.get("cnpj"), raw.get("anoCompra"), raw.get("sequencialCompra")),
        "modalityCode": modality_code,
        "modality": _text(raw.get("modalidadeNome")) or MODALITIES.get(modality_code or 0),
        "disputeModeCode": _int(raw.get("modoDisputaId")),
        "disputeMode": _text(raw.get("modoDisputaNome")),
        "statusCode": _int(raw.get("situacaoCompraId")),
        "status": _text(raw.get("situacaoCompraNome")),
        "instrument": _text(raw.get("tipoInstrumentoConvocatorioNome")),
        "legalBasis": _text(legal.get("nome")),
        "isPriceRegistry": raw.get("srp") if isinstance(raw.get("srp"), bool) else None,
        "object": mask_personal_data(_text(raw.get("objetoCompra"))),
        "additionalInfo": mask_personal_data(_text(raw.get("informacaoComplementar"))),
        "purchaseNumber": mask_personal_data(_text(raw.get("numeroCompra"))),
        "processNumber": mask_personal_data(_text(raw.get("processo"))),
        "year": _int(raw.get("anoCompra")),
        "sequence": _int(raw.get("sequencialCompra")),
        "estimatedValue": _money(raw.get("valorTotalEstimado")),
        "approvedValue": _money(raw.get("valorTotalHomologado")),
        "publishedAt": to_iso(published),
        "publishedDate": published[:10] if published else None,
        "updatedAt": to_iso(raw.get("dataAtualizacaoGlobal") or raw.get("dataAtualizacao")),
        "proposalsOpenAt": to_iso(raw.get("dataAberturaProposta")),
        "proposalsCloseAt": to_iso(raw.get("dataEncerramentoProposta")),
        "agencyCnpj": _text(agency.get("cnpj")),
        "agencyName": _text(agency.get("razaoSocial")),
        "agencySphere": SPHERES.get(_text(agency.get("esferaId")) or ""),
        "agencyBranch": BRANCHES.get(_text(agency.get("poderId")) or ""),
        "unitCode": _text(unit.get("codigoUnidade")),
        "unitName": _text(unit.get("nomeUnidade")),
        "state": _text(unit.get("ufSigla")),
        "stateName": _text(unit.get("ufNome")),
        "city": _text(unit.get("municipioNome")),
        "cityIbgeCode": _text(unit.get("codigoIbge")),
        # usuarioNome is the procurement system that published the record (Compras.gov.br, BLL, Betha...), not a person.
        "sourceSystem": _text(raw.get("usuarioNome")),
        "sourceUrl": _text(raw.get("linkSistemaOrigem")),
        "electronicProcessUrl": _text(raw.get("linkProcessoEletronico")),
        "budgetSources": [
            name for name in (_text(item.get("nome")) for item in raw.get("fontesOrcamentarias") or [] if isinstance(item, dict)) if name
        ],
        "matchedKeywords": [],
        "collectedAt": collected_at,
    }


def portal_url(control_number: str | None, cnpj: Any, year: Any, sequence: Any) -> str | None:
    match = _CONTROL_NUMBER.match(control_number or "")
    if match:
        return PORTAL_URL.format(cnpj=match.group(1), sequence=match.group(2), year=match.group(3))
    if cnpj and year and sequence:
        return PORTAL_URL.format(cnpj=cnpj, year=year, sequence=int(sequence))
    return None


def to_iso(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BRT)
    return parsed.isoformat(timespec="seconds")


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> float | None:
    """Reais as a number. Zero means "not disclosed" (orçamento sigiloso) or not homologated yet, so it becomes null."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 2) if number > 0 else None
