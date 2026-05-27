# Section Adapter Contract

**Status:** binding for v1 (Phases 0-10). Last updated 2026-05-27.

Every section that contributes to the daily brief is implemented as a **section adapter**. This document is the contract every adapter must satisfy. If you (or any future contributor, including Claude in a future session) are adding a new section, follow this contract or refactor it before merging.

The contract exists because we are about to ship ~30 sections and we cannot have ad-hoc shapes for each. This is the price of admission.

---

## What a section is

A **section** is a coherent slice of the daily brief that pulls from one or more data sources, ingests them into the lake, and produces three downstream artifacts: raw data, a human-readable summary, and a structured-data sidecar.

Examples of sections: `state-news`, `state-bills`, `foreign-news`, `chinese-internal`, `markets`, `cyber-threat`, `paper-bets`, `aircraft-convergence`.

Each section has its own folder under `worldscope/sections/<section-name>/`. Naming is `kebab-case`.

---

## The two-phase architecture

Every section runs in two phases. The phases are decoupled:

### Phase A — Ingest (cheap, runs on GitHub Actions cron)

- Pure Python. No Claude API calls.
- Pulls from APIs/RSS/scrapers.
- Writes structured rows into the SQLite lake.
- Idempotent. Re-running the same date never double-counts.
- Per-source rate-limited. Backoff on 429/503 with jitter.
- Schema-validated at the boundary (Pydantic, `strict=True`). Failed rows go to `quarantine/`, never silently dropped.
- Updates `source_health` table with last-success timestamp, record count, schema hash.

### Phase B — Synthesize (LLM, runs inside the daily orchestrator routine)

- Called as a sub-agent (via Task tool) by the orchestrator.
- Reads its section's slice of the lake for today (or N days back if needed).
- Produces three artifacts (see "Output artifacts" below).
- Writes a single structured.json sidecar that the orchestrator merges into the graph.
- Includes a disclosure footer (see "Prediction disclosure" below) when claims involve forward-looking statements.

---

## Required folder layout

```
worldscope/sections/<section-name>/
├── README.md             # human description, source list, license notes
├── ingest.py             # phase A — pulls + validates + writes to lake
├── synthesize.py         # phase B — reads lake, emits artifacts (or a prompt template)
├── schema.py             # Pydantic models for incoming records
├── sources.yaml          # per-source: url, license, rate_limit, attribution
├── tests/                # at minimum: a smoke test that runs ingest against a frozen fixture
└── prompts/              # if Phase B uses LLM templates, they live here
```

---

## Output artifacts (Phase B, per section, per date)

Every section produces three files under `lake/sections/<section-name>/<YYYY-MM-DD>/`:

### 1. `raw.jsonl` — the firehose

- One JSON object per ingested record.
- Untouched by the LLM; pure data.
- Schema-validated against `schema.py` at ingestion time.
- Every record has: `id`, `source_id`, `ingested_at_utc`, `original_url`, `original_text` (≤500 chars), `original_lang`, `entities` (array of normalized entity IDs), `license`.
- This is what queryable surfaces (the MCP server, ad-hoc grep) read.

### 2. `summary.md` — the human-readable section

- 300-1500 words, depending on section.
- Composed by the section's LLM sub-agent during Phase B.
- Top of file: front-matter with `section`, `date`, `record_count`, `model_used`, `tokens_in`, `tokens_out`, `wall_clock_seconds`.
- Body: structured by section convention (most sections: heading + bullets + commentary).
- Cites specific lake records by `id` in inline footnote-style brackets: `[lake:state-bills:9f3a1c…]`.
- This is what the main brief composer reads.

### 3. `structured.json` — the graph payload

- Schema:
  ```json
  {
    "section": "string",
    "date": "YYYY-MM-DD",
    "entities_added": [{"id":"…","type":"…","name":"…","metadata":{…}}],
    "entities_updated": [...],
    "relationships": [{"from":"…","to":"…","type":"…","weight":…,"evidence":["lake:…"]}],
    "predictions": [{"claim":"…","resolution_criteria":"…","target_date":"…","confidence":…,"evidence":[…]}],
    "paper_bets": [{"market_id":"…","side":"YES|NO","size_usd":…,"price_at_bet":…,"rationale":"…"}],
    "anomalies": [{"category":"…","z_score":…,"description":"…","evidence":[…]}]
  }
  ```
