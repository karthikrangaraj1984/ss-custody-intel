"""Build small synthetic N-CEN quarterly zips in the SEC DERA layout, for testing only.

Scenario (fiscal years 2022-2024, Dec year-ends):
  ALPHA  - 3 mutual funds, custody BNY -> State Street in FY2023 (flagged), plus foreign sub-custodians
           that must be ignored; FY2023 filed twice (N-CEN then N-CEN/A with corrected assets)
  BETA   - 2 ETFs, custody JPMorgan -> Northern Trust in FY2024; State Street is administrator throughout
  GAMMA  - 2 ETFs, in-house (affiliated) State Street custody and SSGA administration
  DELTA  - closed-end fund (no series id), U.S. Bank custody, adds UMB in FY2024
  EPS    - small fund with an unmapped custodian
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def d(y, m, day):
    return f"{day:02d}-{MON[m - 1]}-{y}"


def tsv(header, rows):
    out = io.StringIO()
    out.write("\t".join(header) + "\n")
    for r in rows:
        out.write("\t".join("" if r.get(h) is None else str(r.get(h)) for h in header) + "\n")
    return out.getvalue()


HEAD = {
    "SUBMISSION": ["ACCESSION_NUMBER", "SUBMISSION_TYPE", "CIK", "FILING_DATE", "REPORT_ENDING_PERIOD", "IS_REPORT_PERIOD_LT_12MONTH"],
    "REGISTRANT": ["ACCESSION_NUMBER", "REGISTRANT_NAME", "FILE_NUM", "CIK", "LEI", "IS_FAMILY_INVESTMENT_COMPANY",
                   "FAMILY_INVESTMENT_COMPANY_NAME", "INVESTMENT_COMPANY_TYPE", "TOTAL_SERIES"],
    "FUND_REPORTED_INFO": ["FUND_ID", "ACCESSION_NUMBER", "FUND_NAME", "SERIES_ID", "LEI", "IS_ETF", "IS_INDEX",
                           "IS_MONEY_MARKET", "MONTHLY_AVG_NET_ASSETS", "DAILY_AVG_NET_ASSETS",
                           "HAS_CUSTODIAN_HIRED_FIRED_MI", "HAS_ADMIN_HIRED_FIRED", "AGG_COMMISSION"],
    "CUSTODIAN": ["FUND_ID", "SOURCE", "CUSTODIAN_NAME", "CUSTODIAN_LEI", "STATE", "COUNTRY", "IS_AFFILIATED",
                  "IS_SUB_CUSTODIAN", "CUSTODY_TYPE", "OTHER_CUSTODIAN_DESC"],
    "ADMIN": ["FUND_ID", "ADMIN_NAME", "ADMIN_LEI", "OTHER_IDENTIFYING_NUM", "STATE", "COUNTRY", "IS_AFFILIATED", "IS_SUB_ADMIN"],
    "TRANSFER_AGENT": ["FUND_ID", "SOURCE", "TRANSFERAGENT_NAME", "FILE_NUM", "TRANSFERAGENT_LEI", "STATE", "COUNTRY",
                       "IS_AFFILIATED", "IS_SUBTRANSFER_AGENT"],
    "SECURITY_LENDING": ["FUND_ID", "SECURITY_LENDING_SEQNUM", "SECURITIES_AGENT_NAME", "SECURITIES_AGENT_LEI", "IS_AFFILIATED"],
    "SHARES_OUTSTANDING": ["FUND_ID", "CLASS_NAME", "CLASS_ID", "TICKER"],
    "ADVISER": ["FUND_ID", "SOURCE", "ADVISER_TYPE", "ADVISER_NAME", "IS_AFFILIATED"],
}
BANK = "Bank - section 17(f)(1) (15 U.S.C. 80a-17(f)(1))"
FOREIGN = "Foreign custodian - rule 17f-5 (17 CFR 270.17f-5)"


def build(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tables = {}  # quarter -> table -> rows
    acc_n = [0]

    def filing(quarter, cik, reg, fam, ictype, fy, funds, sub_type="N-CEN", filed=None):
        acc_n[0] += 1
        acc = f"0000000000-{fy % 100:02d}-{acc_n[0]:06d}"
        t = tables.setdefault(quarter, {k: [] for k in HEAD})
        t["SUBMISSION"].append({"ACCESSION_NUMBER": acc, "SUBMISSION_TYPE": sub_type, "CIK": cik,
                                "FILING_DATE": filed or d(fy + 1, 3, 1), "REPORT_ENDING_PERIOD": d(fy, 12, 31),
                                "IS_REPORT_PERIOD_LT_12MONTH": "N"})
        t["REGISTRANT"].append({"ACCESSION_NUMBER": acc, "REGISTRANT_NAME": reg, "CIK": cik,
                                "IS_FAMILY_INVESTMENT_COMPANY": "Y" if fam else "N",
                                "FAMILY_INVESTMENT_COMPANY_NAME": fam, "INVESTMENT_COMPANY_TYPE": ictype,
                                "TOTAL_SERIES": len(funds)})
        for f in funds:
            fid = f"{acc}_{cik}_{f.get('series') or ''}"
            t["FUND_REPORTED_INFO"].append({"FUND_ID": fid, "ACCESSION_NUMBER": acc, "FUND_NAME": f["name"],
                                            "SERIES_ID": f.get("series"), "IS_ETF": "Y" if f.get("etf") else "N",
                                            "IS_INDEX": "N", "IS_MONEY_MARKET": "N",
                                            "MONTHLY_AVG_NET_ASSETS": f["na"],
                                            "HAS_CUSTODIAN_HIRED_FIRED_MI": f.get("cflag", "N"),
                                            "HAS_ADMIN_HIRED_FIRED": "N", "AGG_COMMISSION": "1000"})
            for c in f["cust"]:
                t["CUSTODIAN"].append({"FUND_ID": fid, "SOURCE": "FUND", "CUSTODIAN_NAME": c[0],
                                       "IS_AFFILIATED": c[1] if len(c) > 1 else "N",
                                       "IS_SUB_CUSTODIAN": c[2] if len(c) > 2 else "N",
                                       "CUSTODY_TYPE": c[3] if len(c) > 3 else BANK, "COUNTRY": "US"})
            for a in f.get("admin", []):
                t["ADMIN"].append({"FUND_ID": fid, "ADMIN_NAME": a[0], "IS_AFFILIATED": a[1], "IS_SUB_ADMIN": "N"})
            for ta in f.get("ta", []):
                t["TRANSFER_AGENT"].append({"FUND_ID": fid, "TRANSFERAGENT_NAME": ta, "IS_AFFILIATED": "N",
                                            "IS_SUBTRANSFER_AGENT": "N"})
            if f.get("ticker"):
                t["SHARES_OUTSTANDING"].append({"FUND_ID": fid, "CLASS_NAME": "Class A", "CLASS_ID": "C1", "TICKER": f["ticker"]})
            t["ADVISER"].append({"FUND_ID": fid, "SOURCE": "FUND", "ADVISER_TYPE": "Adviser",
                                 "ADVISER_NAME": f"{(fam or reg).title()} Advisers LLC", "IS_AFFILIATED": "Y"})
        return acc

    for fy in (2022, 2023, 2024):
        q = f"{fy + 1}q1"
        # ALPHA
        alpha_cust = [("The Bank of New York Mellon",)] if fy == 2022 else [("State Street Bank and Trust Company",)]
        foreign = [("Citibank Europe plc", "N", "Y", FOREIGN), ("HSBC Bank Bermuda", "N", "N", FOREIGN)]
        alpha = [{"name": f"Alpha Fund {i}", "series": f"S00000000{i}", "na": 1e9 * i * (1 + (fy - 2022) * 0.1),
                  "cust": alpha_cust + foreign, "cflag": "Y" if fy == 2023 else "N",
                  "admin": [("Alpha Fund Services LLC", "Y")], "ta": ["SS&C Global Investor & Distribution Solutions, Inc."],
                  "ticker": f"ALPH{i}X"} for i in (1, 2, 3)]
        filing(q, "1000001", "ALPHA FUNDS TRUST", "ALPHA FUNDS", "N-1A", fy, alpha)
        if fy == 2023:  # amendment filed later with corrected assets
            for f in alpha:
                f["na"] = f["na"] * 2
            filing("2024q3", "1000001", "ALPHA FUNDS TRUST", "ALPHA FUNDS", "N-1A", fy, alpha, "N-CEN/A", d(2024, 8, 15))
        # BETA
        beta_cust = [("JPMorgan Chase Bank, N.A.",)] if fy < 2024 else [("The Northern Trust Company",)]
        beta = [{"name": f"Beta ETF {i}", "series": f"S00000010{i}", "na": 4e9, "etf": True, "cust": beta_cust,
                 "admin": [("State Street Bank and Trust Company", "N")], "ta": ["State Street Bank and Trust Company"],
                 "ticker": f"BET{i}"} for i in (1, 2)]
        filing(q, "1000002", "BETA ETF TRUST", None, "N-1A", fy, beta)
        # GAMMA (in-house)
        gamma = [{"name": f"Gamma Select ETF {i}", "series": f"S00000020{i}", "na": 20e9, "etf": True,
                  "cust": [("State Street Bank & Trust Co.", "Y")], "admin": [("SSGA Funds Management, Inc.", "Y")],
                  "ta": ["State Street Bank and Trust Company"], "ticker": f"GAM{i}"} for i in (1, 2)]
        filing(q, "1000003", "GAMMA SERIES TRUST", "GAMMA", "N-1A", fy, gamma)
        # DELTA closed-end, no series
        dcust = [("U.S. Bank, N.A.",)] + ([("UMB Bank, n.a.",)] if fy == 2024 else [])
        filing(q, "1000004", "DELTA INCOME FUND", None, "N-2", fy,
               [{"name": "Delta Income Fund", "series": None, "na": 0.8e9, "cust": dcust,
                 "admin": [("U.S. Bancorp Fund Services, LLC", "N")], "ta": ["Computershare Trust Company"]}])
        # ZETA phased migration: Fifth Third -> adds BNY in FY2023 -> drops Fifth Third in FY2024
        zcust = {2022: [("Fifth Third Bank, N.A.",)], 2023: [("Fifth Third Bank, N.A.",), ("The Bank of New York Mellon",)],
                 2024: [("The Bank of New York Mellon",)]}[fy]
        filing(q, "1000006", "ZETA TRUST", None, "N-1A", fy,
               [{"name": "Zeta Growth Fund", "series": "S000000401", "na": 0.5e9, "cust": zcust}])
        # also a DTC securities-depository row and self-custody row that must be ignored
        # EPS unmapped
        filing(q, "1000005", "EPSILON TRUST", None, "N-1A", fy,
               [{"name": "Epsilon Value Fund", "series": "S000000301", "na": 0.2e9, "cust": [("Tiny Prairie Trust Bank",),
                         ("Depository Trust Company", "N", "N", "Securities depository - rule 17f-4 (17 CFR 270.17f-4)"),
                         ("Epsilon Trust", "N", "N", "Self - rule 17f-2 (17 CFR 270.17f-2)")]}])

    paths = []
    for quarter, tbls in tables.items():
        p = out_dir / f"{quarter}_ncen.zip"
        with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, rows in tbls.items():
                zf.writestr(f"{quarter}_ncen/{name}.tsv", tsv(HEAD[name], rows))
        paths.append(p)
    return paths


if __name__ == "__main__":
    for p in build(Path(sys.argv[1] if len(sys.argv) > 1 else "fixture_zips")):
        print(p)
