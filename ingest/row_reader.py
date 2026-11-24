"""Builds the text fields of a loan (rate, instrument, flags) from one schedule row."""

import re
from collections.abc import Iterable

from ingest.footnotes import Legend, non_accrual_flag, pik_flag
from ingest.models import ParsedLoan
from ingest.normalize import classify_instrument
from ingest.rates import PIK_WORD, parse_date, parse_number, parse_spread_bps, pik_in_text
from ingest.table_grid import ScheduleRow, clean_name


def rate_text(values: dict[str, str]) -> str | None:
    """Join the rate columns into wording such as "SOFR + 5.75%, 2.00% PIK"."""
    reference = _present(values.get("reference"))
    spread = _present(values.get("spread"))
    if reference and spread and not re.search(r"\+\s*\d", reference):
        text = f"{reference.rstrip('+ ')} + {spread}"
    else:
        text = reference or spread or _present(values.get("coupon"))
    pik = _present(values.get("pik"))
    if pik and (parse_number(pik) or 0) > 0:
        addition = pik if "PIK" in pik.upper() else f"{pik} PIK"
        text = f"{text}, {addition}" if text else addition
    coupon = _present(values.get("coupon"))
    if coupon and text != coupon and PIK_WORD.search(coupon) and not PIK_WORD.search(text or ""):
        text = f"{text}, {coupon}"
    return text


def _present(text: str | None) -> str | None:
    cleaned = clean_name(text or "")
    return None if re.fullmatch(r"(?:n/?a|[-–—\s])*", cleaned, re.IGNORECASE) else cleaned


def row_pik(values: dict[str, str], rates_show_pik: bool = False) -> bool | None:
    """What the rate columns say about PIK; None when they do not say.

    `rates_show_pik` means other rows write PIK there, so a rate without it means no PIK.
    """
    text = rate_text(values) or ""
    if PIK_WORD.search(text):
        return pik_in_text(text)
    return False if "pik" in values or (text and rates_show_pik) else None


def rates_show_pik(rows: Iterable[ScheduleRow]) -> bool:
    """True if this schedule writes PIK in its rate columns, so a rate without it means no PIK."""
    return any(row_pik(row.values) is not None for row in rows if row.kind == "loan")


def loan_from_row(row: ScheduleRow, legend: Legend, scale: float) -> ParsedLoan:
    """One loan from table text alone. `scale` turns the table's units into dollars."""
    values = row.values
    rate = rate_text(values)
    instrument = row_instrument(row)
    return ParsedLoan(
        borrower_raw=row.company or "",
        industry=row.industry,
        instrument=instrument,
        rate_text=rate,
        spread_bps=parse_spread_bps(rate),
        maturity_date=parse_date(values.get("maturity")),
        principal=(None if instrument == "Equity"       # units, not dollars
                   else scaled(values.get("principal"), scale)),
        cost=scaled(values.get("cost"), scale),
        fair_value=scaled(values.get("fair_value"), scale) or 0.0,
        is_pik=pik_flag(row.markers, legend, row_pik(values, legend.rates_show_pik)),
        is_non_accrual=non_accrual_flag(row.markers, legend),
    )


def row_instrument(row: ScheduleRow, *fallbacks: str | None) -> str:
    """The row's own wording, then its section, then other wording, then words after the name."""
    suffix = re.split(r",| - ", row.company or "", maxsplit=1)[1:]
    return classify_instrument(row.values.get("instrument"), row.section_instrument, *fallbacks,
                               *suffix)


def scaled(text: str | None, scale: float) -> float | None:
    value = parse_number(text)
    return None if value is None else value * scale
