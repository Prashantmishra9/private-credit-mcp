"""Pydantic response models. MCPServer turns these into the tools' output schemas."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Signal = Literal["pik", "non_accrual", "markdown"]
MarkPct = Field(description="fair_value / cost, e.g. 0.95. Null when cost is zero, negative or unknown")
Flag = Field(description="Null means the filing does not say")


class Source(BaseModel):
    """Where the data came from. FRED sources have a series_id and no BDC fields."""
    bdc_ticker: str | None = None
    form_type: str | None = None
    period_end: date | None = None
    source_url: str
    series_id: str | None = None


class Metadata(BaseModel):
    cached: bool = False
    data_mode: Literal["live", "mock"] = Field("live", description="mock means every figure is invented")
    data_version: int | None = Field(None, description="Changes whenever ingestion loads data")


class Response(BaseModel):
    """Fields every tool returns."""
    sources: list[Source] = Field([], description=(
        "Filings or series searched for this answer, plus any other lender's filing used to compare "
        "marks. A filing can be listed without having a row in the result"))
    metadata: Metadata = Metadata()


class BdcInfo(BaseModel):
    ticker: str
    name: str
    latest_period_end: date
    periods_available: list[date]
    loan_count: int = Field(description="Schedule lines in the latest period, equity included")


class ListBdcsResponse(Response):
    bdcs: list[BdcInfo]
    not_loaded: list[str] = Field(description="Configured BDCs with no data loaded")


class LoanOut(BaseModel):
    borrower: str
    borrower_key: str
    bdc_ticker: str
    period_end: date
    industry: str | None
    instrument: str
    rate_text: str | None
    spread_bps: int | None
    maturity_date: date | None
    principal: float | None
    cost: float | None
    fair_value: float
    mark_pct: float | None = MarkPct
    is_pik: bool | None = Flag
    is_non_accrual: bool | None = Flag
    stress_score: int = Field(description="0 to 10. Equity always scores 0")


class PortfolioSummary(BaseModel):
    """Totals over every loan matching the filters, not only this page."""
    total_cost: float
    total_fair_value: float
    pik_loan_count: int
    non_accrual_loan_count: int
    non_accrual_pct_of_cost: float | None = Field(
        description="Fraction, e.g. 0.012. Null when no loan has a known flag")


class Page(Response):
    page: int
    page_size: int
    total: int = Field(description="Matching rows across all pages")


class PortfolioResponse(Page):
    bdc: str
    as_of: date = Field(description="Period end used: the latest on or before the requested date")
    summary: PortfolioSummary
    loans: list[LoanOut]


class FlagOut(BaseModel):
    borrower: str
    borrower_key: str
    bdc_ticker: str
    period_end: date
    industry: str | None
    instrument: str
    cost: float | None
    fair_value: float
    mark_pct: float | None = MarkPct
    is_pik: bool | None = Flag
    is_non_accrual: bool | None = Flag
    mark_dispersion_pts: float | None = Field(
        description="Gap between lenders' marks in points, same period. Null with one lender")
    lender_count: int = Field(description="BDCs holding this borrower in the same period")
    stress_score: int
    reasons: list[str]


class FlagsResponse(Page):
    flags: list[FlagOut]


class LenderOut(BaseModel):
    bdc_ticker: str
    borrower: str
    period_end: date
    industry: str | None
    instrument: str
    rate_text: str | None
    maturity_date: date | None
    cost: float | None
    fair_value: float
    mark_pct: float | None = MarkPct
    is_pik: bool | None = Flag
    is_non_accrual: bool | None = Flag
    stress_score: int


class Candidate(BaseModel):
    borrower_key: str = Field(description="Pass this back as `borrower` to pick this one")
    names: list[str]
    bdc_tickers: list[str]


class ExposureResponse(Response):
    status: Literal["found", "ambiguous"] = Field(
        description="Only an exact name is found. When ambiguous, pick one of `candidates` and call again")
    borrower_key: str | None
    matched_names: list[str]
    lenders: list[LenderOut]
    mark_dispersion_pts: float | None = Field(
        description="Largest same-period gap between lenders' cost-weighted debt marks, in points")
    stress_score: int | None = Field(description="Highest score across the borrower's loans")
    comparison_loans: list[LenderOut] = Field(description=(
        "Same borrower in another BDC's older filing, used only to compare marks in that period"))
    candidates: list[Candidate] = Field(description="Best matches first; can be a single one")
    total_candidates: int = Field(description="Matches found; `candidates` may show fewer")
    unreviewed_similar_names: list[str] = Field(
        description="Possibly the same borrower; not included until an analyst approves")


class TrendRow(BaseModel):
    """Counts and totals include equity; the mark figures cover debt only."""
    period_end: date
    loan_count: int
    total_fair_value: float
    non_accrual_pct_of_cost: float | None
    pik_pct_of_fair_value: float | None
    avg_mark_pct: float | None
    loans_marked_below_90_pct: int


class TrendResponse(Response):
    bdc: str
    trend: list[TrendRow] = Field(description="Oldest period first")


class RateOut(BaseModel):
    series_id: str
    description: str
    unit: Literal["percent"] = "percent"
    latest_date: date
    latest_value: float
    start_date: date
    start_value: float
    change: float = Field(description="latest_value minus start_value, in percentage points")


class MarketResponse(Response):
    lookback_days: int
    data_origin: Literal["fred_live", "stored", "mock"] = Field(
        description="stored means FRED was unavailable and ingested values were used; mock is invented")
    series: list[RateOut]
