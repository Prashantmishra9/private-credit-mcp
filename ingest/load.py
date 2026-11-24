"""Creates the DuckDB schema and writes filings, loans and market rates to it.

Each write opens the database, runs one transaction and closes it, so a running server is only
blocked for a moment.
"""

import hashlib
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import duckdb

from config import load_config
from ingest.models import FilingRef, ParsedLoan
from ingest.normalize import normalize_name

SCHEMA = """
CREATE TABLE IF NOT EXISTS filings (
    filing_id TEXT, bdc_ticker TEXT, form_type TEXT, period_end DATE, filed_date DATE,
    source_url TEXT, extraction_method TEXT);
CREATE TABLE IF NOT EXISTS loans (
    loan_id TEXT, filing_id TEXT, bdc_ticker TEXT, borrower_raw TEXT, borrower_key TEXT,
    industry TEXT, instrument TEXT, rate_text TEXT, spread_bps INT, maturity_date DATE,
    principal DECIMAL(20, 2), cost DECIMAL(20, 2), fair_value DECIMAL(20, 2),
    mark_pct DECIMAL(18, 6), is_pik BOOLEAN, is_non_accrual BOOLEAN);
CREATE TABLE IF NOT EXISTS market_rates (series_id TEXT, date DATE, value DECIMAL(18, 6));
CREATE TABLE IF NOT EXISTS borrower_aliases (
    alias TEXT, borrower_key TEXT, match_type TEXT, score DOUBLE, status TEXT);
CREATE TABLE IF NOT EXISTS meta (data_version BIGINT);
"""
LOAN_COLUMNS = 16


@contextmanager
def transaction(path: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """Open for writing, waiting briefly for readers, then commit, or roll back on an error."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = _open(path)
    try:
        connection.execute("BEGIN")
        connection.execute(SCHEMA)
        yield connection
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    finally:
        connection.close()


def _open(path: Path) -> duckdb.DuckDBPyConnection:
    deadline = time.monotonic() + load_config().ingest.db_write_timeout_seconds
    while True:
        try:
            return duckdb.connect(str(path))
        except (duckdb.IOException, duckdb.ConnectionException):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def load_filing(path: Path, filing: FilingRef, method: str, loans: list[ParsedLoan]) -> None:
    """Replace one BDC and period: the filing and all its loans, and bump data_version."""
    rows = [loan_row(filing, loan, number) for number, loan in enumerate(loans)]
    with transaction(path) as db:
        same = "filing_id = ? OR (bdc_ticker = ? AND period_end = ?)"
        key = [filing.filing_id, filing.bdc_ticker, filing.period_end]
        db.execute(f"""DELETE FROM loans WHERE filing_id IN
            (SELECT filing_id FROM filings WHERE {same})""", key)
        db.execute(f"DELETE FROM filings WHERE {same}", key)
        db.execute("INSERT INTO filings VALUES (?, ?, ?, ?, ?, ?, ?)", [
            filing.filing_id, filing.bdc_ticker, filing.form_type, filing.period_end,
            filing.filed_date, filing.source_url, method])
        db.executemany(f"INSERT INTO loans VALUES ({', '.join('?' * LOAN_COLUMNS)})", rows)
        bump_data_version(db)


def loan_row(filing: FilingRef, loan: ParsedLoan, number: int) -> list:
    """`number` keeps the id unique when a borrower has several tranches of one instrument."""
    identity = f"{filing.filing_id}|{loan.borrower_raw}|{loan.instrument}|{number}"
    loan_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    return [loan_id, filing.filing_id, filing.bdc_ticker, loan.borrower_raw,
            normalize_name(loan.borrower_raw), loan.industry, loan.instrument, loan.rate_text,
            loan.spread_bps, loan.maturity_date, loan.principal, loan.cost, loan.fair_value,
            mark_pct(loan.cost, loan.fair_value), loan.is_pik, loan.is_non_accrual]


def mark_pct(cost: float | None, fair_value: float) -> float | None:
    """Fair value over cost. Null for zero or negative cost (unfunded commitments)."""
    return fair_value / cost if cost and cost > 0 else None


def load_rates(path: Path, series_id: str, values: list[tuple[date, float]]) -> None:
    """Replace one FRED series."""
    with transaction(path) as db:
        db.execute("DELETE FROM market_rates WHERE series_id = ?", [series_id])
        if values:
            db.executemany("INSERT INTO market_rates VALUES (?, ?, ?)",
                           [[series_id, day, value] for day, value in values])
        bump_data_version(db)


def bump_data_version(db: duckdb.DuckDBPyConnection) -> None:
    """At least the current time, so a rebuilt database never reuses an old version."""
    old = db.execute("SELECT max(data_version) FROM meta").fetchone()[0] or 0
    db.execute("DELETE FROM meta")
    db.execute("INSERT INTO meta VALUES (?)", [max(old + 1, int(time.time()))])
