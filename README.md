# Custody Market Intelligence Agent

A standalone MCP server and CLI that turns **SEC Form N-CEN** filings into custody and fund-servicing market intelligence. It shows who services US-registered funds, how market share is moving, and which fund families switched providers.

The data covers every US '40 Act fund (mutual funds, ETFs, money market, closed-end and interval funds) from September 2018 onwards. For each fund it records the custodian, administrator, transfer agent, securities lending agent and shareholder servicing agent, along with fund size and type.

## Questions it answers

- What is State Street's custody share of US ETF assets, and how has it moved against BNY, JPMorgan and Northern Trust since 2019?
- Which fund families moved custody **to** State Street in the last two years, and who did they leave?
- Which families did State Street **lose**, and to whom?
- Where does State Street already do fund administration or transfer agency but **not** custody? These are warm cross-sell targets.
- Who are all the service providers for a given fund family or ticker, and when did they change?

## Web app and chat agent

A Streamlit dashboard with the same look and structure as the State Street ETF Monitor (ssga-etf-agent):

| Page | What it shows |
|---|---|
| **Overview** | State Street's custody share and change, net custody switches, share over time, gains/losses by bank, State Street's share in each service, largest moves |
| **Market share** | Any role × fiscal year × fund type, ranked by assets or fund count, with the trend for the top five |
| **Switches** | Wins and losses for any provider and role: who it won from and lost to, and every family-level move (select a row to open the family) |
| **Fund families** | One family's providers by role, fund mix and provider-change history |
| **Prospects** | Families where a provider holds under half of a role, the incumbent, and the services the provider already gives (cross-sell) |
| **Ask the analyst** | Chat with Claude using the same tools as the MCP server. Answers say which fiscal year each figure is for, and *Show working* lists every lookup |

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt   # adds streamlit, pandas, anthropic
.\run_app.bat                                                     # opens http://localhost:8501
```

The chat needs an Anthropic API key. Put `ANTHROPIC_API_KEY=sk-ant-...` in a `.env` file in this folder (it is git-ignored), or set the environment variable. The other pages work without a key. Each browser session is capped at 30 questions. Set `CUSTODY_AGENT_MODEL` to change the model.

Command-line chat: `python -m custody_intel.agent` (or `-q "question"`).

Tests for the web app (they run every page with a Streamlit stand-in and a scripted Claude): `python tests/test_web.py`.

## Tools (MCP)

| Tool | What it returns |
|---|---|
| `data_status` | Quarters loaded, funds and assets per fiscal year, latest complete year, known gaps |
| `market_share` | Ranked share for a role in a year, by assets or fund count. Filters: ETF / Mutual fund / Money market / Closed-end, excluding affiliated (in-house) servicing |
| `share_trend` | Share % per year for chosen providers |
| `provider_switches` | Provider changes, by family or by fund. Filter by provider and direction (`won` / `lost`) |
| `win_loss_summary` | Wins and losses per year, who it won from and lost to, biggest wins and losses |
| `fund_family_profile` | Look up a family (by name, trust, fund or ticker): size, fund mix, providers by role, change history |
| `find_prospects` | Families where a provider holds under 50% of a role, with the incumbents and the roles the provider already serves |
| `search_funds` | Find funds by name or ticker, with their current providers |
| `unmapped_providers` | The biggest provider names not yet grouped (for data-quality upkeep) |

Roles: `custodian`, `administrator`, `transfer_agent`, `securities_lending`, `shareholder_servicing`.

## Setup

Requires Python 3.10+. The only dependency is `mcp`. Storage is SQLite from the standard library.

```bash
cd custody-intel
python -m venv .venv && source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# The SEC requires a contact User-Agent for automated downloads
export SEC_USER_AGENT="Your Name your.email@example.com"   # Windows: set SEC_USER_AGENT=...

python -m custody_intel.cli refresh            # download all quarters (2018q3 onwards), load, build
python -m custody_intel.cli status
python -m custody_intel.cli share --role custodian --category ETF
python -m custody_intel.cli winloss --provider "State Street"
python -m custody_intel.cli switches --provider "State Street" --direction lost
python -m custody_intel.cli family "SPDR"
python -m custody_intel.cli prospects --provider "State Street" --cross-sell-only
```

To start with less data, use `refresh --since 2022q1`. The full history is a few dozen zips.

### Add it to Claude Desktop

Merge the server entry into `claude_desktop_config.json` (an example is in `claude_desktop_config.example.json`):

```json
{
  "mcpServers": {
    "custody-intel": {
      "command": "C:/path/to/custody-intel/.venv/Scripts/python.exe",
      "args": ["-m", "custody_intel.server"],
      "cwd": "C:/path/to/custody-intel",
      "env": { "CUSTODY_INTEL_DB": "C:/path/to/custody-intel/data/custody_intel.sqlite" }
    }
  }
}
```

### Refresh each quarter

The SEC posts a new quarterly file and re-posts recent ones when late filings arrive. `refresh` always re-downloads the two newest quarters. Schedule it quarterly, or run it whenever you like.

## How it works

```
SEC DERA quarterly zips ──download──▶ data/zips/
        │ load (raw tables, all text, as filed, one _quarter tag per load)
        ▼
