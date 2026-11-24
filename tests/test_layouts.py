"""Regression tests on small SYNTHETIC tables that imitate layout problems. Not real filings."""

import httpx
import pytest

from config import load_env_file, read_env
from ingest import run
from ingest.document import load_document
from ingest.edgar_client import EdgarClient, EdgarError
from ingest.footnotes import says_non_accrual, says_pik
from ingest.markers import bare_markers, bracketed_markers
from ingest.parse_html import parse_html
from ingest.parse_xbrl import parse_xbrl
from tests.conftest import SEPT
from tests.test_parsers import filing

LEGEND = "<p>(8) Loan was on non-accrual status.</p>"
PLAIN = ("Company", "Investment", "Coupon", "Reference", "Spread", "Cost", "Fair Value", "")
FSK_LIKE = ("Portfolio Company", "Footnotes", "Industry", "Rate", "Maturity", "Cost", "Fair Value")


def line(*cells: str) -> str:
    return "<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>"


def parse(header: tuple, *rows: str, legend: str = LEGEND):
    body = (f"<html><body><p>Schedule of Investments</p><p>September 30, 2026</p><p>(in thousands)"
            f"</p><table>{line(*header)}{''.join(rows)}</table>{legend}</body></html>")
    return parse_html(load_document(body.encode()), SEPT)


def loan(name="Fixture A", coupon="", marks="") -> str:
    return line(name, "First lien", coupon, "SOFR (Q)", "5.75%", "10", "9", marks)


@pytest.mark.parametrize("coupon", ["10.07% (2.88% PIK)", "9.60% (incl. 2.00% PIK)"])
def test_pik_in_the_coupon_column_is_kept(coupon):
    with_pik, without = parse(PLAIN, loan(coupon=coupon), loan("Fixture B", "9%")).loans
    assert with_pik.is_pik is True and coupon in with_pik.rate_text and with_pik.spread_bps == 575
    assert without.is_pik is False


def test_pik_is_unknown_when_the_filing_never_mentions_it():
    assert parse(PLAIN, loan(coupon="9%")).loans[0].is_pik is None


def test_xbrl_loan_without_a_readable_row_has_unknown_flags():
    content = filing().replace(b"Amortized Cost", b"Amount").replace(b"Fair Value</td>", b"Worth</td>")
    loans = parse_xbrl(load_document(content), SEPT).loans
    assert len(loans) == 3
    assert {(item.is_pik, item.is_non_accrual) for item in loans} == {(None, None)}


@pytest.mark.parametrize("marks", [
    "(2)(8)", "(2, 8)", "<sup>8</sup>", "<sup>2, 8</sup>", "(<sup>8</sup>)", "[8]", "2, 8", "(8)*"])
def test_marker_styles(marks):
    flagged, plain = parse(PLAIN, loan(marks=marks), loan("Fixture B", marks="(2)")).loans
    assert (flagged.is_non_accrual, plain.is_non_accrual) == (True, False)


def test_page_number_does_not_hide_a_bracketed_legend_entry():
    legend = "<p>8 FIXTURE CAPITAL CORPORATION</p>" + LEGEND
    assert parse(PLAIN, loan(marks="(8)"), legend=legend).loans[0].is_non_accrual is True


def test_unbracketed_legend_is_used_when_there_is_no_other():
    legend = "<p><sup>8</sup> Loan was on non-accrual status.</p>"
    assert parse(PLAIN, loan(marks="8"), legend=legend).loans[0].is_non_accrual is True
    legend = "<p>8 Loan was on non-accrual status.</p>"
    assert parse(PLAIN, loan(marks="8"), legend=legend).loans[0].is_non_accrual is True


def test_thousands_are_not_markers():
    assert bracketed_markers("(8,125)") == set() and bare_markers("8,125") == set()
    assert bracketed_markers("(2,8)") == bracketed_markers("(2, 8)") == {"2", "8"}


def test_marker_glued_to_an_amount():
    row = line("Fixture A", "First lien", "", "SOFR", "5%", "10", "9<sup>8</sup>", "")
    found = parse(PLAIN, row).loans[0]
    assert (found.fair_value, found.is_non_accrual) == (9000, True)


def test_numbered_borrower_rows_are_not_read_as_legend():
    row = loan("3 Fixture Sports LLC", "9% PIK")
    assert parse(PLAIN, row, loan("Fixture B", "9%", "(3)")).loans[1].is_pik is False


def test_industry_column_wins_and_asset_class_resets_the_instrument():
    loans = parse(
        FSK_LIKE, line("Senior Secured Loans—First Lien—100.1%"),
        line("Fixture A", "(x)", "Software", "SF+575", "1/1/2029", "10", "9"),
        line("Asset Based Finance—22.0%"),
        line("Fixture Fund, Private Equity", "(g)", "Capital Goods", "", "", "10", "9"),
        line("Fixture Fund", "(g)", "Capital Goods", "", "", "10", "9")).loans
    assert [(item.industry, item.instrument) for item in loans] == [
        ("Software", "First lien"), ("Capital Goods", "Equity"), ("Capital Goods", "Other")]


def test_stray_one_cell_row_keeps_the_section_instrument():
    loans = parse(FSK_LIKE, line("Senior Secured Loans—First Lien"), line("United States"),
                  line("Fixture A", "", "Software", "Fixed", "1/1/2029", "10", "9")).loans
    assert (loans[0].instrument, loans[0].industry) == ("First lien", "Software")


def test_total_row_with_only_numbers_is_a_subtotal():
    header = ("Company", "Investment", "Principal", "Cost", "Fair Value", "")
    loans = parse(header, line("Fixture A", "First lien", "1,000", "1,000", "1,000", ""),
                  line("Total Fixture Group", "", "5", "5", "5", ""),
                  line("", "Total first lien", "5", "5", "5", "")).loans
    assert [item.borrower_raw for item in loans] == ["Fixture A"]


