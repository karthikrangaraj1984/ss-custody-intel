"""Download the SEC DERA Form N-CEN quarterly data sets.

The SEC asks automated clients to send a User-Agent with contact details and to
stay under 10 requests per second. Set SEC_USER_AGENT, e.g.
    export SEC_USER_AGENT="Karthik Rangaraj your.email@example.com"
"""
from __future__ import annotations

import re
import time
import urllib.request
from pathlib import Path

from . import config

LINK_RE = re.compile(r"(?:https://www\.sec\.gov)?(/files/dera/data/form-n-cen-data-sets/(\d{4})q([1-4])_ncen[^\"'\s<>]*\.zip)")


def _request(url: str) -> urllib.request.Request:
    if not config.SEC_USER_AGENT:
        raise SystemExit(
            "Set SEC_USER_AGENT first, e.g.  export SEC_USER_AGENT=\"Your Name you@example.com\"\n"
            "(the SEC blocks requests without a contact User-Agent)."
        )
    return urllib.request.Request(url, headers={"User-Agent": config.SEC_USER_AGENT, "Accept-Encoding": "identity"})


def list_available() -> dict[str, str]:
    """Return {'2025q3': url, ...} scraped from the SEC data set page (latest file per quarter)."""
    with urllib.request.urlopen(_request(config.DATASET_PAGE), timeout=60) as r:
        html = r.read().decode("utf-8", "replace")
    found: dict[str, str] = {}
    for path, year, q in LINK_RE.findall(html):
        quarter = f"{year}q{q}"
        url = "https://www.sec.gov" + path
        # Re-posted files carry a suffix (_0, _1 ...); keep the lexically last one.
        if quarter not in found or url > found[quarter]:
            found[quarter] = url
    return dict(sorted(found.items()))


def download(since: str = "2018q3", refresh_latest: int = 2, force: bool = False) -> list[Path]:
    """Download every quarter >= `since`. The newest `refresh_latest` quarters are always
    re-downloaded, because the SEC re-posts recent quarters when late filings are added."""
    config.ZIP_DIR.mkdir(parents=True, exist_ok=True)
    available = {q: u for q, u in list_available().items() if q >= since}
    if not available:
        raise SystemExit("No N-CEN zip links found on the SEC page; the page layout may have changed.")
    newest = sorted(available)[-refresh_latest:] if refresh_latest else []
    out = []
    for quarter, url in available.items():
        target = config.ZIP_DIR / f"{quarter}_ncen.zip"
        if target.exists() and not force and quarter not in newest:
            out.append(target)
            continue
        print(f"downloading {quarter}  {url}")
        tmp = target.with_suffix(".part")
        with urllib.request.urlopen(_request(url), timeout=600) as r, open(tmp, "wb") as f:
            while chunk := r.read(1 << 20):
                f.write(chunk)
        tmp.replace(target)
        out.append(target)
        time.sleep(0.5)  # be polite
    return out