- Consumed by the orchestrator after all sub-agents return.
- Orchestrator merges into the graph tables and prediction/paper-bet tables.
- Empty arrays are fine. `predictions` / `paper_bets` / `anomalies` only populated when the section has reason to emit them.

---

## Required SQLite lake tables (v1)

Defined in `worldscope/storage/schema.sql`. Every adapter writes to these via the storage layer; nobody writes raw SQL to lake tables from outside the storage module.

```
sources             (id, name, url, license, tier, country, language, attribution_required)
source_health       (source_id, last_success_at, last_record_count, schema_hash, last_failure, consecutive_failures)
records             (id, source_id, ingested_at, original_url, original_text, original_lang, license)
record_entities     (record_id, entity_id)               -- M:N record↔entity
entities            (id, type, canonical_name, aliases, metadata_json)
relationships       (id, from_entity, to_entity, type, weight, first_seen, last_seen, evidence_json)
predictions         (id, made_at, target_date, resolution_criteria, predicted_outcome, confidence,
                     training_window_days, indicators_used, method, evidence_json,
                     resolved_at, actual_outcome, brier_contribution)
paper_bets          (id, market_platform, market_id, market_url, market_question,
                     side, size_usd, price_at_bet, timestamp_bet, rationale, evidence_json,
                     model_version, confidence_band)
paper_bet_marks     (id, bet_id, mark_date, days_since_bet, mark_price, unrealized_pnl, delta_vs_prev)
paper_bet_resolutions (bet_id, resolved_at, final_outcome, final_pnl, holding_period_days)
anomalies           (id, section, category, z_score, description, evidence_json, detected_at)
briefs              (date, kind, title, html_path, md_path, composed_at, tokens_in, tokens_out, cost_usd)
quarantine          (id, source_id, raw_json, validation_error, detected_at)
```

---

## Idempotency

- All writes use UPSERT keyed on a deterministic dedup key.
- The dedup key for each source is specified in `sources.yaml` and tested in `tests/`.
- Re-running an adapter for the same date must produce zero net database changes.
- This is not optional. The first thing the smoke test checks.

---

## Schema validation

- Every adapter has `schema.py` with Pydantic models, `strict=True`.
- Records that fail validation are written to `quarantine` table with the error and the raw JSON.
- The daily brief includes a quarantine summary in the source-health footer.
- Schema-drift detection: each ingestion run computes a hash of (column-name, type) tuples from the upstream response. On change, log + alert.

---

## Rate limiting

- Per-source token-bucket rate limit defined in `sources.yaml`.
- Default: 1 req/sec, bursts to 5.
- 429/503 → exponential backoff with jitter (1s, 2s, 4s, 8s, 16s, give up).
- All HTTP requests use a `User-Agent` of `worldscope/<version> (+https://github.com/ihelfrich/worldscope; contact: ianthelfrich@gmail.com)`. Politeness; some APIs require an identifying UA.

---

## License + attribution

Every source in `sources.yaml` includes:

```yaml
sources:
  - id: gdelt-events
    url: https://api.gdeltproject.org/...
    license: CC-BY-4.0
    attribution_required: true
    attribution_text: "Data from the GDELT Project."
    tier: aggregator
```

The brief's footer auto-aggregates all `attribution_required: true` sources used that day and renders the attribution_text. We never republish full third-party content — links + ≤150 character excerpts only.

---

## Source tier (for trust signaling)

`sources.yaml` declares each source's `tier`:

