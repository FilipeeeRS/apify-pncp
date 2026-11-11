"""Actor input -> validated, immutable `Settings`, plus the API query "streams" they imply."""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

# Official PNCP domain table "Modalidade de Contratação" (manual of the consultation APIs, section 5.2).
MODALITIES: dict[int, str] = {
    1: "Leilão - Eletrônico",
    2: "Diálogo Competitivo",
    3: "Concurso",
    4: "Concorrência - Eletrônica",
    5: "Concorrência - Presencial",
    6: "Pregão - Eletrônico",
    7: "Pregão - Presencial",
    8: "Dispensa de Licitação",
    9: "Inexigibilidade",
    10: "Manifestação de Interesse",
    11: "Pré-qualificação",
    12: "Credenciamento",
    13: "Leilão - Presencial",
}
STATES = (
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
)
MODE_PUBLISHED = "published"
MODE_OPEN = "openProposals"
MODES = (MODE_PUBLISHED, MODE_OPEN)

DEFAULT_MAX_RESULTS = 20
DEFAULT_MAX_API_PAGES = 300
DEFAULT_LAST_DAYS = 1
DEFAULT_PROPOSAL_HORIZON_DAYS = 3650  # the API accepts far-future dates; a 1-year horizon left out ~0.5% of open tenders
DEFAULT_MODALITIES = (6, 8)  # same as the input schema default; an explicit empty list means all 13
# Domain table "Situação da Contratação" (manual, section 5.5).
STATUSES = {1: "Divulgada no PNCP", 2: "Revogada", 3: "Anulada", 4: "Suspensa"}
DEFAULT_OPEN_STATUSES = (1,)  # in open mode, suspended/revoked tenders cannot be bid on
MAX_STREAMS = 200  # modalities x places x agencies: each stream costs at least one paced API call


class InputError(ValueError):
    """The Actor input is invalid. The message is shown to the user as-is."""


@dataclass(frozen=True)
class Settings:
    mode: str
    date_from: date | None  # published mode: publication window (inclusive)
    date_to: date | None
    proposal_deadline_to: date | None  # open-proposals mode: proposals still open, closing up to this date
    modalities: tuple[int, ...]  # empty = all 13
    statuses: tuple[int, ...]  # empty = any status
    states: tuple[str, ...]
    municipalities: tuple[str, ...]  # IBGE codes
    cnpjs: tuple[str, ...]
    keywords: tuple[str, ...]
    exclude_keywords: tuple[str, ...]
    min_value: float | None
    max_value: float | None
    max_results: int
    incremental: bool
    monitor_key: str
    reset_state: bool
    max_api_pages: int


def parse_settings(raw: dict[str, Any] | None, today: date) -> Settings:
    """Validate the raw Actor input. `today` is the current date in Brasília time."""
    raw = raw or {}

    mode = _text(raw.get("mode")) or MODE_PUBLISHED
    if mode not in MODES:
        raise InputError(f"'mode' must be one of {', '.join(MODES)} (got {mode!r}).")

    date_from = date_to = deadline = None
    if mode == MODE_PUBLISHED:
        date_to = _date(raw.get("dateTo"), "dateTo") or today
        date_from = _date(raw.get("dateFrom"), "dateFrom")
        if date_from is None:
            last_days = _int(raw.get("lastDays"), "lastDays", DEFAULT_LAST_DAYS, 0, 3650)
            date_from = date_to - timedelta(days=last_days)
        if date_from > date_to:
            raise InputError(f"'dateFrom' ({date_from}) is after 'dateTo' ({date_to}).")
    else:
        deadline = _date(raw.get("proposalDeadlineTo"), "proposalDeadlineTo") or today + timedelta(days=DEFAULT_PROPOSAL_HORIZON_DAYS)
        if deadline < today:
            raise InputError(f"'proposalDeadlineTo' ({deadline}) is in the past; the PNCP only accepts today or later.")

    if raw.get("modalities") is None:
        modalities = DEFAULT_MODALITIES
    else:
        modalities = tuple(dict.fromkeys(_modality(v) for v in _items(raw.get("modalities"), "modalities")))
    if raw.get("statuses") is None:
        statuses = DEFAULT_OPEN_STATUSES if mode == MODE_OPEN else ()
    else:
        statuses = tuple(dict.fromkeys(_status(v) for v in _items(raw.get("statuses"), "statuses")))
    states = tuple(dict.fromkeys(_state(v) for v in _items(raw.get("states"), "states")))
    municipalities = tuple(dict.fromkeys(_ibge(v) for v in _items(raw.get("municipalityIbgeCodes"), "municipalityIbgeCodes")))
    cnpjs = tuple(dict.fromkeys(normalize_cnpj(v) for v in _items(raw.get("agencyCnpjs"), "agencyCnpjs")))

    min_value = _number(raw.get("minValue"), "minValue")
    max_value = _number(raw.get("maxValue"), "maxValue")
    if min_value is not None and max_value is not None and min_value > max_value:
        raise InputError(f"'minValue' ({min_value}) is greater than 'maxValue' ({max_value}).")

    settings = Settings(
        mode=mode,
        date_from=date_from,
        date_to=date_to,
        proposal_deadline_to=deadline,
        modalities=modalities,
        statuses=statuses,
        states=states,
        municipalities=municipalities,
        cnpjs=cnpjs,
        keywords=tuple(dict.fromkeys(_items(raw.get("keywords"), "keywords"))),
        exclude_keywords=tuple(dict.fromkeys(_items(raw.get("excludeKeywords"), "excludeKeywords"))),
        min_value=min_value,
        max_value=max_value,
        max_results=_int(raw.get("maxResults"), "maxResults", DEFAULT_MAX_RESULTS, 1, 10_000_000),
        incremental=bool(raw.get("incremental", False)),
        monitor_key=_slug(_text(raw.get("monitorName")) or "default"),
        reset_state=bool(raw.get("resetState", False)),
        max_api_pages=_int(raw.get("maxApiPages"), "maxApiPages", DEFAULT_MAX_API_PAGES, 1, 100_000),
    )
    streams = len(build_streams(settings))
    if streams > MAX_STREAMS:
        raise InputError(
            f"Your filters expand to {streams} separate PNCP queries (modalities x states or municipalities x agencies); "
            f"the limit is {MAX_STREAMS}. Narrow the lists, or split the work across several runs."
        )
    return settings


