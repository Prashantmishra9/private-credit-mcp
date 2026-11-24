"""Plain data passed between the parsers and the loader."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class FilingRef:
    """One 10-K or 10-Q on EDGAR."""
    filing_id: str
    bdc_ticker: str
    form_type: str
    period_end: date
    filed_date: date
    source_url: str


@dataclass
class ParsedLoan:
    """One Schedule of Investments line. None means the filing did not tell us."""
    borrower_raw: str
    industry: str | None
    instrument: str
    rate_text: str | None
    spread_bps: int | None
    maturity_date: date | None
    principal: float | None
    cost: float | None
    fair_value: float
    is_pik: bool | None
    is_non_accrual: bool | None


@dataclass
class ParsedFiling:
    """Everything one parser read from one filing."""
    method: str
    loans: list[ParsedLoan] = field(default_factory=list)
    filed_total: float | None = None

    @property
    def total_fair_value(self) -> float:
        return sum(loan.fair_value for loan in self.loans)

    def difference(self) -> float | None:
        """Relative gap to the filing's own total, or None if it cannot be computed."""
        if not self.filed_total or not self.loans:
            return None
        return abs(self.total_fair_value - self.filed_total) / abs(self.filed_total)

    def reconciles(self, tolerance: float) -> bool:
        gap = self.difference()
        return gap is not None and gap <= tolerance
