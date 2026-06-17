# Worldscope architecture & roadmap

This document is the map of *what Worldscope is*, *why it is built the way it
is*, and *where it is going*. It doubles as an orientation for any future
session or Routine working on the repo. Read this before adding a new engine.

---

## 1. The vision, in one paragraph

Worldscope ingests ~40 independent primary sources daily (government actions,
filings, prediction markets, conflict data, foreign/domestic press, cyber
advisories, …), captures everything in a structured **data lake**, and applies
analysis *on top* of that lake to produce a single daily intelligence brief plus
a public, browsable archive. The goal is a beautiful, powerful, free news tool
that democratizes information — and, for its operator, the most comprehensive
global information set possible, with the machinery to read latent trends and
make calibrated, self-graded predictions about what happens next.

---

## 2. The one architectural principle: static, build-time, publicly cost-safe

**All intelligence is generated at build time and published as static files.**
The public site is GitHub Pages — a visitor downloads pre-rendered HTML and
triggers **zero** model calls. This is the load-bearing design decision:

> Public traffic costs ~$0 and **cannot** drain credit, regardless of
> popularity, because there is no live API path from the browser.

The only ways to break that guarantee — and therefore the two hard rules:

1. **Never** ship an API key to client-side / browser code.
2. **Never** stand up an unauthenticated public endpoint that calls a paid API
   (no "chat with my data" box without auth + rate limiting).

Cost is bounded by *how often we rebuild* (the daily cron + the Ukraine-hourly
job), not by how many people visit.

---

## 3. The cost model: tier the work

The single biggest cost lever is **not** LLM-ing everything. Work is tiered:

| Tier | Tool | Cost | Used for |
|------|------|------|----------|
| 1 | Deterministic Python over the lake | free (CPU) | dedup, trend stats, fusion, anomaly/surge detection, credibility scoring, dataset building, graph rollups |
| 2 | Local embeddings (`sentence-transformers`) | free (CPU) | semantic dedup, cross-language clustering, latent-trend grouping |
| 3 | Cheap model (Haiku) — *future* | low | bulk triage / classification / claim scoring across many items |
| 4 | Strong model (Sonnet/Opus) | metered | the few high-value syntheses: section blurbs, the morning overview |

Most of "track latent trends behind the scenes" is **Tier 1/2** — see
`signals.py` and `radar.py`. The model is applied surgically (Tier 4) only where
prose judgment is needed, with **prompt caching** on the static system preambles
(`synth.py`, `overview.py`). Both LLM stages have deterministic fallbacks, so a
missing key or network blip never aborts a brief.

**Backstops:** set a spend cap in the Anthropic console; keep `ANTHROPIC_API_KEY`
in CI secrets only; the lake's `briefs` table records per-run token/cost
accounting.

---

## 4. The data-lake decision: SQLite now, and what would change that

Worldscope's "data lake" is **SQLite committed into the repo**
(`data/store.sqlite` snapshot store + `lake/db/worldscope.sqlite` structured
lake + `lake/sections/**` JSON artifacts). This is the right choice for the
current scale and budget, and it is deliberate:

- **Free, versioned, portable.** Every build is a git commit → time-travel and
  full provenance for free. No infra, no credentials to leak, no network
  dependency that can fail a build.

What each candidate "upgrade" is actually for, and our stance:

| Tech | What it is | Use it when | Stance |
|------|-----------|-------------|--------|
| **SQLite-in-repo** | embedded DB, committed | scale fits in git, build-time access | ✅ current |
| **DuckDB** | in-process analytics engine over SQLite/CSV/Parquet | heavy aggregations, dataset building | ✅ adopt (already an optional dep; `lib/warehouse.py` + `radar.py` datasets) |
| **sentence-transformers** | local embedding models | semantic clustering/dedup, free | ✅ in use (`record_embeddings`) |
| **Hugging Face Datasets** | free public dataset hub (Parquet) | *publishing* curated datasets to the world | 🔜 roadmap — the "democratize" lever |
| **Supabase** | hosted Postgres + auth + APIs | live browser queries, >~1 GB data, concurrent writers, user accounts | ⏸ deferred until we want live interactive querying |

**Trigger to revisit Supabase:** the day we want visitors to slice the data live
in their browser, or the repo approaches ~1 GB. Until then it only adds a bill,
a network dependency, and a key-in-CI surface for capability we don't use.

---

## 5. The engines (how the pieces fit)

