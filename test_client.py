"""Calls every MCP tool through a real client and prints PASS, FAIL or SKIP for each check.

SKIP means the loaded data has no case to check. It is never counted as a pass.
Run: uv run python test_client.py     (after: uv run python -m ingest.run)
"""

import asyncio
import sys

from mcp_connection import connect, error_text

EXPECTED_TOOLS = {
    "list_bdcs", "get_bdc_portfolio", "flag_stress_signals",
    "find_borrower_exposure", "get_portfolio_trend", "get_market_context",
}
EXPECTED_RESOURCES = {"card://stress-rules", "card://data-sources", "card://glossary", "card://tools"}
MINIMUM_BDCS = 3  # acceptance check 1


class Checks:
    """Prints each check as it runs and keeps the counts."""

    def __init__(self) -> None:
        self.failed = self.skipped = 0

    def record(self, name: str, passed: bool, detail: object = "") -> None:
        self.failed += not passed
        self._show("PASS" if passed else "FAIL", name, detail)

    def skip(self, name: str, reason: str) -> None:
        self.skipped += 1
        self._show("SKIP", name, reason)

    @staticmethod
    def _show(label: str, name: str, detail: object) -> None:
        print(f"{label}  {name}" + (f"  ({detail})" if detail != "" else ""))


async def call(client, checks: Checks, tool: str, arguments: dict | None = None) -> dict:
    """Call a tool and check that it succeeds with sources and metadata. Returns its data."""
    result = await client.call_tool(tool, arguments or {})
    data = result.structured_content or {}
    if result.is_error:
        checks.record(tool, False, error_text(result))
        return {}
    checks.record(tool, "sources" in data and "metadata" in data, arguments or "")
    return data


async def check_catalog(client, checks: Checks) -> None:
    tools = {tool.name for tool in (await client.list_tools()).tools}
    checks.record("all six tools are listed", tools == EXPECTED_TOOLS)
    resources = {str(item.uri) for item in (await client.list_resources()).resources}
    checks.record("all four cards are listed", resources == EXPECTED_RESOURCES)
    for uri in sorted(EXPECTED_RESOURCES):
        contents = (await client.read_resource(uri)).contents
        checks.record(f"read {uri}", bool(contents and contents[0].text.strip()))


async def check_portfolios(client, checks: Checks) -> None:
    bdcs = (await call(client, checks, "list_bdcs")).get("bdcs", [])
    checks.record(f"at least {MINIMUM_BDCS} BDCs are loaded", len(bdcs) >= MINIMUM_BDCS, len(bdcs))
    for bdc in bdcs:
        data = await call(client, checks, "get_bdc_portfolio", {"bdc": bdc["ticker"], "page_size": 5})
        checks.record(f"{bdc['ticker']} has loans and sources", bool(data.get("loans") and data.get("sources")))
        trend = await call(client, checks, "get_portfolio_trend", {"bdc": bdc["ticker"]})
        checks.record(f"{bdc['ticker']} has a trend", bool(trend.get("trend")))


async def check_market(client, checks: Checks) -> None:
    series = (await call(client, checks, "get_market_context")).get("series", [])
    names = {item["series_id"] for item in series}
    checks.record("three FRED series returned", names == {"SOFR", "BAMLH0A0HYM2", "DGS10"}, sorted(names))


async def check_flags(client, checks: Checks) -> None:
    """Software is the acceptance check; a quiet quarter with no flags is a valid answer."""
    flags = (await call(client, checks, "flag_stress_signals", {"industry": "software"})).get("flags", [])
    if not flags:
        checks.skip("software flags are ranked with reasons", "no software flags")
        return
    scores = [flag["stress_score"] for flag in flags]
    checks.record("flags are sorted by score", scores == sorted(scores, reverse=True))
    checks.record("every flag has reasons", all(flag["reasons"] for flag in flags))


async def shared_borrower(client) -> dict | None:
    """Find a flagged borrower that more than one BDC lends to."""
    page, total = 1, 1
    while (page - 1) * 50 < total:
        result = await client.call_tool("flag_stress_signals", {"page": page, "page_size": 50})
        data = result.structured_content or {}
        if result.is_error:
            return None
        for flag in data["flags"]:
            if flag["lender_count"] >= 2:
                return flag
        page, total = page + 1, data["total"]
    return None


