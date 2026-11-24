"""Tools over the real MCP protocol (in memory), on synthetic data in a temp database."""

import asyncio
from dataclasses import replace
from datetime import date

import httpx
import pytest
from mcp import Client

from ingest.fred_client import FredError, fetch_series
from ingest.load import load_filing, load_rates
from server import market
from server.main import build_server
from tests.conftest import make_filing, make_loan


def call_many(config, *calls):
    """Run several calls against one server, so they share its cache."""
    async def run():
        async with Client(build_server(config)) as client:
            results = []
            for step in calls:
                if callable(step):
                    step()
                else:
                    results.append(await client.call_tool(*step))
            return results
    return asyncio.run(run())


def call(config, tool, **arguments):
    result = call_many(config, (tool, arguments))[0]
    assert not result.is_error, result.content[0].text
    return result.structured_content


def error(config, tool, **arguments) -> str:
    result = call_many(config, (tool, arguments))[0]
    assert result.is_error
    return result.content[0].text


def test_schemas_and_resources(config):
    async def run():
        async with Client(build_server(config)) as client:
            tools = (await client.list_tools()).tools
            rules = await client.read_resource("card://stress-rules")
            guide = await client.read_resource("card://tools")
            return tools, rules.contents[0].text, guide.contents[0].text
    tools, rules, guide = asyncio.run(run())
    assert [tool.name for tool in tools] == [
        "list_bdcs", "get_bdc_portfolio", "flag_stress_signals", "find_borrower_exposure",
        "get_portfolio_trend", "get_market_context"]
    for tool in tools:
        assert tool.annotations.read_only_hint is True
        assert {"sources", "metadata"} <= tool.output_schema["properties"].keys()
    assert "+4" in rules and "docs/TOOLS.md" in guide


@pytest.mark.parametrize("tool, arguments", [
    ("list_bdcs", {}), ("get_bdc_portfolio", {"bdc": "ARCC"}), ("flag_stress_signals", {}),
    ("find_borrower_exposure", {"borrower": "x"}), ("get_portfolio_trend", {"bdc": "ARCC"})])
def test_no_database_says_how_to_fix_it(config, tool, arguments):
    assert "uv run python -m ingest.run" in error(config, tool, **arguments)


def test_list_bdcs(loaded):
    result = call(loaded, "list_bdcs")
    assert result["bdcs"][0]["periods_available"] == ["2026-06-30", "2026-09-30"]
    assert result["not_loaded"] == ["FSK", "BXSL"]
    assert len(result["sources"]) == 3


@pytest.mark.parametrize("tool", ["get_bdc_portfolio", "flag_stress_signals", "get_portfolio_trend"])
def test_unknown_bdc(loaded, tool):
    message = error(loaded, tool, bdc="ARES")
    assert "Unknown BDC 'ARES'. Valid values: ARCC, OBDC, FSK, BXSL." in message


def test_portfolio_filters_and_summary(loaded):
    result = call(loaded, "get_bdc_portfolio", bdc="arcc", industry="SOFT", instrument="first-lien")
    assert (result["total"], result["as_of"]) == (2, "2026-09-30")
    assert result["summary"] == {
        "total_cost": 200, "total_fair_value": 173, "pik_loan_count": 1,
        "non_accrual_loan_count": 1, "non_accrual_pct_of_cost": 0.5}


def test_pagination(loaded):
    pages = [call(loaded, "get_bdc_portfolio", bdc="ARCC", page=n, page_size=2) for n in (1, 2, 3, 4)]
    assert [len(page["loans"]) for page in pages] == [2, 2, 1, 0]
    assert {page["total"] for page in pages} == {5}
    assert len({str(loan) for page in pages for loan in page["loans"]}) == 5
    assert "less than or equal to 50" in error(loaded, "get_bdc_portfolio", bdc="ARCC", page_size=51)


def test_as_of_uses_latest_period_on_or_before(loaded):
    assert call(loaded, "get_bdc_portfolio", bdc="ARCC", as_of="2026-08-15")["as_of"] == "2026-06-30"
    message = error(loaded, "get_bdc_portfolio", bdc="ARCC", as_of="2020-01-01")
    assert "2026-06-30, 2026-09-30" in message


