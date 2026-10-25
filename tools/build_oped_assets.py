#!/usr/bin/env python3
"""Generate editorial assets for the Securitas op-ed (hero banner + a coin motif).

Pure matplotlib; no external images (none are reachable here, and we avoid any
copyright question by drawing our own). Reproducible:

    python tools/build_oped_assets.py
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle

OUT = Path(__file__).resolve().parent.parent / "research_reports" / "farm-bill-2026" / "oped" / "assets"
OUT.mkdir(parents=True, exist_ok=True)

INK = "#16201c"        # deep slate green
CREAM = "#f2ead6"      # parchment
GOLD = "#c8a24b"       # muted gold
MUTE = "#9bb0a4"       # sage grey


def hero():
    fig = plt.figure(figsize=(8.0, 4.5), dpi=200)
    fig.patch.set_facecolor(INK)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_facecolor(INK)
    ax.set_xlim(0, 16); ax.set_ylim(0, 9); ax.axis("off")

    # faint "coin" motif, lower right
    for r, lw, a in [(2.55, 2.0, 0.5), (2.2, 1.0, 0.35)]:
        ax.add_patch(Circle((13.1, 3.0), r, fill=False, ec=GOLD, lw=lw, alpha=a))
    ax.text(13.1, 3.0, "S", ha="center", va="center", color=GOLD, alpha=0.45,
            fontsize=70, family="serif", fontweight="bold")
    ax.text(13.1, 0.25, "SECVRITAS  ·  S·C", ha="center", va="center",
            color=GOLD, alpha=0.5, fontsize=8, family="serif")

    # title
    ax.text(1.0, 6.0, "S E C U R I T A S", ha="left", va="center",
            color=CREAM, fontsize=44, family="serif", fontweight="bold")
    ax.text(1.05, 4.75, "or, How to Be Afraid for a Living",
            ha="left", va="center", color=GOLD, fontsize=19,
            family="serif", style="italic")
    # rule
    ax.plot([1.05, 9.6], [4.05, 4.05], color=MUTE, lw=0.8, alpha=0.7)
    ax.text(1.05, 3.4,
            "On a farm bill that has stumbled onto the oldest growth\n"
            "industry in the world: the manufacture of danger.",
            ha="left", va="top", color=MUTE, fontsize=11.5, family="serif")
    ax.text(1.05, 0.7,
            "AN ESSAY ON THE 2026 FARM BILL  ·  H.R. 7567, AS PASSED BY THE HOUSE, APRIL 30 2026",
            ha="left", va="center", color=GOLD, fontsize=8.0, family="serif",
            alpha=0.85)

    fig.savefig(OUT / "hero.png", facecolor=INK)
    plt.close(fig)


def coin():
    """A small standalone coin glyph for the closing flourish."""
    fig = plt.figure(figsize=(2.6, 2.6), dpi=200)
    fig.patch.set_facecolor("none")
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off")
    ax.set_xlim(-1.3, 1.3); ax.set_ylim(-1.3, 1.3)
    ax.add_patch(Circle((0, 0), 1.18, fc=GOLD, ec="#9c7d35", lw=3, alpha=0.95))
    ax.add_patch(Circle((0, 0), 1.0, fill=False, ec=INK, lw=1.2, alpha=0.55))
    ax.text(0, 0.05, "S", ha="center", va="center", color=INK, fontsize=46,
            family="serif", fontweight="bold")
    ax.text(0, -0.78, "SECVRITAS", ha="center", va="center", color=INK,
            fontsize=8.5, family="serif")
    fig.savefig(OUT / "coin.png", facecolor="none", transparent=True)
    plt.close(fig)


if __name__ == "__main__":
    hero()
    coin()
    print("wrote:", *(p.name for p in sorted(OUT.glob("*.png"))))
