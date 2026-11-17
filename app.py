"""Custody Market Monitor — web app.

    python -m streamlit run app.py

Six pages: Overview, Market share, Switches, Fund families, Prospects, and Ask the analyst (a chat that
answers in plain English using the same tools as the MCP server and command-line agent).
Reads the database built by `refresh.ps1` (CUSTODY_INTEL_DB, default data/custody_intel.sqlite).
Same look and structure as the State Street ETF Monitor (ssga-etf-agent)."""
from __future__ import annotations

import html
import sqlite3
from contextlib import closing
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

from custody_intel import analytics as an
from custody_intel import config
from custody_intel import dashboard as dd
from custody_intel.agent import DEFAULT_MODEL, CustodyTools, find_api_key, stream_turn

DB_PATH = config.DB_PATH
MAX_QUESTIONS = 30          # per browser session, to cap API spend
PAGES = ["Overview", "Market share", "Switches", "Fund families", "Prospects", "Ask the analyst"]
ME = "State Street"
SUGGESTIONS = [
    "Who has State Street lost custody business to since FY2022, and how much?",
    "How has ETF custody share changed for State Street, JPMorgan and BNY since 2019?",
    "Which fund families use State Street for administration but another bank for custody?",
    "Who are Dimensional's service providers?",
    "Which families moved custody to BNY in the last two years?",
]

st.set_page_config(page_title="Custody Market Monitor", page_icon="🏦", layout="wide",
                   initial_sidebar_state="collapsed")

# ---------------------------------------------------------------------------- look
# Same layout and voice as the State Street ETF Monitor: serif title and figures, a one-line
# takeaway, figure tiles, quiet charts with values printed on them.

FG, MUTED, LINE, PANEL = "#141413", "#6F6D66", "#E6E3DA", "#FFFFFF"
OK, BAD = "#4E7D4A", "#B5503A"
PROVIDER_COLOR = {"State Street": "#D97757", "JPMorgan": "#6A9BCC", "BNY": "#788C5D", "Citi": "#C9A15A",
                  "Brown Brothers Harriman": "#9B7FC4", "Northern Trust": "#5FA8A0", "U.S. Bank": "#8E8B82"}
OTHER = "#B9B5AA"

st.markdown(f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Source+Serif+4:opsz,wght@8..60,400&display=swap');
html, body, .stMarkdown, .stDataFrame, button, input, textarea {{ font-family: Inter, system-ui, -apple-system, "Segoe UI", sans-serif; }}
.block-container {{ padding-top: 1.6rem; padding-bottom: 3rem; max-width: 1180px; }}
header[data-testid="stHeader"] {{ background: transparent; }}
[data-testid="stSidebar"], [data-testid="collapsedControl"], [data-testid="stSidebarCollapsedControl"] {{ display: none; }}
.ss-title {{ font-family: "Source Serif 4", Georgia, serif; font-weight: 400; font-size: 2.1rem; line-height: 1.15;
            color: {FG}; margin: .6rem 0 .35rem; text-wrap: balance; }}
.ss-take {{ font-size: 1.02rem; line-height: 1.6; color: {FG}; max-width: 72ch; margin: 0; }}
.ss-take b {{ font-weight: 600; font-variant-numeric: tabular-nums; }}
.ss-meta {{ color: {MUTED}; font-size: .8rem; line-height: 1.5; margin: .45rem 0 0; max-width: 95ch; }}
.ss-tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 1.4rem 0 .6rem; }}
.ss-tile {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 10px; padding: 16px 18px;
           display: flex; flex-direction: column; gap: 4px; min-width: 0; }}
.ss-tile .lab, .ss-tile .sub {{ color: {MUTED}; font-size: .78rem; line-height: 1.4; }}
.ss-tile .num {{ font-family: "Source Serif 4", Georgia, serif; font-size: 2rem; line-height: 1.1;
                font-variant-numeric: tabular-nums; color: {FG}; }}
.ss-tile .num.up {{ color: {OK}; }} .ss-tile .num.down {{ color: {BAD}; }}
.ss-h2 {{ font-size: 1.05rem; font-weight: 600; color: {FG}; margin: 1.4rem 0 .15rem; }}
.ss-note {{ color: {MUTED}; font-size: .8rem; line-height: 1.5; margin: 0 0 .5rem; max-width: 72ch; }}
.ss-legend {{ display: flex; flex-wrap: wrap; gap: 4px 16px; font-size: .78rem; color: {MUTED}; margin: 0 0 .2rem; }}
.ss-legend i {{ width: 10px; height: 10px; border-radius: 50%; display: inline-block; margin-right: 6px; vertical-align: -1px; }}
.ss-banner {{ border: 1px solid {LINE}; border-left: 3px solid {BAD}; border-radius: 0; padding: 8px 12px; font-size: .85rem;
             background: {PANEL}; margin: .8rem 0 0; }}
