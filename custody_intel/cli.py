"""Command line:  python -m custody_intel.cli <command>

  download [--since 2018q3] [--force]   fetch SEC DERA N-CEN quarterly zips into data/zips
  load                                  load every zip in data/zips into SQLite (raw tables)
  build                                 rebuild curated tables (run after editing providers.csv)
  refresh [--since 2018q3]              download + load + build
  status                                show what is loaded
  share [--role custodian] [--year Y] [--category ETF] [--exclude-affiliated]
  switches [--role custodian] [--provider "State Street"] [--direction won] [--since Y]
  winloss [--provider "State Street"] [--role custodian]
  family NAME
  prospects [--provider "State Street"] [--role custodian]
"""
from __future__ import annotations

import argparse
import json

from . import analytics, config, download, load, model


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="custody_intel", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("download"); d.add_argument("--since", default="2018q3"); d.add_argument("--force", action="store_true")
    sub.add_parser("load"); sub.add_parser("build"); sub.add_parser("status")
    r = sub.add_parser("refresh"); r.add_argument("--since", default="2018q3")
    s = sub.add_parser("share"); s.add_argument("--role", default="custodian"); s.add_argument("--year", type=int)
    s.add_argument("--category"); s.add_argument("--exclude-affiliated", action="store_true"); s.add_argument("--weight", default="assets")
    sw = sub.add_parser("switches"); sw.add_argument("--role", default="custodian"); sw.add_argument("--provider")
    sw.add_argument("--direction", default="any"); sw.add_argument("--since", type=int); sw.add_argument("--level", default="family")
    sw.add_argument("--limit", type=int, default=25)
    wl = sub.add_parser("winloss"); wl.add_argument("--provider", default="State Street"); wl.add_argument("--role", default="custodian")
    wl.add_argument("--since", type=int)
    fm = sub.add_parser("family"); fm.add_argument("name"); fm.add_argument("--year", type=int)
    pr = sub.add_parser("prospects"); pr.add_argument("--provider", default="State Street"); pr.add_argument("--role", default="custodian")
    pr.add_argument("--category"); pr.add_argument("--cross-sell-only", action="store_true")
    args = p.parse_args(argv)

    def show(x):
        print(json.dumps(x, indent=1, default=str))

    if args.cmd in ("download", "refresh"):
        download.download(since=args.since, force=getattr(args, "force", False))
    if args.cmd in ("load", "refresh"):
        conn = load.connect(); load.load_all(conn); conn.close()
    if args.cmd in ("build", "refresh"):
        conn = load.connect(); print(model.build(conn)); conn.close()
        print(f"database: {config.DB_PATH}")
    if args.cmd == "status":
        show(analytics.data_status())
    elif args.cmd == "share":
        show(analytics.market_share(args.role, args.year, args.weight, args.category, args.exclude_affiliated))
    elif args.cmd == "switches":
        show(analytics.provider_switches(args.role, args.provider, args.direction, args.since, level=args.level, limit=args.limit))
    elif args.cmd == "winloss":
        show(analytics.win_loss_summary(args.provider, args.role, args.since))
    elif args.cmd == "family":
        show(analytics.fund_family_profile(args.name, args.year))
    elif args.cmd == "prospects":
        show(analytics.find_prospects(args.provider, args.role, category=args.category, cross_sell_only=args.cross_sell_only))


if __name__ == "__main__":
    main()
