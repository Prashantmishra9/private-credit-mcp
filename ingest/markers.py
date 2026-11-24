"""Finds footnote markers in table cells: "(2)(8)", "(2, 8)", "[8]" or "2, 8"."""

import re

COMMA = r"(?:\s*,\s+|,(?!\d{3}(?!\d)))"       # "(8,125)" is an amount, not two markers
TOKEN = r"(?:\d{1,3}|[a-z]{1,3}|[*#^†‡])"
GROUP = rf"[(\[]\s*{TOKEN}(?:{COMMA}{TOKEN})*\s*[)\]]"
BRACKETED = re.compile(GROUP)
ONLY_MARKERS = re.compile(rf"(?:{GROUP}[\s,*]*)+")
BARE_LIST = re.compile(rf"\d{{1,3}}(?:{COMMA}\d{{1,3}})*")
TRAILING = re.compile(rf"(?:[\s,]*{GROUP})+\s*\*?\s*$")


def bracketed_markers(text: str) -> set[str]:
    """Markers written in brackets anywhere in the text."""
    return {token for group in BRACKETED.findall(text) for token in re.findall(TOKEN, group)}


def bare_markers(text: str) -> set[str]:
    """Markers from a cell that holds nothing but numbers; only for footnote columns."""
    return set(re.findall(r"\d+", text)) if BARE_LIST.fullmatch(text) else set()


def without_trailing(text: str) -> str:
    """Text with its final markers removed."""
    return TRAILING.sub("", text).strip()
