"""Answer for find_borrower_exposure: one borrower across every lender."""

from datetime import date

from mcp.server.mcpserver.exceptions import ToolError
from rapidfuzz import fuzz

from config import Config
from ingest.normalize import normalize_name, plain_words
from server.db import Database, Filing, Loan
from server.models import Candidate, ExposureResponse, LenderOut
from server.scoring import is_scored, stress_score
from server.selection import Snapshot, latest_filings, sources_for, take_snapshot

Groups = dict[str, list[Loan]]      # group_key -> that borrower's loans


def find_exposure(database: Database, config: Config, borrower: str,
                  as_of: date | None) -> ExposureResponse:
    filings = database.filings()
    chosen = latest_filings(filings, as_of)
    snapshot = take_snapshot(database, config, filings, chosen)
    groups: Groups = {}
    for loan in snapshot.loans:
        groups.setdefault(loan.group_key, []).append(loan)
    key = exact_match(borrower, groups)
    if key is None:
        return offer_candidates(borrower, groups, chosen, config)
    shown = groups[key]
    ids = {loan.loan_id for loan in shown}
    compared = [loan for loan in snapshot.peers if loan.group_key == key and loan.loan_id not in ids
                and is_scored(loan, config.scoring) and loan.mark_pct is not None
                and any(loan.period_end == own.period_end for own in shown)]
    names = {loan.borrower_key for loan in shown}
    used = {loan.filing_id for loan in shown + compared}
    response = exposure(key, shown, compared, snapshot, config)
    response.unreviewed_similar_names = database.similar_names(names)
    response.sources = sources_for(filing for filing in filings if filing.filing_id in used)
    return response


def exact_match(query: str, groups: Groups) -> str | None:
    """A borrower_key as given wins; otherwise the query is normalized like a filing name."""
    for wanted in (query.strip().lower(), normalize_name(query)):
        for key, loans in groups.items():
            if wanted == key or any(wanted == loan.borrower_key for loan in loans):
                return key
    return None


def offer_candidates(query: str, groups: Groups, chosen: list[Filing],
                     config: Config) -> ExposureResponse:
    """Similar names for the caller to choose from, even when there is only one."""
    matches = similar_keys(query, groups, config)
    if not matches:
        raise ToolError(f"No borrower matches '{query[:100]}' in the periods searched. Check the "
                        "spelling, try a shorter name, or list names with get_bdc_portfolio.")
    shown = matches[:config.server.max_candidates]
    used = {loan.filing_id for key in shown for loan in groups[key]}
    return ExposureResponse(
        status="ambiguous", borrower_key=None, matched_names=[], lenders=[], comparison_loans=[],
        mark_dispersion_pts=None, stress_score=None, unreviewed_similar_names=[],
        candidates=[candidate(key, groups[key]) for key in shown], total_candidates=len(matches),
        sources=sources_for(filing for filing in chosen if filing.filing_id in used))


def similar_keys(query: str, groups: Groups, config: Config) -> list[str]:
    """Group keys with a similar name, best first."""
    wanted = normalize_name(query)
    scores = {key: max(name_score(wanted, loan) for loan in loans) for key, loans in groups.items()}
    found = [key for key, score in scores.items() if score >= config.server.borrower_match_threshold]
    return sorted(found, key=lambda key: (-scores[key], key))


def name_score(wanted: str, loan: Loan) -> float:
    """Similarity to the key, or a full match on words of the raw name, such as a trade name."""
    if wanted and f" {wanted} " in f" {plain_words(loan.borrower)} ":
        return 100.0
    return similarity(wanted, loan.borrower_key)


def similarity(wanted: str, name: str) -> float:
    """0 to 100. Whole words contained in the name count as a full match."""
    if f" {wanted} " in f" {name} ":
        return 100.0
    return fuzz.token_sort_ratio(wanted, name)


def candidate(key: str, loans: list[Loan]) -> Candidate:
    return Candidate(borrower_key=key, names=sorted({loan.borrower for loan in loans}),
                     bdc_tickers=sorted({loan.bdc_ticker for loan in loans}))


def exposure(key: str, loans: list[Loan], compared: list[Loan], snapshot: Snapshot,
             config: Config) -> ExposureResponse:
    lenders = [lender_out(loan, snapshot, config) for loan in loans]
    gaps = [gap for loan in loans if (gap := snapshot.dispersion_of(loan)) is not None]
    return ExposureResponse(
        status="found", borrower_key=key, matched_names=sorted({loan.borrower for loan in loans}),
        lenders=lenders, mark_dispersion_pts=max(gaps, default=None),
        stress_score=max(lender.stress_score for lender in lenders),
        comparison_loans=[lender_out(loan, snapshot, config) for loan in compared],
        candidates=[], total_candidates=0, unreviewed_similar_names=[])


def lender_out(loan: Loan, snapshot: Snapshot, config: Config) -> LenderOut:
    score = stress_score(loan, snapshot.dispersion_of(loan), config.scoring)
    return LenderOut(
        **{name: getattr(loan, name) for name in LenderOut.model_fields if name != "stress_score"},
        stress_score=score)