def build_streams(settings: Settings) -> list[dict[str, Any]]:
    """One API query per (modality, place, agency) combination; the date window is added by the runner.

    The API requires a modality on `/contratacoes/publicacao`, and heavy modality-less queries on `/contratacoes/proposta`
    ended in HTTP 500 during testing, so an empty modality list always means "all 13, one query each".
    A municipality code already implies its state, so municipalities take precedence over states.
    """
    modalities = settings.modalities or tuple(MODALITIES)
    places = [{"codigoMunicipioIbge": code} for code in settings.municipalities] or [{"uf": uf} for uf in settings.states] or [{}]
    agencies = [{"cnpj": cnpj} for cnpj in settings.cnpjs] or [{}]
    return [
        {"codigoModalidadeContratacao": modality, **place, **agency}
        for modality, place, agency in itertools.product(modalities, places, agencies)
    ]


def normalize_cnpj(value: str) -> str:
    """Strip punctuation and validate the check digits. Accepts the new alphanumeric CNPJ too."""
    cnpj = re.sub(r"[.\-/\s]", "", str(value)).upper()
    if not re.fullmatch(r"[0-9A-Z]{12}[0-9]{2}", cnpj) or not _cnpj_digits_ok(cnpj):
        raise InputError(f"Invalid CNPJ in 'agencyCnpjs': {value!r}.")
    return cnpj


def _cnpj_digits_ok(cnpj: str) -> bool:
    if len(set(cnpj)) == 1:
        return False

    def check_digit(base: str) -> int:
        total = sum((ord(char) - 48) * (2 + position % 8) for position, char in enumerate(reversed(base)))
        rest = total % 11
        return 0 if rest < 2 else 11 - rest

    first = check_digit(cnpj[:12])
    second = check_digit(cnpj[:12] + str(first))
    return cnpj[12:] == f"{first}{second}"


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _items(value: Any, field: str) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise InputError(f"'{field}' must be a list.")
    return [text for text in (_text(item) for item in value) if text]


def _date(value: Any, field: str) -> date | None:
    text = _text(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise InputError(f"'{field}' must be a date as YYYY-MM-DD (got {value!r}).") from None


def _number(value: Any, field: str) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InputError(f"'{field}' must be a number (got {value!r}).") from None
    if number < 0:
        raise InputError(f"'{field}' cannot be negative.")
    return number


def _int(value: Any, field: str, default: int, minimum: int, maximum: int) -> int:
    if value is None or value == "":
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise InputError(f"'{field}' must be an integer (got {value!r}).") from None
    if not minimum <= number <= maximum:
        raise InputError(f"'{field}' must be between {minimum} and {maximum} (got {number}).")
    return number


def _modality(value: str) -> int:
    try:
        code = int(value)
    except ValueError:
        raise InputError(f"Unknown modality {value!r}; use the PNCP codes 1 to 13.") from None
    if code not in MODALITIES:
        raise InputError(f"Unknown modality {value!r}; use the PNCP codes 1 to 13.")
    return code


def _status(value: str) -> int:
    try:
        code = int(value)
    except ValueError:
        code = 0
    if code not in STATUSES:
        raise InputError(f"Unknown status {value!r}; use 1 (Divulgada), 2 (Revogada), 3 (Anulada) or 4 (Suspensa).")
    return code


def _state(value: str) -> str:
    uf = value.upper()
    if uf not in STATES:
        raise InputError(f"Unknown state {value!r}; use the two-letter UF code, for example SP.")
    return uf


def _ibge(value: str) -> str:
    if not re.fullmatch(r"\d{7}", value):
        raise InputError(f"Invalid IBGE municipality code {value!r}; it has 7 digits, for example 3550308.")
    return value


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "default"
