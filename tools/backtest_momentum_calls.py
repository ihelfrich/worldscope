"""Backtest momentum calls over the lake's history and write the prior.

For each past day d, build the momentum board exactly as it stood on d (only
data collected up to d), make that day's calls, and grade each against what
was collected on d + HORIZON_DAYS. The tallies per momentum class are written
to data/momentum_calls_backtest.json, which worldscope.momentum_calls adds to
its live graded record, so stated probabilities start from evidence instead
of an uninformative 50%.

    python tools/backtest_momentum_calls.py --end 2026-11-12 --days 26
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from worldscope import momentum_calls as mc  # noqa: E402
from worldscope.momentum import build_momentum  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--end", default=(date.today() - timedelta(days=1)).isoformat(),
                    help="last day whose target day is known (default yesterday)")
    ap.add_argument("--days", type=int, default=26)
    args = ap.parse_args(argv)
    end = date.fromisoformat(args.end)
    tally: dict[str, list[int]] = {}
    days_used = []
    for back in range(mc.HORIZON_DAYS + 1, args.days + mc.HORIZON_DAYS + 1):
        d = end - timedelta(days=back)
        target = mc.sections_on_day(d + timedelta(days=mc.HORIZON_DAYS))
        if not target:
            continue                     # no collection that day: can't grade
        days_used.append(d.isoformat())
        for c in mc.make_calls(build_momentum(d).get("stories", []), today=d):
            t = tally.setdefault(c["_bucket"], [0, 0])
            t[0] += mc.grade_key_on_day(c["_key"], target) == "YES"
            t[1] += 1
    out = {"method": mc.METHOD, "horizon_days": mc.HORIZON_DAYS,
           "min_sections": mc.MIN_SECTIONS, "made_days": sorted(days_used),
           "by_class": {k: {"yes": v[0], "n": v[1]} for k, v in sorted(tally.items())}}
    mc.BACKTEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    mc.BACKTEST_PATH.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    print(json.dumps(out["by_class"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
