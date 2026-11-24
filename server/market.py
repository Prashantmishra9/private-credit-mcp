"""Answer for get_market_context: live FRED values, or the ingested ones if FRED fails."""

import logging
from datetime import date, timedelta

from mcp.server.mcpserver.exceptions import ToolError

from config import Config
from ingest.fred_client import FredError, fetch_series, series_url
from server.db import Database, DataUnavailable
from server.models import MarketResponse, RateOut, Source

log = logging.getLogger(__name__)
Series = dict[str, list[tuple[date, float]]]


def get_market_context(database: Database, config: Config, lookback_days: int,
                       today: date | None = None) -> MarketResponse:
    start = (today or date.today()) - timedelta(days=lookback_days)
    fetch_from = start - timedelta(days=config.server.holiday_buffer_days)
    series, origin = read_series(database, config, fetch_from)
    rates = [change_over(name, text, series[name], start)
             for name, text in config.fred_series.items()]
    return MarketResponse(
        lookback_days=lookback_days, data_origin=origin, series=rates,
        sources=[Source(series_id=name, source_url=source_url(config, name)) for name in config.fred_series])


def source_url(config: Config, series_id: str) -> str:
    """Invented rates must not cite a real FRED page."""
    mock = config.data_mode == "mock"
    return f"https://example.invalid/mock-rates/{series_id}" if mock else series_url(series_id)


def read_series(database: Database, config: Config, start: date) -> tuple[Series, str]:
    """Mock mode never calls FRED, even if a key is set."""
    if config.data_mode == "mock":
        return mock_series(database, config, start), "mock"
    try:
        return {name: fetch_series(config, name, start) for name in config.fred_series}, "fred_live"
    except FredError as error:
        log.warning("Using stored rates: %s", error)
        return stored_series(database, config, start, str(error)), "stored"


def mock_series(database: Database, config: Config, start: date) -> Series:
    series = {name: database.rates(name, start) for name in config.fred_series}
    if not all(series.values()):
        raise ToolError("The mock rates do not cover this window. Use a larger lookback_days, or add "
                        "rows to mock/data/rates.csv and run `uv run python -m mock.load`.")
    return series


def stored_series(database: Database, config: Config, start: date, problem: str) -> Series:
    try:
        series = {name: database.rates(name, start) for name in config.fred_series}
    except DataUnavailable:
        series = {}
    if not series or not all(series.values()):
        raise ToolError(f"{problem} No stored rates cover this window either; "
                        "run `uv run python -m ingest.run` with a FRED key to store them.")
    return series


def change_over(series_id: str, description: str, values: list[tuple[date, float]],
                start: date) -> RateOut:
    """Compare the latest value with the last one on or before the window's first day."""
    if not values:
        raise ToolError(f"FRED returned no {series_id} values for this window. "
                        "Use a larger lookback_days.")
    before = [item for item in values if item[0] <= start]
    first = before[-1] if before else values[0]
    last = values[-1]
    return RateOut(series_id=series_id, description=description, latest_date=last[0],
                   latest_value=last[1], start_date=first[0], start_value=first[1],
                   change=round(last[1] - first[1], 4))