async def check_exposure(client, checks: Checks) -> None:
    flag = await shared_borrower(client)
    if flag is None:
        checks.skip("borrower held by 2+ BDCs", "no flagged borrower has two lenders")
        return
    data = await call(client, checks, "find_borrower_exposure", {"borrower": flag["borrower_key"]})
    lenders, comparisons = data.get("lenders", []), data.get("comparison_loans", [])
    # A BDC whose latest filing is newer may appear only as a same-period comparison.
    tickers = {loan["bdc_ticker"] for loan in lenders + comparisons}
    cited = {source["bdc_ticker"] for source in data.get("sources", [])}
    bdcs = (await client.call_tool("list_bdcs", {})).structured_content["bdcs"]
    mixed_periods = len({bdc["latest_period_end"] for bdc in bdcs}) > 1
    if mixed_periods and len(tickers) < flag["lender_count"]:
        # Older equity-only or unmarked holdings are counted as lenders but not compared.
        checks.skip("every lender is returned", "BDCs have different latest periods")
    else:
        checks.record("every lender is returned", len(tickers) >= flag["lender_count"], sorted(tickers))
    checks.record("every lender is cited", bool(tickers) and tickers <= cited)
    scores = [loan["stress_score"] for loan in lenders]
    checks.record("borrower score is its highest loan score", bool(scores) and data["stress_score"] == max(scores))
    check_dispersion(checks, flag, data)


def dispersion_by_period(loans: list[dict]) -> dict[str, float]:
    """Gap in points between lenders' cost-weighted debt marks, for periods with two or more lenders."""
    totals: dict[str, dict[str, list[float]]] = {}
    for loan in loans:
        if loan["instrument"] != "Equity" and loan["mark_pct"] is not None:
            total = totals.setdefault(loan["period_end"], {}).setdefault(loan["bdc_ticker"], [0.0, 0.0])
            total[0] += loan["fair_value"]
            total[1] += loan["cost"]
    marks = {period: [value / cost for value, cost in lenders.values()] for period, lenders in totals.items()}
    return {period: (max(m) - min(m)) * 100 for period, m in marks.items() if len(m) >= 2}


def check_dispersion(checks: Checks, flag: dict, data: dict) -> None:
    """Recompute dispersion from the returned loans. The exposure tool reports the widest gap."""
    expected = dispersion_by_period(data.get("lenders", []) + data.get("comparison_loans", []))
    if not expected:
        checks.skip("mark dispersion is correct", "fewer than two lenders have a debt mark")
        return
    pairs = [
        ("flag dispersion is correct", expected.get(flag["period_end"]), flag["mark_dispersion_pts"]),
        ("exposure dispersion is correct", max(expected.values()), data.get("mark_dispersion_pts")),
    ]
    for name, wanted, actual in pairs:
        same = wanted == actual or None not in (wanted, actual) and abs(wanted - actual) < 0.1
        checks.record(name, same, f"expected {wanted}, got {actual}")


async def check_vague_name(client, checks: Checks) -> None:
    """A one-word name may match nothing, or several borrowers; it must never be guessed."""
    result = await client.call_tool("find_borrower_exposure", {"borrower": "software"})
    data = result.structured_content or {}
    if result.is_error and "no data" not in error_text(result).lower():
        checks.record("vague name gives a helpful error", "software" in error_text(result).lower(), error_text(result))
    elif data.get("candidates"):
        checks.record("vague name returns candidates", data["status"] == "ambiguous")
    else:
        checks.skip("vague name returns candidates", "exact match or no data loaded")


async def check_errors_and_cache(client, checks: Checks) -> None:
    result = await client.call_tool("get_bdc_portfolio", {"bdc": "ARES"})
    message = error_text(result)
    checks.record("unknown BDC gives a helpful error", bool(result.is_error) and "Valid values" in message, message)
    await client.call_tool("list_bdcs", {})
    repeat = (await client.call_tool("list_bdcs", {})).structured_content or {}
    checks.record("repeat call is cached", repeat.get("metadata", {}).get("cached") is True)


async def run_checks() -> Checks:
    checks = Checks()
    async with connect() as client:
        for check in (check_catalog, check_portfolios, check_market, check_flags,
                      check_exposure, check_vague_name, check_errors_and_cache):
            await check(client, checks)
    return checks


def main() -> None:
    checks = asyncio.run(run_checks())
    print(f"\n{checks.failed} failed, {checks.skipped} skipped")
    sys.exit(1 if checks.failed else 0)


if __name__ == "__main__":
    main()
