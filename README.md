# WORLDSCOPE

Daily global political, economic, and OSINT briefing engine. Pulls from
~40 free / freemium sources, detects what changed since yesterday,
synthesizes a tight executive paragraph per section, and renders to a
single HTML page archived day by day.

Built by Dr. Ian Helfrich on the chassis pattern from ECONSCOPE and LEXSCOPE.

## What it does (Phase 1 — current)

Sections that ship working today:

- **🏛️ U.S. Federal Action** — every executive order, presidential memo,
  rule, and proposed rule from the Federal Register (last 7 days),
  diffed against yesterday's pull.

## What it does (Phase 1 — planned this week)

- **🏦 Macro + central banks** — FRED daily releases, Fed/ECB/BoE/BIS speeches
- **⚖️ Sanctions + legal + filings** — OpenSanctions deltas, CourtListener
  new opinions, key EDGAR 8-K filings
- **📊 Markets snapshot** — FX, sovereign yields, equity indices, commodities
- **🌍 News digest by region** — GDELT top stories filtered to a country watchlist
- **💬 Commentary + forecasts** — Tooze, Setser, Levine, Smith, Milanović, Weber
  substack posts; Metaculus + GoodJudgment forecast moves
- **✈️ VIP flight convergence** — OpenSky Network, detect 3+ VIP aircraft
  arriving at the same airport within 72h (signals diplomatic activity)

## Architecture

```
worldscope/
├── sections/        one module per briefing section
├── store/           SQLite snapshot store (delta detection)
├── synth.py         LLM synthesis (Claude API) with grounding constraints
├── render.py        HTML page renderer
├── brief.py         orchestrator + CLI
└── .github/workflows/daily-brief.yml   06:00 ET cron + GH Pages deploy
```

Every section subclasses `Section` and implements one method (`pull()`).
The base class handles snapshot storage, delta detection, and HTML rendering.
Adding a new section is one file.

## Run locally

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-...     # optional: enables LLM synthesis
python -m worldscope.brief
open dist/index.html
```

## What ships in CI

GitHub Actions runs the briefing every day at 06:00 America/New_York (10:30
UTC; trim to exact 06:00 local later). The dist/ archive commits back to
the repo and deploys to GitHub Pages. The HTML has `robots: noindex` so
search engines won't pick it up; if you want true privacy, flip the repo
to private (requires GH Pro).

## API keys needed

| Key | Required for | Where |
|---|---|---|
| `ANTHROPIC_API_KEY` | LLM synthesis (paragraphs grounded in cited items) | secrets / `.env` |
| `COURTLISTENER_API_TOKEN` | court-opinion section (lifts rate limit) | already in `~/Projects/econscope/.env` |
| `FRED_API_KEY` | macro section | already in `~/Projects/econscope/.env` |

Without any of these, the corresponding section either uses the anonymous
rate limit or falls back to a deterministic prose summary.

---

Dr. Ian Helfrich · 2026
