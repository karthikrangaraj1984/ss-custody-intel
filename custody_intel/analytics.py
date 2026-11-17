"""Query functions behind the MCP tools and the CLI. All return JSON-friendly dicts."""
from __future__ import annotations

import sqlite3
from typing import Any

from . import config
from .providers import known_groups

ROLE_ALIASES = {
    "custody": "custodian", "custodian": "custodian", "custodians": "custodian",
    "admin": "administrator", "administrator": "administrator", "administration": "administrator",
    "fund administration": "administrator", "fund_admin": "administrator",
    "ta": "transfer_agent", "transfer agent": "transfer_agent", "transfer_agent": "transfer_agent",
    "securities lending": "securities_lending", "securities_lending": "securities_lending", "sec lending": "securities_lending",
    "shareholder servicing": "shareholder_servicing", "shareholder_servicing": "shareholder_servicing",
}

CAVEATS = (
    "Source: SEC Form N-CEN (DERA data sets), US-registered '40 Act funds only - excludes private funds, "
    "UCITS, separate accounts and CITs. Fiscal year = year of the fund's reporting period end. "
    "N-CEN is annual, so a provider change can appear up to a year after it happened. "
    "Assets are monthly-average net assets as reported (daily average for money market funds)."
)


def _conn(conn: sqlite3.Connection | None) -> sqlite3.Connection:
    if conn is not None:
        return conn
    if not config.DB_PATH.exists():
        raise RuntimeError(f"database not found at {config.DB_PATH}; run `python -m custody_intel.cli refresh`")
    c = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True, check_same_thread=False)
    return c


def _role(role: str) -> str:
    r = ROLE_ALIASES.get((role or "custodian").strip().lower())
    if not r:
        raise ValueError(f"unknown role {role!r}; use one of {config.ROLES}")
    return r


def _bn(x: float | None) -> float | None:
    return None if x is None else round(x / 1e9, 2)


