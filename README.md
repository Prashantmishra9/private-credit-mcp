# Private Credit Early-Warning MCP

An MCP server that helps credit analysts spot borrower stress early, using only public data: SEC filings from four BDCs and FRED market rates.

```
SEC EDGAR + FRED  ->  ingest/  ->  data/credit.duckdb  ->  server/ (MCP)  ->  test_client.py, ui/, Claude Desktop
```

## Set up and run

Needs [uv](https://docs.astral.sh/uv/). Run everything from this folder.

1. Install: `uv sync`
2. Add keys: `cp .env.example .env`, then fill in both values. Restart the UI or server after changing them.
3. Load data: `uv run python -m ingest.run`
4. Test every tool: `uv run python test_client.py`
5. Open the UI: `uv run uvicorn ui.backend:app --host 127.0.0.1 --port 8000`, then visit http://127.0.0.1:8000
6. Unit tests (no keys needed): `uv run pytest`

A filing is loaded only if its total fair value is within 1% of the total in the filing. `ingest.run` prints each result.

## Test the UI with mock data (no keys)

The CSV files in `mock/data/` are served as JSON by a small API, then loaded into `data/mock.duckdb`. Every figure is invented, and real data uses a different file.

```sh
uv run uvicorn mock.api:app --host 127.0.0.1 --port 8001      # terminal 1, only needed while loading
uv run python -m mock.load                                    # terminal 2; rerun after editing a CSV
DATA_MODE=mock uv run uvicorn ui.backend:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000. To test every tool: `DATA_MODE=mock uv run python test_client.py`.
To use real data, follow the steps above with `DATA_MODE` blank, and restart the UI.

## Where things are

| Path | What it does |
|---|---|
| `config.yaml` | BDC list, score weights, limits |
| `config.py` | Reads `config.yaml` and `.env` |
| `ingest/run.py` | Start here for ingestion |
| `ingest/edgar_client.py`, `fred_client.py` | Download data |
| `ingest/parse_xbrl.py`, `parse_html.py` | Read loans from filings: XBRL first, HTML fallback |
| `ingest/footnotes.py` | Work out PIK and non-accrual from each filing's footnotes |
| `ingest/normalize.py`, `aliases.py` | Match borrowers across BDCs |
| `ingest/load.py` | Write to DuckDB |
| `server/main.py` | Start here for the MCP server |
| `server/tools.py` | The six tools |
| `server/portfolio.py`, `borrowers.py`, `market.py` | Work out the answers |
| `server/scoring.py` | Stress score |
| `server/cache.py` | Response cache |
| `server/resources.py` | Decision cards |
| `mcp_connection.py` | Starts the server for the test client and UI |
| `test_client.py` | Calls every tool and prints PASS, FAIL or SKIP |
| `ui/` | Web page and its backend |
| `mock/` | Invented CSV data, its API and loader |
| `docs/TOOLS.md` | Tool guide, also served as `card://tools` |
| `tests/` | Unit tests on synthetic fixtures |

Review fuzzy borrower matches with `uv run python -m ingest.aliases list`. They stay out of scores until approved.

## Claude Desktop

```json
{
  "mcpServers": {
    "private-credit": {
      "command": "uv",
      "args": ["--directory", "/full/path/to/private-credit-mcp", "run", "python", "-m", "server.main"]
    }
  }
}
```

Keys come from `.env`. If Claude Desktop cannot find `uv`, use its full path (`which uv`).
Try: *"Which software borrowers are held by more than one BDC and show PIK or markdowns?"*

## Known limits

- Data is quarterly and published 30 to 60 days after quarter end.
- BDCs are only part of private credit. Most private funds disclose nothing publicly.
- Fair values are each BDC's own estimates.
- Covenant waivers and maturity extensions are rarely visible in filings.
- Borrower matching across BDCs is approximate.