div[data-testid="stExpander"] details {{ border: 1px solid {LINE}; border-radius: 10px; background: {PANEL}; }}
div[data-testid="stSegmentedControl"] {{ margin-bottom: .2rem; }}
</style>""", unsafe_allow_html=True)


def md_safe(text: str) -> str:
    """Streamlit treats text between two $ signs as LaTeX maths; escape them so "$857bn ... $412bn" stays text."""
    return text.replace("\\$", "$").replace("$", "\\$")


def esc(x) -> str:
    return html.escape(str(x))


def title(text: str, take: str | None = None, meta: str | None = None):
    st.markdown(f'<div class="ss-title">{esc(text)}</div>'
                + (f'<p class="ss-take">{take}</p>' if take else "")
                + (f'<p class="ss-meta">{meta}</p>' if meta else ""), unsafe_allow_html=True)


def tiles(items: list[tuple]):
    """items: (label, value, sub[, css class for the number])."""
    cells = "".join(
        f'<div class="ss-tile"><span class="lab">{esc(it[0])}</span>'
        f'<span class="num {it[3] if len(it) > 3 else ""}">{esc(it[1])}</span>'
        f'<span class="sub">{esc(it[2])}</span></div>' for it in items)
    st.markdown(f'<div class="ss-tiles">{cells}</div>', unsafe_allow_html=True)


def section(heading: str, note: str | None = None):
    st.markdown(f'<div class="ss-h2">{esc(heading)}</div>'
                + (f'<p class="ss-note">{esc(note)}</p>' if note else ""), unsafe_allow_html=True)


def legend(pairs: list[tuple[str, str]]):
    st.markdown('<div class="ss-legend">' + "".join(
        f'<span><i style="background:{c}"></i>{esc(l)}</span>' for l, c in pairs) + "</div>",
        unsafe_allow_html=True)


def styled(chart, height: int):
    return (chart.properties(height=height)
            .configure(font="Inter")
            .configure_axis(labelColor=MUTED, titleColor=MUTED, labelFontSize=11, titleFontSize=11,
                            titleFontWeight="normal", grid=False, ticks=False, domainColor=LINE,
                            labelPadding=6)
            .configure_view(strokeWidth=0))


def show(chart, height: int):
    st.altair_chart(styled(chart, height), use_container_width=True)


def color_of(provider: str) -> str:
    return PROVIDER_COLOR.get(provider, OTHER)


def color_scale(providers) -> alt.Scale:
    ps = list(dict.fromkeys(providers))
    return alt.Scale(domain=ps, range=[color_of(p) for p in ps])


def dodge(values: list[float], min_gap: float) -> list[float]:
    """Spread label positions so no two are closer than min_gap, keeping their order (for end-of-line labels)."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = list(values)
    for k in range(1, len(order)):
        prev, cur = order[k - 1], order[k]
        if out[cur] - out[prev] < min_gap:
            out[cur] = out[prev] + min_gap
    return out


def trend_chart(tr: pd.DataFrame, names: list[str], height: int = 300):
    """Share-over-time lines with one end label per line, pushed apart so they never overlap."""
    color = alt.Color("provider:N", scale=color_scale(names), legend=None)
    top = max(float(tr.share.max()), 1.0)
    last = tr[tr.year == tr.year.max()].copy()
    last["label_y"] = dodge(list(last.share), top * 0.075)       # ~ one text line at this chart height
    last["label"] = last.apply(lambda r: f"{r.share:.1f}%  {short(r.provider)}", axis=1)
    y_scale = alt.Scale(domain=[0, max(top, float(last.label_y.max())) * 1.08])
    lines = alt.Chart(tr).mark_line(strokeWidth=2, point=alt.OverlayMarkDef(size=18)).encode(
        x=alt.X("year:O", title=None, axis=alt.Axis(labelAngle=0, labelExpr="'FY' + datum.label")),
        y=alt.Y("share:Q", title=None, scale=y_scale,
                axis=alt.Axis(labelExpr="datum.label + '%'", tickCount=5, domain=False)),
        color=color, tooltip=[alt.Tooltip("year:O", title="Fiscal year"), alt.Tooltip("provider:N", title="Provider"),
                              alt.Tooltip("share:Q", title="Share %", format=".1f")])
    ends = alt.Chart(last).mark_text(align="left", dx=10, fontSize=12, fontWeight=500).encode(
        x="year:O", y=alt.Y("label_y:Q", scale=y_scale), text="label:N", color=color)
    return (lines + ends).properties(padding={"left": 5, "top": 5, "right": 110, "bottom": 5})


def short(provider: str) -> str:
    return {"Brown Brothers Harriman": "BBH", "Northern Trust": "N. Trust", "State Street": "State St."}.get(provider, provider)


def diverging_bars(df: pd.DataFrame, cat: str, val: str, label: str, height: int, tooltip: list):
    """Horizontal bars either side of zero, largest gain at the top, with room for labels on both sides.
    Built only from chart features the ETF Monitor already uses (sort by x, side colour, filtered text)."""
    df = df.copy()
    df["side"] = df[val].map(lambda v: "up" if v >= 0 else "down")
    lo, hi = float(min(df[val].min(), 0)), float(max(df[val].max(), 0))
    pad = (hi - lo) * 0.32 or 1.0
    base = alt.Chart(df).encode(
        # Fixed order on the scale itself (biggest gain at the top). A computed sort is dropped by
        # Vega-Lite when the text layers are filtered, which left the banks in alphabetical order.
        y=alt.Y(f"{cat}:N", sort=None, title=None,
                scale=alt.Scale(domain=list(df.sort_values(val, ascending=False)[cat])),
                axis=alt.Axis(domain=False, ticks=False, labelLimit=200, labelPadding=10)),
        x=alt.X(f"{val}:Q", title=None, axis=None,
                scale=alt.Scale(domain=[lo - (pad if lo < 0 else 0), hi + (pad if hi > 0 else 0)])),
        tooltip=tooltip)
    bars = base.mark_bar(cornerRadius=2, height=16).encode(
        color=alt.Color("side:N", scale=alt.Scale(domain=["up", "down"], range=[OK, BAD]), legend=None))
    pos = base.mark_text(dx=6, fontSize=11, color=FG, align="left").encode(text=f"{label}:N").transform_filter(f"datum.{val} >= 0")
    neg = base.mark_text(dx=-6, fontSize=11, color=FG, align="right").encode(text=f"{label}:N").transform_filter(f"datum.{val} < 0")
    return bars + pos + neg


# ---------------------------------------------------------------------------- data access

def db_version() -> float:
    """Changes whenever the database file changes, so cached data refreshes after a refresh."""
    try:
        return Path(DB_PATH).stat().st_mtime
    except OSError:
        return 0.0