```
sources (~40 adapters)                      worldscope/sections/*.py
        │  pull() → items
        ▼
snapshot store  ──┐                         worldscope/store/         (carry-forward, deltas)
                  ├─►  DATA LAKE            worldscope/lake/           (records, entities,
        ▼         │    (SQLite + JSONL)                                 relationships, predictions,
   per-section ───┘                                                     paper_bets, anomalies,
   synthesis (Tier 4, grounded)            worldscope/synth.py         source_health, embeddings)
        │
        ▼   ┌──────────────── analysis on top of the lake ───────────────┐
        │   │ signals.py   cross-source CONVERGENCE → falsifiable,        │
        │   │              self-grading predictions (Brier/calibration)    │
        │   │ radar.py     CHANGE: surges + novel emergence → anomalies;   │
        │   │              source CREDIBILITY (corroboration + tier);      │
        │   │              dataset building + light exploration;           │
        │   │              candidate-source discovery (seed)               │
        │   │ stories.py   EVENT-level clustering → the Top Stories front  │
        │   │              page: groups the day's records into story       │
        │   │              threads (shared entities + headline tokens,     │
        │   │              embeddings when present), ranked by independent │
        │   │              cross-source / cross-language coverage breadth  │
        │   │ embeddings   semantic dedup / cross-language clustering      │
        │   │ graphics/maps figures from the lake                         │
        │   └────────────────────────────────────────────────────────────┘
        ▼
   morning overview (Tier 4, grounded)     worldscope/overview.py
        ▼
   render → dist/ (static HTML)            tools/render_brief.py, site_builder.py
        ▼
   GitHub Pages  +  Pushover               .github/workflows/*
```

**signals vs radar — the distinction that keeps them from overlapping:**

- **signals** answers *"what is converging right now?"* — keys salient across
  many independent sections **today**. A steady, broad key (e.g. "Iran") is a
  strong signal.
- **radar** answers *"what changed?"* — keys that **spiked** vs their own
  baseline (surge) or **broke in broadly for the first time** (novel). A steady
  key is *not* a development. Radar also scores source credibility, builds quant
  datasets, and seeds source discovery.

Both reuse the same key-extraction (`signals.record_key_pairs`) and the same
lake loaders, so they always see identical records and improvements to the
stopword/cleaning layer benefit both. Both run as **defensive post-section
stages** in `brief.py` (`_stage_signals`, `_stage_radar`) — a failure logs but
never blocks the brief.

---

## 6. The intelligence loop (why predictions get better over time)

```
ingest → fuse (signals) / flag (radar) → log falsifiable predictions
   → lake auto-grades matured predictions from its OWN later records
   → Brier / calibration / skill scored → calibrates future confidence
   → strongest, best-corroborated signals back paper-bets → resolve → score
```

Nothing here needs a human in the loop to accrue a track record. The
`predictions`, `paper_bets`, `paper_bet_marks/_resolutions`, and `anomalies`
tables are the rails; `signals.py` and `radar.py` fill them.

---

## 7. Roadmap (cost-aware, in priority order)

1. **Source discovery → human-in-the-loop Routine.** `radar.discover_candidate_sources`
   already surfaces recurring external domains we don't ingest. Next: a weekly
   Claude Code **Routine** that reads that list (+ thin watch-area coverage),
   drafts a new `Section` adapter, and **opens a draft PR** for review. Keeps a
   bad auto-source from silently polluting the lake. Subscription-billed, safe.
2. **Publish datasets to Hugging Face.** Push `lake/datasets/**` Parquet to a HF
   Datasets repo on a cadence — the concrete "democratize information" step.
3. **Semantic trend clustering (Tier 2).** ✅ *shipped (v1) as `stories.py`* — the
   daily **Top Stories** front page clusters records into event-level story
   threads ranked by independent cross-source coverage breadth. It runs the
   deterministic, embedding-free path by default (shared discriminative
   entities + headline-token overlap, with document-frequency pruning to stop
   single-link chaining) and folds in `record_embeddings` cosine when the index
   is populated. *Next:* feed the clusters back into signals/radar and persist
   cross-day story identity so a thread can be tracked as it develops.
4. **Credibility v2.** Blend in prediction-grounded accuracy (did this source's
   claims resolve true?) and decay; expose a per-source scorecard page.
5. **Quant auto-exploration (Tier 1, then optional Tier 3).** Extend
   `radar.explore_dataset` with correlation/lead-lag screens across datasets;
   optionally let a cheap model write a short "what's interesting here" note.
6. **Calibration surfacing.** Promote the Brier/calibration reliability diagram
   to a first-class public page so the track record is visible.

---

## 8. Cost guardrails checklist (keep this true)

- [ ] No API key in any client-side/browser asset.
- [ ] No unauthenticated public endpoint that calls a paid API.
- [ ] Heavy agentic work runs as **Routines** (subscription) or build-time CI.
- [ ] LLM calls are Tier-4 only, cached, with deterministic fallbacks.
- [ ] Anthropic console spend cap set as a backstop.
- [ ] New analysis prefers Tier 1/2 (deterministic / local embeddings) first.
