"""Read-only DuckDB queries. Connections are short so they never hold up ingestion."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import duckdb
from mcp.server.mcpserver.exceptions import ToolError

NO_DATA = ("No data has been loaded yet. Set SEC_USER_AGENT and FRED_API_KEY in .env, then run "
           "`uv run python -m ingest.run` in the project folder.")

NO_MOCK_DATA = ("No mock data has been loaded yet. Start the mock API with `uv run uvicorn mock.api:app "
                "--host 127.0.0.1 --port 8001`, then run `uv run python -m mock.load`.")


class DataUnavailable(ToolError):
    """The database cannot be read; the message says how to fix it."""


@dataclass(frozen=True)
class Filing:
    filing_id: str
    bdc_ticker: str
    form_type: str
    period_end: date
    source_url: str
    loan_count: int


@dataclass(frozen=True)
class Loan:
    loan_id: str
    filing_id: str
    bdc_ticker: str
    borrower: str
    borrower_key: str
    group_key: str          # borrower_key, or the shared key of analyst-approved aliases
    period_end: date
    industry: str | None
    instrument: str
    rate_text: str | None
    spread_bps: int | None
    maturity_date: date | None
    principal: float | None
    cost: float | None
    fair_value: float
    mark_pct: float | None
    is_pik: bool | None
    is_non_accrual: bool | None


class Database:
    def __init__(self, path: Path, open_timeout: float, no_data: str = NO_DATA) -> None:
        self._no_data = no_data
        self._path = path
        self._timeout = open_timeout

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        if not self._path.exists():
            raise DataUnavailable(self._no_data)
        connection = self._open()
        try:
            yield connection
        except duckdb.CatalogException:
            raise DataUnavailable(self._no_data) from None
        finally:
            connection.close()

    def _open(self) -> duckdb.DuckDBPyConnection:
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                return duckdb.connect(str(self._path), read_only=True)
            except duckdb.Error:
                if time.monotonic() >= deadline:
                    raise DataUnavailable(
                        "The database could not be opened. If ingestion is running, try again "
                        f"in a moment. Otherwise delete data/{self._path.name} and load the data again.") from None
                time.sleep(0.05)

    def data_version(self) -> int:
        with self._connect() as db:
            version = db.execute("SELECT max(data_version) FROM meta").fetchone()[0]
        if version is None:
            raise DataUnavailable(self._no_data)
        return version

    def filings(self) -> list[Filing]:
        """All loaded filings, oldest first."""
        with self._connect() as db:
            rows = db.execute("""
                SELECT f.filing_id, f.bdc_ticker, f.form_type, f.period_end, f.source_url,
                       count(l.loan_id)
                FROM filings f LEFT JOIN loans l USING (filing_id)
                GROUP BY ALL ORDER BY f.period_end, f.bdc_ticker""").fetchall()
        if not rows:
            raise DataUnavailable(self._no_data)
        return [Filing(*row) for row in rows]

    def loans(self, filing_ids: list[str]) -> list[Loan]:
        with self._connect() as db:
            groups = approved_groups(db.execute("""
                SELECT alias, borrower_key FROM borrower_aliases
                WHERE match_type = 'fuzzy' AND status = 'approved'""").fetchall())
            rows = db.execute("""
                SELECT l.loan_id, l.filing_id, l.bdc_ticker, l.borrower_raw, l.borrower_key,
                       f.period_end, l.industry, l.instrument, l.rate_text, l.spread_bps,
                       l.maturity_date, l.principal::DOUBLE, l.cost::DOUBLE, l.fair_value::DOUBLE,
                       CASE WHEN l.cost > 0 THEN round(l.fair_value::DOUBLE / l.cost, 10) END,
                       l.is_pik, l.is_non_accrual
                FROM loans l JOIN filings f USING (filing_id)
                WHERE list_contains(?, l.filing_id)
                ORDER BY l.bdc_ticker, l.borrower_raw, l.loan_id""", [filing_ids]).fetchall()
        return [Loan(*row[:5], groups.get(row[4], row[4]), *row[5:]) for row in rows]

    def similar_names(self, keys: set[str]) -> list[str]:
        """Names from fuzzy matches that no analyst has reviewed yet."""
        with self._connect() as db:
            rows = db.execute("""
                SELECT alias, borrower_key FROM borrower_aliases
                WHERE status = 'pending'
                  AND (list_contains($1, alias) OR list_contains($1, borrower_key))""",
                [sorted(keys)]).fetchall()
        return sorted({name for row in rows for name in row} - keys)

    def rates(self, series_id: str, start: date) -> list[tuple[date, float]]:
        with self._connect() as db:
            return db.execute("""
                SELECT date, value::DOUBLE FROM market_rates
                WHERE series_id = ? AND date >= ? ORDER BY date""", [series_id, start]).fetchall()


def approved_groups(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """Map every approved alias to one shared key, following chains of approvals."""
    group: dict[str, set[str]] = {}
    for pair in pairs:
        merged = set().union(*(group.get(key, {key}) for key in pair))
        group.update({key: merged for key in merged})
    return {key: min(members) for key, members in group.items()}
