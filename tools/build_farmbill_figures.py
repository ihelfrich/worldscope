#!/usr/bin/env python3
"""Render data-driven figures for the 2026 Farm Bill analysis.

Two kinds of figure:
  - Derived from the analyst notes (provision-type mix per title), parsed live
    from notes/*.md so the charts stay honest to the notes.
  - Curated datasets transcribed from the notes (with section citations in
    comments) for the bill's most quantitative stories: FSA loan limits,
    headline funding moves, and forestry NEPA categorical-exclusion acreage caps.

Outputs PNGs to research_reports/farm-bill-2026/figures/.

    python tools/build_farmbill_figures.py
"""
from __future__ import annotations

import collections
import glob
import json
import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

BASE = Path(__file__).resolve().parent.parent / "research_reports" / "farm-bill-2026"
FIG = BASE / "figures"
FIG.mkdir(parents=True, exist_ok=True)

plt.rcParams.update({
    "font.size": 10,
    "axes.titlesize": 13,
    "axes.titleweight": "bold",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "figure.dpi": 150,
})

TITLE_LABEL = {
    "00_commodities": "I · Commodities",
    "01_conservation": "II · Conservation",
    "02_trade": "III · Trade",
    "03_nutrition": "IV · Nutrition",
    "04_credit": "V · Credit",
    "05_rural_development": "VI · Rural Dev.",
    "06_research": "VII · Research",
    "07_forestry": "VIII · Forestry",
    "08_energy": "IX · Energy",
    "09_horticulture": "X · Horticulture",
    "10_crop_insurance": "XI · Crop Insurance",
    "11_miscellaneous": "XII · Miscellaneous",
}

# additive -> blue family, neutral -> grey, subtractive -> red
TYPE_ORDER = ["NEW", "INCREASE", "REFORM", "EXTEND", "STUDY", "REPEAL", "DECREASE"]
TYPE_COLOR = {
    "NEW": "#1b7837", "INCREASE": "#5aae61", "REFORM": "#2c7fb8",
    "EXTEND": "#969696", "STUDY": "#807dba", "REPEAL": "#d6604d",
    "DECREASE": "#b2182b",
}
KEYWORDS = ["NEW", "EXTEND", "REFORM", "REPEAL", "INCREASE", "DECREASE"]


def tally_types() -> "collections.OrderedDict":
    per = collections.OrderedDict()
    for f in sorted(glob.glob(str(BASE / "notes" / "*.md"))):
        stem = Path(f).stem
        c = collections.Counter()
        for line in open(f, encoding="utf-8"):
            m = re.match(r"\s*-\s*\*\*Type:\*\*\s*(.*)", line)
            if not m:
                continue
            up = m.group(1).upper()
            for t in KEYWORDS:
                if t in up:
                    c[t] += 1
            if "STUDY" in up or "REPORT" in up:
                c["STUDY"] += 1
        per[stem] = c
    return per


def caption(ax, text):
    ax.annotate(text, xy=(0, -0.16), xycoords="axes fraction",
                fontsize=7.5, color="#666", ha="left", va="top")


# --------------------------------------------------------------------------- #
def fig_action_mix(per):
    labels = [TITLE_LABEL[s] for s in per]
    y = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(8.2, 5.2))
    left = np.zeros(len(labels))
    for t in TYPE_ORDER:
        vals = np.array([per[s].get(t, 0) for s in per], float)
        ax.barh(y, vals, left=left, color=TYPE_COLOR[t], label=t.title(), height=0.72)
        left += vals
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("provisions (multi-label: a section can be both, e.g. NEW + REFORM)")
    ax.set_title("What each title actually does — provision mix by title")
    ax.legend(ncol=7, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.10),
              frameon=False)
    caption(ax, "Source: analyst Type: tags across all 460 sections (notes/). "
                "Forestry (VIII) is the most NEW-heavy; Research (VII) is mostly EXTEND.")
    fig.tight_layout()
    fig.savefig(FIG / "fig1_action_mix_by_title.png", bbox_inches="tight")
    plt.close(fig)


