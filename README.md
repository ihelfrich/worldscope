# WORLDSCOPE

Daily global political, economic and OSINT intelligence engine. ~40 primary
sources are pulled every day into a versioned data lake, analysed
deterministically (surges, convergence, story clusters, claims, calibration),
synthesised into a brief, and published as static HTML on GitHub Pages. A
second, long-form brief is composed by a Claude Code Routine from the day's
verified bundle. A live map at `/live/` refreshes hourly.

Built by Dr. Ian Helfrich.

- Site: https://ihelfrich.github.io/worldscope/
- Live map: https://ihelfrich.github.io/worldscope/live/
- Architecture and roadmap: [ARCHITECTURE.md](ARCHITECTURE.md)
- Latest red-team: [docs/REDTEAM-2026-10-09.md](docs/REDTEAM-2026-10-09.md)

## Products

| Product | Produced by | Path |
|---|---|---|
| Daily section digest + overview | `daily-brief.yml` (Python, Tier-4 synthesis) | `dist/<date>.html` |
| Dated bundle for downstream composition | same | `dist/zips/<date>.zip` + `dist/status/daily/<date>.json` |
| Long-form desk-officer brief | Claude Code Routine ([spec](docs/ROUTINE-desk-officer.md)) | `briefings/<date>.md` → `dist/briefings/<date>.html` |
| Automated edition (when no composed brief exists) | `fallback-brief.yml` → `tools/fallback_brief.py` | same path, marked `<!-- worldscope:fallback-brief -->` |
| Situation Board (homepage) + evidence board | `tools/render_board.py`, `tools/render_reasoned.py` | `dist/index.html` |
| Ukraine theater maps | `ukraine-hourly.yml` | `briefings/<date>-ukraine_*.png` |
| God's-eye live map | `live-map.yml` | `dist/live/` |
| Pushover delivery | `pushover-brief.yml` | phone |

## Sources

Government and filings: Federal Register, Congress/OpenStates, FEC, SEC Form
4, CourtListener, CISA KEV, EPSS, OFAC/EU/UK sanctions, USAspending.
Conflict and hazards: ACLED, GDELT (DOC, GKG, GEO), NASA FIRMS, USGS, GDACS,
ReliefWeb, WHO DON, NWS/SPC/NHC, DeepStateMap and alerts.in.ua for Ukraine.
Markets and macro: FRED, Finnhub, Stooq, CoinGecko, open.er-api. Prediction
markets: Polymarket, Kalshi, Manifold. Press: ~300 RSS feeds across US
state/local, foreign, Chinese, Russian and Ukrainian outlets (Haiku
translation), MediaCloud, Substack commentary. People: Wikidata changes,
OpenSky VIP flights, Forbes, a local OpenSanctions PEP corpus.

GDELT is read from its bulk 15-minute GKG files (`worldscope/lib/gdelt_files.py`),
not the DOC search API, which throttles GitHub's shared runners far below its
documented limit; the DOC API remains the fallback.

Every source has a health state (fresh, fresh_empty, carry_forward,
stale_after_failure, no_data) in `dist/run_report.json`, and the readiness
manifest names any **required** source older than 3 days under
`degradation`. Adapters fail loud: a missing key, rejected credential,
retired feed, schema change or outage raises a typed `SourceUnavailable`
(`worldscope/sections/__init__.py`) instead of returning an empty list, so a
broken source never looks like a quiet day.

## How a day is judged

- **Claims** (`claims.py`) cluster records that assert the same thing, keyed
  on what records say (feed provenance tags are excluded), and grade
  corroboration.
- **Ranking** (`claim_rank.py`), shared by the automated brief, the homepage
  and the evidence board: distinct reporting sections first, then distinct
  outlets with all local stations counted as one; roundups, local-only items,
  structured-feed clusters and "contradicted" clusters are not lead news.
  Each line cites its best source article, or none when no source clearly
  matches.
- **Momentum** (`momentum.py`) compares each story with its own 35-day
  baseline (surging, new, building, fading), always as of the newest day
  with collected data.
