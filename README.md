# PNCP Monitor: Brazilian public tenders (licitações) as clean data

Every public purchase in Brazil under the new procurement law (Lei 14.133/2021) is published on the **PNCP**, the Portal Nacional de Contratações Públicas: about 6,000 new contratações on a busy weekday, from federal ministries to small town halls. This Actor reads the **official, open PNCP API** and gives you those contratações as a clean, stable table, filtered to what matters to you, and on a schedule can send you **only what is new since the last run**.

> **Resumo em português:** monitor de licitações do PNCP pela API oficial. Busca contratações publicadas num período ou com propostas ainda abertas, filtra por modalidade, UF, município (IBGE), órgão (CNPJ), palavras-chave no objeto e faixa de valor, e no modo incremental entrega só o que é novo desde a última execução, ideal para alerta diário. Saída limpa: datas ISO, valores numéricos e link para a página da contratação no PNCP. Você paga só pelo resultado entregue. [Mais em português no fim da página.](#em-português)

## Who uses it

- **Companies that sell to the government** get a daily list of open tenders for their products in their states, with the bidding deadline and, when the agency provides it, the link to the bidding platform.
- **Procurement consultancies (consultorias de licitação)** monitor several clients' niches and agencies with one scheduled task each.
- **Data journalists and researchers** pull every contratação of a city, an agency or a modality for analysis, with the PNCP control number as a stable key.
- **Market intelligence and BI teams** feed Google Sheets, a database or a dashboard with what the public sector is buying, including direct contracting (dispensa, inexigibilidade).

## What you get

- **Two modes.** *Published in a period* (everything published on the PNCP in a date window) and *Open for proposals now* (tenders whose bidding period is still open, the ones you can still win).
- **Filters.** Modality, status, state (UF), municipality (IBGE code), buying agency (CNPJ), keywords and excluded keywords in the object, and estimated value range. Open-for-proposals mode skips suspended and revoked tenders by default.
- **Incremental mode.** The Actor remembers what each monitor already delivered and returns only new contratações. Schedule it daily and plug it into e-mail, Slack, Sheets or your own system.
- **A clean, stable schema.** 40 fields with English names (the PNCP originals are in each field's description), ISO 8601 dates with the Brasília offset, numbers as numbers, `null` instead of empty strings and zeros, and a direct link to the PNCP page with documents and items.
- **Privacy care.** CPF and RG numbers that agencies sometimes type into free text are masked, and so are names that follow markers like *paciente* or *beneficiário*. See [Privacy](#privacy-lgpd) for what this does and does not cover.
- **Built for a fragile source.** The portal rate-limits and has bad days. The Actor paces its requests, retries with backoff, skips a query that keeps failing and reports it, and in incremental mode a run cut short by your result cap or cost limit is continued by the next one, without delivering anything twice.

## Example output

One item per contratação (shortened):

```json
{
  "id": "13864377000130-1-001818/2026",
  "url": "https://pncp.gov.br/app/editais/13864377000130/2026/1818",
  "modality": "Pregão - Eletrônico",
  "status": "Divulgada no PNCP",
  "object": "REGISTRO DE PREÇOS PARA O FORNECIMENTO DE ÓLEOS LUBRIFICANTES E GRAXA PARA EQUIPAMENTOS DE NEBULIZAÇÃO UBV...",
  "estimatedValue": null,
  "isPriceRegistry": true,
  "publishedAt": "2026-11-09T08:02:35-03:00",
  "publishedDate": "2026-11-09",
  "proposalsOpenAt": "2026-11-10T08:00:00-03:00",
  "proposalsCloseAt": "2026-11-23T09:00:00-03:00",
  "agencyCnpj": "13864377000130",
  "agencyName": "FUNDO MUNICIPAL DE SAUDE - FMS",
  "agencySphere": "Municipal",
  "unitName": "PMSP - SECRETARIA MUNICIPAL DE SAÚDE",
  "city": "São Paulo",
  "state": "SP",
  "cityIbgeCode": "3550308",
  "sourceSystem": "Compras.gov.br",
  "sourceUrl": "https://cnetmobile.estaleiro.serpro.gov.br/comprasnet-web/public/compras/acompanhamento-compra?compra=92500305006442026",
  "budgetSources": ["Municipal"],
  "matchedKeywords": [],
  "collectedAt": "2026-11-11T00:52:21Z"
}
```

The **Output** tab has three views: *Overview*, *Bidding details* (deadlines, dispute mode, where to bid) and *Agencies*. Export to JSON, CSV, Excel or XML, or read it through the API.

<details>
<summary>All fields</summary>

| Field | Meaning (PNCP name) |
| --- | --- |
| `id` | PNCP control number, unique and stable (`numeroControlePNCP`) |
| `url` | Public page on the PNCP portal |
| `modalityCode`, `modality` | 1-13: Pregão eletrônico, Dispensa, Concorrência... (`modalidadeId`, `modalidadeNome`) |
| `disputeModeCode`, `disputeMode` | Aberto, Fechado, Aberto-Fechado... (`modoDisputa*`) |
| `statusCode`, `status` | Divulgada, Revogada, Anulada, Suspensa (`situacaoCompra*`) |
| `instrument`, `legalBasis` | Edital, Aviso de contratação direta...; legal article (`amparoLegal`) |
| `isPriceRegistry` | Registro de preços (`srp`) |
| `object`, `additionalInfo` | What is bought (`objetoCompra`, `informacaoComplementar`), with CPF/RG masked |
| `purchaseNumber`, `processNumber`, `year`, `sequence` | Identifiers in the agency's system |
| `estimatedValue`, `approvedValue` | Reais; `null` when not disclosed or not yet homologated |
| `publishedAt`, `publishedDate`, `updatedAt` | ISO 8601, Brasília time |
| `proposalsOpenAt`, `proposalsCloseAt` | Bidding window; `null` for direct contracting without dispute |
| `agencyCnpj`, `agencyName`, `agencySphere`, `agencyBranch` | Buying agency (`orgaoEntidade`) |
| `unitCode`, `unitName`, `city`, `cityIbgeCode`, `state`, `stateName` | Buying unit and its location (`unidadeOrgao`) |
| `sourceSystem`, `sourceUrl`, `electronicProcessUrl` | Platform that published it and where the bidding happens |
| `budgetSources` | Budget sources (`fontesOrcamentarias`) |
| `matchedKeywords` | Which of your keywords were found |
| `collectedAt` | When the Actor read it (UTC) |

</details>

## How much it costs

**US$ 2.00 per 1,000 results** (US$ 0.002 each), plus Apify's small start fee per run. You pay only for contratações delivered to your dataset, never for records scanned and filtered out. A daily alert that finds 30 new tenders costs about US$ 0.06 a day. Set a maximum cost per run in the run options and the Actor stops cleanly when it is reached.

## Examples

**Try it:** press *Start* with the default input. You get the 20 most recent pregões eletrônicos published on the PNCP, in a few seconds. (Dispensas, the second default modality, come after the pregões, so raise `maxResults` to see them.)

**Daily alert of open tenders for your products** (schedule it every morning):

```json
{
  "mode": "openProposals",
  "modalities": ["4", "6", "8"],
  "states": ["SP", "MG", "RJ"],
  "keywords": ["notebook", "computador", "monitor", "impressora"],
  "excludeKeywords": ["locação", "manutenção"],
  "minValue": 20000,
  "maxResults": 500,
  "incremental": true,
  "monitorName": "ti-sudeste"
}
```

**Everything a city published yesterday and today** (for journalism or BI, also with `incremental` for a daily feed):

```json
{
  "mode": "published",
  "lastDays": 1,
  "modalities": [],
  "municipalityIbgeCodes": ["3550308"],
  "maxResults": 5000,
  "incremental": true,
  "monitorName": "sao-paulo-capital"
}
```

**A fixed period for one agency:**

```json
{
  "mode": "published",
  "dateFrom": "2026-10-01",
  "dateTo": "2026-10-31",
  "modalities": [],
  "agencyCnpjs": ["00394460000141"],
  "maxResults": 10000,
  "maxApiPages": 2000
}
```

### Schedule it

In Apify Console: *Saved tasks* → create a task with your input → *Schedules* → daily, e.g. `0 7 * * *` in America/Sao_Paulo. With `incremental: true` every run returns only what is new. Use `lastDays: 2` in published mode so a day the portal was down is caught up the next day.

### Call it from your code

```bash
curl -X POST "https://api.apify.com/v2/acts/filipeeers~pncp-monitor/run-sync-get-dataset-items?token=YOUR_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"mode": "openProposals", "states": ["SP"], "keywords": ["software"], "maxResults": 100}'
```

```python
from apify_client import ApifyClient

client = ApifyClient("YOUR_API_TOKEN")
run = client.actor("filipeeers/pncp-monitor").call(run_input={"mode": "openProposals", "states": ["SP"], "keywords": ["software"]})
for item in client.dataset(run["defaultDatasetId"]).iterate_items():
    print(item["proposalsCloseAt"], item["object"], item["url"])
```

### Google Sheets, Slack, e-mail, Make, Zapier

In your task's **Integrations** tab, chain the *Google Sheets Import & Export* Actor to append every run's results to a sheet, or send Slack and e-mail notifications and webhooks when a run succeeds. Make, Zapier and n8n have Apify modules that start the task and read its dataset.

## Input

| Field | What it does | Default |
| --- | --- | --- |
| `mode` | `published` (published in a window) or `openProposals` (bidding still open) | `published` |
| `lastDays` | Published mode: from N days ago to today (Brasília time) | `1` |
| `dateFrom`, `dateTo` | Published mode: explicit window, overrides `lastDays` | today as end |
| `proposalDeadlineTo` | Open mode: only proposals closing up to this date | ten years ahead (all open) |
| `modalities` | PNCP codes 1-13; an empty list = all 13 | `["6", "8"]` |
| `statuses` | 1 Divulgada, 2 Revogada, 3 Anulada, 4 Suspensa | open mode: `["1"]`; published: any |
| `states` | UF codes; empty = whole country | |
| `municipalityIbgeCodes` | 7-digit IBGE codes; override `states` | |
| `agencyCnpjs` | Buying agency CNPJs | |
| `keywords`, `excludeKeywords` | Words in the object, matched at the start of a word, ignoring case, accents and hyphens; any keyword is enough | |
| `minValue`, `maxValue` | Estimated value in reais; undisclosed values are left out when set | |
| `maxResults` | Stop after this many results | `20` |
| `incremental`, `monitorName`, `resetState` | Only new results, one memory per monitor name, start over | off, `default`, off |
| `maxApiPages` | Safety cap on PNCP pages per run (50 records each) | `300` |

Modality, state, municipality and agency are filtered by the PNCP itself, so they make runs faster. Keywords and value are filtered by the Actor after reading, so a broad keyword search over a long window reads many pages: narrow it with the other filters first.

## Good to know

- **Speed.** The PNCP blocks clients that call it too fast, so the Actor reads one page of 50 records about every 3 seconds: roughly 1,000 records a minute. Runs that find what they need on the first pages finish in seconds.
- **Order and limits.** A one-off run returns the newest publications first, query by query. Incremental runs keep the PNCP's order (oldest first), so when `maxResults` or your cost limit stops one, the next run continues from there. `maxApiPages` is different: incremental runs read from the first page again, so set it high enough to cover the whole window.
- **Incremental memory** is a key-value store named `pncp-monitor-state` in your account, one record per mode and monitor name. Delete it, or use `resetState`, to start over. Changing filters but keeping the name keeps the memory, so give each filter set its own name, and do not start the same monitor twice at the same time.
- **Run summary.** Every run saves `RUN_SUMMARY` in its key-value store: records scanned, filtered, already delivered, delivered and why it stopped.
- **When the PNCP is down.** The Actor retries for several minutes. A query that still fails is skipped and listed in `RUN_SUMMARY`; two failures in a row stop the run. Either way the run is marked as failed, so schedules alert you, and what was delivered stays delivered.

## Privacy (LGPD)

The PNCP is an official public register of government purchases, and this Actor delivers data about agencies and their purchases only. It does not collect supplier, bidder or contract-party records. In the free-text fields, which agencies write by hand:

- CPF and RG numbers are replaced by `[CPF omitido]` / `[RG omitido]`, however they are typed (with or without label, dots, dashes or spaces).
- Names that follow markers such as *paciente*, *acompanhante*, *beneficiário(a)*, *Sr./Sra.*, *TFD* or *aluguel social* are replaced by `[nome omitido]`.
- CNPJs and other company data are kept.

This is a best effort, not a guarantee: a person's name written without such a marker cannot be told apart from a place or company name, so the rest of the text is delivered as published by the government. Use the data in line with the LGPD and the purpose you have for it.

## Em português

O **PNCP Monitor** lê a API oficial e aberta do Portal Nacional de Contratações Públicas e entrega as contratações num formato limpo e estável. Use o modo **Publicadas** para tudo o que saiu num período (inclusive dispensas e inexigibilidades) e o modo **Propostas abertas** para as licitações que ainda aceitam proposta. Filtre por modalidade, situação, UF, município (código IBGE), órgão (CNPJ), palavras-chave no objeto (pelo início da palavra, sem diferenciar maiúsculas, acentos nem hífens) e faixa de valor estimado. No modo de propostas abertas, licitações suspensas e revogadas ficam de fora por padrão. Ligue o **modo incremental** e agende uma tarefa diária para receber só as novidades, por exemplo numa planilha do Google ou no Slack. Cada item traz o link da contratação no PNCP, as datas de abertura e encerramento das propostas, o órgão, a unidade, o município e a plataforma onde a disputa acontece. O preço é de US$ 2,00 por mil resultados entregues, e o que é filtrado não é cobrado. CPF e RG que aparecem nos textos são mascarados, assim como nomes depois de marcadores como paciente e beneficiário.

## Support

Found a bug, a field you need or a filter that would help? Open an issue in the **Issues** tab; it is answered quickly. This is an independent project, not affiliated with the Brazilian government; data comes from the official PNCP API (`pncp.gov.br/api/consulta`).

## Development

```bash
python -m venv ../.venvs/apify-pncp           # keep the venv out of synced folders
pip install -r requirements.txt pytest
pytest                                         # offline tests with recorded PNCP fixtures
apify run --input '{"maxResults": 5}'          # local run against the real API
```

Code layout: `pncp_monitor/client.py` (paced, retrying API client, with the API's measured quirks in its docstring), `settings.py` (input validation), `normalize.py` (output schema), `filters.py`, `privacy.py`, `state.py` (incremental memory) and `runner.py` (orchestration); `main.py` wires them to the Apify SDK.
