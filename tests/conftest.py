"""Shared fixtures. Every row here is SYNTHETIC, lives in a temp directory, and is not from a filing."""

from datetime import date

import pytest

from config import load_config
from ingest.load import load_filing
from ingest.models import FilingRef, ParsedLoan

SEPT = date(2026, 9, 30)
JUNE = date(2026, 6, 30)


def make_loan(borrower: str = "Fixture Co, LLC", cost: float | None = 100.0, value: float = 100.0,
              instrument: str = "First lien", industry: str = "Software", pik: bool | None = False,
              non_accrual: bool | None = False) -> ParsedLoan:
    return ParsedLoan(borrower, industry, instrument, "SOFR + 5.00%", 500, None, cost, cost, value,
                      pik, non_accrual)


def make_filing(bdc: str, period: date = SEPT) -> FilingRef:
    return FilingRef(f"fixture-{bdc}-{period}", bdc, "10-Q", period, period,
                     f"https://example.invalid/{bdc}/{period}")


@pytest.fixture
def config(tmp_path, monkeypatch):
    """Real settings, temp database, and no keys, so nothing can reach the network."""
    base = load_config()
    for name in (base.env.sec_user_agent, base.env.fred_api_key):
        monkeypatch.delenv(name, raising=False)
    paths = base.paths.model_copy(update={
        "database": tmp_path / "fixture.duckdb", "mock_database": tmp_path / "mock.duckdb",
        "edgar_cache": tmp_path / "cache",
        "tool_docs": tmp_path / "TOOLS.md"})
    return base.model_copy(update={"paths": paths, "data_mode": "live"})


@pytest.fixture
def loaded(config):
    """Two synthetic BDC portfolios that share one borrower, marked 20 points apart."""
    path = config.paths.database
    load_filing(path, make_filing("ARCC", JUNE), "xbrl", [make_loan("Shared Fixture, Inc.")])
    load_filing(path, make_filing("ARCC"), "xbrl", [
        make_loan("Shared Fixture, Inc.", value=95),
        make_loan("Shared Fixture, Inc.", value=0, instrument="Equity"),
        make_loan("Troubled Fixture LLC", value=78, pik=True, non_accrual=True),
        make_loan("Healthy Fixture LP", industry="Healthcare"),
        make_loan("Unfunded Fixture LP", cost=0, value=-1, industry="Healthcare")])
    load_filing(path, make_filing("OBDC"), "html", [
        make_loan("Shared Fixture Holdings, LLC", value=75, pik=True, non_accrual=None)])
    return config
