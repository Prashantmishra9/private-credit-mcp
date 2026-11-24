"""Answers for list_bdcs, get_bdc_portfolio, flag_stress_signals and get_portfolio_trend."""

from datetime import date

from config import Config
from server.db import Database, Filing, Loan
from server.models import (
    BdcInfo, FlagOut, FlagsResponse, ListBdcsResponse, LoanOut, PortfolioResponse,
    PortfolioSummary, Signal, TrendResponse, TrendRow,
)
from server.scoring import is_scored, reasons, stress_score
from server.selection import (
    Snapshot, contains, filings_of, latest_filing, latest_filings, page_of, peer_sources,
    sources_for, take_snapshot,
)


def list_bdcs(database: Database, config: Config) -> ListBdcsResponse:
    filings = database.filings()
    infos, missing = [], []
    for ticker, name in config.bdcs.items():
        own = [filing for filing in filings if filing.bdc_ticker == ticker]
        if not own:
            missing.append(ticker)
            continue
        infos.append(BdcInfo(
            ticker=ticker, name=name, latest_period_end=own[-1].period_end,
            periods_available=sorted({filing.period_end for filing in own}),
            loan_count=own[-1].loan_count))
    return ListBdcsResponse(bdcs=infos, not_loaded=missing, sources=sources_for(filings))


def get_portfolio(database: Database, config: Config, bdc: str, as_of: date | None,
                  industry: str | None, instrument: str | None, page: int,
                  page_size: int) -> PortfolioResponse:
    all_filings = database.filings()
    filing = latest_filing(filings_of(all_filings, bdc), as_of)
    snapshot = take_snapshot(database, config, all_filings, [filing])
    loans = [loan for loan in snapshot.loans
             if contains(loan.industry, industry) and contains(loan.instrument, instrument)]
    shown = page_of(loans, page, page_size)
    return PortfolioResponse(
        bdc=bdc, as_of=filing.period_end, summary=summarize(loans),
        loans=[loan_out(loan, snapshot, config) for loan in shown],
        page=page, page_size=page_size, total=len(loans),
        sources=peer_sources(shown, snapshot, all_filings, [filing]))


def summarize(loans: list[Loan]) -> PortfolioSummary:
    return PortfolioSummary(
        total_cost=round(sum(loan.cost or 0 for loan in loans), 2),
        total_fair_value=round(sum(loan.fair_value for loan in loans), 2),
        pik_loan_count=sum(loan.is_pik is True for loan in loans),
        non_accrual_loan_count=sum(loan.is_non_accrual is True for loan in loans),
        non_accrual_pct_of_cost=non_accrual_share(loans))


def non_accrual_share(loans: list[Loan]) -> float | None:
    """Non-accrual cost over total cost. Null when no loan's flag is known."""
    total = sum(loan.cost or 0 for loan in loans)
    if not total or all(loan.is_non_accrual is None for loan in loans):
        return None
    return round(sum(loan.cost or 0 for loan in loans if loan.is_non_accrual) / total, 6)


def pik_share(loans: list[Loan]) -> float | None:
    """PIK fair value over total fair value. Null when no loan's flag is known."""
    total = sum(loan.fair_value for loan in loans)
    if not total or all(loan.is_pik is None for loan in loans):
        return None
    return round(sum(loan.fair_value for loan in loans if loan.is_pik) / total, 6)


def loan_out(loan: Loan, snapshot: Snapshot, config: Config) -> LoanOut:
    score = stress_score(loan, snapshot.dispersion_of(loan), config.scoring)
    return LoanOut(
        **{name: getattr(loan, name) for name in LoanOut.model_fields if name != "stress_score"},
        stress_score=score)


def flag_stress(database: Database, config: Config, bdc: str | None, industry: str | None,
                as_of: date | None, mark_threshold: float, signals: list[Signal],
                min_score: int, page: int, page_size: int) -> FlagsResponse:
    all_filings = database.filings()
    chosen = ([latest_filing(filings_of(all_filings, bdc), as_of)] if bdc
              else latest_filings(all_filings, as_of))
    snapshot = take_snapshot(database, config, all_filings, chosen)
    flags = [(flag_out(loan, snapshot, config, mark_threshold), loan) for loan in snapshot.loans
             if is_scored(loan, config.scoring) and contains(loan.industry, industry)
             and has_signal(loan, signals, mark_threshold)]
    flags = [pair for pair in flags if pair[0].stress_score >= min_score]
    flags.sort(key=lambda pair: (-pair[0].stress_score, _mark(pair[0]), pair[0].borrower.lower(),
                                 pair[1].loan_id))
    shown = page_of(flags, page, page_size)
    return FlagsResponse(
        flags=[flag for flag, _ in shown], page=page, page_size=page_size, total=len(flags),
        sources=peer_sources([loan for _, loan in shown], snapshot, all_filings, chosen))


def _mark(flag: FlagOut) -> float:
    return flag.mark_pct if flag.mark_pct is not None else float("inf")


def has_signal(loan: Loan, signals: list[Signal], mark_threshold: float) -> bool:
    """True if the loan shows at least one of the wanted signals."""
    shown = {"pik": loan.is_pik is True, "non_accrual": loan.is_non_accrual is True,
             "markdown": loan.mark_pct is not None and loan.mark_pct < mark_threshold}
    return any(shown[signal] for signal in signals)


def flag_out(loan: Loan, snapshot: Snapshot, config: Config, mark_threshold: float) -> FlagOut:
    spread = snapshot.dispersion_of(loan)
    derived = {"mark_dispersion_pts", "lender_count", "stress_score", "reasons"}
    return FlagOut(
        **{name: getattr(loan, name) for name in FlagOut.model_fields if name not in derived},
        mark_dispersion_pts=spread, lender_count=snapshot.lender_count(loan),
        stress_score=stress_score(loan, spread, config.scoring),
        reasons=reasons(loan, spread, config.scoring, mark_threshold))


def get_trend(database: Database, config: Config, bdc: str, periods: int) -> TrendResponse:
    recent = filings_of(database.filings(), bdc)[-periods:]
    loans = database.loans([filing.filing_id for filing in recent])
    rows = [trend_row(filing, [loan for loan in loans if loan.filing_id == filing.filing_id], config)
            for filing in recent]
    return TrendResponse(bdc=bdc, trend=rows, sources=sources_for(recent))


def trend_row(filing: Filing, loans: list[Loan], config: Config) -> TrendRow:
    marks = [loan.mark_pct for loan in loans
             if loan.mark_pct is not None and is_scored(loan, config.scoring)]
    return TrendRow(
        period_end=filing.period_end, loan_count=len(loans),
        total_fair_value=round(sum(loan.fair_value for loan in loans), 2),
        non_accrual_pct_of_cost=non_accrual_share(loans), pik_pct_of_fair_value=pik_share(loans),
        avg_mark_pct=round(sum(marks) / len(marks), 6) if marks else None,
        loans_marked_below_90_pct=sum(mark < config.scoring.markdown_below for mark in marks))
