# Tool guide

Six read-only tools over public BDC filings (SEC EDGAR) and market rates (FRED).
The server also publishes this file as the resource `card://tools`.

## Which tool to use

| Question | Tool |
|---|---|
| What data is loaded? | `list_bdcs` |
| What loans does one BDC hold? | `get_bdc_portfolio` |
| Which loans look stressed? | `flag_stress_signals` |
| Who lends to this borrower, and do they agree on value? | `find_borrower_exposure` |
| Is stress rising at a BDC? | `get_portfolio_trend` |
| What are rates and credit spreads doing? | `get_market_context` |

## Rules for every tool

- **Sources.** Every response has `sources[]`: `bdc_ticker`, `form_type`, `period_end`, `source_url`, `series_id`. FRED sources fill only `series_id` and `source_url`. Filings that were searched are cited even if no row from them is returned, as are filings used only to compare marks.
- **Metadata.** Every response has `metadata`: `cached`, `data_version` and `data_mode` (`live` or `mock`; mock figures are invented).
- **Units.** `mark_pct` and other `_pct` fields are fractions: 0.95 means 95%. Dispersion (`_pts`) is in percentage points.
- **Null means unknown.** A null `is_pik` or `is_non_accrual` means the filing did not say. It does not mean "no".
- **Dates.** `as_of` is `YYYY-MM-DD`. The latest period on or before it is used. Blank means the latest period.
- **Pages.** `get_bdc_portfolio` and `flag_stress_signals` take `page` (default 1) and `page_size` (default 25, max 50), and return `total`.
- **Equity.** Counted in portfolio totals, but left out of scores, flags and dispersion.
- **Inputs.** A misspelled input name is an error, not ignored.
- **Errors.** The text says how to fix the call, e.g. `Unknown BDC 'ARES'. Valid values: ARCC, OBDC, FSK, BXSL.`
- **No data yet.** Tools that need loans tell you to run `uv run python -m ingest.run`.

Scoring rules are in the resource `card://stress-rules`.

---

## list_bdcs
Shows the loaded BDCs and periods. Call it first when unsure.

**Inputs:** none

**Output:** `bdcs[]` with `ticker`, `name`, `latest_period_end`, `periods_available`, `loan_count`; plus `not_loaded[]`.

```json
{}
```

## get_bdc_portfolio
The loan list for one BDC. The summary covers every loan that matches the filters, not just this page.

| Input | Type | Required | Notes |
|---|---|---|---|
| `bdc` | ARCC, OBDC, FSK, BXSL | yes | |
| `as_of` | date | no | |
| `industry` | text | no | Contains, any case |
| `instrument` | text | no | Contains, e.g. "first lien" |
| `page`, `page_size` | int | no | |

**Output:** `bdc`, `as_of`, `loans[]`, `page`, `page_size`, `total`, and `summary`:
`total_cost`, `total_fair_value`, `pik_loan_count`, `non_accrual_loan_count`, `non_accrual_pct_of_cost`.

Each loan: `borrower`, `borrower_key`, `bdc_ticker`, `period_end`, `industry`, `instrument`, `rate_text`, `spread_bps`, `maturity_date`, `principal`, `cost`, `fair_value`, `mark_pct`, `is_pik`, `is_non_accrual`, `stress_score`.

```json
{"bdc": "ARCC", "instrument": "first lien", "page_size": 10}
```

## flag_stress_signals
Stressed loans, highest score first. The main watchlist tool.

| Input | Type | Default | Notes |
|---|---|---|---|
| `bdc` | ARCC, OBDC, FSK, BXSL | all | |
| `industry` | text | all | Contains, any case |
| `as_of` | date | latest per BDC | |
| `mark_threshold` | number | 0.90 | Marks below this count as `markdown` |
| `signals` | list of `pik`, `non_accrual`, `markdown` | all three | A loan needs at least one |
| `min_score` | int, 0 to 10 | 1 | |
| `page`, `page_size` | int | 1, 25 | |

**Output:** `flags[]`, `page`, `page_size`, `total`.

Each flag: `borrower`, `borrower_key`, `bdc_ticker`, `period_end`, `industry`, `instrument`, `cost`, `fair_value`, `mark_pct`, `is_pik`, `is_non_accrual`, `mark_dispersion_pts`, `lender_count`, `stress_score`, `reasons`.

- Use `lender_count` of 2 or more for "held by more than one BDC".
- Marks from 0.90 up earn no points. To see them, raise `mark_threshold` and set `min_score` to 0.

```json
{"industry": "software", "signals": ["pik", "markdown"]}
```

## find_borrower_exposure
Every BDC that lends to one borrower, and how each values it.

| Input | Type | Required | Notes |
|---|---|---|---|
| `borrower` | text | yes | Name or `borrower_key`. Close spellings return candidates |
| `as_of` | date | no | |

**Output:** `status`, `borrower_key`, `matched_names`, `lenders[]`, `comparison_loans[]`, `mark_dispersion_pts`, `stress_score`, `candidates[]`, `total_candidates`, `unreviewed_similar_names`.

Each lender: `bdc_ticker`, `borrower`, `period_end`, `industry`, `instrument`, `rate_text`, `maturity_date`, `cost`, `fair_value`, `mark_pct`, `is_pik`, `is_non_accrual`, `stress_score`.

- `comparison_loans[]` has the same fields. These loans are not in `lenders[]`, but were used to compare marks in the same period. The borrower's `stress_score` covers `lenders[]` only.
- `status: "found"`: the name or key matched exactly.
- `status: "ambiguous"`: no exact match. There may be one candidate or several. Pick one from `candidates[]` (`borrower_key`, `names`, `bdc_tickers`) and call again with its `borrower_key`.
- No match is an error.

```json
{"borrower": "<a borrower name from get_bdc_portfolio>"}
```

## get_portfolio_trend
Stress measures for one BDC by quarter, oldest first.

| Input | Type | Required | Default |
|---|---|---|---|
| `bdc` | ARCC, OBDC, FSK, BXSL | yes | |
| `periods` | int | no | 4 |

**Output:** `bdc`, `trend[]` with `period_end`, `loan_count`, `total_fair_value`, `non_accrual_pct_of_cost`, `pik_pct_of_fair_value`, `avg_mark_pct`, `loans_marked_below_90_pct`.

```json
{"bdc": "OBDC", "periods": 4}
```

## get_market_context
Rates and credit spreads, because higher rates raise borrowers' interest costs. Needs `FRED_API_KEY`, or rates stored by an earlier ingestion.

| Input | Type | Default |
|---|---|---|
| `lookback_days` | int, 1 to 1095 | 365 |

**Output:** `lookback_days`, `data_origin` (`fred_live`, `stored` or `mock`), and `series[]` with `series_id`, `description`, `unit`, `latest_date`, `latest_value`, `start_date`, `start_value`, `change`. Covers `SOFR`, `BAMLH0A0HYM2` and `DGS10`.

```json
{"lookback_days": 365}
```
