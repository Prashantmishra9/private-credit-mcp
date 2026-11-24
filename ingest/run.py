"""Ingestion entry point: uv run python -m ingest.run [--bdc ARCC ...]"""

import argparse
import sys
from datetime import date, timedelta

from config import Config, load_config
from ingest.aliases import refresh_aliases
from ingest.document import load_document
from ingest.edgar_client import EdgarClient, EdgarError
from ingest.fred_client import FredError, fetch_series
from ingest.load import load_filing, load_rates
from ingest.models import FilingRef, ParsedFiling
from ingest.parse_html import parse_html
from ingest.parse_xbrl import parse_xbrl

HEADINGS = ("BDC", "FORM", "PERIOD", "METHOD", "LOANS", "OUR FAIR VALUE", "FILED TOTAL", "GAP")
ROW = "{:5} {:5} {:11} {:7} {:>6} {:>18} {:>18} {:>8}  {}"


def parse_filing(content: bytes, period_end: date, tolerance: float) -> ParsedFiling:
    """XBRL first; HTML only if XBRL does not match the filing's own total."""
    root = load_document(content)
    tagged = parse_xbrl(root, period_end)
    if tagged.reconciles(tolerance):
        return tagged
    from_html = parse_html(root, period_end, tagged.filed_total)
    if from_html.reconciles(tolerance) or from_html.loans and not tagged.loans:
        return from_html
    return tagged


def ingest_filing(client: EdgarClient, config: Config, filing: FilingRef) -> bool:
    """Download, parse, check and load one filing. A filing that does not reconcile is not loaded."""
    tolerance = config.ingest.reconcile_tolerance
    parsed = parse_filing(client.fetch_document(filing), filing.period_end, tolerance)
    passed = parsed.reconciles(tolerance)
    if passed:
        load_filing(config.paths.database, filing, parsed.method, parsed.loans)
    print_result(filing, parsed, passed)
    return passed


def print_result(filing: FilingRef, parsed: ParsedFiling, passed: bool) -> None:
    total, gap = parsed.filed_total, parsed.difference()
    print(ROW.format(
        filing.bdc_ticker, filing.form_type, str(filing.period_end), parsed.method,
        len(parsed.loans), f"{parsed.total_fair_value:,.0f}",
        f"{total:,.0f}" if total else "not found", f"{gap:.2%}" if gap is not None else "n/a",
        "PASS" if passed else "FAIL (not loaded)"), flush=True)


def ingest_bdc(client: EdgarClient, config: Config, ticker: str) -> list[bool]:
    """Ingest one BDC. One bad filing does not stop the others."""
    results = []
    for filing in client.recent_filings(ticker, client.lookup_cik(ticker)):
        try:
            results.append(ingest_filing(client, config, filing))
        except Exception as error:      # report and continue with the next filing
            print(f"{ticker:5} {filing.form_type:5} {filing.period_end} FAIL (not loaded): "
                  f"{type(error).__name__}: {error}")
            results.append(False)
    return results


def ingest_rates(config: Config) -> bool:
    """Store every FRED series that can be read. One bad series does not stop the others."""
    settings, loaded = config.server, []
    start = date.today() - timedelta(days=settings.max_lookback_days + settings.holiday_buffer_days)
    for series_id in config.fred_series:
        try:
            values = fetch_series(config, series_id, start)
        except FredError as error:
            print(f"FRED {series_id} not loaded: {error}")
            continue
        load_rates(config.paths.database, series_id, values)
        print(f"FRED {series_id}: {len(values)} values loaded")
        loaded.append(series_id)
    return len(loaded) == len(config.fred_series)


def main() -> int:
    config = load_config()
    if config.data_mode == "mock":
        print("Cannot start: DATA_MODE is mock. Remove it to load real data.", file=sys.stderr)
        return 2
    parser = argparse.ArgumentParser(description="Load BDC filings and FRED rates into DuckDB")
    parser.add_argument("--bdc", nargs="+", choices=list(config.bdcs), default=list(config.bdcs))
    tickers = parser.parse_args().bdc
    try:
        client = EdgarClient(config)
    except EdgarError as error:
        print(f"Cannot start: {error}", file=sys.stderr)
        return 2
    print(ROW.format(*HEADINGS, "RESULT"))
    results: list[bool] = []
    for ticker in tickers:
        try:
            results += ingest_bdc(client, config, ticker)
        except Exception as error:      # report and continue with the next BDC
            print(f"{ticker:5} FAIL: {type(error).__name__}: {error}")
            results.append(False)
    client.close()
    rates_ok = ingest_rates(config)
    if any(results):
        pending = refresh_aliases(config.paths.database, config.ingest.fuzzy_match_threshold)
        print(f"{pending} fuzzy borrower matches await review: uv run python -m ingest.aliases list")
    print(f"{sum(results)} of {len(results)} filings loaded")
    return 0 if results and all(results) and rates_ok else 1


if __name__ == "__main__":
    sys.exit(main())
