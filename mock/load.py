"""Fills data/mock.duckdb from the mock API: uv run python -m mock.load

Only ever writes to the mock database. Start mock.api first.
"""

import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from typing import Any

import httpx
from pydantic import TypeAdapter, ValidationError

from config import Config, load_config
from ingest.aliases import refresh_aliases
from ingest.load import load_filing, load_rates, transaction
from ingest.models import FilingRef, ParsedLoan

Loans = dict[str, list[ParsedLoan]]
Rates = dict[str, list[tuple[date, float]]]
READ_FILING = TypeAdapter(FilingRef).validate_python
READ_LOAN = TypeAdapter(ParsedLoan).validate_python
READ_RATE = TypeAdapter(tuple[date, float]).validate_python


class MockDataError(RuntimeError):
    """The mock data could not be used; the message says what to fix."""


def fetch(http: httpx.Client, name: str) -> list[dict[str, Any]]:
    try:
        response = http.get(f"/{name}")
        response.raise_for_status()
        return response.json()
    except (httpx.HTTPError, ValueError) as error:
        raise MockDataError(
            f"Could not read /{name} from the mock API ({type(error).__name__}). Start it with: "
            "uv run uvicorn mock.api:app --host 127.0.0.1 --port 8001") from None


def check_row(name: str, number: int, read: Any, values: Any) -> Any:
    """Convert one row, or say which CSV row and column is wrong."""
    try:
        return read(values)
    except ValidationError as error:
        problem = error.errors()[0]
        column = ".".join(str(part) for part in problem["loc"])
        raise MockDataError(f"{name}.csv row {number}, {column}: {problem['msg']}") from None


def read_filings(rows: list[dict], config: Config) -> list[FilingRef]:
    filings = [check_row("filings", number, READ_FILING, row) for number, row in enumerate(rows, 2)]
    for number, filing in enumerate(filings, 2):
        if filing.bdc_ticker not in config.bdcs:
            raise MockDataError(f"filings.csv row {number}: unknown BDC '{filing.bdc_ticker}'. "
                                f"Valid values: {', '.join(config.bdcs)}.")
    return filings


def read_loans(rows: list[dict], filings: list[FilingRef]) -> Loans:
    known, loans = {filing.filing_id for filing in filings}, defaultdict(list)
    for number, row in enumerate(rows, 2):
        filing_id = row.pop("filing_id")
        if filing_id not in known:
            raise MockDataError(f"loans.csv row {number}: filing_id '{filing_id}' is not in filings.csv.")
        loans[filing_id].append(check_row("loans", number, READ_LOAN, row))
    return loans


def read_rates(rows: list[dict]) -> Rates:
    rates = defaultdict(list)
    for number, row in enumerate(rows, 2):
        rates[row["series_id"]].append(check_row("rates", number, READ_RATE, (row["date"], row["value"])))
    return rates


def replace_everything(path: Path, filings: list[FilingRef], loans: Loans, rates: Rates) -> None:
    with transaction(path) as db:
        for table in ("loans", "filings", "market_rates", "borrower_aliases"):
            db.execute(f"DELETE FROM {table}")
    for filing in filings:
        load_filing(path, filing, "mock", loans[filing.filing_id])
    for series_id, values in rates.items():
        load_rates(path, series_id, values)


def load_mock(config: Config, http: httpx.Client) -> str:
    """Check every row first, then replace the mock database."""
    filings = read_filings(fetch(http, "filings"), config)
    loans = read_loans(fetch(http, "loans"), filings)
    rates = read_rates(fetch(http, "rates"))
    path = config.paths.mock_database
    replace_everything(path, filings, loans, rates)
    refresh_aliases(path, config.ingest.fuzzy_match_threshold)
    return (f"Loaded MOCK data: {len(filings)} filings, {sum(map(len, loans.values()))} loans, "
            f"{sum(map(len, rates.values()))} rates into {path.name}")


def main() -> int:
    config = load_config()
    try:
        with httpx.Client(base_url=config.mock_api_url, timeout=20, trust_env=False) as http:
            print(load_mock(config, http))
    except MockDataError as error:
        print(f"Mock data not loaded: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
