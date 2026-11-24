"""Turns Schedule of Investments tables into rows with named values.

Filings split one column over several cells ("$", "1,234", ")"), so each cell is placed on a
column grid and gets the role ("cost", "fair_value", ...) of the header above it.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from lxml import etree

from ingest.document import local_name, text_of
from ingest.markers import BRACKETED, ONLY_MARKERS, bare_markers, bracketed_markers, without_trailing
from ingest.normalize import classify_instrument
from ingest.rates import parse_number

# Order matters: the first pattern found wins. "ignore" columns are dropped.
ROLE_PATTERNS = [
    ("ignore", r"%\s*of|percent|acquisition|floor"),
    ("fair_value", r"fair\s*value"),
    ("cost", r"\bcost\b"),
    ("pik", r"\bPIK\b"),
    ("maturity", r"maturity|expiration"),
    ("footnote", r"footnote|^notes?$"),
    ("principal", r"principal|\bpar\b"),
    ("shares", r"shares|units"),
    ("industry", r"industry"),
    ("description", r"business description"),
    ("reference", r"\bref\b|reference"),
    ("spread", r"spread|\bcash\b"),
    ("coupon", r"coupon|interest|\brate\b"),
    ("company", r"company|issuer|borrower"),
    ("instrument", r"investment|instrument|security|\btype\b"),
]
DETAIL_ROLES = {"instrument", "industry", "description", "reference", "spread", "coupon"}
RATE_ROLES = {"reference", "spread", "coupon", "pik"}
NUMERIC_ROLES = {"fair_value", "cost", "principal", "shares"}
MAX_HEADER_ROWS = 6
NUMBER_CELL = re.compile(r"[A-Z]{0,3}[\s$€£(\-–—−]*[\d,.]*[\s)\-–—−]*")
CONTINUED = re.compile(r"\s*\((?:continued|cont\.?)\)", re.IGNORECASE)
HEADING_PERCENT = re.compile(r"\s*[-–—]*\s*[\d.,]+\s*%.*$")
WIDE_HEADING = re.compile(
    r"affiliat|controlled|^(?:debt )?investments\b|^portfolio compan|asset[\s-]based finance|"
    r"^other debt\b|strategic credit", re.IGNORECASE)
INDUSTRY_WORDING = re.compile(r"real estate|REIT", re.IGNORECASE)   # "Equity Real Estate ..."
SUMMARY = re.compile(r"(?:sub-?\s*)?total\b|net\b|unfunded\b.*commitments?\b|"
                     r"(?:other )?(?:assets|liabilities)\b|cash\b", re.IGNORECASE)


@dataclass
class ScheduleRow:
    """One table row. `kind` is "loan", "total" or "other"."""
    element: etree._Element
    kind: str
    values: dict[str, str]
    markers: set[str] = field(default_factory=set)
    company: str | None = None
    industry: str | None = None
    section_instrument: str | None = None


@dataclass
class Section:
    """What the rows above have established."""
    company: str | None = None
    industry: str | None = None
    instrument: str | None = None


def table_cells(table: etree._Element) -> list[tuple[etree._Element, list[tuple[int, int, str]]]]:
    """Each row as (element, [(first column, width, text)]), with colspan and rowspan applied."""
    rows, held = [], {}                     # held: column -> rows still covered from above
    for row in (node for node in table.iter() if local_name(node) == "tr"):
        cells, column, covered = [], 0, held
        held = {spot: left - 1 for spot, left in covered.items() if left > 1}
        for cell in (node for node in row if local_name(node) in {"td", "th"}):
            while column in covered:
                column += 1
            width = _span(cell, "colspan")
            if _span(cell, "rowspan") > 1:
                held.update({column + i: _span(cell, "rowspan") - 1 for i in range(width)})
            cells.append((column, width, text_of(cell)))
            column += width
        rows.append((row, cells))
    return rows


def _span(cell: etree._Element, name: str) -> int:
    value = (cell.get(name) or "1").strip()
    return max(1, min(int(value), 200)) if value.isdigit() else 1


def find_roles(rows: list) -> tuple[dict[int, str], int] | None:
    """Column roles from stacked header rows, and the index of the first body row."""
    headers: dict[int, str] = {}
    found = None
    for index, (_, cells) in enumerate(rows[:MAX_HEADER_ROWS]):
        filled = [cell for cell in cells if cell[2]]
        if len(filled) < 2:
            continue                        # a title, not column names
        if found and not _looks_like_header(filled):
            break
        for column, width, text in filled:
            for spot in range(column, column + width):
                headers[spot] = f"{headers.get(spot, '')} {text}".strip()
        roles = {spot: role for spot, text in headers.items() if (role := _role_of(text))}
        if {"fair_value", "cost"} <= set(roles.values()):
            found = (_name_company_column(roles), index + 1)
    return found


def _looks_like_header(filled: list[tuple[int, int, str]]) -> bool:
    known = [text for _, _, text in filled if _role_of(text)]
    return len(known) >= 2 and not any(re.search(r"\d{2,}", text) for text in known)


def _role_of(text: str) -> str | None:
    plain = BRACKETED.sub("", text).strip()
    return next((role for role, pattern in ROLE_PATTERNS
                 if re.search(pattern, plain, re.IGNORECASE)), None)


def _name_company_column(roles: dict[int, str]) -> dict[int, str]:
    """Some schedules head the company column "Investments"."""
    if "company" in roles.values():
        return roles
    return {spot: "company" if role == "instrument" else role for spot, role in roles.items()}


def read_schedule(tables: Iterable[etree._Element]) -> list[ScheduleRow]:
    """Rows of consecutive schedule tables. Headings carry over page breaks."""
    section, result, roles, width = Section(), [], None, 0
    for table in tables:
        rows = table_cells(table)
        table_width = max((c + w for _, cells in rows for c, w, _ in cells), default=0)
        header = find_roles(rows)
        if header:
            roles, start, width = header[0], header[1], table_width
        elif roles and table_width == width:
            start = 0                       # a continued page without its own header
        else:
            continue
        result += [_read_row(row, cells, roles, section) for row, cells in rows[start:]]
    return result


def _read_row(element, cells, roles: dict[int, str], section: Section) -> ScheduleRow:
    filled = [(column, text) for column, _, text in cells if text]
    values, markers = _collect(filled, roles)
    label = next((text for _, text in filled if re.search(r"[A-Za-z]{2}", text)), "")
    row = ScheduleRow(element, "other", values, markers)
    if is_summary(label, values):
        row.kind = "total"
        values["label"] = label
    elif len([text for _, text in filled if not ONLY_MARKERS.fullmatch(text)]) == 1 and label:
        _apply_heading(clean_name(label), section, "industry" in roles.values())
    elif "fair_value" in values and (values.get("company") or values.get("instrument")):
        if values.get("company"):
            section.company = clean_name(values["company"])
        row.kind = "loan" if section.company else "other"
    row.company = section.company
    row.industry = values.get("industry") or section.industry or values.get("description")
    row.section_instrument = section.instrument
    return row


def is_summary(label: str, values: dict[str, str]) -> bool:
    """A total, net or adjustment line: worded like one, with nothing but numbers beside it.

    "Total Example LLC" with an instrument, rate wording or maturity beside it is a borrower.
    """
    worded = any(re.search(r"[A-Za-z]", values.get(role, "")) and label not in values[role]
                 for role in DETAIL_ROLES)
    return bool(SUMMARY.match(label)) and not worded and not re.search(
        r"\d", values.get("maturity", ""))


def _collect(filled, roles: dict[int, str]) -> tuple[dict[str, str], set[str]]:
    """Join text per role. In number columns keep the first number; "(12)" there is not a marker."""
    values: dict[str, str] = {}
    markers: set[str] = set()
    last_amount = max((c for c, role in roles.items() if role == "fair_value"), default=0)
    for column, text in filled:
        role = roles.get(column)
        if role in NUMERIC_ROLES:
            markers |= _read_number(role, text, values)
            continue
        found = bracketed_markers(text)
        if role in RATE_ROLES:              # "SOFR (q)" is a payment period, not a footnote
            found = {marker for marker in found if not marker.isalpha()}
        if role == "footnote" or (role is None and column > last_amount):
            found |= bare_markers(text)
        markers |= found
        if role and role != "ignore":
            values[role] = f"{values.get(role, '')} {text}".strip()
    return values, markers


def _read_number(role: str, text: str, values: dict[str, str]) -> set[str]:
    """Keep a column's first number; return markers found beside or after it."""
    if role in values:
        return bracketed_markers(text) if ONLY_MARKERS.fullmatch(text) else set()
    amount = without_trailing(text) if re.match(r"[^(]*\d", text) else text
    if NUMBER_CELL.fullmatch(amount) and parse_number(amount) is not None:
        values[role] = amount
        return bracketed_markers(text[len(amount):])
    return set()


def _apply_heading(text: str, section: Section, has_industry_column: bool) -> None:
    """With an Industry column, only a recognised asset class or grouping changes anything."""
    text = HEADING_PERCENT.sub("", text).strip()
    instrument = "Other" if INDUSTRY_WORDING.search(text) else classify_instrument(text)
    section.company = None
    if instrument != "Other":
        section.instrument, section.industry = instrument, None
    elif WIDE_HEADING.search(text):
        section.instrument = section.industry = None
    elif not has_industry_column:
        section.industry = text or None


def clean_name(text: str) -> str:
    """Drop "(continued)" and trailing footnote markers."""
    return without_trailing(CONTINUED.sub("", text))
