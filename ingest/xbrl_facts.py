"""Reads contexts, facts and footnote links out of an inline XBRL document."""

from dataclasses import dataclass, field
from datetime import date

from lxml import etree

from ingest.document import local_name, text_of
from ingest.rates import parse_number

IDENTIFIER_AXIS = "InvestmentIdentifierAxis"
# Axes that only describe a position. Any other axis (such as a joint venture's) rules it out.
DESCRIPTIVE_AXES = {"InvestmentTypeAxis", "EquitySecuritiesByIndustryAxis",
                    "InvestmentIssuerAffiliationAxis", "IndustrySectorAxis",
                    "LienCategoryAxis", "VariableRateAxis", "FinancialInstrumentAxis"}


# Newer taxonomies renamed these; both spellings are read as one.
RENAMED = {"InvestmentOwnedFairValue": "InvestmentOwnedAtFairValue",
           "InvestmentOwnedCost": "InvestmentOwnedAtCost"}


@dataclass
class Context:
    instant: date | None
    identifier: str | None = None
    other_axes: set[str] = field(default_factory=set)

    @property
    def is_position(self) -> bool:
        return self.identifier is not None and self.other_axes <= DESCRIPTIVE_AXES

    @property
    def is_whole_company(self) -> bool:
        return self.identifier is None and not self.other_axes


@dataclass
class Fact:
    element: etree._Element
    name: str
    context: Context

    @property
    def text(self) -> str:
        return text_of(self.element)

    @property
    def number(self) -> float | None:
        """Value in base units, with the tag's scale and sign applied."""
        if "zero" in (self.element.get("format") or "") or self.text in {"—", "–", "-"}:
            return 0.0
        value = parse_number(self.text)
        if value is None:
            return None
        sign = -1 if self.element.get("sign") == "-" else 1
        return sign * abs(value) * 10 ** int(self.element.get("scale") or 0)


def read_contexts(root: etree._Element) -> dict[str, Context]:
    contexts = {}
    for node in (item for item in root.iter() if local_name(item) == "context"):
        instant = next((text_of(item) for item in node.iter() if local_name(item) == "instant"), None)
        context = Context(date.fromisoformat(instant[:10]) if instant else None)
        for member in node.iter():
            if local_name(member) in {"typedmember", "explicitmember"}:
                axis = (member.get("dimension") or "").rsplit(":", 1)[-1]
                if axis == IDENTIFIER_AXIS:
                    context.identifier = text_of(member)
                else:
                    context.other_axes.add(axis)
        contexts[node.get("id")] = context
    return contexts


def read_facts(root: etree._Element, contexts: dict[str, Context]) -> list[Fact]:
    """Every tagged fact whose context is known. Nil facts are skipped."""
    facts = []
    for node in root.iter():
        kind = local_name(node)
        context = contexts.get(node.get("contextRef", "")) if kind in {"nonfraction", "nonnumeric"} else None
        nil = any(name.endswith("nil") and value == "true" for name, value in node.attrib.items())
        if context is not None and not nil:
            name = (node.get("name") or "").rsplit(":", 1)[-1]
            facts.append(Fact(node, RENAMED.get(name, name), context))
    return facts


def linked_footnotes(root: etree._Element) -> dict[str, str]:
    """Fact id to the text of the footnotes attached to it by ix:relationship."""
    notes = {node.get("id"): text_of(node) for node in root.iter() if local_name(node) == "footnote"}
    linked: dict[str, str] = {}
    for node in (item for item in root.iter() if local_name(item) == "relationship"):
        text = " ".join(notes.get(ref, "") for ref in (node.get("toRefs") or "").split()).strip()
        if text:
            for fact_id in (node.get("fromRefs") or "").split():
                linked[fact_id] = f"{linked.get(fact_id, '')} {text}".strip()
    return linked
