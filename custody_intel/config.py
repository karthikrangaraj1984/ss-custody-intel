"""Paths and settings. Everything can be overridden with environment variables."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_DIR = Path(os.environ.get("CUSTODY_INTEL_DATA", PROJECT_ROOT / "data"))
ZIP_DIR = DATA_DIR / "zips"
DB_PATH = Path(os.environ.get("CUSTODY_INTEL_DB", DATA_DIR / "custody_intel.sqlite"))
PROVIDER_MAP = Path(os.environ.get("CUSTODY_INTEL_PROVIDERS", PROJECT_ROOT / "config" / "providers.csv"))

# SEC requires a descriptive User-Agent with contact details for automated access.
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "")

DATASET_PAGE = "https://www.sec.gov/dera/data/form-ncen-data-sets"
ZIP_BASE = "https://www.sec.gov/files/dera/data/form-n-cen-data-sets/"

# DERA tables this project loads (file stem inside each quarterly zip).
TABLES = [
    "SUBMISSION",
    "REGISTRANT",
    "FUND_REPORTED_INFO",
    "CUSTODIAN",
    "ADMIN",
    "TRANSFER_AGENT",
    "SECURITY_LENDING",
    "SHAREHOLDER_SERVICING_AGENT",
    "ADVISER",
    "SHARES_OUTSTANDING",
]

# FUND_REPORTED_INFO is wide; keep only what the model uses.
FUND_INFO_COLUMNS = [
    "FUND_ID", "ACCESSION_NUMBER", "FUND_NAME", "SERIES_ID", "LEI", "IS_FIRST_FILING",
    "IS_ETF", "IS_ETMF", "IS_INDEX", "IS_INTERVAL", "IS_FUND_OF_FUND", "IS_MASTER_FEEDER",
    "IS_MONEY_MARKET", "IS_TARGET_DATE", "IS_SEC_LENDING_AUTHORIZED", "DID_LEND_SECURITIES",
    "MONTHLY_AVG_NET_ASSETS", "DAILY_AVG_NET_ASSETS",
    "HAS_CUSTODIAN_HIRED_FIRED_MI", "HAS_CUSTODIAN_HIRED_FIRED_CE",
    "HAS_ADMIN_HIRED_FIRED", "HAS_XAGENT_HIRED_FIRED_MI", "HAS_XAGENT_HIRED_FIRED_CE",
    "HAS_SH_SRVC_HIRED_FIRED",
]

ROLES = ["custodian", "administrator", "transfer_agent", "securities_lending", "shareholder_servicing"]
