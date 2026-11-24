"""Fetches FRED series. The API key is sent as a parameter, so URLs are never logged or raised."""

import logging
from datetime import date

import httpx

from config import Config, read_env

OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
logging.getLogger("httpx").setLevel(logging.WARNING)   # httpx logs full URLs at INFO
logging.getLogger("httpcore").setLevel(logging.WARNING)


class FredError(RuntimeError):
    """FRED could not be used; the message says how to fix it and never holds the key."""


def series_url(series_id: str) -> str:
    """Public page for a series, safe to show."""
    return f"https://fred.stlouisfed.org/series/{series_id}"


def fetch_series(
    config: Config, series_id: str, start: date, transport: httpx.BaseTransport | None = None
) -> list[tuple[date, float]]:
    """Observations from `start` to today, oldest first."""
    name = config.env.fred_api_key
    key = read_env(name)
    if not key:
        raise FredError(f"{name} is not set. Copy .env.example to .env and add a free FRED key.")
    params = {"series_id": series_id, "api_key": key, "file_type": "json",
              "observation_start": start.isoformat()}
    try:
        with httpx.Client(timeout=20, transport=transport) as http:
            response = http.get(OBSERVATIONS_URL, params=params)
    except httpx.HTTPError as error:
        raise FredError(f"FRED request for {series_id} failed: {type(error).__name__}") from None
    if response.status_code != 200:
        raise FredError(f"FRED request for {series_id} failed: HTTP {response.status_code}. "
                        f"Check that {name} is valid.")
    try:
        return [(date.fromisoformat(item["date"]), float(item["value"]))
                for item in response.json()["observations"] if item["value"] != "."]
    except (ValueError, KeyError, TypeError):
        raise FredError(f"FRED returned unreadable data for {series_id}. Try again later.") from None
