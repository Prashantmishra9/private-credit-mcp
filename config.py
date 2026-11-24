"""Loads config.yaml and .env. Secrets are read by name and never stored in the config."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from dotenv import dotenv_values
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent


class EnvNames(BaseModel):
    sec_user_agent: str
    fred_api_key: str


class Paths(BaseModel):
    database: Path
    mock_database: Path
    edgar_cache: Path
    tool_docs: Path


class IngestConfig(BaseModel):
    quarter_ends: int
    reconcile_tolerance: float
    min_request_interval_seconds: float
    max_retries: int
    db_write_timeout_seconds: float
    fuzzy_match_threshold: float


class Points(BaseModel):
    non_accrual: int
    pik: int
    deep_markdown: int
    markdown: int
    dispersion: int


class ScoringConfig(BaseModel):
    points: Points
    deep_markdown_below: float
    markdown_below: float
    dispersion_over_pts: float
    max_score: int
    exclude_instruments: list[str]


class ServerConfig(BaseModel):
    default_page_size: int
    max_page_size: int
    default_mark_threshold: float
    default_min_score: int
    default_trend_periods: int
    default_lookback_days: int
    max_lookback_days: int
    holiday_buffer_days: int
    borrower_match_threshold: float
    max_candidates: int
    db_open_timeout_seconds: float
    fred_cache_seconds: float
    fred_fallback_cache_seconds: float


class Config(BaseModel):
    data_mode: Literal["live", "mock"]
    mock_api_url: str
    bdcs: dict[str, str]
    env: EnvNames
    paths: Paths
    ingest: IngestConfig
    scoring: ScoringConfig
    server: ServerConfig
    fred_series: dict[str, str]


def load_env_file(path: Path) -> None:
    """Fill in variables that are missing or blank in the shell."""
    for name, value in dotenv_values(path).items():
        if value and not read_env(name):
            os.environ[name] = value


@lru_cache
def load_config() -> Config:
    """Read config.yaml once and make its paths absolute."""
    load_env_file(ROOT / ".env")
    raw = yaml.safe_load((ROOT / "config.yaml").read_text())
    raw["paths"] = {name: ROOT / path for name, path in raw["paths"].items()}
    mode = (read_env("DATA_MODE") or "live").lower()
    if mode not in ("live", "mock"):
        raise ValueError(f"DATA_MODE is '{mode}'. Valid values: live, mock.")
    if mode == "mock":
        raw["paths"]["database"] = raw["paths"]["mock_database"]
    return Config.model_validate(raw | {"data_mode": mode})


def read_env(name: str) -> str | None:
    """Return an environment value, treating blank as unset."""
    return os.environ.get(name, "").strip() or None
