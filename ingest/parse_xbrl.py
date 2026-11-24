"""Main parser: amounts come from inline XBRL tags, names and markers from the same table row."""

import re
from datetime import date

from lxml import etree

from ingest.document import closest, elements
from ingest.footnotes import Legend, non_accrual_flag, pik_flag, read_legend
from ingest.models import ParsedFiling, ParsedLoan
from ingest.normalize import classify_instrument
from ingest.rates import parse_date, parse_spread_bps, pik_in_text
from ingest.row_reader import rate_text, rates_show_pik, row_instrument, row_pik
from ingest.table_grid import ScheduleRow, read_schedule
from ingest.xbrl_facts import Fact, linked_footnotes, read_contexts, read_facts

FAIR_VALUE = "InvestmentOwnedAtFairValue"
COST = "InvestmentOwnedAtCost"
PRINCIPAL = "InvestmentOwnedBalancePrincipalAmount"
SHARES = "InvestmentOwnedBalanceShares"
SPREAD = "InvestmentBasisSpreadVariableRate"
PIK_RATE = "InvestmentInterestRatePaidInKind"
MATURITY = "InvestmentMaturityDate"

Position = dict[str, Fact]      # concept name -> first fact for one investment


def parse_xbrl(root: etree._Element, period_end: date) -> ParsedFiling:
    """Parse positions dated `period_end`; filings also tag the prior year-end schedule."""
    facts = read_facts(root, read_contexts(root))
    now = [fact for fact in facts if fact.context.instant == period_end]
    positions = group_positions(now)
    result = ParsedFiling("xbrl", filed_total=filed_total(now))
    if not positions:
        return result
    current, prior = schedule_tables(root, facts, period_end)
    rows = {row.element: row for row in read_schedule(current) if row.kind == "loan"}
    legend = read_legend(root, current, prior)
    legend.rates_show_pik = rates_show_pik(rows.values()) or any(
        PIK_RATE in position for position in positions.values())
    links = linked_footnotes(root)
    result.loans = [build_loan(identifier, position, rows, legend, links)
                    for identifier, position in positions.items()]
    return result


def group_positions(facts: list[Fact]) -> dict[str, Position]:
    """Facts per investment identifier; needs a fair value plus cost, principal or shares."""
    grouped: dict[str, Position] = {}
    for fact in facts:
        if fact.context.is_position:
            grouped.setdefault(fact.context.identifier, {}).setdefault(fact.name, fact)
    return {identifier: position for identifier, position in grouped.items()
            if FAIR_VALUE in position and position[FAIR_VALUE].number is not None
            and position.keys() & {COST, PRINCIPAL, SHARES}}


def filed_total(facts: list[Fact]) -> float | None:
    """The filing's own total: fair value tagged for the whole company."""
    values = [fact.number for fact in facts
              if fact.name == FAIR_VALUE and fact.context.is_whole_company]
    return next((value for value in values if value), None)


def schedule_tables(root: etree._Element, facts: list[Fact], period_end: date) -> tuple[list, list]:
    """Tables holding this period's positions, and tables holding only other periods'."""
    current, other = set(), set()
    for fact in facts:
        if fact.name == FAIR_VALUE and fact.context.is_position:
            table = closest(fact.element, "table")
            if table is not None:
                (current if fact.context.instant == period_end else other).add(table)
    ordered = list(elements(root, "table"))
    return ([t for t in ordered if t in current], [t for t in ordered if t in other - current])


def build_loan(identifier: str, position: Position, rows: dict[etree._Element, ScheduleRow],
               legend: Legend, links: dict[str, str]) -> ParsedLoan:
    row = rows.get(closest(position[FAIR_VALUE].element, "tr"))
    name, wording = split_identifier(identifier)
    values = row.values if row else {}
    rate = rate_text(values) if row else None
    note = " ".join(links.get(fact.element.get("id", ""), "") for fact in position.values())
    markers = row.markers if row else None
    return ParsedLoan(
        borrower_raw=(row.company if row else None) or name,
        industry=row.industry if row else None,
        instrument=row_instrument(row, wording) if row else classify_instrument(wording),
        rate_text=rate,
        spread_bps=_spread_bps(position, rate),
        maturity_date=_maturity(position, values),
        principal=_number(position, PRINCIPAL),
        cost=_number(position, COST),
        fair_value=position[FAIR_VALUE].number or 0.0,
        is_pik=pik_flag(markers, legend, _stated_pik(position, values, wording, legend), note),
        is_non_accrual=non_accrual_flag(markers, legend, note),
    )


def split_identifier(identifier: str) -> tuple[str, str]:
    """Split "Example Co, Inc., First lien loan 2" into the name and the rest."""
    parts = [part.strip() for part in identifier.split(",")]
    for index in range(1, len(parts)):
        if classify_instrument(parts[index]) != "Other":
            return ", ".join(parts[:index]), ", ".join(parts[index:])
    return re.sub(r"\s+\d{1,2}$", "", identifier.strip()), identifier


def _number(position: Position, concept: str) -> float | None:
    return position[concept].number if concept in position else None


def _spread_bps(position: Position, rate: str | None) -> int | None:
    spread = _number(position, SPREAD)
    return round(spread * 10_000) if spread is not None else parse_spread_bps(rate)


def _maturity(position: Position, values: dict[str, str]) -> date | None:
    tagged = parse_date(position[MATURITY].text) if MATURITY in position else None
    return tagged or parse_date(values.get("maturity"))


def _stated_pik(position: Position, values: dict[str, str], wording: str,
                legend: Legend) -> bool | None:
    """The PIK tag wins; otherwise the row's rate columns, then the identifier's wording."""
    tagged = _number(position, PIK_RATE)
    if tagged is not None:
        return tagged > 0
    shown = row_pik(values, legend.rates_show_pik)
    return True if pik_in_text(wording) else shown