- **Forecasts** are falsifiable and self-grading. Persistence calls
  (`signals.py`) and momentum calls (`momentum_calls.py`: "still in 3+
  sections in 3 days?") state probabilities calibrated against each method's
  own graded record; momentum calls start from an out-of-sample backtest
  (`tools/backtest_momentum_calls.py`, `data/momentum_calls_backtest.json`).

## When something breaks

Alerts are GitHub issues titled `[pipeline-alert:<key>]` (`worldscope/alerting.py`):
opened once, quiet while the condition persists, closed by the next healthy
check. Pushover delivery failures open one too. `worldscope/source_actions.py`
keeps a single issue listing the fixes only the owner can make (missing or
rejected credentials, retired feeds, long outages), rewritten every daily run.
If no composed brief exists by mid-afternoon UTC, an automated edition is
published instead of going dark, and says so at the top.

## Layout

```
worldscope/
├── sections/        one adapter per source (subclass Section, implement pull())
├── lake/            SQLite lake + per-day JSONL; schema in lake/__init__.py
├── history.py       cross-time query API over live DB + lake/archive partitions
├── godseye.py       builds dist/live (GeoJSON layers + Leaflet page)
├── signals.py radar.py stories.py claims.py   deterministic analysis
├── claim_rank.py momentum.py momentum_calls.py ranking, momentum, forecasts
├── alerting.py source_actions.py              alert issues + owner fix list
├── lib/gdelt_files.py                         GDELT bulk GKG reader
├── synth.py overview.py                       Tier-4 LLM synthesis, cached
├── readiness.py     dated producer/consumer contract (publish-daily, check-daily)
├── lake_maintenance.py  size ceiling with archive-before-evict
├── brief.py         orchestrator + CLI
tools/render_brief.py   Markdown brief → Tailwind HTML with embedded map
tools/fallback_brief.py automated edition from the evidence engine
mcp-server/             read-only MCP server over the lake
docs/                   routine prompt, editorial spec, red-team reports
```

## Run locally

```bash
pip install -e ".[all,dev]"
export ANTHROPIC_API_KEY=sk-...        # optional: enables LLM synthesis
python -m worldscope.brief --out dist  # full daily build
python -m worldscope.godseye --out dist/live --days 7
python -m worldscope.history counts --since 2026-06-01
pytest
python tools/fallback_brief.py --date 2026-11-12 --out /tmp/brief.md   # automated edition
```

CI (`ci.yml`) runs the test suite and a bug-class static check (undefined
names, use before assignment and similar; no style rules) on every push.

## Keys

| Key | Needed for |
|---|---|
| `ANTHROPIC_API_KEY` | section synthesis, overview, translation, paper-bet placement |
| `FRED_API_KEY` | macro |
| `COURTLISTENER_API_TOKEN` | court opinions (rate limit) |
| `ACLED_EMAIL`, `ACLED_PASSWORD` | ACLED OAuth |
| `FIRMS_MAP_KEY` | NASA FIRMS |
| `MEDIACLOUD_API_KEY`, `OPENSTATES_API_KEY`, `FINNHUB_API_KEY`, `OPENFEC_API_KEY` | respective sections |
| `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN` | delivery |

Keys live only in CI secrets and `.env`. No key is ever shipped to the
browser; the public site is static and a visitor triggers zero API calls.

## Schedules (UTC)

| Workflow | Cron | Note |
|---|---|---|
| `daily-brief.yml` | eight entries a day, gated | GitHub starts this repo's crons 7-10 h late; the first firing after 06:10Z without a manifest for the day builds, the rest exit |
| `compose-brief.yml` | after `daily-brief.yml` | needs `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` |
| Claude Routine | 06:45 America/Chicago | configured in claude.ai; delivers by pull request |
| `auto-merge-briefs.yml` | on the Routine's pull request | lands a PR that carries exactly one valid brief |
| `render-briefings.yml` | dispatched by the composer + catch-up crons | |
| `fallback-brief.yml` | `50 15`, `50 19` | 15:50 waits for the day's collection; 19:50 publishes regardless |
| `pushover-brief.yml` | after render + catch-up crons | |
| `brief-deadman.yml` | three entries a day | alerts from 13:00 Chicago if today's brief is missing |
| `site-freshness-watchdog.yml` | `23 16` | alerts if the published site is stale |
| `ukraine-hourly.yml` | `15 * * * *` | |
| `live-map.yml` | `37 * * * *` | |
