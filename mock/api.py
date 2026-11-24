"""Mock data API: serves the CSV files in mock/data as JSON. Every figure is invented.

Run: uv run uvicorn mock.api:app --host 127.0.0.1 --port 8001
"""

import csv
from pathlib import Path

from fastapi import FastAPI

DATA = Path(__file__).with_name("data")
Row = dict[str, str | None]
app = FastAPI(title="Mock Private Credit Data (not real filings)")


def read_rows(name: str, column: str, wanted: str | None) -> list[Row]:
    """Read a CSV file on every request, so edits show at once. Blank cells become null."""
    with open(DATA / f"{name}.csv", newline="", encoding="utf-8") as file:
        rows = [{key: value or None for key, value in row.items()} for row in csv.DictReader(file)]
    return [row for row in rows if wanted is None or row[column] == wanted]


@app.get("/filings")
def filings(bdc_ticker: str | None = None) -> list[Row]:
    return read_rows("filings", "bdc_ticker", bdc_ticker)


@app.get("/loans")
def loans(filing_id: str | None = None) -> list[Row]:
    return read_rows("loans", "filing_id", filing_id)


@app.get("/rates")
def rates(series_id: str | None = None) -> list[Row]:
    return read_rows("rates", "series_id", series_id)
