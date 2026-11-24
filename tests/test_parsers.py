"""Parsers, on small SYNTHETIC documents that imitate filing layouts. Not excerpts of real filings."""

from datetime import date

import pytest

from ingest.document import load_document
from ingest.edgar_client import EdgarClient, EdgarError, filings_by_period
from ingest.parse_html import parse_html
from ingest.run import parse_filing
from tests.conftest import SEPT

NAMESPACES = ('xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="http://www.xbrl.org/2013/inlineXBRL" '
              'xmlns:xbrli="http://www.xbrl.org/2003/instance" '
              'xmlns:xbrldi="http://xbrl.org/2006/xbrldi"')
HEAD = ("<tr><td>Company (1)</td><td>Investment</td><td>Reference</td><td>Spread</td>"
        "<td>Maturity Date</td><td colspan='3'>Principal</td><td colspan='3'>Amortized Cost</td>"
        "<td colspan='3'>Fair Value</td><td></td></tr>")


def context(name: str, day: str, identifier: str = "", extra: str = "") -> str:
    member = (f'<xbrldi:typedMember dimension="us-gaap:InvestmentIdentifierAxis"><d>{identifier}</d>'
              "</xbrldi:typedMember>") if identifier else ""
    return (f'<xbrli:context id="{name}"><xbrli:entity><xbrli:segment>{member}{extra}'
            f"</xbrli:segment></xbrli:entity><xbrli:period><xbrli:instant>{day}</xbrli:instant>"
            "</xbrli:period></xbrli:context>")


def number(concept: str, ctx: str, text: str, sign: str = "") -> str:
    attribute = ' sign="-"' if sign else ""
    return (f'<ix:nonFraction name="us-gaap:{concept}" contextRef="{ctx}" scale="6"{attribute}>'
            f"{text}</ix:nonFraction>")


def row(ctx: str, company: str, wording: str, cost: str, value: str, marks: str = "",
        sign: str = "") -> str:
    left, right = ("(", ")") if sign else ("", "")
    return (f"<tr><td>{company}</td><td>{wording}</td><td>SOFR (Q)</td><td>5.75%</td>"
            f"<td>03/2029</td><td>$</td><td>{cost}</td><td></td>"
            f"<td>$</td><td>{number('InvestmentOwnedAtCost', ctx, cost)}</td><td></td>"
            f"<td>$</td><td>{left}{number('InvestmentOwnedAtFairValue', ctx, value, sign)}</td>"
            f"<td>{right}</td><td>{marks}</td></tr>")


def filing(total: str = "173.5", tag_loans: bool = True) -> bytes:
    jv = ('<xbrldi:explicitMember dimension="srt:ScheduleOfEquityMethodInvestmentEquityMethodInvestee'
          'NameAxis">x:JvMember</xbrldi:explicitMember>')
    contexts = [context("total", "2026-09-30"), context("jv", "2026-09-30", "JV Co, First lien", jv),
                context("old", "2025-12-31", "Alpha Fixture, Inc., First lien loan")]
    if tag_loans:
        contexts += [context("a", "2026-09-30", "Alpha Fixture, Inc., First lien loan"),
                     context("b", "2026-09-30", "Alpha Fixture, Inc., Revolver"),
                     context("c", "2026-09-30", "Beta Fixture LLC, Second lien loan")]
    return f"""<html {NAMESPACES}><body><div style="display:none">{''.join(contexts)}</div>
      <p>Consolidated Schedule of Investments</p><p>As of September 30, 2026</p>
      <p>(dollar amounts in millions)</p>
      <table>{HEAD}<tr><td colspan="15">Software and Services</td></tr>
      {row('a', 'Alpha Fixture, Inc. (4)', 'First lien senior secured loan', '100.0', '95.0', '(2)(11)')}
      {row('b', '', 'First lien revolving loan', '10.0', '1.5', '(2)', 'minus')}
      <tr><td colspan="8"></td><td>$</td><td>110.0</td><td></td><td>$</td><td>93.5</td><td></td><td></td></tr>
      {row('c', 'Beta Fixture LLC', 'Second lien loan', '100.0', '80.0', '(9)')}
      <tr><td>Total investments</td><td colspan="8"></td><td>210.0</td><td></td><td>$</td>
      <td>{number('InvestmentOwnedAtFairValue', 'total', total)}</td><td></td><td></td></tr></table>
      <p>(2) Pledged as collateral.</p><p>(9) Loan was on non-accrual status.</p>
      <p>(11) Includes a payment-in-kind provision.</p>
      <p>Consolidated Schedule of Investments</p><p>As of December 31, 2025</p>
      <table>{HEAD}{row('old', 'Alpha Fixture, Inc.', 'First lien loan', '50.0', '50.0')}</table>
      <p>Notes to Consolidated Financial Statements</p>
      <table>{HEAD}{row('jv', 'JV Co', 'First lien loan', '9.0', '9.0')}</table>
      </body></html>""".encode()


def check_loans(loans) -> None:
    assert [loan.borrower_raw for loan in loans] == [
        "Alpha Fixture, Inc.", "Alpha Fixture, Inc.", "Beta Fixture LLC"]
    assert [loan.fair_value for loan in loans] == [95e6, -1.5e6, 80e6]
    assert [loan.is_pik for loan in loans] == [True, False, False]
    assert [loan.is_non_accrual for loan in loans] == [False, False, True]
    first = loans[0]
    assert (first.industry, first.instrument) == ("Software and Services", "First lien")
    assert (first.rate_text, first.spread_bps) == ("SOFR (Q) + 5.75%", 575)
    assert (first.maturity_date, first.cost) == (date(2029, 3, 31), 100e6)


def test_xbrl_keeps_only_this_period_and_this_company():
    parsed = parse_filing(filing(), SEPT, 0.01)
    assert (parsed.method, parsed.filed_total) == ("xbrl", 173.5e6)
    check_loans(parsed.loans)


def test_html_fallback_when_loans_are_not_tagged():
    parsed = parse_filing(filing(tag_loans=False), SEPT, 0.01)
    assert parsed.method == "html" and parsed.reconciles(0.01)
    check_loans(parsed.loans)


def test_html_reads_its_own_total_and_units():
    parsed = parse_html(load_document(filing()), SEPT)
    assert parsed.filed_total == 173.5e6


def test_mismatch_is_refused():
    assert not parse_filing(filing(total="200.0"), SEPT, 0.01).reconciles(0.01)


def test_missing_legend_leaves_flags_unknown():
    content = filing()
    for marker in (b"(2) Pledged", b"(9) Loan", b"(11) Includes"):
        content = content.replace(marker, b"Note")
    loans = parse_filing(content, SEPT, 0.01).loans
    assert {loan.is_non_accrual for loan in loans} == {None}


def test_filing_list_keeps_10k_and_10q_only():
    page = {"accessionNumber": ["1-2-3", "1-2-4"], "form": ["10-Q", "8-K"],
            "reportDate": ["2026-09-30", "2026-09-30"], "filingDate": ["2026-10-28", "2026-10-28"],
            "primaryDocument": ["a.htm", "b.htm"]}
    found = filings_by_period(page, "ARCC", 5)
    assert found[SEPT].source_url.endswith("/5/123/a.htm") and len(found) == 1


def test_edgar_refuses_to_start_without_a_contact(config):
    with pytest.raises(EdgarError, match="SEC_USER_AGENT is not set"):
        EdgarClient(config)