| tier | meaning | example |
|---|---|---|
| `primary_document` | Government filing, court docket, central-bank publication. Highest weight. | OFAC SDN, SEC EDGAR, Federal Register |
| `mainstream_independent` | Editorially independent major outlet. | Reuters, AP, BBC, Bloomberg |
| `mainstream_partisan_left` | Mainstream, audience-skewed left. | The Guardian, MSNBC |
| `mainstream_partisan_right` | Mainstream, audience-skewed right. | Wall Street Journal opinion, Fox |
| `state_controlled` | Direct editorial control by a state. | Xinhua, RT, Press TV |
| `aggregator` | Re-publishes other sources. | GDELT, MediaCloud |
| `community` | Crowd-sourced. | Wikipedia, OSM |
| `speculative_blog` | Single-author, opinion-heavy. | Substacks |
| `prediction_market` | Crowd-priced. | Polymarket, Kalshi |

The brief surfaces tier alongside each cited record. The synthesis pass weights tiers when conflicting accounts exist.

---

## Failure handling

- A section failure does not fail the brief.
- Orchestrator catches each sub-agent failure, logs it to `quarantine`, and writes a `summary.md` stub for that section: `## <Section> — data unavailable today\n\n_Reason: <error>. Last successful run: <timestamp>._`
- The main brief composer treats stub sections as low-priority.
- If a section has been stub-only for 48+ hours, auto-file a GitHub Issue.

---

## Prediction disclosure footer

When a section's `summary.md` makes a forward-looking claim or the `structured.json` emits a `predictions[]` entry, every claim/prediction gets a disclosure block:

```
[prediction:claim-id]
Training window: 47 days (2026-04-10 to 2026-05-27)
Indicators used: yield_curve_spread, ACLED_protests_7d, sanctions_adds_7d
Method: nearest-neighbor across 11 historical analogs
Confidence: low (training window < 90 days)
```

This is non-negotiable. It is how we maintain reader trust without strict pre-registration.

---

## Paper-bet emission

When a section's synthesis identifies a Polymarket / Kalshi / PredictIt / Manifold market where our credence diverges from the market price by ≥ threshold (default 8%) AND the evidence base is strong enough, it emits a `paper_bets[]` entry in `structured.json`. The orchestrator records the bet in `paper_bets`. The daily routine also marks-to-market every active bet at 1/5/14/30/60/90 day milestones.

Sizing rule (Kelly-lite): `size_usd = base_size_usd × min(edge × 5, 1.0) × confidence_multiplier`, where:
- `base_size_usd` = 100 (the system's "unit bet")
- `edge` = `|our_credence - market_price|`
- `confidence_multiplier` ∈ {0.5, 1.0, 1.5} for {low, medium, high} confidence bands

Cap: no single bet exceeds 5% of total notional ever risked.

---

## Cost accounting

Every Phase B run logs to `briefs.tokens_in`, `briefs.tokens_out`, `briefs.cost_usd`. The daily brief footer shows the day's total cost. The weekly digest shows rolling 7-day and 30-day spend. If the rolling 7-day average exceeds a configured ceiling (default $5/day), the next run defers all optional-priority sections and emits a warning.

---

## Versioning

- Each section adapter has a `__version__` in its module.
- The orchestrator records each section's version in the brief's metadata.
- Bumping a major version (breaking schema change) requires a migration script under `migrations/` that brings existing lake data forward.

---

## Smoke tests

Each section's `tests/` includes at minimum:

1. **Frozen-fixture ingest test:** ingest from a captured `fixtures/<source>.json`. Assert that the output matches an expected golden file. Re-running produces zero net changes.
2. **Schema-drift test:** a synthetic mutated fixture that should fail validation. Assert it goes to `quarantine`.
3. **Idempotency test:** ingest twice in a row, assert no double-counting.

Tests run in CI on every push to `main`. Failing tests block merge.

---

## What this contract does NOT specify

- The synthesis prompt for each section (lives in `prompts/<section>.md`, free to evolve)
- The exact RSS/API/scraper implementation in `ingest.py` (per-source quirks vary)
- The visual rendering of `summary.md` into HTML (handled by the renderer downstream)
- The MCP server's exposed tool surface (separate contract in `docs/MCP_CONTRACT.md`)

---

*This document is binding. Changes to the contract require an explicit PR titled `contract:` and a migration plan for existing sections.*
