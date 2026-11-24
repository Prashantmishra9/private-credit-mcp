"""Reads interest-rate wording, numbers and dates out of filing text."""

import calendar
import re
from datetime import date, datetime

REFERENCE = (r"\b(?:SOFR|SF|SONIA|SONI|SA|SN|EURIBOR|EU|LIBOR|PRIME|BASE RATE|BBSY|CORRA|CDOR|S|L|E|P|C)\b"
             r"[^+%\d]{0,20}\+\s*")
SPREAD = re.compile(REFERENCE + r"(?:\d+(?:\.\d+)?\s*%\s*\+\s*)?(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
SPREAD_IN_BPS = re.compile(REFERENCE + r"(\d{2,4})\b(?!\s*[.%\d])", re.IGNORECASE)   # "SF+600"
PIK_AMOUNT = re.compile(r"(\d+(?:\.\d+)?)\s*%?\s*\(?(max(?:imum)?\s+)?PIK\b", re.IGNORECASE)
PIK_AMOUNT_AFTER = re.compile(r"(?:^|[(,])\s*PIK\s*:?\s*(\d+(?:\.\d+)?)\s*%", re.IGNORECASE)
PIK_WORD = re.compile(r"\bPIK\b|paid[\s-]in[\s-]kind|(?:payment|payable)[\s-]in[\s-]kind", re.IGNORECASE)
DASHES = {"-", "–", "—", "$ -", "$ –", "$ —"}


def parse_number(text: str | None) -> float | None:
    """Parse "$ 1,234.5", "(12)" or a dash. Returns None when there is no number."""
    cleaned = (text or "").replace("\xa0", " ").replace("−", "-").strip()
    if cleaned in DASHES or re.sub(r"[\s$€£]", "", cleaned) in DASHES:
        return 0.0
    match = re.search(r"\d[\d,]*(?:\.\d+)?|\.\d+", cleaned)
    if not match:
        return None
    value = float(match.group().replace(",", ""))
    negative = re.match(r"^[\s$€£A-Z]*[(\-–—]", cleaned)
    return -value if negative else value


def parse_spread_bps(rate_text: str | None) -> int | None:
    """Basis points over the reference rate, e.g. "SOFR + 5.75%" gives 575."""
    if match := SPREAD.search(rate_text or ""):
        return round(float(match.group(1)) * 100)
    match = SPREAD_IN_BPS.search(rate_text or "")
    return int(match.group(1)) if match else None


def pik_in_text(rate_text: str | None) -> bool:
    """True if the wording shows a PIK rate above zero. "Max PIK" alone is only an option."""
    text = rate_text or ""
    if not PIK_WORD.search(text):
        return False
    if amounts_after := PIK_AMOUNT_AFTER.findall(text):
        return any(float(value) > 0 for value in amounts_after)
    amounts = PIK_AMOUNT.findall(text)
    if amounts:
        return any(float(value) > 0 and not maximum for value, maximum in amounts)
    return not re.search(r"\bmax(?:imum)?\s+PIK\b", text, re.IGNORECASE)


def parse_date(text: str | None) -> date | None:
    """Parse the maturity and heading date styles used in filings. Month-only means month end."""
    cleaned = re.sub(r"\s+", " ", text or "").strip()
    for pattern in ("%m/%d/%Y", "%m/%d/%y", "%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, pattern).date()
        except ValueError:
            continue
    for pattern in ("%m/%Y", "%b-%y", "%B %Y", "%b %Y"):
        try:
            month = datetime.strptime(cleaned, pattern)
        except ValueError:
            continue
        return date(month.year, month.month, calendar.monthrange(month.year, month.month)[1])
    return None
