"""Borrower names, instruments and rate wording."""

from datetime import date

import pytest

from ingest.aliases import refresh_aliases, review
from ingest.load import load_filing
from ingest.normalize import classify_instrument, fuzzy_pairs, normalize_name
from ingest.rates import parse_date, parse_number, parse_spread_bps, pik_in_text
from server.db import Database
from tests.conftest import make_filing, make_loan


@pytest.mark.parametrize("name", [
    "Fixture Co, LLC", "FIXTURE CO., INC.", "Fixture Holdings, L.P.", "Fixture (dba Other Name)",
    "Fixture Corp. d/b/a Other Name", "Fixture, Inc. d.b.a. Other Name", "Fixture/DBA Other Name",
    "Fixture LLC doing business as Other", "Fixture, Inc. (f.k.a. Other Inc.)", "Fixture L. P.",
    "Fixture (dba Other (US))"])
def test_names_normalize_to_one_key(name):
    assert normalize_name(name) == "fixture"


def test_three_letter_names_and_short_keys_are_kept():
    assert normalize_name("Fixture ABA Group") == "fixture aba group"
    assert normalize_name("Bank D B A Inc") == "bank d b a"
    assert normalize_name("---") != normalize_name("***")


def test_a_name_made_of_suffixes_is_not_emptied():
    assert normalize_name("Holdings, Inc.") == "holdings"


@pytest.mark.parametrize("text, label", [
    ("First lien senior secured loan", "First lien"), ("Second-lien term loan", "Second lien"),
    ("Unitranche first lien", "Unitranche"), ("Mezzanine", "Subordinated"),
    ("Class A common units", "Equity"), ("Earnout", "Equity"),
    ("Limited liability company interests", "Equity"), ("Revolver", "First lien"), ("???", "Other")])
def test_instruments(text, label):
    assert classify_instrument(text) == label


def test_fuzzy_pairs_need_different_lenders():
    names = {"A": {"fixture software group", "fixture software groups"},
             "B": {"fixture softwares group"}}
    pairs = [set(pair[:2]) for pair in fuzzy_pairs(names, 92)]
    assert {"fixture software group", "fixture softwares group"} in pairs
    assert {"fixture software group", "fixture software groups"} not in pairs


def test_fuzzy_match_joins_only_after_approval_and_survives_refresh(config):
    path = config.paths.database
    load_filing(path, make_filing("ARCC"), "xbrl", [make_loan("Fixture Software Group")])
    load_filing(path, make_filing("FSK"), "xbrl", [make_loan("Fixture Softwares Group")])
    assert refresh_aliases(path, 92) == 1
    database = Database(path, 1)
    ids = [filing.filing_id for filing in database.filings()]
    assert len({loan.group_key for loan in database.loans(ids)}) == 2
    assert review(path, "fixture softwares group", "fixture software group", "approved")
    assert len({loan.group_key for loan in database.loans(ids)}) == 1
    assert refresh_aliases(path, 92) == 0
    assert len({loan.group_key for loan in database.loans(ids)}) == 1


@pytest.mark.parametrize("text, bps", [
    ("SOFR + 5.75%", 575), ("S + 6.25%, 2.00% PIK", 625), ("SF+600", 600),
    ("SOFR (Q) + 5.00 %", 500), ("Base Rate (Q) + 5.00%", 500), ("SOFR+0.10%+5.75%", 575), ("10.5%", None), (None, None)])
def test_spreads(text, bps):
    assert parse_spread_bps(text) == bps


@pytest.mark.parametrize("text, expected", [
    ("8.0% (3.0% PIK)", True), ("8.0% (3.0% Max PIK)", False), ("0.0% PIK", False),
    ("5% (PIK 0.00%)", False), ("PIK", True), ("SOFR + 5.00%", False)])
def test_pik_wording(text, expected):
    assert pik_in_text(text) is expected


def test_numbers_and_dates():
    assert [parse_number(t) for t in ("$ 1,234.5", "(12)", "—", "n/a", "−5")] == [1234.5, -12, 0, None, -5]
    assert parse_date("02/2028") == date(2028, 2, 29)
