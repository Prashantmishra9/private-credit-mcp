"""Stores borrower name matches, and lets an analyst review the fuzzy ones.

    uv run python -m ingest.aliases list
    uv run python -m ingest.aliases approve "alias key" "borrower key"
    uv run python -m ingest.aliases reject "alias key" "borrower key"
"""

import argparse
from pathlib import Path

from config import load_config
from ingest.load import bump_data_version, transaction
from ingest.normalize import fuzzy_pairs

REVIEWED = {"approve": "approved", "reject": "rejected"}


def refresh_aliases(path: Path, threshold: float) -> int:
    """Rebuild exact and pending matches, keep analyst decisions. Returns the pending count."""
    with transaction(path) as db:
        db.execute("DELETE FROM borrower_aliases WHERE match_type = 'exact' OR status = 'pending'")
        db.execute("""INSERT INTO borrower_aliases
            SELECT DISTINCT borrower_raw, borrower_key, 'exact', 100, 'approved' FROM loans""")
        keys: dict[str, set[str]] = {}
        for bdc, key in db.execute("SELECT DISTINCT bdc_ticker, borrower_key FROM loans").fetchall():
            keys.setdefault(bdc, set()).add(key)
        reviewed = set(db.execute("""SELECT alias, borrower_key FROM borrower_aliases
            WHERE match_type = 'fuzzy'""").fetchall())
        pending = [[alias, key, "fuzzy", score, "pending"]
                   for alias, key, score in fuzzy_pairs(keys, threshold)
                   if (alias, key) not in reviewed]
        if pending:
            db.executemany("INSERT INTO borrower_aliases VALUES (?, ?, ?, ?, ?)", pending)
        bump_data_version(db)
    return len(pending)


def review(path: Path, alias: str, key: str, status: str) -> bool:
    """Record a decision on one fuzzy match. False if there is no such match."""
    with transaction(path) as db:
        changed = db.execute("""UPDATE borrower_aliases SET status = ?
            WHERE alias = ? AND borrower_key = ? AND match_type = 'fuzzy' RETURNING alias""",
            [status, alias, key]).fetchall()
        bump_data_version(db)
    return bool(changed)


def print_fuzzy(path: Path) -> None:
    with transaction(path) as db:
        rows = db.execute("""SELECT status, score, alias, borrower_key FROM borrower_aliases
            WHERE match_type = 'fuzzy' ORDER BY status, score DESC""").fetchall()
    for status, score, alias, key in rows:
        print(f"{status:9} {score:5.1f}  {alias!r}  ~  {key!r}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review fuzzy borrower matches")
    parser.add_argument("action", choices=["list", *REVIEWED])
    parser.add_argument("names", nargs="*", help="alias key, then borrower key")
    args = parser.parse_args()
    path = load_config().paths.database
    if not path.exists():
        parser.error("No database yet. Run: uv run python -m ingest.run")
    if args.action == "list":
        print_fuzzy(path)
    elif len(args.names) != 2:
        parser.error("Give the alias key and the borrower key, as shown by `list`.")
    elif review(path, *args.names, REVIEWED[args.action]):
        print("Saved.")
    else:
        parser.error("No such fuzzy match. Run `list` to see them.")


if __name__ == "__main__":
    main()
