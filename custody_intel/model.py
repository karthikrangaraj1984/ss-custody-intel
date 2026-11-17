"""Build the curated tables from the raw N-CEN tables.

    filing         one row per kept N-CEN filing (latest amendment per registrant + period)
    fund_year      one row per fund per fiscal year (fiscal year = year of the period end)
    fund_provider  one row per fund-year, role and provider group (primary providers only)
    switch_event   a fund's provider set for a role changed between consecutive filings
    switch_leg     the provider groups that gained or lost each switch

Primary-provider rules (configurable below):
    custodian      not a sub-custodian, and custody type is "Bank - section 17(f)(1)" or "Other"
                   (drops foreign custodians/depositories, DTC-type depositories, FCMs, self-custody)
    administrator  not a sub-administrator
    transfer agent not a sub-transfer agent
    shareholder servicing   not a sub-agent
    securities lending      every lending agent listed
"""
from __future__ import annotations

import re
import sqlite3
from datetime import datetime

from .providers import provider_group

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], start=1)}

# Only bank custodians (section 17(f)(1)) and "Other" count as a fund's primary custodian. Excluded:
# foreign custodians (17f-5), foreign depositories (17f-7), US securities depositories such as DTC (17f-4),
# futures commission merchants (17f-6), self-custody (17f-2), exchange members (17f-1), insurance sponsors (26a-2).
PRIMARY_CUSTODY = re.compile(r"^\s*(bank\b|other\b)", re.I)