raw_submission, raw_registrant, raw_fund_reported_info, raw_custodian, raw_admin,
raw_transfer_agent, raw_security_lending, raw_shareholder_servicing_agent, raw_adviser, raw_shares_outstanding
        │ build
        ▼
filing         latest amendment per registrant and reporting period (N-CEN/A replaces N-CEN)
fund_year      one row per fund (series ID; CIK for closed-end funds) per fiscal year
fund_provider  primary providers per role, grouped (config/providers.csv), weight split if several
switch_event   provider set changed between consecutive fiscal years (gap ≤ 2 years)
switch_leg     which group gained or lost in each event
```

**Rules that matter for the numbers:**
- **Primary custodian only.** Only rows with custody type "Bank - section 17(f)(1)" or "Other" that are not marked sub-custodian count. Excluded: foreign custodians (17f-5), foreign depositories (17f-7), US securities depositories such as DTC (17f-4), futures commission merchants (17f-6), self-custody (17f-2), exchange members (17f-1) and insurance sponsors (26a-2). Without this, global sub-custody networks would inflate Citi, BNY and HSBC.
- **Asset weighting** uses monthly-average net assets (daily average for money market funds). A fund with two primary custodians counts half to each.
- **Fiscal year** is the year the fund's reporting period ends. The latest year fills in over time, because N-CEN is due about 75 days after year end. `latest_complete_year` therefore picks the latest year with at least 90% of the prior year's fund count.
- **Switch types:** `replaced` (no overlap); `phased` (the new provider is added one year and the old one dropped within 2 years, merged into one move dated when the new provider first appeared, with `completed_fy` showing when the old one left); `added`; `dropped`; `partial`. `flagged_in_filing=1` means the fund also answered yes to the N-CEN question about hiring or terminating that provider in the period. That independently confirms the change.
- **Affiliated:** each provider row carries the filing's own `IS_AFFILIATED` flag. Use `exclude_affiliated` to look only at the market a third party could win (e.g. drop SPDR funds custodied by State Street itself).

### Provider grouping

`config/providers.csv` maps name patterns (regex, first match wins) to groups such as State Street, BNY, JPMorgan, Northern Trust, Citi, BBH, U.S. Bank, UMB, SS&C, Ultimus and Fidelity. Unmapped names keep a cleaned version of their own name, so they still count. After loading real data, run `unmapped_providers` for each role, add any large names to the CSV, then run `python -m custody_intel.cli build`. Rebuilding is fast and doesn't re-download anything.

## Known limits (say these when presenting)

1. **US '40 Act funds only.** No private funds, UCITS, CITs or separate accounts, so this is *US registered-fund* custody share, not total assets under custody.
2. **Annual filing.** A switch shows up in the first N-CEN after it happens, often 3 to 15 months later.
3. **SEC gap on schema 3.1.** The DERA data sets don't yet include N-CEN filings made in XML schema 3.1 (EDGAR 25.2+), so the newest year may be under-counted until the SEC back-fills them. `data_status` shows coverage by year. A planned v2 adds a raw-XML loader for those filings.
4. **Data is as filed.** Registrants make mistakes, and names vary. Grouping and the primary-custodian filter handle most of this, but check large moves against `fund_family_profile` before quoting them.
5. **No time of switch within the year.** The data says the switch happened between two filings, not the exact date.

## Tests

```bash
python tests/test_pipeline.py
```

This builds synthetic N-CEN zips in the SEC layout (a BNY→State Street custody move, a JPMorgan→Northern Trust move, in-house SPDR-style custody, foreign sub-custodians that must be ignored, an amendment, a closed-end fund and an unmapped bank). It then checks share, switch, win/loss, prospect and profile outputs end to end.

## Ideas for v2

- A raw XML loader for schema 3.1 filings, straight from EDGAR.
- Join to Form N-PORT for current fund assets, and to the ETF and KPI servers to reconcile against State Street's reported AUC/A.
- An alert on newly filed N-CENs that name a different provider than the year before.
