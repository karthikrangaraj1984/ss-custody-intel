r"""Copy the curated tables (no raw tables) into data/curated.sqlite for sharing/review.
Run from the project folder:  .\.venv\Scripts\python.exe tools\export_curated.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from custody_intel import config  # noqa: E402

out = config.DB_PATH.parent / "curated.sqlite"
out.unlink(missing_ok=True)
src = sqlite3.connect(config.DB_PATH)
src.execute(f"ATTACH DATABASE '{out.as_posix()}' AS c")
for t in ["load_log", "filing", "fund_year", "fund_provider", "switch_event", "switch_leg", "fund_ticker"]:
    src.execute(f"CREATE TABLE c.{t} AS SELECT * FROM main.{t}")
# small raw sample of custodian rows for checking custody-type values
src.execute("""CREATE TABLE c.custody_types AS SELECT CUSTODY_TYPE, IS_SUB_CUSTODIAN, count(*) AS n
               FROM main.raw_custodian GROUP BY 1, 2""")
for (sql,) in src.execute("""SELECT sql FROM main.sqlite_master WHERE type='index' AND sql IS NOT NULL
                             AND tbl_name IN ('filing','fund_year','fund_provider','switch_event','switch_leg','fund_ticker')""").fetchall():
    src.execute(sql.replace("CREATE INDEX ", "CREATE INDEX c.", 1).replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX c.", 1))
src.commit()
src.execute("DETACH DATABASE c")
src.close()
c = sqlite3.connect(out); c.execute("VACUUM"); c.close()
print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
