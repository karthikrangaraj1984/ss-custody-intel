"""Load DERA N-CEN quarterly zips into raw SQLite tables (all text, as filed).

Each raw table gets a `_quarter` column so re-loading a quarter replaces it cleanly.
Columns are added on the fly, because the SEC has added fields over time.
"""
from __future__ import annotations

import csv
import io
import re
import sqlite3
import sys
import zipfile
from pathlib import Path

from . import config

csv.field_size_limit(sys.maxsize)
QUARTER_RE = re.compile(r"(\d{4}q[1-4])", re.I)


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    db_path = db_path or config.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("CREATE TABLE IF NOT EXISTS load_log (quarter TEXT PRIMARY KEY, zip_name TEXT, loaded_at TEXT, row_counts TEXT)")
    return conn


def _existing_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _ensure_table(conn: sqlite3.Connection, table: str, columns: list[str]) -> None:
    existing = _existing_columns(conn, table)
    if not existing:
        cols = ", ".join(f'"{c}" TEXT' for c in ["_quarter", *columns])
        conn.execute(f'CREATE TABLE "{table}" ({cols})')
        conn.execute(f'CREATE INDEX "ix_{table}_q" ON "{table}"(_quarter)')
        return
    for c in columns:
        if c not in existing:
            conn.execute(f'ALTER TABLE "{table}" ADD COLUMN "{c}" TEXT')


def _members_by_table(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map TABLE name -> member path, matching file stems case-insensitively, any folder."""
    out = {}
    for name in zf.namelist():
        stem = Path(name).stem.upper()
        if Path(name).suffix.lower() in (".tsv", ".txt") and stem in config.TABLES:
            out[stem] = name
    return out


def load_zip(conn: sqlite3.Connection, zip_path: Path) -> dict[str, int]:
    m = QUARTER_RE.search(zip_path.name)
    if not m:
        raise ValueError(f"cannot tell the quarter from file name {zip_path.name}")
    quarter = m.group(1).lower()
    counts: dict[str, int] = {}
    with zipfile.ZipFile(zip_path) as zf:
        members = _members_by_table(zf)
        for table in config.TABLES:
            raw = f"raw_{table.lower()}"
            if table not in members:
                counts[table] = 0
                continue
            with zf.open(members[table]) as fh:
                reader = csv.reader(io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline=""),
                                    delimiter="\t", quoting=csv.QUOTE_NONE)
                header = [h.strip().upper() for h in next(reader)]
                keep = list(range(len(header)))
                if table == "FUND_REPORTED_INFO":
                    keep = [i for i, h in enumerate(header) if h in config.FUND_INFO_COLUMNS]
                cols = [header[i] for i in keep]
                _ensure_table(conn, raw, cols)
                conn.execute(f'DELETE FROM "{raw}" WHERE _quarter = ?', (quarter,))
                placeholders = ", ".join("?" * (len(cols) + 1))
                collist = ", ".join(f'"{c}"' for c in ["_quarter", *cols])
                sql = f'INSERT INTO "{raw}" ({collist}) VALUES ({placeholders})'
                n = 0
                batch = []
                for row in reader:
                    if not row:
                        continue
                    row += [""] * (len(header) - len(row))
                    batch.append([quarter, *[(row[i].strip() or None) for i in keep]])
                    if len(batch) >= 5000:
                        conn.executemany(sql, batch)
                        n += len(batch)
                        batch.clear()
                if batch:
                    conn.executemany(sql, batch)
                    n += len(batch)
                counts[table] = n
    conn.execute("INSERT OR REPLACE INTO load_log VALUES (?, ?, datetime('now'), ?)",
                 (quarter, zip_path.name, ", ".join(f"{k}={v}" for k, v in counts.items())))
    conn.commit()
    return counts


def load_all(conn: sqlite3.Connection, zip_dir: Path | None = None) -> None:
    zip_dir = zip_dir or config.ZIP_DIR
    zips = sorted(zip_dir.glob("*ncen*.zip"))
    if not zips:
        raise SystemExit(f"no N-CEN zips in {zip_dir}; run `python -m custody_intel.cli download` first")
    for z in zips:
        counts = load_zip(conn, z)
        print(f"loaded {z.name}: FUND_REPORTED_INFO={counts.get('FUND_REPORTED_INFO', 0)} CUSTODIAN={counts.get('CUSTODIAN', 0)}")
