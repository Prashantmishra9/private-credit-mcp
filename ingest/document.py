"""Loads a filing document and gives simple access to its text and tables."""

import re
from collections.abc import Iterator

from lxml import etree, html

BLOCKS = {"p", "div", "li"}
SPACED = BLOCKS | {"br"}


def load_document(content: bytes) -> etree._Element:
    """Parse XHTML (inline XBRL), or ordinary HTML when that finds no tables."""
    parser = etree.XMLParser(recover=True, huge_tree=True, remove_comments=True,
                             remove_pis=True, resolve_entities=False, no_network=True)
    content = content.replace(b"&nbsp;", b"&#160;")     # not defined in XML
    root = etree.fromstring(content, parser)
    if root is None or next(elements(root, "table"), None) is None:
        root = html.fromstring(content, parser=html.HTMLParser(huge_tree=True,
                                                               remove_comments=True))
    for node in root.iter():
        if local_name(node) in SPACED:
            node.tail = " " + (node.tail or "")   # keep "First lien<br/>loan" as two words
        elif local_name(node) == "sup":
            _bracket_superscript(node)
    return root


def _bracket_superscript(node: etree._Element) -> None:
    """Write a bare footnote number such as <sup>2, 8</sup> as "(2, 8)"."""
    if len(node) == 0 and re.fullmatch(r"\s*\d{1,3}(?:\s*,\s*\d{1,3})*\s*", node.text or ""):
        before = node.getprevious().tail if node.getprevious() is not None else node.getparent().text
        if not (before or "").rstrip().endswith(("(", "[")):
            node.text = f" ({node.text.strip()})"


def local_name(node: etree._Element) -> str:
    """Tag without namespace or prefix, lowercased."""
    if not isinstance(node.tag, str):
        return ""
    return node.tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def elements(root: etree._Element, name: str) -> Iterator[etree._Element]:
    """Descendants with this local name, in document order."""
    return (node for node in root.iter() if local_name(node) == name)


def text_of(node: etree._Element) -> str:
    """All text inside a node, with whitespace collapsed."""
    return " ".join("".join(node.itertext()).split())


def closest(node: etree._Element, name: str) -> etree._Element | None:
    """Nearest ancestor with this local name."""
    return next((item for item in node.iterancestors() if local_name(item) == name), None)


def text_blocks(root: etree._Element) -> Iterator[tuple[etree._Element, str]]:
    """Table rows and innermost paragraphs with their text, in document order."""
    for node in root.iter():
        name = local_name(node)
        if name == "tr" or (name in BLOCKS and _is_innermost(node)):
            text = text_of(node)
            if text:
                yield node, text


def _is_innermost(node: etree._Element) -> bool:
    inner = any(local_name(child) in BLOCKS | {"table"} for child in node.iterdescendants())
    return not inner and closest(node, "tr") is None
