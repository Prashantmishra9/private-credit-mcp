"""Registers the decision cards: markdown an agent reads before choosing or reading tools."""

from mcp.server.mcpserver import MCPServer

from config import Config
from server.db import Database, DataUnavailable

GLOSSARY = """# Glossary

- **BDC**: Business Development Company. A private credit fund whose shares trade publicly, so it
  files 10-Ks and 10-Qs with the SEC.
- **Schedule of Investments**: the filing's line-by-line list of every loan and equity position.
- **First lien**: a loan with first claim on the borrower's collateral.
- **Second lien**: paid from the same collateral after the first lien.
- **Unitranche**: one loan that combines senior and junior debt. Many BDCs report it as first lien.
- **Subordinated**: debt that ranks behind senior loans.
- **SOFR spread**: the margin paid over SOFR, the base rate. "SOFR + 5.75%" is 575 basis points.
- **Cost**: amortized cost, roughly what the BDC paid, adjusted over time.
- **Fair value**: the BDC's own estimate of what the position is worth now.
- **PIK**: payment in kind. Interest is added to the loan balance instead of being paid in cash.
"""

LIMITS = """
## Limits

- Quarterly data, published 30 to 60 days after quarter end, so always somewhat dated.
- BDCs are only part of private credit. Most private funds disclose nothing publicly.
- Fair values are each BDC's own estimates, not independent valuations.
- Covenant waivers and maturity extensions are rarely visible in filings.
- Borrower matching across BDCs is approximate. Fuzzy matches stay out until an analyst approves.
- PIK and non-accrual come from each filing's own tags and footnotes. Null means it does not say.
"""


def register_resources(mcp: MCPServer, config: Config, database: Database) -> None:
    @mcp.resource("card://stress-rules", mime_type="text/markdown")
    def stress_rules() -> str:
        """Signal definitions, the stress score table, and how to read mark_pct."""
        return stress_rules_text(config)

    @mcp.resource("card://data-sources", mime_type="text/markdown")
    def data_sources() -> str:
        """Which BDCs and periods are covered, where data comes from, and its limits."""
        return data_sources_text(config, database)

    @mcp.resource("card://glossary", mime_type="text/markdown")
    def glossary() -> str:
        """Private credit terms."""
        return GLOSSARY

    @mcp.resource("card://tools", mime_type="text/markdown")
    def tools() -> str:
        """The tool guide, read from docs/TOOLS.md."""
        path = config.paths.tool_docs
        if path.exists():
            return path.read_text(encoding="utf-8")
        return "The tool guide is missing. Create docs/TOOLS.md in the project to see it here."


def stress_rules_text(config: Config) -> str:
    rules, points = config.scoring, config.scoring.points
    deep, light = f"{rules.deep_markdown_below:.0%}", f"{rules.markdown_below:.0%}"
    return f"""# Stress rules

- **Non-accrual**: the lender has stopped recognizing interest because payment is doubtful.
- **PIK**: interest is added to the balance instead of paid in cash. Some loans are designed this
  way from the start, so PIK alone is a prompt to look closer.
- **mark_pct**: fair value divided by cost. 1.00 is at cost; 0.78 means valued at 78% of cost. Null
  when cost is zero, negative or unknown.
- **Markdown**: mark_pct below the threshold.
- **Mark dispersion**: the gap in points between lenders' cost-weighted debt marks on the same
  borrower in the same period. Only exact or analyst-approved name matches count.

| Signal | Points |
|---|---|
| On non-accrual | +{points.non_accrual} |
| PIK interest | +{points.pik} |
| Mark below {deep} of cost | +{points.deep_markdown} |
| Mark from {deep} up to {light} of cost | +{points.markdown} |
| Lenders' marks more than {rules.dispersion_over_pts:g} points apart | +{points.dispersion} |

Scores are capped at {rules.max_score}. A borrower's score is its highest loan score.
Not scored, and left out of dispersion: {', '.join(rules.exclude_instruments) or 'nothing'}.
An unknown (null) flag earns no points; it does not mean the loan is healthy.
"""


def data_sources_text(config: Config, database: Database) -> str:
    if config.data_mode == "mock":
        return ("# Data sources\n\n**MOCK DATA. Every loan, filing and rate is invented for testing. "
                f"Do not use it for analysis.**\n\n{coverage_text(config, database)}\n")
    return (f"# Data sources\n\nSEC EDGAR 10-K and 10-Q filings, and FRED series "
            f"{', '.join(config.fred_series)}. All public.\n\n"
            f"{coverage_text(config, database)}\n{LIMITS}")


def coverage_text(config: Config, database: Database) -> str:
    try:
        filings = database.filings()
    except DataUnavailable as error:
        return str(error)
    lines = ["| BDC | Name | Periods |", "|---|---|---|"]
    for ticker, name in config.bdcs.items():
        periods = [str(filing.period_end) for filing in filings if filing.bdc_ticker == ticker]
        lines.append(f"| {ticker} | {name} | {', '.join(periods) or 'not loaded'} |")
    return "\n".join(lines)