def test_flags_are_sorted_with_reasons_and_sources(loaded):
    result = call(loaded, "flag_stress_signals", industry="software")
    assert [(f["borrower"], f["stress_score"]) for f in result["flags"]] == [
        ("Troubled Fixture LLC", 8), ("Shared Fixture Holdings, LLC", 6)]
    assert result["flags"][0]["reasons"] == ["On non-accrual", "PIK interest", "Marked at 78% of cost"]
    assert all(flag["reasons"] for flag in result["flags"]) and result["sources"]


def test_flag_dispersion_uses_other_lenders_even_when_filtered(loaded):
    flag = call(loaded, "flag_stress_signals", bdc="OBDC")
    assert (flag["flags"][0]["mark_dispersion_pts"], flag["flags"][0]["lender_count"]) == (20, 2)
    assert {source["bdc_ticker"] for source in flag["sources"]} == {"ARCC", "OBDC"}


def test_flag_filters(loaded):
    assert call(loaded, "flag_stress_signals", signals=["non_accrual"])["total"] == 1
    assert call(loaded, "flag_stress_signals", min_score=7)["total"] == 1
    assert call(loaded, "flag_stress_signals", mark_threshold=0.96, signals=["markdown"])["total"] == 3


def test_exposure_lists_every_lender(loaded):
    result = call(loaded, "find_borrower_exposure", borrower="Shared Fixture Inc")
    assert {lender["bdc_ticker"] for lender in result["lenders"]} == {"ARCC", "OBDC"}
    assert (result["mark_dispersion_pts"], result["stress_score"]) == (20, 6)
    assert len(result["lenders"]) == 3 and len(result["sources"]) == 2


def test_ambiguous_borrower_returns_candidates_and_a_key_resolves(loaded):
    result = call(loaded, "find_borrower_exposure", borrower="fixture")
    assert result["status"] == "ambiguous" and len(result["candidates"]) == 4
    key = result["candidates"][0]["borrower_key"]
    assert call(loaded, "find_borrower_exposure", borrower=key)["status"] == "found"


def test_close_spelling_offers_one_candidate_and_no_match_is_an_error(loaded):
    result = call(loaded, "find_borrower_exposure", borrower="Trubled Fixture")
    assert (result["status"], result["total_candidates"], result["lenders"]) == ("ambiguous", 1, [])
    assert "No borrower matches" in error(loaded, "find_borrower_exposure", borrower="zzzz")


def test_trend(loaded):
    trend = call(loaded, "get_portfolio_trend", bdc="ARCC")["trend"]
    assert [row["period_end"] for row in trend] == ["2026-06-30", "2026-09-30"]
    assert trend[1]["non_accrual_pct_of_cost"] == 0.25
    assert (trend[1]["loans_marked_below_90_pct"], trend[1]["avg_mark_pct"]) == (1, 0.91)


def test_unknown_flags_give_null_share(loaded):
    trend = call(loaded, "get_portfolio_trend", bdc="OBDC")["trend"]
    assert trend[0]["non_accrual_pct_of_cost"] is None


def test_cached_then_cleared_after_new_load(loaded):
    def reload():
        load_filing(loaded.paths.database, make_filing("FSK"), "xbrl", [make_loan()])
    results = call_many(loaded, ("list_bdcs", {}), ("list_bdcs", {}), reload, ("list_bdcs", {}))
    first, repeat, after = (result.structured_content for result in results)
    assert [r["metadata"]["cached"] for r in (first, repeat, after)] == [False, True, False]
    assert after["metadata"]["data_version"] > first["metadata"]["data_version"]
    assert len(after["bdcs"]) == 3


def test_same_instrument_tranches_keep_unique_ids(config):
    load_filing(config.paths.database, make_filing("ARCC"), "xbrl", [make_loan(), make_loan()])
    assert call(config, "get_bdc_portfolio", bdc="ARCC")["total"] == 2


