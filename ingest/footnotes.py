"""Reads a schedule's own footnote legend to learn which markers mean PIK or non-accrual."""

import re
from dataclasses import dataclass, field

from lxml import etree

from ingest.document import local_name, text_blocks
from ingest.rates import PIK_WORD

ENTRY = re.compile(r"^[(\[]([0-9]{1,3}|[a-z]{1,3}|[*#^†‡])[)\]]\s*(\S.*)$")
BARE_ENTRY = re.compile(r"^([0-9]{1,3})\.?\s+([A-Z].*)$")       # "8 Loan was on non-accrual"
NON_ACCRUAL = re.compile(r"non[\s-]?accrual", re.IGNORECASE)
NEGATED = re.compile(       # the negation must govern "non-accrual", not some other words
    r"(?:\b(?:not|never)\s+(?:(?:currently|yet|been|placed|considered|designated)\s+)*"
    r"(?:(?:on|in|as)\s+)?|\bno\s+longer\s+(?:(?:placed|classified|carried)\s+)?(?:(?:on|in|as)\s+)?|"
    r"\b(?:removed|excluded|returned|restored|taken)\s+(?:from|off)\s+(?:being\s+)?(?:on\s+)?|"
    r"\b(?:may|might|could)\s+be\s+(?:(?:placed|put|moved)\s+)?(?:(?:on|to)\s+)?|\b(?:none|neither)\b[^.;,]{0,60}|"
    r"\bno\s+(?:\w+\s+){0,2}(?:loans?|investments?|securities|assets)\b[^.;,]{0,40}|\bno\s+)"
    r"non[\s-]?accrual", re.IGNORECASE)
PIK_OPTIONAL = re.compile(r"\b(?:may\b(?!\s+\d)|option|optional|elect|election|permitted)\b",
                          re.IGNORECASE)
PIK_PAYING = re.compile(        # an option counts only when the text says PIK is paid now
    r"\b(?:currently|now)\s+(?:being\s+)?(?:paying|paid|accruing)\s+(?:interest\s+)?(?:in\s+)?"
    r"(?:PIK\b|pa(?:id|yment)[\s-]in[\s-]kind)", re.IGNORECASE)
PIK_NOT_PAYING = re.compile(r"\b(?:no|not|none)\b[^.;]*\b(?:currently|now)\b", re.IGNORECASE)
NOTES_HEADING = re.compile(r"^notes to (?:the )?(?:condensed )?(?:consolidated )?financial",
                           re.IGNORECASE)


@dataclass
class Legend:
    """Marker meanings. `found` is False when the filing's legend could not be read."""
    found: bool = False
    pik: set[str] = field(default_factory=set)
    non_accrual: set[str] = field(default_factory=set)
    rates_show_pik: bool = False            # this schedule writes PIK in its rate columns


def says_non_accrual(text: str) -> bool:
    return bool(NON_ACCRUAL.search(text)) and not NEGATED.search(text)


def says_pik(text: str) -> bool:
    """True for wording that states PIK interest; an option counts only if it is being used."""
    paying = PIK_PAYING.search(text) and not PIK_NOT_PAYING.search(text)
    in_use = paying or not PIK_OPTIONAL.search(text)
    return bool(PIK_WORD.search(text) and in_use)


def read_legend(root: etree._Element, current: list[etree._Element],
                prior: list[etree._Element]) -> Legend:
    """Read "(12) Loan was on non-accrual status" lines between the current and prior schedules."""
    legend = Legend()
    if not current:
        return legend
    order = {node: position for position, node in enumerate(root.iter())
             if local_name(node) in {"tr", "p", "div", "li", "table"}}
    tables = set(current)
    first, last = order[current[0]], order[current[-1]]
    stop = next((order[table] for table in prior if order[table] > last), None)
    bracketed, bare = [], []
    meanings: dict[str, str] = {}
    for node, text in text_blocks(root):
        position = order[node]
        if position < first:
            continue
        if stop is not None and position >= stop:
            break
        if position > last and NOTES_HEADING.match(text):
            break
        if entry := ENTRY.match(text):
            bracketed.append(entry)
        elif not any(parent in tables for parent in node.iterancestors()):
            if entry := BARE_ENTRY.match(text):
                bare.append(entry)
    for entry in bracketed or bare:         # "8 ARES ..." may be a page number
        if re.search(r"[A-Za-z]{3}", entry.group(2)):
            meanings.setdefault(*entry.groups())
    for marker, meaning in meanings.items():
        _add(legend, marker, meaning)
    return legend


def _add(legend: Legend, marker: str, meaning: str) -> None:
    legend.found = True
    if says_non_accrual(meaning):
        legend.non_accrual.add(marker)
    if says_pik(meaning):
        legend.pik.add(marker)


def non_accrual_flag(markers: set[str] | None, legend: Legend, linked_text: str = "") -> bool | None:
    """`markers` is None when the row was not found. False needs a read row and a legend meaning."""
    if (markers or set()) & legend.non_accrual or says_non_accrual(linked_text):
        return True
    return False if markers is not None and legend.non_accrual else None


def pik_flag(markers: set[str] | None, legend: Legend, stated: bool | None,
             linked_text: str = "") -> bool | None:
    """`stated` is what the rate wording or PIK tag says, None when neither mentions PIK."""
    if stated or (markers or set()) & legend.pik or says_pik(linked_text):
        return True
    return False if stated is False or (markers is not None and legend.pik) else None
