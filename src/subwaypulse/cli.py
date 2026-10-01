"""subwaypulse: snapshot the MTA real-time feeds, store route health, render the status board."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import truststore

from subwaypulse.feeds import snapshot
from subwaypulse.metrics import route_health
from subwaypulse.report import render
from subwaypulse.store import Store

DAY = 24 * 3600


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="subwaypulse", description=__doc__)
    p.add_argument("--db", default="subwaypulse.sqlite")
    p.add_argument("--out", default="site/index.html")
    p.add_argument("--keep-days", type=int, default=7)
    args = p.parse_args(argv)

    truststore.inject_into_ssl()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    snap = snapshot()
    health = route_health(snap)
    store = Store(args.db)
    try:
        store.record(snap.taken_at, health)
        store.prune(snap.taken_at - args.keep_days * DAY)
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(render(store.latest(), store.history(snap.taken_at - DAY), store.snapshots(), snap.errors),
                       encoding="utf-8")
    finally:
        store.close()

    for h in health:
        print(f"{h.route:>3}  trains={h.trains:<3} typical={h.median_headway_min!s:>5}m  "
              f"worst={h.worst_gap_min!s:>5}m  {h.status}")
    for feed, error in snap.errors.items():
        print(f"  ! {feed}: {error}")
    return 0 if health else 1


if __name__ == "__main__":
    sys.exit(main())