def fig_overall(per):
    overall = collections.Counter()
    for c in per.values():
        overall.update(c)
    order = [t for t in TYPE_ORDER if overall.get(t)]
    vals = [overall[t] for t in order]
    fig, ax = plt.subplots(figsize=(7.4, 3.6))
    bars = ax.barh(order, vals, color=[TYPE_COLOR[t] for t in order])
    ax.invert_yaxis()
    for b, v in zip(bars, vals):
        ax.text(b.get_width() + 2, b.get_y() + b.get_height() / 2, str(v),
                va="center", fontsize=9, fontweight="bold")
    ax.set_xlim(0, max(vals) * 1.12)
    ax.set_title("The whole bill, by provision type")
    ax.set_xlabel("count across 460 sections (multi-label)")
    caption(ax, "REFORM + EXTEND dominate: this is a policy-and-reauthorization "
                "vehicle, not a clean-sheet rewrite. 152 genuinely NEW provisions; only 16 repeals.")
    fig.tight_layout()
    fig.savefig(FIG / "fig2_overall_action_types.png", bbox_inches="tight")
    plt.close(fig)


def fig_loan_limits():
    # Title V — Credit (notes/04): §5105, §5202, §5203.
    rows = [
        ("Direct farm ownership", 600_000, 850_000),
        ("Guaranteed farm ownership", 1_750_000, 3_500_000),
        ("Direct operating", 400_000, 750_000),
        ("Guaranteed operating", 1_750_000, 3_000_000),
        ("Operating microloan", 50_000, 100_000),
    ]
    labels = [r[0] for r in rows]
    before = [r[1] for r in rows]
    after = [r[2] for r in rows]
    y = np.arange(len(labels))
    h = 0.38
    fig, ax = plt.subplots(figsize=(8.0, 4.3))
    ax.barh(y + h / 2, before, height=h, color="#bdbdbd", label="before")
    ax.barh(y - h / 2, after, height=h, color="#2c7fb8", label="after (H.R. 7567)")
    for yi, b, a in zip(y, before, after):
        ax.text(b + 4e4, yi + h / 2, f"${b/1e3:,.0f}k", va="center", fontsize=8, color="#555")
        ax.text(a + 4e4, yi - h / 2, f"${a/1e6:,.2f}M" if a >= 1e6 else f"${a/1e3:,.0f}k",
                va="center", fontsize=8, fontweight="bold")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("loan limit (USD)")
    ax.set_xlim(0, 3_900_000)
    ax.set_title("FSA farm-loan limits roughly double")
    ax.legend(loc="lower right", frameon=False)
    caption(ax, "Source: Title V (Credit), §§5105/5202/5203. Indexing also rebased "
                "from input prices to land values (§5106).")
    fig.tight_layout()
    fig.savefig(FIG / "fig3_fsa_loan_limits.png", bbox_inches="tight")
    plt.close(fig)


def fig_funding_moves():
    # $ millions/yr. Sources noted per row (Titles I/II/III/IV/VI).
    rows = [
        ("Ag trade promotion (total)", 255, 533),     # III §3201
        ("  Market Access Program", 200, 410),        # III §3201
        ("  Foreign Market Dev.", 34.5, 82),          # III §3201
        ("EQIP (FY27 -> FY31)", 2530, 3255),          # II §2501
        ("RCPP", 0, 450),                             # II §2501 (newly named line)
        ("State soil-health (new)", 0, 100),          # II §2303
        ("ReConnect broadband", 0, 350),              # VI §6201 (permanent)
        ("Local Farmers Feeding (new)", 0, 200),      # IV §4306
        ("Forest Conserv. Easement (FY31)", 0, 65),   # II §2501/§2701
    ]
    labels = [r[0] for r in rows]
    before = [r[1] for r in rows]
    after = [r[2] for r in rows]
    y = np.arange(len(labels))
    h = 0.38
    fig, ax = plt.subplots(figsize=(8.4, 5.0))
    ax.barh(y + h / 2, before, height=h, color="#bdbdbd", label="prior / baseline")
    ax.barh(y - h / 2, after, height=h, color="#1b7837", label="H.R. 7567")
    for yi, b, a in zip(y, before, after):
        if b:
            ax.text(b + 25, yi + h / 2, f"${b:,.0f}M", va="center", fontsize=7.5, color="#555")
        ax.text(a + 25, yi - h / 2, f"${a:,.0f}M", va="center", fontsize=7.5, fontweight="bold")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("$ millions per year")
    ax.set_xlim(0, 3600)
    ax.set_title("Where the money moves (annual, $M)")
    ax.legend(loc="lower right", frameon=False)
    caption(ax, "Sources: Title III §3201 (trade); Title II §§2501/2303/2701 (conservation); "
                "Title VI §6201 (broadband); Title IV §4306 (local food). '0' = newly created/named line.")
    fig.tight_layout()
    fig.savefig(FIG / "fig4_funding_moves.png", bbox_inches="tight")
    plt.close(fig)