def connect() -> sqlite3.Connection:
    # A fresh read-only connection each time: Streamlit runs each session on its own thread.
    return sqlite3.connect(f"file:{Path(DB_PATH).as_posix()}?mode=ro", uri=True, check_same_thread=False)


def q(fn, *args, **kwargs):
    with closing(connect()) as c:
        return fn(c, *args, **kwargs)


@st.cache_data(show_spinner="Loading N-CEN data…")
def load_summary(_v: float):
    return q(dd.summary, ME)


@st.cache_data(show_spinner=False)
def load_years(_v: float):
    return q(dd.years)


@st.cache_data(show_spinner=False)
def load_trend(_v: float, role: str, category: str):
    return q(dd.share_trend, role, None, category)


@st.cache_data(show_spinner=False)
def load_roles(_v: float):
    return q(dd.provider_roles, ME)


@st.cache_data(show_spinner=False)
def load_flows(_v: float, role: str, since: int, category: str):
    return q(dd.net_flows, role, since, category)


@st.cache_data(show_spinner=False)
def load_share(_v: float, role: str, year: int, category: str, weight: str):
    return q(dd.share_table, role, year, category, weight)


@st.cache_data(show_spinner=False)
def load_switches(_v: float, role: str, provider, direction: str, since: int, category: str, min_m: float):
    return q(dd.switches, role, provider, direction, since, category, min_m)


@st.cache_data(show_spinner=False)
def load_families(_v: float):
    return q(dd.families)


@st.cache_data(show_spinner=False)
def load_providers(_v: float, role: str):
    return q(dd.providers, role)


@st.cache_data(show_spinner="Profiling the family…")
def load_profile(_v: float, name: str, year: int):
    return q(lambda c: an.fund_family_profile(name, year, conn=c))


@st.cache_data(show_spinner="Finding prospects…")
def load_prospects(_v: float, provider: str, role: str, category: str, min_bn: float, cross: bool):
    return q(lambda c: an.find_prospects(provider, role, None, dd._cat(category), min_bn, cross, 60, conn=c))


def usd(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    a = abs(v)
    sign = "−" if v < 0 else ""
    if a >= 1e12:
        return f"{sign}${a / 1e12:.2f}tn"
    if a >= 1e9:
        return f"{sign}${a / 1e9:.0f}bn" if a >= 1e11 else f"{sign}${a / 1e9:.1f}bn"
    if a >= 1e6:
        return f"{sign}${a / 1e6:.0f}m"
    return f"{sign}${a:,.0f}"


def bn(v) -> str:
    return "—" if v is None or pd.isna(v) else usd(v * 1e9)


def pct(v, d=1, signed=False) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.{d}f}".replace("-", "−") + (" pts" if signed else "%") if signed else f"{v:.{d}f}%"


def tone(v) -> str:
    return "" if v is None or pd.isna(v) else ("up" if v >= 0 else "down")


def fam_name(f: str) -> str:
    """Families are filed in capitals (and some as codes, e.g. TROWEPRICE); show them in title case."""
    return f.title() if f and f.isupper() and len(f) > 4 else (f or "—")


def open_family(name: str):
    # Used only as a widget callback: Streamlit allows changing the page control there.
    st.session_state.family = name
    st.session_state.page = "Fund families"


def open_selected(table_key: str, list_key: str):
    sel = st.session_state.get(table_key)
    rows = sel.selection.rows if sel is not None and hasattr(sel, "selection") else (sel or {}).get("selection", {}).get("rows", [])
    if rows:
        open_family(st.session_state[list_key][rows[0]])


