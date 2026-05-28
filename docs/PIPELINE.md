# WORLDSCOPE pipeline wiring — what's connected to what

This document traces the daily-brief orchestration so a contributor or
agent can see, in one place, what each step produces, what consumes it,
and where the artifacts land. Use it to verify nothing is dangling.

## High-level flow

```
                 ┌──────────────────────────────────────────────┐
                 │  cron 07:00 UTC  →  daily-brief.yml         │
                 └──────────────────────────────────────────────┘
                                       │
                                       ▼
                          ┌─────────────────────────┐
                          │ python -m worldscope.brief │
                          └─────────────────────────┘
                                       │
   ┌─────────────────────────┬─────────┴──────────┬───────────────────────┐
   ▼                         ▼                    ▼                       ▼
SECTIONS                  ANALYSIS              ARTIFACTS              RENDER
(step 1)                  (1a-1d)               (1d-bis–1d-septies)    (5)
   │                         │                    │                       │
states[sid]              embeddings,           cross_section.json     dist/index.html
sections_html            anomaly maps,         (lake/sections/_meta/)  dist/<date>.html
synth_by_section         ukraine maps,         today.json, entities,   dist/sections/...
   │                     section-volume.png    signals.json (data/)    dist/graph/
   │                         │                 figures.json (data/)    dist/threads/
   │                         │                 graph.json (data/)
   │                         │                 threads.json (data/)
   │                         │                    │                       │
   └─────────────────────────┴────────────────────┴───────────────────────┘
                                       │
                                       ▼
                               bundle.zip + commit
```

## Step-by-step

| Step       | Module                            | Output                                                  | Consumed by                  |
|------------|-----------------------------------|---------------------------------------------------------|------------------------------|
| 1          | `worldscope/sections/`            | `states[sid]`, `synth_by_section[sid]`, `sections_html` | Steps 1d-septies, 4, 5, 6    |
| 1a         | `worldscope/embeddings.py`        | `lake/db/worldscope.sqlite` (`record_embeddings`)       | MCP semantic_search          |
| 1b         | `worldscope/graphics.py`          | `figures/daily/<date>/*.png`                            | Step 1e mirroring            |
| 1c         | `worldscope/cartography.py`       | `figures/daily/<date>/maps/*.png`                       | Step 1e mirroring            |
| 1d         | `worldscope/cartography_ukraine.py`| Ukraine theater PNGs                                    | Step 1e mirroring            |
| 1d-bis     | `analysis/cross_section.py`        | `lake/sections/_meta/<date>/cross_section.json`         | 1d-quater, 1d-septies, 5     |
| 1d-ter     | `worldscope/site_builder.py`       | `dist/sections/<id>/<date>.html`                        | Direct nav                   |
| 1d-quater  | `worldscope/lake_export.py`        | `dist/data/{today,entities,signals}.json`               | Chat widget, 1d-quinquies    |
| 1d-quinquies| `worldscope/figures_engine.py`    | `dist/data/figures.json`                                | Step 5 figures band          |
| 1d-sexies  | `worldscope/graph_export.py` + `graph_page.py` | `dist/data/graph.json`, `dist/graph/index.html` | `/graph/` page client-side |
| 1d-septies | `worldscope/threads.py` + `threads_page.py`    | `dist/data/threads.json`, `dist/threads/...`    | Step 5 (hero + threads band)|
| 1e         | inline shutil                      | `briefings/<date>-<name>.png`                            | `tools/render_brief.py`      |
| 2          | `worldscope/trends.py`             | `trends[sid]`                                            | Step 4 overview               |
| 3          | `worldscope/calendar.py`           | `cal_items`                                              | Step 4 overview               |
| 4          | `worldscope/overview.py`           | `overview_md`                                            | Step 5, 6, 7                  |
| 5          | `worldscope/render.py`             | `dist/index.html`, `dist/<date>.html`                    | Pages deploy                  |
| 6          | `worldscope/bundle.py`             | `dist/zips/<date>.zip`                                   | Pushover, desk-officer        |
| 7          | inline write                       | `dist/<date>.md`                                         | `tools/render_brief.py`       |

## What `render.py` actually consumes (step 5)

`render_page()` signature:

```python
render_page(
    today, sections_html, out_dir,
    overview_md=...,            # step 4
    archive_dates=...,           # _list_archive()
    states=...,                  # step 1
    synth_by_section=...,        # step 1
    cross_section=...,           # 1d-bis (re-read from disk)
    figures=...,                 # 1d-quinquies (re-read from disk)
    threads=...,                 # 1d-septies (re-read from disk)
    store_db_path=...,           # data/store.sqlite (for volume σ)
    network_seed_json=...,       # built from cross_section
)
```

Every kwarg has a producer step. No orphans.

## What the desk-officer routine consumes

The desk-officer Claude session (external — runs at 09:00 UTC against
`dist/zips/<date>.zip`) reads:

  - The full bundle ZIP
  - Section markdown summaries (`lake/sections/<id>/<date>/summary.md`)
  - Structured records (`lake/sections/<id>/<date>/structured.json`)
  - Cross-section signals (`cross_section.json` inside the bundle)

Writes:

  - `briefings/<date>.md` — composed narrative

Then `.github/workflows/render-briefings.yml` triggers on push to
`briefings/*.md`:

  1. **Fact-check** every claim (`worldscope.fact_check --fail-on FAIL`).
     Annotates in place with `[⚠ actual ≈ $X]`. Build FAILS if any
     numeric claim diverges from the lake.
  2. `tools/render_brief.py` → `dist/briefings/<date>.html`
  3. Commits + pages-deploys

## Section state machine — visible in the section card

Each section card surfaces one of these five visual treatments:

| State                | Visual                                                   |
|----------------------|----------------------------------------------------------|
| `fresh`              | source-tier badge + "N new · M total" + items list      |
| `fresh_empty`        | teal dot · "clean pull · no signal in watch areas today"|
| `carry_forward`      | gold pill · "carried · YYYY-MM-DD (Nd ago)"             |
| `stale_after_failure`| crimson pill · "stale · last good YYYY-MM-DD (Nd ago)"  |
| `no_data`            | grey pill · "section unavailable today"                  |

Reader can tell at a glance whether an empty card is a quiet day or a
broken pull.

## Things to verify after wiring changes

  - All 105 tests run (60 pass without dev env, all 85 relevant pass)
  - `python -m worldscope.brief --help` resolves
  - `python -m worldscope.fact_check --help` resolves
  - Regenerated `dist/index.html` contains all bands in order:
    topnav, hero, threads, figures, overview, sections, archive, footer
  - No orphan asset references (`network.js`, `worldscope-chat.js`,
    `worldscope-figures.js`, `worldscope-graph.js` all exist)
