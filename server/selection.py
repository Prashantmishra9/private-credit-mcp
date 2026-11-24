"""Shared choices: which BDC, which filings, and how borrowers and sources are gathered."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date

from mcp.server.mcpserver.exceptions import ToolError

from config import Config
from server.db import Database, Filing, Loan
from server.models import Source
from server.scoring import dispersion_by_borrower


def check_bdc(bdc: str, config: Config) -> str:
    """Return the ticker in capitals, or explain the valid choices."""
    ticker = bdc.strip().upper()
    if ticker not in config.bdcs:
        raise ToolError(f"Unknown BDC '{bdc}'. Valid values: {', '.join(config.bdcs)}.")
    return ticker


def filings_of(filings: list[Filing], bdc: str) -> list[Filing]:
    """One BDC's filings, oldest first."""
    found = [filing for filing in filings if filing.bdc_ticker == bdc]
    if not found:
        loaded = ", ".join(sorted({filing.bdc_ticker for filing in filings}))
        raise ToolError(f"No data is loaded for {bdc}. Loaded BDCs: {loaded}. "
                        f"To load it, run `uv run python -m ingest.run --bdc {bdc}`.")
    return found


def latest_filing(filings: list[Filing], as_of: date | None) -> Filing:
    """The newest filing on or before `as_of`, from one BDC's filings."""
    found = [filing for filing in filings if as_of is None or filing.period_end <= as_of]
    if not found:
        periods = ", ".join(str(filing.period_end) for filing in filings)
        raise ToolError(f"{filings[0].bdc_ticker} has no period on or before {as_of}. "
                        f"Periods available: {periods}.")
    return found[-1]


def latest_filings(filings: list[Filing], as_of: date | None) -> list[Filing]:
    """The newest filing of every BDC that has one on or before `as_of`."""
    newest = {filing.bdc_ticker: filing for filing in filings
              if as_of is None or filing.period_end <= as_of}
    if not newest:
        raise ToolError(f"No BDC has a period on or before {as_of}. "
                        f"The earliest period is {filings[0].period_end}.")
    return list(newest.values())


@dataclass
class Snapshot:
    """Loans of the chosen filings, with lender facts drawn from every BDC in the same periods."""
    loans: list[Loan]
    dispersion: dict[tuple[str, date], float]
    lenders: dict[tuple[str, date], set[str]]
    peers: list[Loan]           # every BDC's loans in those periods

    def dispersion_of(self, loan: Loan) -> float | None:
        return self.dispersion.get((loan.group_key, loan.period_end))

    def lender_count(self, loan: Loan) -> int:
        return len(self.lenders[loan.group_key, loan.period_end])


def take_snapshot(database: Database, config: Config, all_filings: list[Filing],
                  chosen: list[Filing]) -> Snapshot:
    periods = {filing.period_end for filing in chosen}
    chosen_ids = {filing.filing_id for filing in chosen}
    peers = database.loans([f.filing_id for f in all_filings if f.period_end in periods])
    lenders: dict[tuple[str, date], set[str]] = {}
    for loan in peers:
        lenders.setdefault((loan.group_key, loan.period_end), set()).add(loan.bdc_ticker)
    return Snapshot([loan for loan in peers if loan.filing_id in chosen_ids],
                    dispersion_by_borrower(peers, config.scoring), lenders, peers)


def peer_sources(loans: list[Loan], snapshot: Snapshot, all_filings: list[Filing],
                 chosen: list[Filing]) -> list[Source]:
    """Chosen filings, plus other lenders' filings whose marks or presence shaped these loans."""
    used = {filing.filing_id for filing in chosen}
    pairs = {(bdc, loan.period_end) for loan in loans
             for bdc in snapshot.lenders[loan.group_key, loan.period_end]}
    used |= {f.filing_id for f in all_filings if (f.bdc_ticker, f.period_end) in pairs}
    return sources_for(filing for filing in all_filings if filing.filing_id in used)


def sources_for(filings: Iterable[Filing]) -> list[Source]:
    return [Source(bdc_ticker=f.bdc_ticker, form_type=f.form_type, period_end=f.period_end,
                   source_url=f.source_url) for f in filings]


def contains(text: str | None, wanted: str | None) -> bool:
    """Case-insensitive contains; hyphens count as spaces. No filter matches everything."""
    def plain(value: str) -> str:
        return " ".join(value.lower().replace("-", " ").split())
    return not wanted or plain(wanted) in plain(text or "")


def page_of(rows: list, page: int, page_size: int) -> list:
    return rows[(page - 1) * page_size: page * page_size]