def _rows(cur: sqlite3.Cursor) -> list[dict[str, Any]]:
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def year_coverage(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return _rows(conn.execute("""SELECT fiscal_year, count(*) AS funds, round(sum(net_assets)/1e9, 1) AS net_assets_usd_bn
                                 FROM fund_year GROUP BY fiscal_year ORDER BY fiscal_year"""))


def latest_complete_year(conn: sqlite3.Connection) -> int:
    """Latest fiscal year whose fund count reaches 90% of the prior year's (filings lag by ~75 days)."""
    cov = year_coverage(conn)
    if not cov:
        raise RuntimeError("no data loaded")
    for i in range(len(cov) - 1, 0, -1):
        if cov[i]["funds"] >= 0.9 * cov[i - 1]["funds"]:
            return cov[i]["fiscal_year"]
    return cov[-1]["fiscal_year"]


def _filters(category: str | None, exclude_affiliated: bool, alias: str = "") -> tuple[str, list]:
    p = f"{alias}." if alias else ""
    sql, args = "", []
    if category:
        sql += f" AND lower({p}category) LIKE ?"
        args.append(f"%{category.lower()}%")
    if exclude_affiliated:
        sql += f" AND {p}is_affiliated = 0"
    return sql, args


# --------------------------------------------------------------------------------------
def data_status(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    loads = _rows(conn.execute("SELECT quarter, zip_name, loaded_at FROM load_log ORDER BY quarter"))
    return {
        "quarters_loaded": [l["quarter"] for l in loads],
        "last_load": max((l["loaded_at"] for l in loads), default=None),
        "coverage_by_fiscal_year": year_coverage(conn),
        "latest_complete_fiscal_year": latest_complete_year(conn),
        "switch_events": conn.execute("SELECT count(*) FROM switch_event").fetchone()[0],
        "known_gap": "SEC DERA data sets currently omit N-CEN filings in XML schema 3.1 (EDGAR 25.2+); "
                     "the most recent fiscal year may be under-counted until the SEC back-fills them.",
        "caveats": CAVEATS,
    }


def market_share(role: str = "custodian", year: int | None = None, weight: str = "assets",
                 category: str | None = None, exclude_affiliated: bool = False, top_n: int = 10,
                 conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    role = _role(role)
    year = year or latest_complete_year(conn)
    f, args = _filters(category, exclude_affiliated)
    rows = _rows(conn.execute(f"""
        SELECT provider_group, sum(weight * coalesce(net_assets, 0)) AS assets, sum(weight) AS funds,
               count(DISTINCT family) AS families
        FROM fund_provider WHERE role = ? AND fiscal_year = ? {f}
        GROUP BY provider_group""", [role, year, *args]))
    tot_a = sum(r["assets"] for r in rows) or 1
    tot_f = sum(r["funds"] for r in rows) or 1
    key = "assets" if weight == "assets" else "funds"
    rows.sort(key=lambda r: r[key], reverse=True)
    out = []
    for i, r in enumerate(rows, 1):
        out.append({"rank": i, "provider": r["provider_group"],
                    "share_by_assets_pct": round(100 * r["assets"] / tot_a, 2),
                    "share_by_fund_count_pct": round(100 * r["funds"] / tot_f, 2),
                    "net_assets_usd_bn": _bn(r["assets"]), "funds": round(r["funds"], 1),
                    "fund_families": r["families"]})
    top = out[:top_n]
    rest = out[top_n:]
    if rest:
        top.append({"rank": None, "provider": f"All others ({len(rest)})",
                    "share_by_assets_pct": round(sum(r["share_by_assets_pct"] for r in rest), 2),
                    "share_by_fund_count_pct": round(sum(r["share_by_fund_count_pct"] for r in rest), 2),
                    "net_assets_usd_bn": round(sum(r["net_assets_usd_bn"] or 0 for r in rest), 2),
                    "funds": round(sum(r["funds"] for r in rest), 1), "fund_families": None})
    return {"role": role, "fiscal_year": year, "ranked_by": key, "category": category or "all",
            "exclude_affiliated": exclude_affiliated, "total_net_assets_usd_bn": _bn(tot_a),
            "total_funds": round(tot_f), "providers": top,
            "note": "Funds with several primary providers in a role are split equally between them. " + CAVEATS}


def share_trend(providers: list[str] | None = None, role: str = "custodian", weight: str = "assets",
                start_year: int | None = None, end_year: int | None = None, category: str | None = None,
                exclude_affiliated: bool = False, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    role = _role(role)
    providers = providers or ["State Street", "BNY", "JPMorgan", "Northern Trust", "Citi", "Brown Brothers Harriman", "U.S. Bank"]
    end_year = end_year or latest_complete_year(conn)
    start_year = start_year or (end_year - 6)
    f, args = _filters(category, exclude_affiliated)
    measure = "weight * coalesce(net_assets, 0)" if weight == "assets" else "weight"
    rows = conn.execute(f"""
        SELECT fiscal_year, provider_group, sum({measure}) FROM fund_provider
        WHERE role = ? AND fiscal_year BETWEEN ? AND ? {f} GROUP BY fiscal_year, provider_group""",
                        [role, start_year, end_year, *args]).fetchall()
    totals: dict[int, float] = {}
    vals: dict[tuple[int, str], float] = {}
    for y, g, v in rows:
        totals[y] = totals.get(y, 0) + (v or 0)
        vals[(y, g)] = v or 0
    lower = {p.lower(): p for p in providers}
    series = []
    for y in sorted(totals):
        row = {"fiscal_year": y}
        for g_low, g in lower.items():
            match = next((k for k in vals if k[0] == y and k[1].lower() == g_low), None)
            row[g] = round(100 * vals[match] / totals[y], 2) if match else 0.0
        series.append(row)
    return {"role": role, "measure": f"share of {'net assets' if weight == 'assets' else 'fund count'} (%)",
            "category": category or "all", "exclude_affiliated": exclude_affiliated, "series": series,
            "coverage": [c for c in year_coverage(conn) if start_year <= c["fiscal_year"] <= end_year],
            "note": CAVEATS}


def provider_switches(role: str = "custodian", provider: str | None = None, direction: str = "any",
                      since_year: int | None = None, category: str | None = None,
                      min_net_assets_usd_m: float = 0, level: str = "family", include_partial: bool = True,
                      limit: int = 25, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    role = _role(role)
    since_year = since_year or (latest_complete_year(conn) - 2)
    where = ["e.role = ?", "e.year_to >= ?", "coalesce(e.net_assets, 0) >= ?"]
    args: list[Any] = [role, since_year, min_net_assets_usd_m * 1e6]
    if not include_partial:
        where.append("e.change_type = 'replaced'")
    if category:
        where.append("lower(e.category) LIKE ?")
        args.append(f"%{category.lower()}%")
    join = ""
    if provider:
        dirs = {"won": ["gained"], "lost": ["lost"], "any": ["gained", "lost"]}[direction]
        join = (f"JOIN (SELECT DISTINCT event_id FROM switch_leg WHERE lower(provider_group) = ? "
                f"AND direction IN ({','.join('?' * len(dirs))})) l ON l.event_id = e.id")
        args = [provider.lower(), *dirs, *args]
    w = " AND ".join(where)
    if level == "fund":
        rows = _rows(conn.execute(f"""
            SELECT e.year_from, e.year_to, e.family, e.fund_name, e.category,
                   round(e.net_assets/1e9, 3) AS net_assets_usd_bn, e.from_groups AS from_providers,
                   e.to_groups AS to_providers, e.change_type, e.completed_fy, e.flagged_in_filing
            FROM switch_event e {join} WHERE {w} ORDER BY e.net_assets DESC LIMIT ?""", [*args, limit]))
    else:
        rows = _rows(conn.execute(f"""
            SELECT e.family, e.year_to AS first_seen_fy, e.lost AS lost_by, e.gained AS won_by,
                   count(*) AS funds_moved, round(sum(coalesce(e.net_assets, 0))/1e9, 2) AS net_assets_usd_bn,
                   max(e.flagged_in_filing) AS flagged_in_filing, max(e.completed_fy) AS completed_fy,
                   group_concat(DISTINCT e.change_type) AS change_types,
                   substr(group_concat(e.fund_name, '; '), 1, 300) AS sample_funds
            FROM switch_event e {join} WHERE {w}
            GROUP BY e.family, e.year_to, e.lost, e.gained
            ORDER BY sum(coalesce(e.net_assets, 0)) DESC LIMIT ?""", [*args, limit]))
    return {"role": role, "provider": provider, "direction": direction, "since_fiscal_year": since_year,
            "level": level, "results": rows,
            "how_to_read": "first_seen_fy is the first fiscal year the new provider appears in the fund's N-CEN. "
                           "flagged_in_filing=1 means the fund also ticked 'provider hired or terminated' that year. "
                           "change_type: replaced (no overlap), phased (new provider added, old one dropped within 2 years; completed_fy is when the old one left), added, dropped, partial.",
            "note": CAVEATS}


def win_loss_summary(provider: str = "State Street", role: str = "custodian", since_year: int | None = None,
                     category: str | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    role = _role(role)
    since_year = since_year or (latest_complete_year(conn) - 4)
    cat_sql, cat_args = ("AND lower(e.category) LIKE ?", [f"%{category.lower()}%"]) if category else ("", [])
    by_year = _rows(conn.execute(f"""
        SELECT e.year_to AS fiscal_year,
               sum(l.direction = 'gained') AS funds_won,
               round(sum(CASE WHEN l.direction = 'gained' THEN coalesce(e.net_assets, 0) END)/1e9, 2) AS won_usd_bn,
               sum(l.direction = 'lost') AS funds_lost,
               round(sum(CASE WHEN l.direction = 'lost' THEN coalesce(e.net_assets, 0) END)/1e9, 2) AS lost_usd_bn
        FROM switch_leg l JOIN switch_event e ON e.id = l.event_id
        WHERE lower(l.provider_group) = ? AND e.role = ? AND e.year_to >= ? {cat_sql}
        GROUP BY e.year_to ORDER BY e.year_to""", [provider.lower(), role, since_year, *cat_args]))
    for r in by_year:
        r["net_usd_bn"] = round((r["won_usd_bn"] or 0) - (r["lost_usd_bn"] or 0), 2)

    def counterparties(my_dir: str, their_dir: str) -> list[dict]:
        return _rows(conn.execute(f"""
            SELECT o.provider_group AS counterparty, count(*) AS funds,
                   round(sum(coalesce(e.net_assets, 0))/1e9, 2) AS net_assets_usd_bn
            FROM switch_leg l JOIN switch_event e ON e.id = l.event_id
            JOIN switch_leg o ON o.event_id = e.id AND o.direction = ?
            WHERE lower(l.provider_group) = ? AND l.direction = ? AND e.role = ? AND e.year_to >= ? {cat_sql}
            GROUP BY o.provider_group ORDER BY 3 DESC LIMIT 10""",
                                  [their_dir, provider.lower(), my_dir, role, since_year, *cat_args]))

    return {"provider": provider, "role": role, "since_fiscal_year": since_year, "category": category or "all",
            "by_year": by_year,
            "won_from": counterparties("gained", "lost"),
            "lost_to": counterparties("lost", "gained"),
            "biggest_wins": provider_switches(role, provider, "won", since_year, category, level="family",
                                              limit=10, conn=conn)["results"],
            "biggest_losses": provider_switches(role, provider, "lost", since_year, category, level="family",
                                                limit=10, conn=conn)["results"],
            "note": "Added providers (no one lost) count as wins with no counterparty. " + CAVEATS}


def _resolve_families(conn, query: str, year: int) -> list[dict]:
    q = query.strip()
    rows = _rows(conn.execute("""
        SELECT fy.family, count(*) AS funds, sum(net_assets) AS assets FROM fund_year fy
        WHERE fiscal_year = ? AND fy.family IN (
            SELECT DISTINCT f2.family FROM fund_year f2 LEFT JOIN fund_ticker t ON t.fund_key = f2.fund_key
            WHERE upper(f2.family) LIKE ? OR upper(f2.fund_name) LIKE ? OR upper(f2.registrant_name) LIKE ? OR t.ticker = ?)
        GROUP BY fy.family ORDER BY assets DESC LIMIT 10""",
                              [year, f"%{q.upper()}%", f"%{q.upper()}%", f"%{q.upper()}%", q.upper()]))
    return rows


def fund_family_profile(name: str, year: int | None = None, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    year = year or latest_complete_year(conn)
    fams = _resolve_families(conn, name, year)
    if not fams:
        return {"query": name, "fiscal_year": year, "matches": [], "message": "no fund family matched"}
    fam = fams[0]["family"]
    mix = _rows(conn.execute("""SELECT category, count(*) AS funds, round(sum(net_assets)/1e9, 2) AS net_assets_usd_bn
                                FROM fund_year WHERE family = ? AND fiscal_year = ? GROUP BY category ORDER BY 3 DESC""",
                             [fam, year]))
    providers: dict[str, list] = {}
    for role in config.ROLES:
        rows = _rows(conn.execute("""
            SELECT provider_group AS provider, round(sum(weight*coalesce(net_assets,0))/1e9, 2) AS net_assets_usd_bn,
                   round(sum(weight), 1) AS funds, max(is_affiliated) AS affiliated,
                   substr(group_concat(DISTINCT provider_names), 1, 200) AS names_as_filed
            FROM fund_provider WHERE family = ? AND fiscal_year = ? AND role = ?
            GROUP BY provider_group ORDER BY 2 DESC""", [fam, year, role]))
        if rows:
            providers[role] = rows
    switches = _rows(conn.execute("""
        SELECT role, year_to AS first_seen_fy, lost AS lost_by, gained AS won_by, count(*) AS funds,
               round(sum(coalesce(net_assets,0))/1e9, 2) AS net_assets_usd_bn
        FROM switch_event WHERE family = ? GROUP BY role, year_to, lost, gained ORDER BY year_to DESC, 6 DESC LIMIT 20""",
                                  [fam]))
    advisers = []
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='raw_adviser'").fetchone():
        advisers = [r[0] for r in conn.execute("""
            SELECT a.ADVISER_NAME FROM raw_adviser a JOIN fund_year fy ON fy.fund_id = a.FUND_ID
            WHERE fy.family = ? AND fy.fiscal_year = ? GROUP BY a.ADVISER_NAME ORDER BY count(*) DESC LIMIT 5""",
                                                [fam, year])]
    return {"family": fam, "fiscal_year": year,
            "funds": fams[0]["funds"], "net_assets_usd_bn": _bn(fams[0]["assets"]),
            "main_advisers": advisers, "category_mix": mix, "providers_by_role": providers,
            "provider_changes": switches,
            "other_matches": [{"family": f["family"], "funds": f["funds"], "net_assets_usd_bn": _bn(f["assets"])}
                              for f in fams[1:]],
            "note": CAVEATS}


def find_prospects(provider: str = "State Street", target_role: str = "custodian", year: int | None = None,
                   category: str | None = None, min_family_assets_usd_bn: float = 1.0,
                   cross_sell_only: bool = False, limit: int = 25,
                   conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Fund families where `provider` holds little or none of `target_role`, ranked by assets, showing
    the incumbent and whether `provider` already serves the family in another role."""
    conn = _conn(conn)
    role = _role(target_role)
    year = year or latest_complete_year(conn)
    cat_sql, cat_args = ("AND lower(category) LIKE ?", [f"%{category.lower()}%"]) if category else ("", [])
    fams = _rows(conn.execute(f"""
        SELECT family, sum(weight*coalesce(net_assets,0)) AS assets,
               sum(CASE WHEN lower(provider_group) = ? THEN weight*coalesce(net_assets,0) ELSE 0 END) AS mine,
               sum(CASE WHEN is_affiliated = 1 THEN weight*coalesce(net_assets,0) ELSE 0 END) AS affiliated
        FROM fund_provider WHERE role = ? AND fiscal_year = ? {cat_sql}
        GROUP BY family HAVING assets >= ? ORDER BY assets DESC""",
                             [provider.lower(), role, year, *cat_args, min_family_assets_usd_bn * 1e9]))
    out = []
    for f in fams:
        if f["mine"] / (f["assets"] or 1) > 0.5 or f["affiliated"] / (f["assets"] or 1) > 0.5:
            continue  # already ours, or served in-house
        inc = conn.execute("""SELECT provider_group, sum(weight*coalesce(net_assets,0)) FROM fund_provider
                              WHERE family=? AND fiscal_year=? AND role=? GROUP BY provider_group ORDER BY 2 DESC LIMIT 3""",
                           [f["family"], year, role]).fetchall()
        other_roles = [r[0] for r in conn.execute("""SELECT DISTINCT role FROM fund_provider
                              WHERE family=? AND fiscal_year=? AND lower(provider_group)=? AND role<>?""",
                                                  [f["family"], year, provider.lower(), role])]
        if cross_sell_only and not other_roles:
            continue
        recent = conn.execute("""SELECT max(year_to) FROM switch_event WHERE family=? AND role=?""",
                              [f["family"], role]).fetchone()[0]
        out.append({"family": f["family"], f"{role}_assets_usd_bn": _bn(f["assets"]),
                    f"{provider}_share_pct": round(100 * f["mine"] / (f["assets"] or 1), 1),
                    "incumbents": [{"provider": g, "share_pct": round(100 * v / (f["assets"] or 1), 1)} for g, v in inc],
                    f"{provider}_already_serves_as": other_roles,
                    f"last_{role}_change_fy": recent})
        if len(out) >= limit:
            break
    return {"provider": provider, "target_role": role, "fiscal_year": year, "category": category or "all",
            "prospects": out,
            "how_to_read": f"Families where {provider} has under 50% of {role} assets and the role is not served "
                           f"in-house. '{provider}_already_serves_as' lists roles where {provider} is already in "
                           "the family - a warm cross-sell. A recent last change can mean a relationship in flux "
                           "or a newly locked-in incumbent.",
            "note": CAVEATS}


def unmapped_providers(role: str = "custodian", year: int | None = None, limit: int = 30,
                       conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    role = _role(role)
    year = year or latest_complete_year(conn)
    groups = set(known_groups())
    rows = _rows(conn.execute("""
        SELECT provider_group, round(sum(weight*coalesce(net_assets,0))/1e9, 2) AS net_assets_usd_bn,
               round(sum(weight), 1) AS funds, substr(group_concat(DISTINCT provider_names), 1, 200) AS names_as_filed
        FROM fund_provider WHERE role = ? AND fiscal_year = ? GROUP BY provider_group ORDER BY 2 DESC""", [role, year]))
    rows = [r for r in rows if r["provider_group"] not in groups][:limit]
    return {"role": role, "fiscal_year": year, "unmapped": rows,
            "how_to_fix": f"Add a row (group,pattern) to {config.PROVIDER_MAP} and re-run `python -m custody_intel.cli build`."}


def search_funds(query: str, limit: int = 15, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    conn = _conn(conn)
    q = query.strip().upper()
    rows = _rows(conn.execute("""
        SELECT fy.fund_key, fy.fund_name, fy.family, fy.category, fy.fiscal_year AS latest_fy,
               round(fy.net_assets/1e9, 3) AS net_assets_usd_bn,
               (SELECT group_concat(DISTINCT ticker) FROM fund_ticker t WHERE t.fund_key = fy.fund_key) AS tickers
        FROM fund_year fy
        WHERE fy.fiscal_year = (SELECT max(fiscal_year) FROM fund_year f2 WHERE f2.fund_key = fy.fund_key)
          AND (upper(fy.fund_name) LIKE ? OR fy.fund_key IN (SELECT fund_key FROM fund_ticker WHERE ticker = ?))
        ORDER BY fy.net_assets DESC LIMIT ?""", [f"%{q}%", q, limit]))
    for r in rows:
        r["providers"] = {role: grp for role, grp in conn.execute("""
            SELECT role, group_concat(provider_group, ' | ') FROM fund_provider
            WHERE fund_key = ? AND fiscal_year = ? GROUP BY role""", [r["fund_key"], r["latest_fy"]])}
    return {"query": query, "results": rows}
