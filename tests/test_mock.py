"""Mock mode: the CSV API, the loader, and separation from live data."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import config as settings
from ingest import run
from mock import api, load
from server import market
from server.db import Database
from server.portfolio import list_bdcs


@pytest.fixture
def http():
    return TestClient(api.app)


@pytest.fixture
def mock_config(config):
    paths = config.paths.model_copy(update={"database": config.paths.mock_database})
    return config.model_copy(update={"data_mode": "mock", "paths": paths})


def test_api_returns_json_and_filters(http):
    filings = http.get("/filings").json()
    assert len(filings) == 16
    rows = http.get("/loans", params={"filing_id": filings[0]["filing_id"]}).json()
    assert rows and {row["filing_id"] for row in rows} == {filings[0]["filing_id"]}
    assert None in {row["is_pik"] for row in rows}


def test_every_mock_row_is_labelled(http):
    assert all("Mock" in row["borrower_raw"] for row in http.get("/loans").json())
    assert all("example.invalid" in row["source_url"] for row in http.get("/filings").json())


def test_loader_fills_only_the_mock_database(config, mock_config, http):
    load.load_mock(config, http)
    assert not config.paths.database.exists()
    database = Database(mock_config.paths.database, 5)
    assert [bdc.ticker for bdc in list_bdcs(database, mock_config).bdcs] == list(config.bdcs)
    count = len(database.filings())
    load.load_mock(config, http)
    assert len(database.filings()) == count


def test_bad_row_is_named_and_old_data_kept(config, mock_config, http, tmp_path, monkeypatch):
    load.load_mock(config, http)
    for name in ("filings", "loans", "rates"):
        (tmp_path / f"{name}.csv").write_text((api.DATA / f"{name}.csv").read_text())
    bad = tmp_path / "loans.csv"
    bad.write_text(bad.read_text().replace(",40000000,40000000,", ",40000000,oops,", 1))
    monkeypatch.setattr(api, "DATA", tmp_path)
    with pytest.raises(load.MockDataError, match="loans.csv row 2, cost"):
        load.load_mock(config, http)
    assert Database(mock_config.paths.database, 5).filings()


def test_mock_mode_never_calls_fred(config, mock_config, http, monkeypatch):
    load.load_mock(config, http)
    monkeypatch.setenv(config.env.fred_api_key, "dummy")
    monkeypatch.setattr(market, "fetch_series", lambda *_: pytest.fail("FRED was called"))
    database = Database(mock_config.paths.database, 5)
    answer = market.get_market_context(database, mock_config, 365, today=date(2026, 11, 23))
    assert answer.data_origin == "mock"
    assert "stlouisfed" not in json.dumps(answer.model_dump(mode="json"))


def test_live_ingestion_refuses_mock_mode(mock_config, monkeypatch):
    monkeypatch.setattr(run, "load_config", lambda: mock_config)
    assert run.main() == 2


@pytest.mark.parametrize(("value", "file"), [("", "credit.duckdb"), ("mock", "mock.duckdb")])
def test_data_mode_picks_the_database(value, file, monkeypatch):
    monkeypatch.setenv("DATA_MODE", value)
    monkeypatch.setattr(settings, "load_env_file", lambda _: None)
    assert settings.load_config.__wrapped__().paths.database.name == file


def test_unknown_data_mode_is_an_error(monkeypatch):
    monkeypatch.setenv("DATA_MODE", "demo")
    with pytest.raises(ValueError, match="Valid values: live, mock"):
        settings.load_config.__wrapped__()
