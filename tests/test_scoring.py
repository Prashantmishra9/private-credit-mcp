"""Stress score rules, on synthetic loans."""

from datetime import date

import pytest

from config import load_config
from server.db import Loan
from server.scoring import dispersion_by_borrower, reasons, stress_score

RULES = load_config().scoring


def loan(mark=1.0, pik=False, non_accrual=False, instrument="First lien", bdc="ARCC", cost=100.0):
    return Loan("id", "f", bdc, "Fixture", "fixture", "fixture", date(2026, 9, 30), None,
                instrument, None, None, None, None, cost, (mark or 0) * cost, mark, pik, non_accrual)


@pytest.mark.parametrize("mark, points", [(0.7999, 2), (0.80, 1), (0.8999, 1), (0.90, 0), (None, 0)])
def test_markdown_boundaries(mark, points):
    assert stress_score(loan(mark), None, RULES) == points


def test_all_signals_add_up():
    assert stress_score(loan(0.5, True, True), 10.1, RULES) == 10


def test_score_is_capped():
    rules = RULES.model_copy(update={"max_score": 5})
    assert stress_score(loan(0.5, True, True), 20, rules) == 5


def test_exactly_ten_points_is_not_dispersed():
    assert stress_score(loan(), 10.0, RULES) == 0


def test_unknown_flags_earn_nothing():
    assert stress_score(loan(pik=None, non_accrual=None), None, RULES) == 0


def test_equity_is_not_scored():
    assert stress_score(loan(0.1, True, instrument="Equity"), 50, RULES) == 0


def test_dispersion_is_cost_weighted_per_lender_and_skips_equity():
    loans = [loan(1.0, cost=300), loan(0.8, cost=100), loan(0.75, bdc="OBDC"),
             loan(0.0, instrument="Equity", bdc="FSK")]
    assert list(dispersion_by_borrower(loans, RULES).values()) == [20.0]


def test_one_lender_has_no_dispersion():
    assert dispersion_by_borrower([loan(1.0), loan(0.5)], RULES) == {}


def test_reasons():
    assert reasons(loan(0.78, True, True), 14.2, RULES, 0.9) == [
        "On non-accrual", "PIK interest", "Marked at 78% of cost",
        "Lenders' marks differ by 14.2 points"]


def test_reasons_never_round_across_a_boundary():
    assert reasons(loan(0.7999), 10.01, RULES, 0.9) == [
        "Marked at 79.99% of cost", "Lenders' marks differ by 10.01 points"]
