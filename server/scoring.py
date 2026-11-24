"""Stress score and the reasons behind it. All weights come from config.yaml."""

import math
from collections.abc import Iterable
from datetime import date

from config import ScoringConfig
from server.db import Loan


def is_scored(loan: Loan, rules: ScoringConfig) -> bool:
    """Equity stays in totals but is not scored."""
    return loan.instrument not in rules.exclude_instruments


def stress_score(loan: Loan, dispersion: float | None, rules: ScoringConfig) -> int:
    """0 to max_score for one loan. Unknown (null) flags earn no points."""
    if not is_scored(loan, rules):
        return 0
    points, score = rules.points, 0
    if loan.is_non_accrual:
        score += points.non_accrual
    if loan.is_pik:
        score += points.pik
    if loan.mark_pct is not None and loan.mark_pct < rules.deep_markdown_below:
        score += points.deep_markdown
    elif loan.mark_pct is not None and loan.mark_pct < rules.markdown_below:
        score += points.markdown
    if is_dispersed(dispersion, rules):
        score += points.dispersion
    return min(score, rules.max_score)


def is_dispersed(dispersion: float | None, rules: ScoringConfig) -> bool:
    return dispersion is not None and dispersion > rules.dispersion_over_pts


def reasons(loan: Loan, dispersion: float | None, rules: ScoringConfig,
            mark_threshold: float) -> list[str]:
    """Plain-language explanation of the signals on one loan."""
    found = []
    if loan.is_non_accrual:
        found.append("On non-accrual")
    if loan.is_pik:
        found.append("PIK interest")
    if loan.mark_pct is not None and loan.mark_pct < max(mark_threshold, rules.markdown_below):
        found.append(f"Marked at {_round_down(loan.mark_pct * 100)}% of cost")
    if is_dispersed(dispersion, rules):
        found.append(f"Lenders' marks differ by {_round_down(dispersion)} points")
    return found


def _round_down(value: float) -> str:
    """At most two decimals, rounded down, so 79.999 is never shown as 80."""
    return f"{math.floor(round(value * 100, 6)) / 100:.2f}".rstrip("0").removesuffix(".")


def lender_marks(loans: Iterable[Loan], rules: ScoringConfig) -> dict[str, float]:
    """Each lender's cost-weighted mark over its debt positions with a positive cost."""
    totals: dict[str, list[float]] = {}
    for loan in loans:
        if is_scored(loan, rules) and loan.mark_pct is not None and loan.cost:
            total = totals.setdefault(loan.bdc_ticker, [0.0, 0.0])
            total[0] += loan.fair_value
            total[1] += loan.cost
    return {bdc: value / cost for bdc, (value, cost) in totals.items()}


def dispersion_by_borrower(loans: Iterable[Loan],
                           rules: ScoringConfig) -> dict[tuple[str, date], float]:
    """Gap in points between the highest and lowest lender mark, per borrower and period."""
    groups: dict[tuple[str, date], list[Loan]] = {}
    for loan in loans:
        groups.setdefault((loan.group_key, loan.period_end), []).append(loan)
    result = {}
    for key, members in groups.items():
        marks = lender_marks(members, rules).values()
        if len(marks) > 1:
            result[key] = round((max(marks) - min(marks)) * 100, 6)
    return result
