"""Borrower name normalization, instrument classification and cross-BDC fuzzy matching."""

import re
from collections.abc import Iterable

from rapidfuzz import fuzz, process

SUFFIXES = {
    "inc", "incorporated", "llc", "lp", "llp", "holdings", "holding", "corp", "corporation",
    "co", "company", "ltd", "limited",
}
OTHER_NAME = (r"(?:(?:d(?:[./]\s?)?b|[fa](?:[./]\s?)?k)(?:[./]\s?)?a\b\.?|doing business as\b|"
              r"(?:formerly|also) known as\b)[\s:]*")
OTHER_NAME_IN_BRACKETS = re.compile(
    r"\(\s*" + OTHER_NAME + r"(?:[^()]|\([^()]*\))*\)", re.IGNORECASE)
OTHER_NAME_TO_END = re.compile(r"[\s,;/(]+" + OTHER_NAME + r".*$", re.IGNORECASE)
SPACED_SUFFIX = re.compile(r"\bl\s+(?:(l)\s+)?([cp])$")        # "L. P." and "L. L. C."

# Order matters: the first pattern found wins.
INSTRUMENT_PATTERNS = [
    ("Unitranche", r"unitranche"),
    ("Second lien", r"second[\s-]+lien|2nd[\s-]+lien"),
    ("First lien", r"first[\s-]+lien|1st[\s-]+lien"),
    ("Subordinated", r"subordinated|mezzanine|junior debt|unsecured"),
    ("Equity", r"\bequity\b|\bstock\b|\bshares?\b|\bwarrants?\b|\bpreferred\b|\bcommon\b|"
               r"earn-?outs?\b|\blimited liability company interests?\b|"
               r"\b(?:class [a-z0-9-]+|partnership|membership|llc|lp) (?:units?|interests?)\b|"
               r"\bunits?\b|joint venture|senior loan fund|\bpartnership interests?\b"),
    ("First lien", r"senior secured|revolv|delayed draw|term loan"),
]


def normalize_name(name: str) -> str:
    """Return the key used to match one borrower across BDCs. Applying it twice changes nothing."""
    current, previous = name.strip(), None
    while current != previous:
        current, previous = _simplify(current), current
    return current


def plain_words(name: str) -> str:
    """The whole name in plain words, trade names included; for searching only."""
    return " ".join(re.sub(r"[^\w\s]|_", " ", re.sub(r"[.'’]", "", name.lower())).split())


def _simplify(name: str) -> str:
    text = OTHER_NAME_TO_END.sub("", OTHER_NAME_IN_BRACKETS.sub(" ", name))
    text = re.sub(r"[.'’]", "", text.lower().replace("&", " and "))
    text = " ".join(re.sub(r"[^\w\s]|_", " ", text).split())
    words = SPACED_SUFFIX.sub(r"l\1\2", text).split()
    while len(words) > 1 and words[-1] in SUFFIXES:
        words.pop()
    return " ".join(words) or name.strip().lower()      # punctuation-only names stay distinct


def classify_instrument(*texts: str | None) -> str:
    """Map instrument wording to a category. Earlier texts are trusted over later ones."""
    for text in texts:
        for label, pattern in INSTRUMENT_PATTERNS:
            if text and re.search(pattern, text, re.IGNORECASE):
                return label
    return "Other"


def fuzzy_pairs(
    keys_by_bdc: dict[str, set[str]], threshold: float
) -> Iterable[tuple[str, str, float]]:
    """Yield (key, similar_key, score) for different names that no single BDC uses both of."""
    owners: dict[str, set[str]] = {}
    for bdc, keys in keys_by_bdc.items():
        for key in keys:
            owners.setdefault(key, set()).add(bdc)
    ordered = sorted(owners)
    for position, key in enumerate(ordered):
        matches = process.extract(
            key, ordered[:position], scorer=fuzz.token_sort_ratio,
            score_cutoff=threshold, limit=None,
        )
        for other, score, _ in matches:
            if not owners[key] & owners[other]:
                yield key, other, score
