"""DataFrames for the web app. Thin wrappers over SQL and analytics; no Streamlit here."""
from __future__ import annotations

import sqlite3

import pandas as pd

from . import analytics as a

MAJORS = ["State Street", "JPMorgan", "BNY", "Citi", "Brown Brothers Harriman", "Northern Trust", "U.S. Bank"]
ROLE_LABELS = {"custodian": "Custody", "administrator": "Fund administration", "transfer_agent": "Transfer agency",
               "securities_lending": "Securities lending", "shareholder_servicing": "Shareholder servicing"}
CATEGORIES = ["All funds", "ETF", "Mutual fund", "Money market", "Closed-end"]


def ensure_indexes(db_path) -> None:
    """Add indexes that databases built by older versions lack. Safe to call on every start."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE INDEX IF NOT EXISTS ix_sl_event ON switch_leg(event_id, direction)")
        conn.commit()
    except sqlite3.Error:
        pass  # read-only or locked: the app still works, just slower on win/loss views
    finally:
        conn.close()


def _cat(category: str | None) -> str | None:
    return None if not category or category == "All funds" else category


def summary(conn: sqlite3.Connection, provider: str = "State Street") -> dict:
    y = a.latest_complete_year(conn)
    tot = conn.execute("SELECT count(*), sum(net_assets), count(DISTINCT family) FROM fund_year WHERE fiscal_year=?",
                       [y]).fetchone()
    ms = a.market_share("custodian", y, top_n=50, conn=conn)["providers"]
    share = {p["provider"]: p for p in ms}
    base_y = y - 3
    ms0 = {p["provider"]: p for p in a.market_share("custodian", base_y, top_n=50, conn=conn)["providers"]}
    since = y - 2
    sw = conn.execute("""SELECT count(DISTINCT family || '|' || year_to), sum(coalesce(net_assets,0)) FROM switch_event
                         WHERE role='custodian' AND year_to >= ? AND change_type IN ('replaced','phased')""", [since]).fetchone()
    wl = a.win_loss_summary(provider, "custodian", since, conn=conn)
    won = sum(r["won_usd_bn"] or 0 for r in wl["by_year"])
    lost = sum(r["lost_usd_bn"] or 0 for r in wl["by_year"])
    me = share.get(provider, {})
    return {
        "year": y, "base_year": base_y, "since": since,
        "funds": tot[0], "assets": tot[1] or 0, "families": tot[2],
        "share": me.get("share_by_assets_pct"), "rank": me.get("rank"),
        "share_base": ms0.get(provider, {}).get("share_by_assets_pct"),
        "leader": ms[0]["provider"] if ms else None,
        "moves": sw[0] or 0, "moved_assets": sw[1] or 0,
        "won_bn": won, "lost_bn": lost, "net_bn": won - lost,
        "top3": [(p["provider"], p["share_by_assets_pct"]) for p in ms[:3]],
    }


def share_trend(conn, role="custodian", providers=None, category=None, start=None, end=None) -> pd.DataFrame:
    providers = providers or MAJORS
    t = a.share_trend(providers, role, "assets", start, end, _cat(category), conn=conn)
    rows = [{"year": r["fiscal_year"], "provider": p, "share": r.get(p, 0.0)} for r in t["series"] for p in providers]
    return pd.DataFrame(rows)


def share_table(conn, role="custodian", year=None, category=None, weight="assets", top_n=12) -> pd.DataFrame:
    ms = a.market_share(role, year, weight, _cat(category), top_n=top_n, conn=conn)
    df = pd.DataFrame(ms["providers"])
    df.attrs["year"] = ms["fiscal_year"]
    df.attrs["total_bn"] = ms["total_net_assets_usd_bn"]
    df.attrs["total_funds"] = ms["total_funds"]
    return df


def provider_roles(conn, provider="State Street", year=None) -> pd.DataFrame:
    """The provider's share in every role, next to the role leader."""
    year = year or a.latest_complete_year(conn)
    rows = []
    for role, label in ROLE_LABELS.items():
        ms = a.market_share(role, year, top_n=50, conn=conn)["providers"]
        me = next((p for p in ms if p["provider"] == provider), None)
        leader = ms[0] if ms else None
        rows.append({"role": label, "share": me["share_by_assets_pct"] if me else 0.0,
                     "rank": me["rank"] if me else None,
                     "leader": leader["provider"] if leader else None,
                     "leader_share": leader["share_by_assets_pct"] if leader else None})
    return pd.DataFrame(rows)


def net_flows(conn, role="custodian", since=None, category=None) -> pd.DataFrame:
    """Assets won minus lost per provider group, from switches first seen since `since`."""
    since = since or a.latest_complete_year(conn) - 2
    cat_sql, args = ("AND lower(e.category) LIKE ?", [f"%{category.lower()}%"]) if _cat(category) else ("", [])
    df = pd.read_sql_query(f"""
        SELECT l.provider_group AS provider,
               sum(CASE WHEN l.direction='gained' THEN coalesce(e.net_assets,0) ELSE 0 END) AS won,
               sum(CASE WHEN l.direction='lost' THEN coalesce(e.net_assets,0) ELSE 0 END) AS lost,
               sum(l.direction='gained') AS funds_won, sum(l.direction='lost') AS funds_lost
        FROM switch_leg l JOIN switch_event e ON e.id = l.event_id
        WHERE e.role = ? AND e.year_to >= ? {cat_sql}
        GROUP BY l.provider_group""", conn, params=[role, since, *args])
    df["net"] = df.won - df.lost
    df = df[(df.won + df.lost) > 0]
    return df.reindex(df.net.abs().sort_values(ascending=False).index)


def switches(conn, role="custodian", provider=None, direction="any", since=None, category=None,
             min_usd_m=0.0, limit=200) -> pd.DataFrame:
    r = a.provider_switches(role, provider, direction, since, _cat(category), min_usd_m, "family", True, limit, conn=conn)
    df = pd.DataFrame(r["results"])
    df.attrs["since"] = r["since_fiscal_year"]
    return df


def families(conn, year=None, limit=400) -> pd.DataFrame:
    year = year or a.latest_complete_year(conn)
    return pd.read_sql_query("""SELECT family, count(*) AS funds, sum(net_assets) AS assets FROM fund_year
                                WHERE fiscal_year=? GROUP BY family ORDER BY assets DESC LIMIT ?""",
                             conn, params=[year, limit])


def years(conn) -> list[int]:
    return [r[0] for r in conn.execute("SELECT DISTINCT fiscal_year FROM fund_year ORDER BY 1")]


def providers(conn, role="custodian", year=None, min_bn=1.0) -> list[str]:
    year = year or a.latest_complete_year(conn)
    return [r[0] for r in conn.execute("""SELECT provider_group FROM fund_provider WHERE role=? AND fiscal_year=?
                                          GROUP BY 1 HAVING sum(weight*coalesce(net_assets,0)) >= ? ORDER BY sum(weight*coalesce(net_assets,0)) DESC""",
                                       [role, year, min_bn * 1e9])]
