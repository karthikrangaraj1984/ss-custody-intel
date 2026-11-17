"""Resolve the many spellings of a service provider to one provider group.

Mapping lives in config/providers.csv (group, regex pattern). Patterns are tried
top to bottom against the upper-cased name; first match wins. Anything unmapped
keeps a cleaned version of its own name, so smaller providers still show up in
market share and switch detection. Use the `unmapped_providers` tool to find the
biggest unmapped names and add them to the CSV.
"""
from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

from . import config

_SUFFIXES = re.compile(
    r"\b(THE|INC|INCORPORATED|LLC|L L C|LTD|LIMITED|CORP|CORPORATION|CO|COMPANY|N ?A|NATIONAL ASSOCIATION|"
    r"PLC|LP|L P|SA|AG)\b"
)


def clean_name(name: str | None) -> str:
    if not name:
        return ""
    s = name.upper().replace("&", " & ")
    s = re.sub(r"[.,'’()\"]", " ", s)
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


@lru_cache(maxsize=1)
def _rules(path: str) -> list[tuple[str, re.Pattern]]:
    rules = []
    p = Path(path)
    if not p.exists():
        return rules
    with open(p, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pat = (row.get("pattern") or "").strip()
            grp = (row.get("group") or "").strip()
            if pat and grp:
                rules.append((grp, re.compile(pat, re.I)))
    return rules


@lru_cache(maxsize=200_000)
def provider_group(name: str | None) -> str | None:
    if not name or not name.strip():
        return None
    upper = name.upper()
    if re.fullmatch(r"\W*(N/?A|NONE|NOT APPLICABLE|NA|-+)\W*", upper.strip()):
        return None
    for grp, rx in _rules(str(config.PROVIDER_MAP)):
        if rx.search(upper):
            return grp
    cleaned = clean_name(name)
    return cleaned.title() if cleaned else None


def is_mapped(name: str | None) -> int:
    if not name:
        return 0
    upper = name.upper()
    return int(any(rx.search(upper) for _, rx in _rules(str(config.PROVIDER_MAP))))


def known_groups() -> list[str]:
    seen = []
    for g, _ in _rules(str(config.PROVIDER_MAP)):
        if g not in seen:
            seen.append(g)
    return seen
