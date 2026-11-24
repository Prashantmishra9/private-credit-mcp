"""Fallback parser: reads the Schedule of Investments from its HTML tables alone."""

import re
from datetime import date

from lxml import etree

from ingest.document import elements, local_name, text_blocks, text_of
from ingest.footnotes import ENTRY, NOTES_HEADING, read_legend
from ingest.models import ParsedFiling
from ingest.rates import parse_date
from ingest.row_reader import loan_from_row, rates_show_pik, scaled
from ingest.table_grid import ScheduleRow, find_roles, read_schedule, table_cells

MAX_HEADING_LENGTH = 250
HEADING = re.compile(r"schedules? of investments", re.IGNORECASE)
PROSE = re.compile(r"\b(?:see|refer|accompanying|integral|included|these|above)\b|\.$", re.IGNORECASE)
CONTINUED = re.compile(r"\(continued\)|\bcontinued\b", re.IGNORECASE)
HEADING_DATE = re.compile(r"\b([A-Z][A-Za-z]+ \d{1,2}, \d{4})\b")
UNITS = re.compile(r"\bin (thousands|millions)\b", re.IGNORECASE)
SCALES = {"thousands": 1e3, "millions": 1e6}
TOTAL_LABEL = re.compile(r"total (?:portfolio )?investments\b", re.IGNORECASE)
GRAND_TOTAL = re.compile(TOTAL_LABEL.pattern + r"[\s\W\d]*", re.IGNORECASE)


def parse_html(root: etree._Element, period_end: date,
               filed_total: float | None = None) -> ParsedFiling:
    """Parse the schedule dated `period_end`. A tagged total, if known, beats the table's own."""
    by_date, scale = schedule_tables(root)
    current = by_date.pop(period_end, [])
    prior = [table for tables in by_date.values() for table in tables]
    if not current:
        return ParsedFiling("html", filed_total=filed_total)
    legend = read_legend(root, current, sorted_in_document(root, prior))
    rows = read_schedule(current)
    legend.rates_show_pik = rates_show_pik(rows)
    loans = [loan_from_row(row, legend, scale) for row in rows if row.kind == "loan"]
    return ParsedFiling("html", loans, filed_total or stated_total(rows, scale))


def schedule_tables(root: etree._Element) -> tuple[dict[date | None, list], float]:
    """Schedule tables grouped by the date in the heading above them, and the dollar scale."""
    result: dict[date | None, list] = {}
    in_schedule, date_open, heading_date = False, False, None
    scale, seen = 1.0, set()
    tables = {row: table for table in elements(root, "table") for row in table.iter()
              if local_name(row) == "tr"}
    for node, text in text_blocks(root):
        if len(text) <= MAX_HEADING_LENGTH:
            if NOTES_HEADING.match(text):
                in_schedule = False
            if HEADING.search(text) and not PROSE.search(text):
                in_schedule = date_open = True
                heading_date = heading_date if CONTINUED.search(text) else None
            if date_open and not ENTRY.match(text) and (found := HEADING_DATE.search(text)):
                heading_date, date_open = parse_date(found.group(1)), False
            if in_schedule and (units := UNITS.search(text)):
                scale = SCALES[units.group(1).lower()]
        table = tables.get(node)
        if table is not None and in_schedule and table not in seen:
            if _is_schedule(table, bool(result)):
                if date_open:
                    heading_date = _date_inside(table) or heading_date
                date_open = False
                result.setdefault(heading_date, []).append(table)
            seen.add(table)
    return result, scale


def _is_schedule(table: etree._Element, started: bool) -> bool:
    """A table with schedule columns, or a wide continuation once a schedule has started."""
    rows = table_cells(table)
    return find_roles(rows) is not None or (started and max((len(c) for _, c in rows), default=0) >= 6)


def _date_inside(table: etree._Element) -> date | None:
    """A date in the first rows, for tables that hold their own title."""
    rows = [row for row in table.iter() if local_name(row) == "tr"][:4]
    found = next((m for row in rows if (m := HEADING_DATE.search(text_of(row)))), None)
    return parse_date(found.group(1)) if found else None


def sorted_in_document(root: etree._Element, tables: list) -> list:
    wanted = set(tables)
    return [table for table in elements(root, "table") if table in wanted]


def stated_total(rows: list[ScheduleRow], scale: float) -> float | None:
    """The last plain "Total investments" line, else the last that starts that way."""
    totals = [row for row in rows if row.kind == "total" and "fair_value" in row.values
              and TOTAL_LABEL.match(row.values["label"])]
    plain = [row for row in totals if GRAND_TOTAL.fullmatch(row.values["label"])]
    chosen = plain or totals
    return scaled(chosen[-1].values["fair_value"], scale) if chosen else None
