# Farm Bill 2026 — full-text analysis (H.R. 7567)

Every-word analyst review of the **Farm, Food, and National Security Act of 2026**,
**H.R. 7567, Engrossed-in-House (as-passed) text**, passed by the House 224–200 on
**2026-04-30**. Reauthorizes USDA programs through **FY2031**.

## What's here

| Path | Contents |
|---|---|
| [`ANALYSIS.md`](ANALYSIS.md) | The cross-cutting analysis — read this first. Synthesizes all 12 titles: the reconciliation-vs-farm-bill split, the national-security throughline, where the money moves, the deregulation theme, winners/losers. |
| [`Farm-Bill-2026-Notes.pdf`](Farm-Bill-2026-Notes.pdf) | Single PDF: analysis + all 12 title note-sets (129 pp). |
| [`notes/`](notes/) | Verbatim, structured section-by-section notes, one file per title (455 of 460 sections; the 2 gaps are the front-matter short-title/definitions sections). |
| [`source/`](source/) | The source text: `BILLS-119hr7567eh.{xml,htm,pdf}`, MODS metadata, the per-title split (`by_title/`), and `section_index.json` / `manifest.json`. |

## Method

The engrossed XML (~141k words) was split into its 12 statutory titles (460
sections) by `source/by_title/`. Each title was read in full by a dedicated
analyst that produced structured notes (does / amends / numbers / dates / type /
who's affected / flags) for every section. The analysis reads those 12 note-sets
in context. Every figure traces to a cited section.

## Rebuild the PDF

```bash
pip install fpdf2
python tools/build_farmbill_pdf.py
```

## Headline finding

This is effectively the **second half of a two-part farm bill**: the marquee
SNAP changes and commodity reference-price increases were already enacted in the
**2025 budget-reconciliation law (P.L. 119-21)**, so H.R. 7567 carries the
*policy* priorities — national security (foreign farmland, China/Russia screens,
trade enforcement), a near-doubling of trade-promotion funding, deregulation of
forest/chemical management, and a "precision agriculture" industrial policy —
while merely extending the traditional commodity and nutrition machinery through
FY2031. See `ANALYSIS.md`.