@pytest.mark.parametrize("heading", [
    "<p>FIXTURE CORP CONSOLIDATED SCHEDULE OF INVESTMENTS December 31, 2025</p>",
    "<p>FIXTURE CORP CONSOLIDATED SCHEDULE OF INVESTMENTS AS OF DECEMBER 31, 2025</p>",
    "<p>Schedule of Investments</p>"])
def test_prior_or_undated_schedule_does_not_merge(heading):
    prior = f"{LEGEND}{heading}<table>{line(*PLAIN)}{loan('Fixture Old')}</table>"
    assert [item.borrower_raw for item in parse(PLAIN, loan(), legend=prior).loans] == ["Fixture A"]


def test_company_name_heading_and_date_in_one_block():
    body = (f"<html><body><p>FIXTURE CORP Schedule of Investments September 30, 2026</p>"
            f"<table>{line(*PLAIN)}{loan()}</table></body></html>")
    assert len(parse_html(load_document(body.encode()), SEPT).loans) == 1


def test_net_and_adjustment_lines_are_not_loans_and_the_gap_stays_visible():
    parsed = parse(
        FSK_LIKE, line("Senior Secured Loans—First Lien"),
        line("Fixture A", "(x)", "Software", "SF+575", "1/1/2029", "100", "100"),
        line("Unfunded Loan Commitments", "", "", "", "", "(5)", "(5)"),
        line("Net Senior Secured Loans—First Lien", "", "", "", "", "95", "95"),
        line("TOTAL INVESTMENTS—150%", "", "", "", "", "95", "95"),
        line("Net Assets", "", "", "", "", "", "70"))
    assert [item.borrower_raw for item in parsed.loans] == ["Fixture A"]
    assert parsed.filed_total == 95_000 and not parsed.reconciles(0.01)


def test_borrowers_named_total_or_net_are_kept():
    loans = parse(PLAIN, loan("Total Community Fixture LLC"), loan("Net Fixture Inc")).loans
    assert len(loans) == 2


def test_continued_page_without_a_date_keeps_the_period():
    page = line(*PLAIN) + loan("Fixture B")
    parsed = parse(PLAIN, loan(), legend=(
        "<p>See the consolidated schedule of investments as of December 31, 2025</p>"
        f"<p>Schedule of Investments (continued)</p><table>{page}</table>{LEGEND}"))
    assert len(parsed.loans) == 2


def test_equity_units_are_not_scaled_into_principal():
    header = ("Company", "Investment", "Par / Units", "Cost", "Fair Value", "")
    parsed = parse(header, line("Fixture A", "Class A units", "500", "10", "9", ""),
                   line("Fixture B", "First lien", "500", "10", "9", ""))
    assert [item.principal for item in parsed.loans] == [None, 500_000]


def test_renamed_xbrl_concepts():
    content = filing().replace(b"OwnedAtFairValue", b"OwnedFairValue").replace(b"OwnedAtCost", b"OwnedCost")
    parsed = parse_xbrl(load_document(content), SEPT)
    assert len(parsed.loans) == 3 and parsed.reconciles(0.01)


@pytest.mark.parametrize("text, expected", [
    ("Asset is not income producing and is on non-accrual status", True),
    ("No interest is recognized as the loan is on non-accrual", True),
    ("Loan is no longer on non-accrual status", False),
    ("Loan was removed from non-accrual status", False),
    ("Interest is excluded from non-accrual", False),
    ("Loan may be placed on non-accrual", False),
    ("The loan is not on non-accrual status", False), ("None of the loans were on non-accrual", False)])
def test_non_accrual_wording(text, expected):
    assert says_non_accrual(text) is expected


@pytest.mark.parametrize("text, expected", [
    ("The issuer has the option to pay PIK; currently paying PIK", True),
    ("Interest is paid in cash or PIK at the option of the borrower", False),
    ("Currently paid in cash; may be PIK at borrower's option", False),
    ("Interest may be paid in cash or PIK; interest is paid in cash", False),
    ("May pay PIK; not currently paying PIK", False), ("The issuer may pay PIK", False), ("PIK security as of May 31, 2026", True)])
def test_pik_wording_in_legends(text, expected):
    assert says_pik(text) is expected


def html_client(config, monkeypatch) -> EdgarClient:
    """A client whose only transport is a local stub; the contact is a fixture and never sent."""
    monkeypatch.setenv(config.env.sec_user_agent, "fixture@example.invalid")
    stub = httpx.MockTransport(lambda request: httpx.Response(200, text="<html>maintenance</html>"))
    return EdgarClient(config, transport=stub)


def test_non_json_from_sec_is_an_error_per_bdc_not_a_crash(config, monkeypatch, capsys):
    client = html_client(config, monkeypatch)
    with pytest.raises(EdgarError, match="did not return JSON"):
        client.lookup_cik("ARCC")
    monkeypatch.setattr(run, "load_config", lambda: config)
    monkeypatch.setattr(run, "EdgarClient", lambda _: client)
    monkeypatch.setattr("sys.argv", ["run", "--bdc", "ARCC", "FSK"])
    assert run.main() == 1
    printed = capsys.readouterr().out
    assert "ARCC  FAIL" in printed and "FSK   FAIL" in printed


def test_blank_shell_variable_does_not_block_env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("FIXTURE_NAME=from-file\nFIXTURE_SET=from-file\n")
    monkeypatch.setenv("FIXTURE_NAME", " ")
    monkeypatch.setenv("FIXTURE_SET", "from-shell")
    load_env_file(path)
    assert (read_env("FIXTURE_NAME"), read_env("FIXTURE_SET")) == ("from-file", "from-shell")
