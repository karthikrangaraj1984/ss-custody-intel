"""End-to-end test on the synthetic fixture:  python -m pytest tests  (or python tests/test_pipeline.py)"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import make_fixture  # noqa: E402
from custody_intel import analytics as a, load, model  # noqa: E402


def build_db():
    tmp = Path(tempfile.mkdtemp())
    make_fixture.build(tmp / "zips")
    conn = load.connect(tmp / "t.sqlite")
    load.load_all(conn, tmp / "zips")
    stats = model.build(conn)
    return conn, stats


def test_all():
    conn, stats = build_db()
    assert stats["fund_years"] == 3 * 10, stats  # 9 funds x 3 years, amendment de-duplicated

    # amendment wins: ALPHA FY2023 assets doubled
    na = conn.execute("SELECT net_assets FROM fund_year WHERE fund_key='S000000001' AND fiscal_year=2023").fetchone()[0]
    assert abs(na - 2.2e9) < 1, na

    # foreign sub-custodians excluded
    assert not conn.execute("SELECT 1 FROM fund_provider WHERE provider_group IN ('Citi','HSBC')").fetchone()

    ms = a.market_share("custodian", 2024, conn=conn)
    shares = {p["provider"]: p for p in ms["providers"]}
    total = 6.6e9 + 8e9 + 40e9 + 0.8e9 + 0.2e9  # ALPHA 1.2+2.4+3.6 (+20% by FY24)... check below
    alpha24 = sum(1e9 * i * 1.2 for i in (1, 2, 3))
    total = alpha24 + 8e9 + 40e9 + 0.8e9 + 0.2e9 + 0.5e9
    assert abs(shares["State Street"]["net_assets_usd_bn"] - round((alpha24 + 40e9) / 1e9, 2)) < 0.01
    assert abs(ms["total_net_assets_usd_bn"] - round(total / 1e9, 2)) < 0.01
    assert shares["U.S. Bank"]["funds"] == 0.5 and shares["UMB"]["funds"] == 0.5  # split weight

    ex = a.market_share("custodian", 2024, exclude_affiliated=True, conn=conn)
    assert {p["provider"]: p for p in ex["providers"]}["State Street"]["net_assets_usd_bn"] == round(alpha24 / 1e9, 2)

    sw = a.provider_switches("custodian", "State Street", "won", since_year=2023, conn=conn)["results"]
    assert len(sw) == 1 and sw[0]["family"] == "ALPHA FUNDS" and sw[0]["lost_by"] == "BNY" and sw[0]["funds_moved"] == 3
    assert sw[0]["first_seen_fy"] == 2023 and sw[0]["flagged_in_filing"] == 1

    nt = a.provider_switches("custodian", "Northern Trust", "won", since_year=2022, conn=conn)["results"]
    assert nt[0]["family"] == "BETA ETF TRUST" and nt[0]["lost_by"] == "JPMorgan"

    added = a.provider_switches("custodian", "UMB", "won", since_year=2022, level="fund", conn=conn)["results"]
    assert added[0]["change_type"] == "added"

    ph = a.provider_switches("custodian", "BNY", "won", since_year=2022, level="fund", conn=conn)["results"]
    assert ph[0]["change_type"] == "phased" and ph[0]["year_to"] == 2023 and ph[0]["completed_fy"] == 2024, ph
    assert a.win_loss_summary("BNY", "custodian", 2022, conn=conn)["won_from"][0]["counterparty"] == "Fifth Third"
    assert not conn.execute("SELECT 1 FROM fund_provider WHERE provider_names LIKE '%Depository%' OR provider_names = 'Epsilon Trust'").fetchone()

    wl = a.win_loss_summary("State Street", "custodian", 2022, conn=conn)
    assert wl["won_from"][0]["counterparty"] == "BNY"
    jl = a.win_loss_summary("JPMorgan", "custodian", 2022, conn=conn)
    assert jl["lost_to"][0]["counterparty"] == "Northern Trust"

    tr = a.share_trend(["State Street", "BNY"], start_year=2022, end_year=2024, conn=conn)
    assert tr["series"][1]["BNY"] < tr["series"][0]["BNY"]

    prof = a.fund_family_profile("BET1", conn=conn)  # ticker lookup
    assert prof["family"] == "BETA ETF TRUST"
    assert prof["providers_by_role"]["administrator"][0]["provider"] == "State Street"

    pros = a.find_prospects("State Street", "custodian", 2024, min_family_assets_usd_bn=0.5, conn=conn)["prospects"]
    fams = [p["family"] for p in pros]
    assert "BETA ETF TRUST" in fams and "GAMMA" not in fams and "ALPHA FUNDS" not in fams
    beta = next(p for p in pros if p["family"] == "BETA ETF TRUST")
    assert set(beta["State Street_already_serves_as"]) == {"administrator", "transfer_agent"}

    um = a.unmapped_providers("custodian", 2024, conn=conn)["unmapped"]
    assert um and um[0]["provider_group"] == "Tiny Prairie Trust Bank"

    st = a.data_status(conn)
    assert st["latest_complete_fiscal_year"] == 2024
    sf = a.search_funds("delta", conn=conn)["results"]
    assert sf and sf[0]["fund_key"].startswith("CIK:")
    print("all checks passed", stats)


if __name__ == "__main__":
    test_all()
