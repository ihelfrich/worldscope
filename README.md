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

Every source has a health state (fresh, fresh_empty, carry_forward,
stale_after_failure, no_data) in `dist/run_report.json`, and the readiness
manifest names any **required** source older than 3 days under
`degradation`.

## Layout

```
worldscope/
├── sections/        one adapter per source (subclass Section, implement pull())
├── lake/            SQLite lake + per-day JSONL; schema in lake/__init__.py
├── history.py       cross-time query API over live DB + lake/archive partitions
├── godseye.py       builds dist/live (GeoJSON layers + Leaflet page)
├── signals.py radar.py stories.py claims.py   deterministic analysis
├── synth.py overview.py                       Tier-4 LLM synthesis, cached
├── readiness.py     dated producer/consumer contract (publish-daily, check-daily)
├── lake_maintenance.py  size ceiling with archive-before-evict
├── brief.py         orchestrator + CLI
tools/render_brief.py   Markdown brief → Tailwind HTML with embedded map
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
```

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
| `daily-brief.yml` | `17 6 * * *` | off-peak minute; see the comment in the file for why |
| Claude Routine | 10:30 (retry 13:30) | configured in claude.ai |
| `render-briefings.yml` | on push + `15 11-13` | |
| `pushover-brief.yml` | after render + `30 11-13` | |
| `ukraine-hourly.yml` | `15 * * * *` | |
| `live-map.yml` | `37 * * * *` | |
