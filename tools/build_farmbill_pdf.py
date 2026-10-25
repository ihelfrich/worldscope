#!/usr/bin/env python3
"""Build a single PDF from the farm-bill analysis + all 12 per-title notes.

Pure-Python (fpdf2), no system libraries. Robust manual markdown layout with
Unicode->Latin-1 transliteration so the core fonts never choke. Reproducible:

    python tools/build_farmbill_pdf.py
"""
from __future__ import annotations

import re
from pathlib import Path

from fpdf import FPDF

BASE = Path(__file__).resolve().parent.parent / "research_reports" / "farm-bill-2026"
OUT = BASE / "Farm-Bill-2026-Notes.pdf"

NOTE_ORDER = [
    "00_commodities", "01_conservation", "02_trade", "03_nutrition",
    "04_credit", "05_rural_development", "06_research", "07_forestry",
    "08_energy", "09_horticulture", "10_crop_insurance", "11_miscellaneous",
]

# Unicode -> Latin-1 transliteration (core PDF fonts are Latin-1 only).
TRANS = {
    "—": "-", "–": "-", "−": "-", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", "→": "->", "←": "<-",
    "≤": "<=", "≥": ">=", "•": "*", "×": "x", "·": "-",
    "≈": "~", " ": " ", " ": " ", "‑": "-", "️": "",
}
_EMOJI = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)


def sanitize(text: str) -> str:
    for k, v in TRANS.items():
        text = text.replace(k, v)
    text = _EMOJI.sub("", text)
    return text.encode("latin-1", "replace").decode("latin-1")


def strip_inline(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"`(.+?)`", r"\1", text)
    text = re.sub(r"\[(.+?)\]\((.+?)\)", r"\1", text)
    return text


def _png_size(path: Path) -> tuple[int, int]:
    """(width, height) in px from a PNG IHDR — no Pillow dependency."""
    with open(path, "rb") as fh:
        head = fh.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return (1600, 900)
    return (int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big"))


class PDF(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(140)
        self.cell(0, 8, f"Farm Bill 2026 (H.R. 7567)  -  page {self.page_no()}",
                  align="C")
        self.set_text_color(0)


def render_markdown(pdf: PDF, md_text: str) -> None:
    lines = md_text.splitlines()
    in_code = False
    for raw in lines:
        line = sanitize(raw.rstrip())
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            pdf.set_font("Courier", size=8)
            pdf.multi_cell(0, 4, line)
            continue
        if not line.strip():
            pdf.ln(2)
            continue
        pdf.set_x(pdf.l_margin)  # always start each block at the left margin
        # Embedded figure: ![alt](relative/path.png)
        mi = re.match(r"!\[(.*?)\]\((.*?)\)", line.strip())
        if mi:
            alt, rel = mi.group(1), mi.group(2)
            img = (BASE / rel).resolve()
            if img.exists():
                usable = pdf.w - pdf.l_margin - pdf.r_margin
                iw, ih = _png_size(img)
                draw_h = usable * (ih / iw)
                # page-break if the figure won't fit in the remaining space
                if pdf.get_y() + draw_h + 8 > pdf.h - pdf.b_margin:
                    pdf.add_page()
                pdf.ln(2)
                pdf.image(str(img), x=pdf.l_margin, w=usable)
                if alt:
                    pdf.set_font("Helvetica", "I", 8)
                    pdf.set_text_color(110)
                    pdf.multi_cell(0, 4, "Figure: " + strip_inline(alt))
                    pdf.set_text_color(0)
                pdf.ln(2)
            continue
        # Table row -> render cells joined (simple, readable).
        if line.lstrip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if all(set(c) <= {"-", ":", " "} for c in cells):
                continue  # separator row
            pdf.set_font("Helvetica", size=8)
            pdf.multi_cell(0, 4.5, "   " + "  |  ".join(strip_inline(c) for c in cells))
            continue
        # Headings.
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = len(m.group(1))
            txt = strip_inline(m.group(2))
            sizes = {1: 16, 2: 13, 3: 11, 4: 10, 5: 10, 6: 10}
            pdf.ln(2)
            pdf.set_font("Helvetica", "B", sizes.get(level, 10))
            pdf.multi_cell(0, 6, txt)
            pdf.ln(1)
            continue
        # Bullets (indent expressed as leading spaces, capped, never via cursor).
        mb = re.match(r"^(\s*)[-*]\s+(.*)$", line)
        if mb:
            depth = min(len(mb.group(1)) // 2, 3)
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Helvetica", size=9)
            pdf.multi_cell(0, 4.6, "   " * depth + "- " + strip_inline(mb.group(2)))
            continue
        # Numbered.
        mn = re.match(r"^(\s*)(\d+)\.\s+(.*)$", line)
        if mn:
            pdf.set_x(pdf.l_margin)
            pdf.set_font("Helvetica", size=9)
            pdf.multi_cell(0, 4.6, f"{mn.group(2)}. " + strip_inline(mn.group(3)))
            continue
        # Body.
        pdf.set_x(pdf.l_margin)
        pdf.set_font("Helvetica", size=9)
        pdf.multi_cell(0, 4.8, strip_inline(line))


def main() -> None:
    pdf = PDF(format="Letter")
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.set_margins(left=16, top=16, right=16)

    # Cover.
    pdf.add_page()
    pdf.ln(38)
    pdf.set_font("Helvetica", "B", 22)
    pdf.multi_cell(0, 11, "Farm, Food, and National Security Act of 2026", align="C")
    pdf.set_font("Helvetica", "", 13)
    pdf.ln(2)
    pdf.multi_cell(0, 8, "H.R. 7567  -  Engrossed in House (passed 2026-04-30)", align="C")
    pdf.ln(8)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(
        0, 6,
        "Every-word analyst review: a cross-cutting analysis followed by "
        "verbatim section-by-section notes for all 12 titles (460 sections). "
        "Source: GovInfo BILLS-119hr7567eh.", align="C")
    pdf.ln(10)
    pdf.set_font("Helvetica", "I", 10)
    pdf.multi_cell(0, 6, "Prepared for Dr. Ian Helfrich  -  WorldScope", align="C")

    files = [BASE / "ANALYSIS.md"] + [BASE / "notes" / f"{s}.md" for s in NOTE_ORDER]
    for path in files:
        if not path.exists():
            continue
        pdf.add_page()
        render_markdown(pdf, path.read_text(encoding="utf-8"))

    pdf.output(str(OUT))
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB, {pdf.pages_count} pages)")


if __name__ == "__main__":
    main()