def parse_date(s: str | None) -> str | None:
    """Return ISO yyyy-mm-dd for the date formats seen in DERA files."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"^(\d{1,2})-([A-Za-z]{3})-(\d{2,4})", s)
    if m:
        d, mon, y = int(m.group(1)), _MONTHS.get(m.group(2).upper()), int(m.group(3))
        if mon:
            y = y + 2000 if y < 100 else y
            return f"{y:04d}-{mon:02d}-{d:02d}"
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def to_num(s: str | None) -> float | None:
    if s is None or s == "":
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def is_primary_custody(custody_type: str | None, is_sub: str | None) -> int:
    if (is_sub or "").upper().startswith("Y"):
        return 0
    if custody_type and not PRIMARY_CUSTODY.search(custody_type):
        return 0
    return 1


def _register(conn: sqlite3.Connection) -> None:
    conn.create_function("pdate", 1, parse_date, deterministic=True)
    conn.create_function("pnum", 1, to_num, deterministic=True)
    conn.create_function("pgroup", 1, provider_group, deterministic=True)
    conn.create_function("primary_custody", 2, is_primary_custody, deterministic=True)


def _has(conn, table: str) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone())


def _col(conn, table: str, col: str, default: str = "NULL") -> str:
    """Column reference that tolerates columns missing from older files."""
    cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')}
    return f'"{col}"' if col in cols else default


def build(conn: sqlite3.Connection) -> dict[str, int]:
    _register(conn)
    for t in ("raw_submission", "raw_registrant", "raw_fund_reported_info"):
        if not _has(conn, t):
            raise SystemExit(f"{t} missing - load the data first")
    c = conn.cursor()
    c.executescript("""
        DROP TABLE IF EXISTS filing; DROP TABLE IF EXISTS fund_year; DROP TABLE IF EXISTS fund_provider;
        DROP TABLE IF EXISTS switch_event; DROP TABLE IF EXISTS switch_leg; DROP TABLE IF EXISTS fund_ticker;
    """)

    def rcol(name):
        ref = _col(conn, "raw_registrant", name)
        return ref if ref == "NULL" else "r." + ref
    fam_flag = rcol("IS_FAMILY_INVESTMENT_COMPANY")
    fam_name = rcol("FAMILY_INVESTMENT_COMPANY_NAME")
    ic_type = rcol("INVESTMENT_COMPANY_TYPE")
    c.execute(f"""
        CREATE TABLE filing AS
        WITH f AS (
            SELECT s.ACCESSION_NUMBER AS accession, s.CIK AS cik, s.SUBMISSION_TYPE AS submission_type,
                   pdate(s.FILING_DATE) AS filing_date, pdate(s.REPORT_ENDING_PERIOD) AS report_end,
                   s._quarter AS quarter, r.REGISTRANT_NAME AS registrant_name,
                   CASE WHEN upper(coalesce({fam_flag}, '')) LIKE 'Y%' AND coalesce({fam_name}, '') <> ''
                        THEN upper(trim({fam_name})) ELSE upper(trim(r.REGISTRANT_NAME)) END AS family,
                   {ic_type} AS inv_company_type
            FROM raw_submission s JOIN raw_registrant r ON r.ACCESSION_NUMBER = s.ACCESSION_NUMBER
            WHERE upper(s.SUBMISSION_TYPE) IN ('N-CEN', 'N-CEN/A')
        )
        SELECT * FROM (
            SELECT f.*, ROW_NUMBER() OVER (PARTITION BY cik, report_end
                                           ORDER BY filing_date DESC, accession DESC) AS rn
            FROM f WHERE report_end IS NOT NULL
        ) WHERE rn = 1
    """)
    c.execute("CREATE UNIQUE INDEX ix_filing ON filing(accession)")

    def fcol(name):
        ref = _col(conn, "raw_fund_reported_info", name)
        return ref if ref == "NULL" else "fi." + ref

    c.execute(f"""
        CREATE TABLE fund_year AS
        SELECT * FROM (
            SELECT coalesce(nullif(fi.SERIES_ID, ''), 'CIK:' || fl.cik) AS fund_key,
                   CAST(substr(fl.report_end, 1, 4) AS INTEGER) AS fiscal_year,
                   fi.FUND_ID AS fund_id, fl.accession, fl.cik, fi.SERIES_ID AS series_id,
                   fi.FUND_NAME AS fund_name, fl.family, fl.registrant_name, fl.inv_company_type,
                   fl.report_end, fl.filing_date,
                   CASE WHEN upper(coalesce({fcol('IS_MONEY_MARKET')}, '')) LIKE 'Y%' THEN 'Money market'
                        WHEN upper(coalesce({fcol('IS_ETF')}, '')) LIKE 'Y%'
                          OR upper(coalesce({fcol('IS_ETMF')}, '')) LIKE 'Y%' THEN 'ETF'
                        WHEN upper(coalesce({fcol('IS_INTERVAL')}, '')) LIKE 'Y%'
                          OR coalesce(fl.inv_company_type, '') LIKE '%N-2%' THEN 'Closed-end / interval'
                        WHEN coalesce(fl.inv_company_type, '') LIKE '%N-1A%' THEN 'Mutual fund'
                        ELSE 'Other' END AS category,
                   CASE WHEN upper(coalesce({fcol('IS_INDEX')}, '')) LIKE 'Y%' THEN 1 ELSE 0 END AS is_index,
                   coalesce(pnum({fcol('MONTHLY_AVG_NET_ASSETS')}), pnum({fcol('DAILY_AVG_NET_ASSETS')})) AS net_assets,
                   CASE WHEN upper(coalesce({fcol('HAS_CUSTODIAN_HIRED_FIRED_MI')}, '') || coalesce({fcol('HAS_CUSTODIAN_HIRED_FIRED_CE')}, '')) LIKE '%Y%' THEN 1 ELSE 0 END AS flag_custodian,
                   CASE WHEN upper(coalesce({fcol('HAS_ADMIN_HIRED_FIRED')}, '')) LIKE 'Y%' THEN 1 ELSE 0 END AS flag_administrator,
                   CASE WHEN upper(coalesce({fcol('HAS_XAGENT_HIRED_FIRED_MI')}, '') || coalesce({fcol('HAS_XAGENT_HIRED_FIRED_CE')}, '')) LIKE '%Y%' THEN 1 ELSE 0 END AS flag_transfer_agent,
                   CASE WHEN upper(coalesce({fcol('HAS_SH_SRVC_HIRED_FIRED')}, '')) LIKE 'Y%' THEN 1 ELSE 0 END AS flag_shareholder_servicing,
                   ROW_NUMBER() OVER (PARTITION BY coalesce(nullif(fi.SERIES_ID, ''), 'CIK:' || fl.cik),
                                                   substr(fl.report_end, 1, 4)
                                      ORDER BY fl.report_end DESC, fl.filing_date DESC) AS rn
            FROM raw_fund_reported_info fi JOIN filing fl ON fl.accession = fi.ACCESSION_NUMBER
        ) WHERE rn = 1
    """)
    c.execute("CREATE UNIQUE INDEX ix_fy ON fund_year(fund_key, fiscal_year)")
    c.execute("CREATE UNIQUE INDEX ix_fy_id ON fund_year(fund_id)")
    c.execute("CREATE INDEX ix_fy_family ON fund_year(family)")

    # ---- providers by role -------------------------------------------------------------
    parts = []
    if _has(conn, "raw_custodian"):
        parts.append(f"""SELECT FUND_ID, 'custodian', CUSTODIAN_NAME, CUSTODIAN_LEI, IS_AFFILIATED
                         FROM raw_custodian WHERE primary_custody({_col(conn, 'raw_custodian', 'CUSTODY_TYPE')},
                                                                  {_col(conn, 'raw_custodian', 'IS_SUB_CUSTODIAN')}) = 1""")
    if _has(conn, "raw_admin"):
        parts.append(f"""SELECT FUND_ID, 'administrator', ADMIN_NAME, ADMIN_LEI, IS_AFFILIATED FROM raw_admin
                         WHERE upper(coalesce({_col(conn, 'raw_admin', 'IS_SUB_ADMIN')}, 'N')) NOT LIKE 'Y%'""")
    if _has(conn, "raw_transfer_agent"):
        parts.append(f"""SELECT FUND_ID, 'transfer_agent', TRANSFERAGENT_NAME, TRANSFERAGENT_LEI, IS_AFFILIATED
                         FROM raw_transfer_agent
                         WHERE upper(coalesce({_col(conn, 'raw_transfer_agent', 'IS_SUBTRANSFER_AGENT')}, 'N')) NOT LIKE 'Y%'""")
    if _has(conn, "raw_security_lending"):
        parts.append("""SELECT FUND_ID, 'securities_lending', SECURITIES_AGENT_NAME, SECURITIES_AGENT_LEI, IS_AFFILIATED
                        FROM raw_security_lending""")
    if _has(conn, "raw_shareholder_servicing_agent"):
        parts.append(f"""SELECT FUND_ID, 'shareholder_servicing', AGENT_NAME, AGENT_LEI, IS_AFFILIATED
                         FROM raw_shareholder_servicing_agent
                         WHERE upper(coalesce({_col(conn, 'raw_shareholder_servicing_agent', 'IS_SUBSHARE')}, 'N')) NOT LIKE 'Y%'""")
    union = " UNION ALL ".join(parts)
    c.execute(f"""
        CREATE TABLE fund_provider AS
        WITH u(FUND_ID, role, name, lei, aff) AS ({union}),
        p AS (
            SELECT u.FUND_ID AS fund_id, u.role, pgroup(u.name) AS provider_group, u.name, u.lei,
                   CASE WHEN upper(coalesce(u.aff, '')) LIKE 'Y%' THEN 1 ELSE 0 END AS aff
            FROM u
            WHERE coalesce(u.name, '') <> ''
        ), g AS (
            SELECT fund_id, role, provider_group, max(aff) AS is_affiliated,
                   group_concat(DISTINCT name) AS provider_names, max(lei) AS provider_lei
            FROM p WHERE provider_group IS NOT NULL GROUP BY fund_id, role, provider_group
        )
        SELECT fy.fund_key, fy.fiscal_year, fy.family, fy.category, fy.net_assets, g.*,
               1.0 / count(*) OVER (PARTITION BY g.fund_id, g.role) AS weight
        FROM g JOIN fund_year fy ON fy.fund_id = g.fund_id
    """) if parts else c.execute("CREATE TABLE fund_provider (fund_key, fiscal_year, family, category, net_assets, fund_id, role, provider_group, is_affiliated, provider_names, provider_lei, weight)")
    c.execute("CREATE INDEX ix_fp ON fund_provider(role, fiscal_year, provider_group)")
    c.execute("CREATE INDEX ix_fp_fund ON fund_provider(fund_key, role, fiscal_year)")
    c.execute("CREATE INDEX ix_fp_family ON fund_provider(family, role)")

    if _has(conn, "raw_shares_outstanding"):
        c.execute("""CREATE TABLE fund_ticker AS
                     SELECT DISTINCT fy.fund_key, upper(so.TICKER) AS ticker, so.CLASS_NAME AS class_name
                     FROM raw_shares_outstanding so JOIN fund_year fy ON fy.fund_id = so.FUND_ID
                     WHERE coalesce(so.TICKER, '') NOT IN ('', 'N/A', 'NONE')""")
    else:
        c.execute("CREATE TABLE fund_ticker (fund_key, ticker, class_name)")
    c.execute("CREATE INDEX ix_ticker ON fund_ticker(ticker)")

    n_switch = _build_switches(conn)
    conn.commit()
    return {
        "filings": conn.execute("SELECT count(*) FROM filing").fetchone()[0],
        "fund_years": conn.execute("SELECT count(*) FROM fund_year").fetchone()[0],
        "fund_provider_rows": conn.execute("SELECT count(*) FROM fund_provider").fetchone()[0],
        "switch_events": n_switch,
    }


def _build_switches(conn: sqlite3.Connection, max_gap_years: int = 2) -> int:
    """Compare each fund's provider set per role between consecutive observed fiscal years.

    Phased migrations are common: the new provider is added one year and the old one dropped the next.
    An 'added' event followed within two years by a 'dropped' event for the same fund and role is merged
    into one 'phased' event dated when the new provider first appeared, so wins and losses pair up.
    """
    conn.execute("""CREATE TABLE switch_event (
        id INTEGER PRIMARY KEY, fund_key TEXT, role TEXT, year_from INTEGER, year_to INTEGER,
        fund_name TEXT, family TEXT, category TEXT, net_assets REAL,
        from_groups TEXT, to_groups TEXT, gained TEXT, lost TEXT, change_type TEXT, flagged_in_filing INTEGER,
        completed_fy INTEGER)""")
    conn.execute("CREATE TABLE switch_leg (event_id INTEGER, provider_group TEXT, direction TEXT)")

    sets: dict[tuple[str, str], dict[int, set[str]]] = {}
    for fund_key, role, year, grp in conn.execute(
            "SELECT fund_key, role, fiscal_year, provider_group FROM fund_provider"):
        sets.setdefault((fund_key, role), {}).setdefault(year, set()).add(grp)

    meta = {(r[0], r[1]): r[2:] for r in conn.execute(
        """SELECT fund_key, fiscal_year, fund_name, family, category, net_assets, flag_custodian,
                  flag_administrator, flag_transfer_agent, flag_shareholder_servicing FROM fund_year""")}
    flag_idx = {"custodian": 4, "administrator": 5, "transfer_agent": 6, "shareholder_servicing": 7}

    events = []
    for (fund_key, role), by_year in sets.items():
        years = sorted(by_year)
        fund_events = []
        for prev, cur in zip(years, years[1:]):
            if cur - prev > max_gap_years:
                continue
            a, b = by_year[prev], by_year[cur]
            if a == b:
                continue
            m = meta.get((fund_key, cur)) or meta.get((fund_key, prev))
            if not m:
                continue
            na = m[3]
            if na is None:
                pm = meta.get((fund_key, prev))
                na = pm[3] if pm else None
            fund_events.append({
                "fund_key": fund_key, "role": role, "year_from": prev, "year_to": cur,
                "fund_name": m[0], "family": m[1], "category": m[2], "net_assets": na,
                "from": set(a), "to": set(b), "gained": b - a, "lost": a - b,
                "flagged": m[flag_idx[role]] if role in flag_idx else None, "completed_fy": None})
        # merge add -> later drop into one phased migration
        merged = []
        for e in fund_events:
            last = merged[-1] if merged else None
            if (last and last["gained"] and not last["lost"] and e["lost"] and not e["gained"]
                    and e["year_to"] - last["year_to"] <= max_gap_years and not (e["lost"] & last["gained"])):
                last["lost"] = set(e["lost"])
                last["to"] = set(e["to"])
                last["completed_fy"] = e["year_to"]
                last["flagged"] = max(last["flagged"] or 0, e["flagged"] or 0)
                last["phased"] = True
                continue
            merged.append(e)
        events += merged

    rows, legs = [], []
    for eid, e in enumerate(events, 1):
        a, b = e["from"], e["to"]
        if e.get("phased"):
            change = "phased"
        elif not (a & b):
            change = "replaced"
        elif not e["lost"]:
            change = "added"
        elif not e["gained"]:
            change = "dropped"
        else:
            change = "partial"
        rows.append((eid, e["fund_key"], e["role"], e["year_from"], e["year_to"], e["fund_name"], e["family"],
                     e["category"], e["net_assets"], " | ".join(sorted(a)), " | ".join(sorted(b)),
                     " | ".join(sorted(e["gained"])), " | ".join(sorted(e["lost"])), change, e["flagged"],
                     e["completed_fy"]))
        legs += [(eid, g, "gained") for g in e["gained"]] + [(eid, g, "lost") for g in e["lost"]]
    conn.executemany("INSERT INTO switch_event VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO switch_leg VALUES (?,?,?)", legs)
    conn.execute("CREATE INDEX ix_se ON switch_event(role, year_to)")
    conn.execute("CREATE INDEX ix_sl ON switch_leg(provider_group, direction)")
    conn.execute("CREATE INDEX ix_sl_event ON switch_leg(event_id, direction)")
    return len(rows)
