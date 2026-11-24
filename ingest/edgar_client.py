"""Rate-limited SEC EDGAR client with retries and an on-disk cache of filing documents."""

import hashlib
import json
import time
from datetime import date

import httpx

from config import Config, read_env
from ingest.models import FilingRef

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/"
ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
FORMS = {"10-K", "10-Q"}


class EdgarError(RuntimeError):
    """EDGAR could not be used; the message says how to fix it."""


class EdgarClient:
    def __init__(self, config: Config, transport: httpx.BaseTransport | None = None) -> None:
        name = config.env.sec_user_agent
        user_agent = read_env(name)
        if not user_agent or "@" not in user_agent:
            raise EdgarError(
                f"{name} is not set. The SEC requires a contact name and email on every request. "
                f"Copy .env.example to .env and set {name} to your own name and email."
            )
        self._settings = config.ingest
        self._cache_dir = config.paths.edgar_cache
        self._last_request = 0.0
        self._http = httpx.Client(
            headers={"User-Agent": user_agent}, timeout=60, follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def lookup_cik(self, ticker: str) -> int:
        """Ticker to CIK, from the SEC's own list."""
        for company in self._get_json(TICKERS_URL).values():
            if company["ticker"].upper() == ticker:
                return int(company["cik_str"])
        raise EdgarError(f"{ticker} is not in the SEC ticker list ({TICKERS_URL}).")

    def recent_filings(self, ticker: str, cik: int) -> list[FilingRef]:
        """The newest 10-K/10-Q filings, one per quarter end, newest first."""
        wanted = self._settings.quarter_ends
        listing = self._get_json(f"{SUBMISSIONS_URL}CIK{cik:010d}.json")["filings"]
        by_period = filings_by_period(listing["recent"], ticker, cik)
        for older in listing.get("files", []):
            if len(by_period) >= wanted:
                break
            page = self._get_json(SUBMISSIONS_URL + older["name"])
            by_period = filings_by_period(page, ticker, cik) | by_period
        newest = sorted(by_period, reverse=True)[:wanted]
        return [by_period[period] for period in newest]

    def fetch_document(self, filing: FilingRef) -> bytes:
        """Download a filing document, or reuse the copy on disk. Filed documents never change."""
        suffix = hashlib.sha256(filing.source_url.encode()).hexdigest()[:12]
        path = self._cache_dir / f"{filing.bdc_ticker}_{filing.filing_id}_{suffix}.htm"
        if path.exists():
            return path.read_bytes()
        content = self._get(filing.source_url)
        path.parent.mkdir(parents=True, exist_ok=True)
        partial = path.with_suffix(".part")
        partial.write_bytes(content)
        partial.replace(path)
        return content

    def _get_json(self, url: str) -> dict:
        try:
            data = json.loads(self._get(url))
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise EdgarError(f"EDGAR did not return JSON data: {url}. Try again later.")
        return data

    def _get(self, url: str) -> bytes:
        problem = "no attempt made"
        for attempt in range(self._settings.max_retries):
            if attempt:
                time.sleep(2 ** (attempt - 1))
            self._wait_for_turn()
            try:
                response = self._http.get(url)
            except httpx.TransportError as error:
                problem = type(error).__name__
                continue
            if response.status_code == 200:
                return response.content
            problem = f"HTTP {response.status_code}"
            if response.status_code != 429 and response.status_code < 500:
                break
        hint = " Check that SEC_USER_AGENT is a real contact." if problem == "HTTP 403" else ""
        raise EdgarError(f"EDGAR request failed ({problem}): {url}.{hint}")

    def _wait_for_turn(self) -> None:
        ready_at = self._last_request + self._settings.min_request_interval_seconds
        time.sleep(max(0.0, ready_at - time.monotonic()))
        self._last_request = time.monotonic()


def filings_by_period(page: dict, ticker: str, cik: int) -> dict[date, FilingRef]:
    """Turn one page of the submissions API into filings keyed by period end. Amendments are skipped."""
    result: dict[date, FilingRef] = {}
    columns = ("accessionNumber", "form", "reportDate", "filingDate", "primaryDocument")
    for accession, form, report, filed, document in zip(*(page[name] for name in columns)):
        if form not in FORMS or not report:
            continue
        period_end = date.fromisoformat(report)
        folder = accession.replace("-", "")
        result.setdefault(period_end, FilingRef(
            filing_id=accession, bdc_ticker=ticker, form_type=form, period_end=period_end,
            filed_date=date.fromisoformat(filed),
            source_url=f"{ARCHIVES_URL}/{cik}/{folder}/{document}",
        ))
    return result
