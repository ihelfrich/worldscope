"""
people.py — CLI for querying the world-leaders + officials roster.

Usage:
    python -m worldscope.people --build-index             # one-time, ~3 min
    python -m worldscope.people --hos                     # all heads of state
    python -m worldscope.people --hog                     # all heads of government
    python -m worldscope.people --country "Ukraine"       # officials from Ukraine
    python -m worldscope.people --role "minister of finance"   # by position pattern
    python -m worldscope.people --top                     # PEP counts by country
    python -m worldscope.people --export roster.json      # full dump

Combines Wikidata SPARQL (heads of state / govt for every sovereign country)
with the local OpenSanctions PEP index (~625K politically-exposed persons).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .lib import peps, wikidata


def cmd_hos(args):
    rows = wikidata.current_heads_of_state()
    for r in rows:
        print(f"  {r['country']:30s}  {r['leader_name']:35s}  {r.get('position','')[:50]}")
    print(f"\n{len(rows)} heads of state")


def cmd_hog(args):
    rows = wikidata.current_heads_of_government()
    for r in rows:
        print(f"  {r['country']:30s}  {r['leader_name']:35s}")
    print(f"\n{len(rows)} heads of government")


def cmd_country(args):
    rows = peps.by_country(args.country, limit=args.limit)
    if not rows:
        print(f"  (no PEPs found for {args.country!r}; is the index built?)")
        return
    for r in rows:
        pos = (r.get("position") or "")[:80]
        print(f"  [{r.get('modified','?')[:10]:10s}] {r['name'][:50]:50s}  {pos}")
    print(f"\n{len(rows)} PEPs from {args.country}")


def cmd_role(args):
    rows = peps.by_role_pattern(args.role, limit=args.limit)
    if not rows:
        print(f"  (no PEPs match role pattern {args.role!r})")
        return
    for r in rows:
        print(f"  [{r.get('modified','?')[:10]:10s}] {r['name'][:40]:40s}  {r['countries']:20s}  {(r['position'] or '')[:60]}")


def cmd_top(args):
    h = peps.count_by_country()
    for c, n in list(h.items())[:30]:
        print(f"  {c[:25]:25s}  {n}")


def cmd_export(args):
    out = {
        "heads_of_state": wikidata.current_heads_of_state(),
        "heads_of_government": wikidata.current_heads_of_government(),
        "pep_counts_by_country": peps.count_by_country(),
    }
    Path(args.export).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {args.export}")


def cmd_build(args):
    if not args.force and peps.is_index_built():
        print("PEP index already built; pass --force to rebuild")
        return
    n = peps.build_index()
    print(f"indexed {n} PEPs into {peps.INDEX_PATH}")


def main():
    p = argparse.ArgumentParser(description="World leaders + officials roster")
    sub = p.add_subparsers(dest="cmd")

    p_b = sub.add_parser("build-index", help="(re)build the PEP index from the FtM corpus")
    p_b.add_argument("--force", action="store_true")
    p_b.set_defaults(func=cmd_build)

    p_hos = sub.add_parser("hos", help="list all current heads of state")
    p_hos.set_defaults(func=cmd_hos)

    p_hog = sub.add_parser("hog", help="list all current heads of government")
    p_hog.set_defaults(func=cmd_hog)

    p_c = sub.add_parser("country", help="show PEPs from a country")
    p_c.add_argument("country", help="country name or ISO code")
    p_c.add_argument("--limit", type=int, default=20)
    p_c.set_defaults(func=cmd_country)

    p_r = sub.add_parser("role", help="show PEPs whose position matches a pattern")
    p_r.add_argument("role", help="regex / substring to match position")
    p_r.add_argument("--limit", type=int, default=30)
    p_r.set_defaults(func=cmd_role)

    p_t = sub.add_parser("top", help="show PEP counts by country")
    p_t.set_defaults(func=cmd_top)

    p_e = sub.add_parser("export", help="export full roster + counts to JSON")
    p_e.add_argument("export", help="output path")
    p_e.set_defaults(func=cmd_export)

    # Convenience: top-level flags also work
    p.add_argument("--hos", action="store_true", help="shortcut for `hos`")
    p.add_argument("--hog", action="store_true", help="shortcut for `hog`")
    p.add_argument("--country", help="shortcut for `country <name>`")
    p.add_argument("--role", help="shortcut for `role <pattern>`")
    p.add_argument("--top", action="store_true", help="shortcut for `top`")
    p.add_argument("--export", help="shortcut for `export <path>`")
    p.add_argument("--build-index", dest="build_index", action="store_true")
    p.add_argument("--limit", type=int, default=20)

    args = p.parse_args()
    if args.build_index: cmd_build(argparse.Namespace(force=False)); return
    if args.hos:         cmd_hos(args); return
    if args.hog:         cmd_hog(args); return
    if args.country:     cmd_country(args); return
    if args.role:        cmd_role(args); return
    if args.top:         cmd_top(args); return
    if args.export:      cmd_export(args); return
    if hasattr(args, "func"): args.func(args); return
    p.print_help()


if __name__ == "__main__":
    main()