def test_market_context_change_and_cache(config, monkeypatch):
    def fake_series(config, series_id, start):
        return [(start, 4.0), (date.today(), 4.5)]
    monkeypatch.setattr(market, "fetch_series", fake_series)
    results = call_many(config, ("get_market_context", {}), ("get_market_context", {}))
    first, repeat = (result.structured_content for result in results)
    assert first["series"][0]["change"] == 0.5 and first["data_origin"] == "fred_live"
    assert repeat["metadata"] == {"cached": True, "data_mode": "live", "data_version": None}


def test_market_context_without_key_or_rates(config):
    assert "FRED_API_KEY is not set" in error(config, "get_market_context")
    with pytest.raises(FredError):
        market.fetch_series(config, "SOFR", date.today())


def test_candidate_keys_resolve_to_the_same_company(config):
    load_filing(config.paths.database, make_filing("ARCC"), "xbrl", [
        make_loan("Alpha, Inc. d.b.a. Bar Group"), make_loan("Alpha Bar Group LLC"),
        make_loan("Bar Group Ltd doing business as Alpha"), make_loan("---"), make_loan("***")])
    loans = call(config, "get_bdc_portfolio", bdc="ARCC")["loans"]
    assert len({loan["borrower_key"] for loan in loans}) == 5
    for loan in loans:
        found = call(config, "find_borrower_exposure", borrower=loan["borrower_key"])
        assert found["matched_names"] == [loan["borrower"]]


def test_trade_name_returns_a_candidate_never_found(config):
    load_filing(config.paths.database, make_filing("ARCC"), "xbrl", [
        make_loan("Fixture Buyer, LLC (dba Tradename)")])
    result = call(config, "find_borrower_exposure", borrower="tradename")
    assert (result["status"], len(result["candidates"])) == ("ambiguous", 1)


def test_peer_filing_used_for_marks_is_visible_and_cited(config):
    path, december = config.paths.database, date(2026, 12, 31)
    load_filing(path, make_filing("ARCC"), "xbrl", [make_loan("Late Fixture", value=95)])
    load_filing(path, make_filing("FSK"), "xbrl", [
        make_loan("Late Fixture", value=70), make_loan("Late Fixture", value=1, instrument="Equity")])
    load_filing(path, make_filing("FSK", december), "xbrl", [make_loan("Late Fixture", value=94)])
    result = call(config, "find_borrower_exposure", borrower="late fixture")
    assert (result["mark_dispersion_pts"], result["stress_score"]) == (25, 2)
    compared = result["comparison_loans"]
    assert [(loan["bdc_ticker"], loan["period_end"]) for loan in compared] == [("FSK", "2026-09-30")]
    cited = {(source["bdc_ticker"], source["period_end"]) for source in result["sources"]}
    shown = {(loan["bdc_ticker"], loan["period_end"]) for loan in result["lenders"] + compared}
    assert cited == shown and len(cited) == 3


def test_blank_optional_inputs_and_unknown_inputs(loaded):
    assert call(loaded, "flag_stress_signals", bdc=" ", as_of="")["total"] == 2
    assert "ticker" in error(loaded, "flag_stress_signals", ticker="ARCC")


def test_second_filing_for_a_period_replaces_the_first(loaded):
    again = replace(make_filing("ARCC"), filing_id="fixture-amended")
    load_filing(loaded.paths.database, again, "xbrl", [make_loan()])
    result = call(loaded, "list_bdcs")["bdcs"][0]
    assert (result["periods_available"], result["loan_count"]) == (["2026-06-30", "2026-09-30"], 1)


def test_score_uses_the_unrounded_mark(config):
    load_filing(config.paths.database, make_filing("ARCC"), "xbrl",
                [make_loan(cost=1_000_000, value=799_999.6)])
    flag = call(config, "flag_stress_signals")["flags"][0]
    assert flag["stress_score"] == 2 and flag["reasons"] == ["Marked at 79.99% of cost"]


def test_stored_rates_are_used_when_fred_returns_non_json(config, monkeypatch):
    reply = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>"))
    monkeypatch.setenv(config.env.fred_api_key, "fixture-not-a-key")
    monkeypatch.setattr(market, "fetch_series",
                        lambda cfg, name, start: fetch_series(cfg, name, start, transport=reply))
    for name in config.fred_series:
        load_rates(config.paths.database, name, [(date.today(), 4.0)])
    assert call(config, "get_market_context")["data_origin"] == "stored"