def fig_ce_caps():
    # Title VIII — Forestry (notes/07). NEPA categorical-exclusion acreage caps.
    rows = [
        ("Collaborative restoration (§8402)", 3000, 10000),
        ("Wildfire resilience (§8403)", 3000, 10000),
        ("Fuel breaks (§8404)", 3000, 10000),
        ("Fuel-reduction CE (§8407, new)", 0, 10000),
        ("Hazard trees (§8401, new)", 0, 6000),
        ("Sage-grouse/mule deer rangeland (§8405)", 0, 7500),
        ("Save Our Sequoias groves (§8705, new)", 0, 2000),
    ]
    labels = [r[0] for r in rows]
    before = [r[1] for r in rows]
    after = [r[2] for r in rows]
    y = np.arange(len(labels))
    h = 0.38
    fig, ax = plt.subplots(figsize=(8.4, 4.6))
    ax.barh(y + h / 2, before, height=h, color="#bdbdbd", label="before")
    ax.barh(y - h / 2, after, height=h, color="#d6604d", label="H.R. 7567")
    for yi, b, a in zip(y, before, after):
        if b:
            ax.text(b + 120, yi + h / 2, f"{b:,}", va="center", fontsize=7.5, color="#555")
        ax.text(a + 120, yi - h / 2, f"{a:,} ac", va="center", fontsize=7.5, fontweight="bold")
    ax.set_yticks(y, labels)
    ax.invert_yaxis()
    ax.set_xlabel("acreage cap exempt from full NEPA review (acres)")
    ax.set_xlim(0, 11500)
    ax.set_title("Forestry: NEPA categorical-exclusion caps expand sharply")
    ax.legend(loc="lower right", frameon=False)
    caption(ax, "Source: Title VIII §§8401-8407, 8705. '0' before = a newly created "
                "categorical exclusion. Three existing CEs tripled 3,000 -> 10,000 acres.")
    fig.tight_layout()
    fig.savefig(FIG / "fig5_forestry_ce_caps.png", bbox_inches="tight")
    plt.close(fig)


def fig_title_size():
    man = json.loads((BASE / "source" / "manifest.json").read_text())
    man = sorted(man, key=lambda m: m["file"])
    labels = [TITLE_LABEL.get(Path(m["file"]).stem.replace(".txt", ""),
                              m["title"]) for m in man]
    secs = [m["sections"] for m in man]
    words = [m["words"] for m in man]
    y = np.arange(len(labels))
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(8.6, 4.8), sharey=True)
    ax1.barh(y, secs, color="#2c7fb8")
    ax1.set_yticks(y, labels); ax1.invert_yaxis()
    ax1.set_title("Sections per title"); ax1.set_xlabel("sections")
    for yi, v in zip(y, secs):
        ax1.text(v + 1, yi, str(v), va="center", fontsize=8)
    ax2.barh(y, [w / 1000 for w in words], color="#807dba")
    ax2.set_title("Words per title"); ax2.set_xlabel("thousands of words")
    for yi, v in zip(y, words):
        ax2.text(v / 1000 + 0.2, yi, f"{v/1000:.0f}k", va="center", fontsize=8)
    fig.suptitle("Anatomy of the bill — 460 sections, ~133k words of operative text",
                 fontsize=13, fontweight="bold")
    fig.tight_layout()
    fig.savefig(FIG / "fig6_title_size.png", bbox_inches="tight")
    plt.close(fig)


def main():
    per = tally_types()
    fig_action_mix(per)
    fig_overall(per)
    fig_loan_limits()
    fig_funding_moves()
    fig_ce_caps()
    fig_title_size()
    pngs = sorted(p.name for p in FIG.glob("*.png"))
    print(f"wrote {len(pngs)} figures to {FIG}:")
    for p in pngs:
        print("  -", p)


if __name__ == "__main__":
    main()