def switch_view(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    v = df.copy()
    v["Family"] = v.family.map(fam_name)
    v["From"] = v.lost_by.replace("", "— (added)")
    v["To"] = v.won_by.replace("", "— (dropped)")
    v["Confirmed"] = v.flagged_in_filing.map(lambda x: "Yes" if x == 1 else "No")
    v["Completed"] = v.completed_fy.map(lambda x: "" if pd.isna(x) else f"FY{int(x)}")
    return v.rename(columns={"first_seen_fy": "First seen", "funds_moved": "Funds", "net_assets_usd_bn": "Assets ($bn)",
                             "change_types": "Type", "sample_funds": "Example funds"})[
        ["Family", "First seen", "From", "To", "Funds", "Assets ($bn)", "Type", "Completed", "Confirmed", "Example funds"]]


SWITCH_COLUMNS = {
    "Assets ($bn)": st.column_config.NumberColumn(format="$%.1f bn"),
    "First seen": st.column_config.NumberColumn(format="FY%d", help="First fiscal year the new provider appears in the N-CEN"),
    "Confirmed": st.column_config.TextColumn(help="The funds also ticked 'custodian/agent hired or terminated' that year"),
    "Example funds": st.column_config.TextColumn(width="large"),
}

# ---------------------------------------------------------------------------- start-up

if not Path(DB_PATH).exists():
    st.error(f"No database at `{DB_PATH}`. Run `refresh.ps1` in this folder first, or set CUSTODY_INTEL_DB to its path.")
    st.stop()

@st.cache_resource(show_spinner=False)
def prepare_db(path: str):
    dd.ensure_indexes(path)
    return True


prepare_db(str(DB_PATH))
VERSION = db_version()
S = load_summary(VERSION)
YEARS = load_years(VERSION)
FY = S["year"]
st.session_state.setdefault("page", PAGES[0])
st.session_state.setdefault("family", None)

if hasattr(st, "segmented_control"):
    st.segmented_control("Page", PAGES, key="page", label_visibility="collapsed")
else:  # Streamlit before 1.40
    st.radio("Page", PAGES, key="page", horizontal=True, label_visibility="collapsed")
page = st.session_state.page or st.session_state.get("last_page", PAGES[0])  # clicking the active page clears it
st.session_state.last_page = page

SOURCE_LINE = (f"SEC Form N-CEN filings (DERA data sets), fiscal years {YEARS[0]}–{YEARS[-1]}; FY{FY} is the latest "
               "complete year. US-registered '40 Act funds only: mutual funds, ETFs, money market and closed-end funds, "
               "not private funds, UCITS, CITs or separate accounts. Factual analysis of public filings.")
ROLE_OPTS = list(dd.ROLE_LABELS)

# ---------------------------------------------------------------------------- Overview

if page == "Overview":
    change = (S["share"] or 0) - (S["share_base"] or 0)
    t1, t2, t3 = S["top3"]
    take = (f"In FY{FY}, State Street was custodian for <b>{pct(S['share'])}</b> of US registered-fund assets, ahead of "
            f"{esc(t2[0])} (<b>{pct(t2[1])}</b>) and {esc(t3[0])} (<b>{pct(t3[1])}</b>). That share is "
            f"<b>{pct(abs(change))}</b> points {'lower' if change < 0 else 'higher'} than in FY{S['base_year']}, "
            f"with a net <b>{bn(S['net_bn'])}</b> of fund assets moving {'away' if S['net_bn'] < 0 else 'in'} through "
            f"custody switches since FY{S['since']}.")
    title("Custody Market Monitor", take, SOURCE_LINE)
    tiles([
        ("US registered-fund assets", usd(S["assets"]), f"FY{FY} · {S['funds']:,} funds in {S['families']:,} families"),
        ("State Street custody share", pct(S["share"]), f"#{S['rank']} of all custodians, by assets"),
        (f"Change since FY{S['base_year']}", pct(change, signed=True), "Share of custody assets", tone(change)),
        (f"Net custody switches since FY{S['since']}", bn(S["net_bn"]),
         f"Won {bn(S['won_bn'])} · lost {bn(S['lost_bn'])}", tone(S["net_bn"])),
    ])

    left, right = st.columns(2, gap="large")
    with left:
        section("Custody share over time", "Share of US registered-fund assets by primary custodian, per fiscal year.")
        tr = load_trend(VERSION, "custodian", "All funds")
        tr = tr[tr.provider.isin(["State Street", "JPMorgan", "BNY", "Citi", "Brown Brothers Harriman"])]
        names = list(dict.fromkeys(tr.provider))
        legend([(p, color_of(p)) for p in names])
        show(trend_chart(tr, names), 300)
    with right:
        section(f"Who gained and lost custody assets since FY{S['since']}",
                "Assets in funds that switched custodian: won minus lost, per bank. Funds with two custodians count half to each.")
        fl = load_flows(VERSION, "custodian", S["since"], "All funds").head(8).copy()
        fl["provider"] = fl.provider.replace({"Brown Brothers Harriman": "BBH"})
        fl["label"] = fl.net.map(usd)
        fl["won_l"], fl["lost_l"] = fl.won.map(usd), fl.lost.map(usd)
        show(diverging_bars(fl, "provider", "net", "label", 300,
                            [alt.Tooltip("provider:N", title="Custodian"), alt.Tooltip("label:N", title="Net"),
                             alt.Tooltip("won_l:N", title="Won"), alt.Tooltip("lost_l:N", title="Lost")]), 300)

    left, right = st.columns([2, 3], gap="large")
    with left:
        section(f"State Street across fund services, FY{FY}",
                "Share of assets in each role. Large managers such as Vanguard and Fidelity do administration and "
                "transfer agency in-house and appear as their own provider.")
        r = load_roles(VERSION).copy()
        r["label"] = r.apply(lambda x: f"{x.share:.1f}% · #{int(x['rank'])}" if pd.notna(x["rank"]) else "—", axis=1)
        base = alt.Chart(r).encode(
            y=alt.Y("role:N", sort=None, title=None, axis=alt.Axis(domain=False, ticks=False, labelLimit=200)),
            x=alt.X("share:Q", axis=None, scale=alt.Scale(domain=[0, float(r.share.max()) * 1.4])),
            tooltip=[alt.Tooltip("role:N", title="Role"), alt.Tooltip("label:N", title="State Street"),
                     alt.Tooltip("leader:N", title="Leader"), alt.Tooltip("leader_share:Q", title="Leader share %", format=".1f")])
        show(base.mark_bar(cornerRadiusEnd=3, height=18, color=color_of(ME))
             + base.mark_text(align="left", dx=6, fontSize=12, color=FG).encode(text="label:N"), 220)
    with right:
        section(f"Largest custody moves since FY{S['since']}", "Fund families whose custodian changed, biggest first. Select a row to open the family.")
        sw = load_switches(VERSION, "custodian", None, "any", S["since"], "All funds", 0.0)
        top = sw[sw.change_types.str.contains("replaced|phased", na=False)].head(12)
        st.session_state.ov_fams = list(top.family)
        st.dataframe(switch_view(top)[["Family", "First seen", "From", "To", "Assets ($bn)", "Confirmed"]],
                     hide_index=True, use_container_width=True, height=300, column_config=SWITCH_COLUMNS,
                     on_select=lambda: open_selected("ov_moves", "ov_fams"), selection_mode="single-row", key="ov_moves")

    with st.expander("How these figures are worked out"):
        st.markdown(f"""
- **Source**: every Form N-CEN filed since September 2018, from the SEC's quarterly DERA data sets. Amendments replace the original filing.
- **Fiscal year**: the year in which a fund's reporting period ends. FY{FY} is the latest year with at least 90% of the prior year's funds filed; later years are still filling in.
- **Primary custodian**: bank custodians (section 17(f)(1)) that are not sub-custodians. Foreign sub-custodians, securities depositories, futures commission merchants and self-custody are excluded.
- **Assets**: each fund's monthly-average net assets for the year (daily average for money market funds). A fund with two primary custodians counts half to each.
- **Provider groups**: names as filed are grouped by bank (e.g. "The Bank of New York Mellon" and "BNY Mellon Investment Servicing" → BNY). The mapping is in `config/providers.csv`.
- **Switches**: a fund's provider set changed between consecutive filings. *Phased* moves add the new provider one year and drop the old one within two years; they count once, in the year the new provider first appears. *Confirmed* means the fund also reported hiring or terminating that provider.
- **Lag**: N-CEN is annual, so a switch appears in the first filing after it happens, often 3–15 months later.
""")

# ---------------------------------------------------------------------------- Market share

elif page == "Market share":
    title("Market share", "Who services US registered funds, by role, fund type and year.", SOURCE_LINE)
    c1, c2, c3, c4 = st.columns(4)
    role = c1.selectbox("Role", ROLE_OPTS, format_func=dd.ROLE_LABELS.get)
    year = c2.selectbox("Fiscal year", YEARS[::-1], index=YEARS[::-1].index(FY), format_func=lambda y: f"FY{y}")
    cat = c3.selectbox("Fund type", dd.CATEGORIES)
    weight = c4.selectbox("Rank by", ["assets", "funds"], format_func=lambda w: "Assets" if w == "assets" else "Fund count")
    ms = load_share(VERSION, role, year, cat, weight)
    if ms.empty:
        st.info("No data for this selection.")
    else:
        key = "share_by_assets_pct" if weight == "assets" else "share_by_fund_count_pct"
        lead = ms.iloc[0]
        me = ms[ms.provider == ME]
        tiles([
            ("Assets in scope", bn(ms.attrs["total_bn"]), f"FY{year} · {ms.attrs['total_funds']:,} funds"),
            ("Leader", lead.provider, f"{pct(lead[key])} {'of assets' if weight == 'assets' else 'of funds'}"),
            (f"{ME}", pct(me[key].iloc[0]) if len(me) else "—", f"Rank #{int(me['rank'].iloc[0])}" if len(me) and pd.notna(me['rank'].iloc[0]) else "Not in the top list"),
        ])
        left, right = st.columns(2, gap="large")
        with left:
            section(f"{dd.ROLE_LABELS[role]} share, FY{year}", "Share of " + ("net assets" if weight == "assets" else "funds") + " by provider group.")
            ms = ms.copy()
            ms["label"] = ms[key].map(lambda v: f"{v:.1f}%")
            base = alt.Chart(ms).encode(
                y=alt.Y("provider:N", sort=None, title=None, axis=alt.Axis(domain=False, ticks=False, labelLimit=220)),
                x=alt.X(f"{key}:Q", axis=None, scale=alt.Scale(domain=[0, float(ms[key].max()) * 1.3])),
                tooltip=[alt.Tooltip("provider:N", title="Provider"), alt.Tooltip("label:N", title="Share"),
                         alt.Tooltip("net_assets_usd_bn:Q", title="Assets $bn", format=",.1f"),
                         alt.Tooltip("funds:Q", title="Funds", format=",.0f"), alt.Tooltip("fund_families:Q", title="Families")])
            show(base.mark_bar(cornerRadiusEnd=3, height=16).encode(
                color=alt.Color("provider:N", scale=color_scale(ms.provider), legend=None))
                 + base.mark_text(align="left", dx=6, fontSize=12, color=FG).encode(text="label:N"), 40 + 26 * len(ms))
        with right:
            section("Share over time", "Share of net assets per fiscal year for the five largest providers in this role.")
            tr = q(dd.share_trend, role, [p for p in ms.provider[:5] if not str(p).startswith("All others")], cat)
            names = list(dict.fromkeys(tr.provider))
            legend([(p, color_of(p)) for p in names])
            show(trend_chart(tr, names), 300)
        section("Table")
        st.dataframe(ms.drop(columns=["label"]).rename(columns={
            "rank": "#", "provider": "Provider", "share_by_assets_pct": "Share of assets", "share_by_fund_count_pct": "Share of funds",
            "net_assets_usd_bn": "Assets ($bn)", "funds": "Funds", "fund_families": "Families"}),
            hide_index=True, use_container_width=True, column_config={
                "Share of assets": st.column_config.NumberColumn(format="%.2f%%"),
                "Share of funds": st.column_config.NumberColumn(format="%.2f%%"),
                "Assets ($bn)": st.column_config.NumberColumn(format="$%.1f bn")})

# ---------------------------------------------------------------------------- Switches

elif page == "Switches":
    title("Provider switches", "Fund families that changed custodian, administrator or another provider, and who won and lost.", SOURCE_LINE)
    c1, c2, c3, c4, c5 = st.columns([1.2, 1.4, 1, 1, 1])
    role = c1.selectbox("Role", ROLE_OPTS, format_func=dd.ROLE_LABELS.get, key="sw_role")
    provs = load_providers(VERSION, role)
    prov = c2.selectbox("Provider", ["All providers"] + provs, index=(provs.index(ME) + 1) if ME in provs else 0)
    direction = c3.selectbox("Direction", ["any", "won", "lost"], format_func={"any": "Won or lost", "won": "Won", "lost": "Lost"}.get,
                             disabled=prov == "All providers")
    since = c4.selectbox("First seen from", YEARS[1:][::-1], index=YEARS[1:][::-1].index(FY - 2) if FY - 2 in YEARS[1:] else 0,
                         format_func=lambda y: f"FY{y}")
    min_m = c5.selectbox("Minimum size", [0.0, 100.0, 1000.0, 10000.0],
                         format_func=lambda m: "Any size" if not m else f"${m/1000:g}bn+" if m >= 1000 else f"${m:g}m+")
    provider = None if prov == "All providers" else prov
    sw = load_switches(VERSION, role, provider, direction if provider else "any", since, "All funds", min_m)
    if provider:
        wl = q(lambda c: an.win_loss_summary(provider, role, since, conn=c))
        won = sum(r["won_usd_bn"] or 0 for r in wl["by_year"])
        lost = sum(r["lost_usd_bn"] or 0 for r in wl["by_year"])
        tiles([
            ("Won", bn(won), f"{sum(r['funds_won'] or 0 for r in wl['by_year']):,} funds since FY{since}", "up"),
            ("Lost", bn(lost), f"{sum(r['funds_lost'] or 0 for r in wl['by_year']):,} funds since FY{since}", "down"),
            ("Net", bn(won - lost), dd.ROLE_LABELS[role], tone(won - lost)),
        ])
        left, right = st.columns(2, gap="large")
        for col, key, head in ((left, "won_from", f"{provider} won business from"), (right, "lost_to", f"{provider} lost business to")):
            with col:
                section(head, "Assets of the funds that moved, by the other provider involved.")
                cp = pd.DataFrame(wl[key])
                if cp.empty:
                    st.markdown('<p class="ss-note">None in this period.</p>', unsafe_allow_html=True)
                    continue
                cp["label"] = cp.net_assets_usd_bn.map(bn)
                base = alt.Chart(cp).encode(
                    y=alt.Y("counterparty:N", sort="-x", title=None, axis=alt.Axis(domain=False, ticks=False, labelLimit=200)),
                    x=alt.X("net_assets_usd_bn:Q", axis=None, scale=alt.Scale(domain=[0, float(cp.net_assets_usd_bn.max()) * 1.35])),
                    tooltip=[alt.Tooltip("counterparty:N", title="Provider"), alt.Tooltip("label:N", title="Assets"),
                             alt.Tooltip("funds:Q", title="Funds")])
                show(base.mark_bar(cornerRadiusEnd=3, height=16, color=OK if key == "won_from" else BAD)
                     + base.mark_text(align="left", dx=6, fontSize=12, color=FG).encode(text="label:N"), 40 + 26 * len(cp))
    section("Moves", f"{len(sw)} family-level moves first seen in FY{since} or later. Select a row to open the family.")
    st.session_state.sw_fams = list(sw.family) if len(sw) else []
    st.dataframe(switch_view(sw), hide_index=True, use_container_width=True, height=480, column_config=SWITCH_COLUMNS,
                 on_select=lambda: open_selected("sw_table", "sw_fams"), selection_mode="single-row", key="sw_table")

# ---------------------------------------------------------------------------- Fund families

elif page == "Fund families":
    fams = load_families(VERSION)
    names = list(fams.family)
    current = st.session_state.family
    if current and current not in names:
        names = [current] + names
    pick = st.selectbox("Fund family", names, index=names.index(current) if current in names else 0,
                        format_func=lambda f: f"{fam_name(f)}", label_visibility="collapsed")
    st.session_state.family = pick
    year = FY
    p = load_profile(VERSION, pick, year)
    if not p.get("family"):
        st.info("No data for this family.")
    else:
        mix = pd.DataFrame(p["category_mix"])
        title(fam_name(p["family"]), None,
              " · ".join(esc(x) for x in [f"FY{year}", ", ".join(p.get("main_advisers") or [])] if x))
        cust = p["providers_by_role"].get("custodian", [])
        tiles([
            ("Net assets", bn(p["net_assets_usd_bn"]), f"{p['funds']:,} funds"),
            ("Main custodian", cust[0]["provider"] if cust else "—", f"{pct(100 * cust[0]['net_assets_usd_bn'] / p['net_assets_usd_bn'])} of assets" if cust and p["net_assets_usd_bn"] else ""),
            ("Largest fund type", mix.iloc[0]["category"] if len(mix) else "—", bn(mix.iloc[0]["net_assets_usd_bn"]) if len(mix) else ""),
            ("Provider changes on file", f"{len(p['provider_changes'])}", "Across all roles, FY2019 onwards"),
        ])
        left, right = st.columns([3, 2], gap="large")
        with left:
            section("Service providers by role", f"Share of the family's FY{year} assets each provider services. Affiliated providers are marked.")
            rows = []
            for role, provs in p["providers_by_role"].items():
                total = sum(x["net_assets_usd_bn"] or 0 for x in provs) or 1
                for x in provs[:4]:
                    rows.append({"role": dd.ROLE_LABELS[role], "provider": x["provider"] + (" (affiliate)" if x["affiliated"] else ""),
                                 "share": 100 * (x["net_assets_usd_bn"] or 0) / total, "assets": x["net_assets_usd_bn"],
                                 "names": x["names_as_filed"]})
            pr = pd.DataFrame(rows)
            if len(pr):
                # One small bar chart per role: every provider gets its own row and its own label.
                for role_label in [r for r in dd.ROLE_LABELS.values() if r in set(pr.role)]:
                    part = pr[pr.role == role_label].sort_values("share", ascending=False).copy()
                    part["label"] = part.apply(lambda r: f"{r.share:.0f}% · {bn(r.assets)}", axis=1)
                    st.markdown(f'<p class="ss-note" style="margin:.6rem 0 0;font-weight:600;color:{FG}">{esc(role_label)}</p>',
                                unsafe_allow_html=True)
                    base = alt.Chart(part).encode(
                        y=alt.Y("provider:N", sort=None, title=None,
                                axis=alt.Axis(domain=False, ticks=False, labelLimit=230, labelPadding=8,
                                              labelOverlap=False)),
                        x=alt.X("share:Q", axis=None, scale=alt.Scale(domain=[0, 135])),
                        tooltip=[alt.Tooltip("provider:N", title="Provider"), alt.Tooltip("share:Q", title="Share %", format=".1f"),
                                 alt.Tooltip("assets:Q", title="Assets $bn", format=",.1f"), alt.Tooltip("names:N", title="Names as filed")])
                    show(base.mark_bar(cornerRadiusEnd=3, height=14).encode(
                        color=alt.Color("provider:N", legend=None, scale=alt.Scale(
                            domain=list(part.provider), range=[color_of(x.replace(" (affiliate)", "")) for x in part.provider])))
                         + base.mark_text(align="left", dx=6, fontSize=11, color=FG).encode(text="label:N"),
                         30 * len(part) + 16)
                with st.expander("Provider names as filed"):
                    st.dataframe(pr[["role", "provider", "share", "assets", "names"]].rename(columns={
                        "role": "Role", "provider": "Provider", "share": "Share of family", "assets": "Assets ($bn)",
                        "names": "Names as filed"}),
                        hide_index=True, use_container_width=True, column_config={
                            "Share of family": st.column_config.NumberColumn(format="%.0f%%"),
                            "Assets ($bn)": st.column_config.NumberColumn(format="$%.1f bn")})
        with right:
            section("Fund mix", f"FY{year} net assets by fund type.")
            if len(mix):
                mix = mix.copy()
                mix["label"] = mix.net_assets_usd_bn.map(bn)
                base = alt.Chart(mix).encode(
                    y=alt.Y("category:N", sort="-x", title=None, axis=alt.Axis(domain=False, ticks=False)),
                    x=alt.X("net_assets_usd_bn:Q", axis=None, scale=alt.Scale(domain=[0, float(mix.net_assets_usd_bn.max()) * 1.4])),
                    tooltip=["category", alt.Tooltip("funds:Q", title="Funds"), alt.Tooltip("label:N", title="Assets")])
                show(base.mark_bar(cornerRadiusEnd=3, height=16, color="#6A9BCC")
                     + base.mark_text(align="left", dx=6, fontSize=12, color=FG).encode(text="label:N"), 40 + 30 * len(mix))
            section("Provider changes")
            ch = pd.DataFrame(p["provider_changes"])
            if len(ch):
                ch["Role"] = ch.role.map(dd.ROLE_LABELS)
                st.dataframe(ch.rename(columns={"first_seen_fy": "First seen", "lost_by": "From", "won_by": "To", "funds": "Funds",
                                                "net_assets_usd_bn": "Assets ($bn)"})[["Role", "First seen", "From", "To", "Funds", "Assets ($bn)"]],
                             hide_index=True, use_container_width=True,
                             column_config={"First seen": st.column_config.NumberColumn(format="FY%d"),
                                            "Assets ($bn)": st.column_config.NumberColumn(format="$%.1f bn")})
            else:
                st.markdown('<p class="ss-note">No provider changes on file.</p>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------- Prospects

elif page == "Prospects":
    title("Prospects", "Fund families where a provider holds under half of a role, with their incumbents and the "
                       "services the provider already gives them.", SOURCE_LINE)
    c1, c2, c3, c4, c5 = st.columns([1.3, 1.2, 1, 1, 1])
    provs = load_providers(VERSION, "custodian")
    prov = c1.selectbox("Provider", provs, index=provs.index(ME) if ME in provs else 0, key="pr_prov")
    role = c2.selectbox("Target role", ROLE_OPTS, format_func=dd.ROLE_LABELS.get, key="pr_role")
    cat = c3.selectbox("Fund type", dd.CATEGORIES, key="pr_cat")
    min_bn = c4.selectbox("Family size", [1.0, 10.0, 50.0, 100.0], index=1, format_func=lambda v: f"${v:g}bn+")
    cross = c5.selectbox("Show", [False, True], format_func=lambda v: "Cross-sell only" if v else "All prospects")
    pr = load_prospects(VERSION, prov, role, cat, min_bn, cross)
    rows = pr["prospects"]
    if not rows:
        st.info("No prospects for this selection.")
    else:
        df = pd.DataFrame([{
            "family": r["family"], "Family": fam_name(r["family"]),
            "Assets ($bn)": r[f"{pr['target_role']}_assets_usd_bn"],
            "Incumbent": r["incumbents"][0]["provider"] if r["incumbents"] else "—",
            "Incumbent share": r["incumbents"][0]["share_pct"] if r["incumbents"] else None,
            f"{prov} share": r[f"{prov}_share_pct"],
            f"{prov} already provides": ", ".join(dd.ROLE_LABELS[x] for x in r[f"{prov}_already_serves_as"]) or "—",
            "Last change": r[f"last_{pr['target_role']}_change_fy"]} for r in rows])
        warm = df[df[f"{prov} already provides"] != "—"]
        tiles([
            ("Prospect families", f"{len(df)}", f"{dd.ROLE_LABELS[role]} · families ${min_bn:g}bn+"),
            ("Assets in play", bn(df["Assets ($bn)"].sum()), f"FY{pr['fiscal_year']}"),
            ("Warm (cross-sell)", f"{len(warm)}", f"Already use {prov} for another service"),
        ])
        section("Largest prospects by incumbent", "Bar length is the family's assets in the target role; colour is the current main provider.")
        top = df.head(15).copy()
        top["label"] = top["Assets ($bn)"].map(bn)
        legend([(p, color_of(p)) for p in dict.fromkeys(top.Incumbent)])
        base = alt.Chart(top).encode(
            y=alt.Y("Family:N", sort=None, title=None, axis=alt.Axis(domain=False, ticks=False, labelLimit=240)),
            x=alt.X("Assets ($bn):Q", axis=None, scale=alt.Scale(type="sqrt", domain=[0, float(top["Assets ($bn)"].max()) * 1.6])),
            tooltip=["Family", "Incumbent", alt.Tooltip("Incumbent share:Q", format=".0f"), alt.Tooltip("Assets ($bn):Q", format=",.0f"),
                     f"{prov} already provides"])
        show(base.mark_bar(cornerRadiusEnd=3, height=14).encode(
            color=alt.Color("Incumbent:N", scale=color_scale(top.Incumbent), legend=None))
             + base.mark_text(align="left", dx=6, fontSize=11, color=FG).encode(text="label:N"), 40 + 24 * len(top))
        section("All prospects", "Select a row to open the family.")
        st.session_state.pr_fams = list(df.family)
        st.dataframe(df.drop(columns=["family"]), hide_index=True, use_container_width=True, height=440,
                     on_select=lambda: open_selected("pr_table", "pr_fams"), selection_mode="single-row", key="pr_table",
                     column_config={"Assets ($bn)": st.column_config.NumberColumn(format="$%.1f bn"),
                                    "Incumbent share": st.column_config.NumberColumn(format="%.0f%%"),
                                    f"{prov} share": st.column_config.NumberColumn(format="%.0f%%"),
                                    "Last change": st.column_config.NumberColumn(format="FY%d")})
        st.markdown('<p class="ss-note">Families served mainly in-house (affiliated providers) are left out. N-CEN has no '
                    'contract dates or fees, so this ranks size and fit, not timing.</p>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------- Ask the analyst

elif page == "Ask the analyst":
    title("Ask the analyst", "Ask about custody and fund-servicing market share, switches and prospects in plain English.",
          "Answers use only the loaded N-CEN data and say which fiscal year each figure is for. "
          "Open <i>Show working</i> under an answer to see every data lookup behind it.")
    api_key = find_api_key()
    try:
        api_key = api_key or st.secrets.get("ANTHROPIC_API_KEY")
    except Exception:
        pass
    if not api_key:
        st.warning("Chat needs an Anthropic API key. Put `ANTHROPIC_API_KEY=sk-ant-...` in a `.env` file in "
                   "this folder (or set the environment variable), then refresh this page.")
        st.stop()

    st.session_state.setdefault("api_messages", [])
    st.session_state.setdefault("transcript", [])

    def render_tool(call: dict):
        r = call["result"]
        args = ", ".join(f"{k}={v!r}" for k, v in call["input"].items())
        st.markdown(f"`{call['name']}({args})`")
        if "error" in r:
            st.caption(f"Returned an error: {r['error']}")
            return
        for key in ("providers", "results", "series", "by_year", "prospects", "unmapped", "coverage_by_fiscal_year"):
            val = r.get(key)
            if isinstance(val, list) and val and isinstance(val[0], dict):
                df = pd.DataFrame(val)
                df = df[[c for c in df.columns if not df[c].map(lambda x: isinstance(x, (list, dict))).any()]]
                st.dataframe(df.head(25), hide_index=True, use_container_width=True)
                return
        st.json(r, expanded=False)

    def family_buttons(calls: list, key: str):
        seen = []
        for c in calls:
            r = c["result"]
            if c["name"] == "fund_family_profile" and r.get("family"):
                seen.append(r["family"])
        seen = list(dict.fromkeys(seen))
        if seen:
            cols = st.columns(min(len(seen), 4))
            for i, f in enumerate(seen[:4]):
                cols[i].button(f"Open {fam_name(f)}", key=f"{key}-{i}", on_click=open_family, args=(f,))

    for i, msg in enumerate(st.session_state.transcript):
        with st.chat_message(msg["role"]):
            st.markdown(md_safe(msg["text"]))
            if msg.get("tools"):
                with st.expander(f"Show working ({len(msg['tools'])} data lookup{'s' if len(msg['tools']) != 1 else ''})"):
                    for call in msg["tools"]:
                        render_tool(call)
                family_buttons(msg["tools"], key=f"m{i}")

    asked = sum(1 for m in st.session_state.transcript if m["role"] == "user")
    question = None
    if not st.session_state.transcript:
        section("Try one of these")
        cols = st.columns(len(SUGGESTIONS))
        for c, s in zip(cols, SUGGESTIONS):
            if c.button(s, use_container_width=True):
                question = s
    typed = st.chat_input("Ask about custody market share, switches or prospects…", disabled=asked >= MAX_QUESTIONS)
    question = typed or question
    if asked >= MAX_QUESTIONS:
        st.info(f"This session has reached its limit of {MAX_QUESTIONS} questions. Refresh the page to start a new one.")

    if question:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        st.session_state.transcript.append({"role": "user", "text": question})
        checkpoint = len(st.session_state.api_messages)
        st.session_state.api_messages.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(md_safe(question))
        with st.chat_message("assistant"):
            status = st.status("Looking up the filings…", expanded=False)
            box = st.empty()
            text, calls = "", []
            try:
                with closing(connect()) as conn:
                    for ev in stream_turn(client, CustodyTools(conn), st.session_state.api_messages, DEFAULT_MODEL):
                        if ev["type"] == "text":
                            text += ev["text"]
                            box.markdown(md_safe(text) + "▌")
                        elif ev["type"] == "tool":
                            calls.append(ev)
                            status.write(f"Used `{ev['name']}`")
                            text += "\n\n" if text and not text.endswith("\n") else ""
                        else:
                            text += "\n\n" + ev["text"]
                status.update(label=f"Done · {len(calls)} data lookup{'s' if len(calls) != 1 else ''}", state="complete")
            except anthropic.APIError as exc:
                status.update(label="The request failed", state="error")
                text = text or f"Sorry, the request to Claude failed: {exc}"
                # Roll back this question's partial exchange so the next one starts cleanly
                del st.session_state.api_messages[checkpoint:]
            box.markdown(md_safe(text))
        st.session_state.transcript.append({"role": "assistant", "text": text, "tools": calls})
        st.rerun()
