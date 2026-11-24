"""Registers the six tools: checks inputs, uses the cache, and calls the answer modules."""

from collections.abc import Callable
from datetime import date
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BeforeValidator, Field

from config import Config
from server import borrowers, market, portfolio
from server.cache import ResponseCache
from server.db import Database, DataUnavailable
from server.models import (
    ExposureResponse, FlagsResponse, ListBdcsResponse, MarketResponse, PortfolioResponse, Response,
    Signal, TrendResponse,
)
from server.selection import check_bdc

ALL_SIGNALS: list[Signal] = ["pik", "non_accrual", "markdown"]
READ_ONLY = ToolAnnotations(read_only_hint=True)
AsOf = Annotated[date | None, BeforeValidator(lambda value: blank_to_none(value)), Field(
    description="YYYY-MM-DD. Uses the latest period on or before this date; default is the latest")]
Industry = Annotated[str | None, Field(description="Case-insensitive contains match")]


class CachedRunner:
    """Runs an answer through the response cache and fills in the metadata."""

    def __init__(self, database: Database, data_mode: str) -> None:
        self._data_mode = data_mode
        self._database = database
        self._cache = ResponseCache()

    def run(self, tool: str, arguments: dict[str, Any], answer: Callable[[], Response],
            ttl: Callable[[Response], float] | None = None, needs_data: bool = True) -> Any:
        version = self._version(needs_data)
        key = self._cache.key(tool, arguments)
        hit = self._cache.get(key, version)
        if hit is not None:
            return self._with_metadata(hit, True, version)
        response = answer()
        self._cache.put(key, version, response, ttl(response) if ttl else None)
        return self._with_metadata(response, False, version)

    def _version(self, needs_data: bool) -> int | None:
        try:
            return self._database.data_version()
        except DataUnavailable:
            if needs_data:
                raise
            return None

    def _with_metadata(self, response: Response, cached: bool, version: int | None) -> Response:
        copy = response.model_copy(deep=True)
        copy.metadata.cached, copy.metadata.data_version = cached, version
        copy.metadata.data_mode = self._data_mode
        return copy


def register_tools(mcp: MCPServer, config: Config, database: Database) -> None:
    runner, limits = CachedRunner(database, config.data_mode), config.server
    tickers = {"enum": list(config.bdcs)}       # checked in the tool, for a clearer error
    Bdc = Annotated[str, Field(json_schema_extra=tickers, description="BDC ticker")]
    Page = Annotated[int, Field(ge=1)]
    PageSize = Annotated[int, Field(ge=1, le=limits.max_page_size)]
    tool = mcp.tool(annotations=READ_ONLY)

    @tool
    def list_bdcs() -> ListBdcsResponse:
        """List the loaded BDCs and their periods. Call this first when unsure what is available."""
        return runner.run("list_bdcs", {}, lambda: portfolio.list_bdcs(database, config))

    @tool
    def get_bdc_portfolio(
        bdc: Bdc, as_of: AsOf = None, industry: Industry = None,
        instrument: Annotated[str | None, Field(description='Contains match, e.g. "first lien"')] = None,
        page: Page = 1, page_size: PageSize = limits.default_page_size,
    ) -> PortfolioResponse:
        """Loan list for one BDC, with a summary over all loans matching the filters."""
        args = {"bdc": check_bdc(bdc, config), "as_of": as_of, "industry": clean(industry),
                "instrument": clean(instrument), "page": page, "page_size": page_size}
        return runner.run("get_bdc_portfolio", args,
                          lambda: portfolio.get_portfolio(database, config, **args))

    @tool
    def flag_stress_signals(
        bdc: Annotated[Bdc | None, Field(description="Default is all BDCs")] = None,
        industry: Industry = None, as_of: AsOf = None,
        mark_threshold: Annotated[float, Field(
            gt=0, le=2, description="A loan marked below this fraction of cost shows `markdown`",
        )] = limits.default_mark_threshold,
        signals: Annotated[list[Signal] | None, Field(
            description="Signals to look for; a loan needs at least one. Default is all three")] = None,
        min_score: Annotated[int, Field(
            ge=0, le=config.scoring.max_score,
            description="Lowest stress score to return. Marks above 0.90 earn no points, so use 0 "
                        "with a higher mark_threshold")] = limits.default_min_score,
        page: Page = 1, page_size: PageSize = limits.default_page_size,
    ) -> FlagsResponse:
        """Stressed loans, highest stress score first, with reasons. The main watchlist tool.

        lender_count shows how many BDCs hold the borrower. Equity is not scored.
        """
        args = {"bdc": check_bdc(bdc, config) if clean(bdc) else None, "industry": clean(industry),
                "as_of": as_of, "mark_threshold": mark_threshold,
                "signals": sorted(set(signals or ALL_SIGNALS)), "min_score": min_score,
                "page": page, "page_size": page_size}
        return runner.run("flag_stress_signals", args,
                          lambda: portfolio.flag_stress(database, config, **args))

    @tool
    def find_borrower_exposure(
        borrower: Annotated[str, Field(description="A borrower_key or a name; close spellings return candidates")],
        as_of: AsOf = None,
    ) -> ExposureResponse:
        """Every BDC lending to one borrower and how each values it.

        Only an exact name is "found". If status is "ambiguous", choose one of candidates and call again with its borrower_key.
        """
        if not borrower.strip():
            raise ToolError("borrower is empty. Give a borrower name, e.g. from get_bdc_portfolio.")
        args = {"borrower": borrower.strip(), "as_of": as_of}
        return runner.run("find_borrower_exposure", args,
                          lambda: borrowers.find_exposure(database, config, **args))

    @tool
    def get_portfolio_trend(
        bdc: Bdc,
        periods: Annotated[int, Field(
            ge=1, description="Most recent quarters")] = limits.default_trend_periods,
    ) -> TrendResponse:
        """Stress measures for one BDC by quarter, oldest first."""
        args = {"bdc": check_bdc(bdc, config), "periods": periods}
        return runner.run("get_portfolio_trend", args,
                          lambda: portfolio.get_trend(database, config, **args))

    @tool
    def get_market_context(
        lookback_days: Annotated[int, Field(
            ge=1, le=limits.max_lookback_days)] = limits.default_lookback_days,
    ) -> MarketResponse:
        """SOFR, high-yield spread and 10-year Treasury: latest, start of window, and change."""
        def ttl(response: MarketResponse) -> float:
            live = response.data_origin == "fred_live"
            return limits.fred_cache_seconds if live else limits.fred_fallback_cache_seconds
        args = {"lookback_days": lookback_days}
        return runner.run("get_market_context", args,
                          lambda: market.get_market_context(database, config, **args),
                          ttl=ttl, needs_data=False)

    reject_unknown_arguments(mcp)


def blank_to_none(value: Any) -> Any:
    """A blank date means no date was given."""
    return None if isinstance(value, str) and not value.strip() else value


def reject_unknown_arguments(mcp: MCPServer) -> None:
    """Make a misspelled input an error; otherwise it would be ignored without notice."""
    for tool in mcp._tool_manager.list_tools():     # the SDK has no public setting for this
        arguments = tool.fn_metadata.arg_model
        arguments.model_config["extra"] = "forbid"
        arguments.model_rebuild(force=True)
        tool.parameters["additionalProperties"] = False


def clean(text: str | None) -> str | None:
    """Trim a filter; blank means no filter."""
    return (text or "").strip() or None
