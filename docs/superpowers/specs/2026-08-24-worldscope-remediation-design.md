# Worldscope remediation — audit, as-built, and what is still blocked

**Date:** 2026-08-24
**Status:** Phases 1–4 built and committed. Phase 5 partially built.
**Baseline:** `origin/main` at `4eaf0a4b`.

---

## 1. What was actually wrong

Worldscope reported success on roughly 180 consecutive workflow runs — **100 of
the last 100 green** — while producing substantially less than it claimed to.
Every one of the failures below was spelled `return []` or
`except Exception: pass`, so none of it was visible from outside.

### 1.1 The system had never done the thing it exists to do

`ANTHROPIC_API_KEY` was not among the repository's 13 secrets.

- `paper_bet_placement.py` logged `no ANTHROPIC_API_KEY; skipping placement`
  to stderr and returned `[]`. **Zero paper bets placed on 66 consecutive
  days** (2026-05-20 → 2026-08-24); open positions 0, resolved all-time 0.
  The mechanism that tests investment hypotheses against markets — the one the
  code calls the killer feature — had never executed.
- `synth.py:99`, `overview.py:122` fell back to a deterministic template, so
  the brief's prose was not model-written.
- `chinese_internal`, `russian_internal`, `ukrainian_internal` — the
  cross-language analysis, the one edge English-only desks do not have — ran
  on stubs.

### 1.2 The scorecard already said the forecasting was failing

From the 2026-08-24 `paper_bets` summary, printed in the brief every day:

| metric | value |
|---|---|
| predictions resolved | 424 |
| Brier | 0.067 |
| **Brier skill vs climatology** | **−1.42** |
| ECE | 0.199 |
| overconfidence | −0.199 |

A skill score of −1.42 means substantially **worse than the base rate**. The
flattering Brier came from predicting near-certain outcomes.

### 1.3 Thirty percent of the political-figures score was structurally dead

`political_figures.py` hardcoded `"speeches": []`. `speech_volume` (0.15) and
`speech_topic_drift` (0.15) therefore scored 0.0 for every figure on every day
since the section was written. Because the composite is a weighted sum, the
shortfall was invisible: it compressed every score toward zero and reordered
the top-10 ranking.

### 1.4 The forecast record was rewritable

Six tables were written with `INSERT OR REPLACE` against a single primary key
— a full-row overwrite. And `daily-brief.yml` triggered on `push` as well as
cron; the desk-officer routine commits with a PAT, and PAT pushes fire
workflows. Actions history for 2026-08-24: `schedule 07:50`, then
`push 09:20`. **Two full crawls of ~41 sources per day, and the second run
overwrote the first run's forecasts by construction.**

### 1.5 948 MB of generated data lived in a public git repository

`daily-brief.yml:85` ran `git add -f dist/ data/store.sqlite lake/`. The `-f`
overrode the `.gitignore` meant to stop it.

| path | size | share |
|---|---|---|
| `lake/` | 563.5 MB | 57% |
| `dist/` | 309.6 MB | 31% |
| `data/` | 74.6 MB | 8% |
| everything worth keeping | ~40 MB | 4% |

Packed history 1.36 GB, growing ~35 MB/day, with four workflows contending on
one branch through an 8-attempt `git rebase -X theirs` loop — and
`ukraine-hourly` cloning the full history 24 times a day at `fetch-depth: 0`.

### 1.6 Five of eleven stages could not run

`brief.py:131` `_run_stage` caught everything and printed. `daily-brief.yml`
installed the bare package, so `embeddings`, `graphics`, `maps`,
`ukraine-maps` and the DuckDB warehouse `ImportError`'d every run.

Consequence: **`worldscope/graphics.py`, 1,330 lines and the largest module in
the repository, had never produced a chart in production** — while STEP 3 of
the morning routine prompt had a cloud agent redraw the same four charts in
matplotlib by hand, daily.

### 1.7 Credential wiring

- `ukraine-hourly.yml` exported `NASA_FIRMS_KEY`; `firms.py` reads
  `FIRMS_MAP_KEY`. The thermal-anomaly layer was blind 24×/day.
- `FIRMS_MAP_KEY`, `MEDIACLOUD_API_KEY`, `OPENFEC_API_KEY`,
  `ALERTS_IN_UA_TOKEN` were never set. FEC ran on `DEMO_KEY` (~30 req/hr).
- `BEA_API_KEY`, `BLS_API_KEY`, `CONGRESS_GOV_API_KEY` are configured and read
  by **zero code**.
- `GOVINFO_API_KEY` was documented as required and read by zero code.

---

## 2. As-built

### 2.1 Capability contract (`worldscope/sections/__init__.py`)

The trust rule was already documented at `sections/__init__.py:44` and never
enforced. `Section` now carries `requires_env` / `optional_env` /
`requires_packages`; the base class checks them before `pull()` is reached, so
no adapter can opt out. All 15 credential-reading sections annotated; the three
swallowing guards removed.

### 2.2 `worldscope/preflight.py`

One command renders the whole capability surface, exits non-zero on a missing
**required** credential or package, and prints the exact `gh secret set` to
run. Wired into `daily-brief` **before** the crawl.

### 2.3 `worldscope/sections/congressional_record.py`

Harvests GovInfo CREC via **package-level MODS** — one request per published
day covering all ~78 granules, rather than one per granule — joined to
`figures_registry.yaml` on `bioGuideId`. Verified live: 35 attributions, 17
members. Two approximations documented rather than hidden: word counts
estimated from page extent, and topic vectors that are lexical hashed TF-IDF
(numpy only), not semantic embeddings.

### 2.4 Component coverage (`worldscope/scoring/figure_anomaly.py`)

The scorer reports which components had input data and what share of the
composite weight that represents. **Reporting only** — the score is unchanged,
because silently renormalising would replace one invisible distortion with
another.

### 2.5 `worldscope/blobsync.py`

Restore order: local → newest `data-YYYY-MM-DD` release → **git history** →
cold start. The git-history step is the migration safety net: these paths were
committed for months, so `git log --diff-filter=D` + `git archive` recovers the
last good tree, and it deepens a shallow clone on demand so the daily job runs
at `fetch-depth: 1`.

Two things assumption would have gotten wrong, caught before shipping:

- **`dist/` does need durable storage.** A Pages deployment replaces the whole
  site and `render-briefings.yml` deploys independently, so rendering into an
  empty `dist/` would have published the briefs while deleting `zips/`,
  `sections/` and `assets/` — including the `zips/<date>.zip` the desk-officer
  routine fetches each morning.
- **Archives must preserve their relative path**, so `ukraine-hourly` can
  persist only `lake/sections/ukraine_theater` instead of moving 563 MB hourly.

Result: tracked tree **988 MB → 40.9 MB**. History deliberately not rewritten.

### 2.6 Append-only forecast record (`worldscope/lake/__init__.py`)

Each forecast table becomes `<t>_versions` with a **view at the original name**
selecting the original columns in the original order. That is what kept the
change cheap: `track_record`, `signals`, `claims`, `graphics`, `site_builder`
and the MCP server all keep issuing `SELECT * FROM predictions` unchanged.

`Lake.knowledge_as_of(table, t)` answers "what did the system hold to be true
at instant *t*" — the precondition for a backtest free of look-ahead.

Three corrections the implementation forced:

- **`as_of` is the write time, not the domain time.** Seeding it from
  `made_at` conflated "when this was true" with "when the system came to
  believe it"; only the second answers the point-in-time question.
- **Second resolution is not enough.** Two forecasts written in the same second
  were indistinguishable in time. The version timeline uses microseconds.
- **`claim_evidence` carried `REFERENCES claims(id)`**, and SQLite reports
  `foreign key mismatch` on every insert once `claims` is a view.

`run_id` is stamped from `GITHUB_RUN_ID`/`GITHUB_RUN_ATTEMPT`.
**`run_id IS NULL` marks rows that predate immutability** and must never be
cited as pre-registered calls. The honest record starts at the migration
boundary.

### 2.7 Honest scorecard (`worldscope/scoring/venues.py`, `track_record.py`)

- **Venue classification.** Manifold is mana; PredictIt is wound down. On
  2026-08-24, **82 of 140 indexed markets carried no capital.**
  `headline_edge()` reports real-money venues only, and refuses to report at
  all below 20 resolved bets. An unrecognised venue classifies as `UNKNOWN`,
  never `REAL_MONEY`.
- **Transaction costs.** Spreads widen toward 0 and 1 — exactly where the
  model's apparent edges are largest — so omitting them biased hardest in the
  direction the strategy most wanted to trade. Placement now requires a
  real-money venue and net edge above `MIN_NET_EDGE`. The cost model is a
  **stated approximation, not a fitted one**: there is no order-book history in
  the lake to fit against, and every constant is named and overridable.
- **Self-graded vs externally-resolved.** `signals.py` predicts a key will stay
  cross-section-salient and resolves it by re-reading the same lake. The lake
  is both forecaster and referee, which is how Brier 0.067 sits beside skill
  −1.42. `score_predictions_split()` reports them separately with an explicit
  caveat that the self-graded number must not be cited as predictive edge.
- `track_record` is now a pipeline stage writing `dist/scorecard.json`.

### 2.8 Stage tiering and run report

`ImportError`/`ModuleNotFoundError` are fatal — a missing dependency is a
deployment defect, and that single rule catches all five silently-dead stages.
Anything else is recorded and survived; the job fails at the end only if a
**required** stage was among them. The brief is still written either way.
`dist/run_report.json` records every stage's fate and every section's counts.

### 2.9 Feed diagnoses

| feed | verdict |
|---|---|
| `gdacs` | **FIXED, 0 → 100 items.** `/geteventlist/MAP` now 400; `/SEARCH` returns the identical GeoJSON. |
| `conflict`, `cisa_kev`, `epss`, `usgs_quakes` | work |
| `reliefweb` | v1 decommissioned (410); v2 rejects unregistered callers (403). Moved to v2, `RELIEFWEB_APPNAME` added. **Blocked on registration.** |
| `promed` | ProMED is now an SPA; every RSS path 404s or returns the HTML shell, which XML-parses to zero items. Now raises. Their Payload API at `/api/posts` holds 5 announcements, not the archive. |
| `firms`, `mediacloud`, `acled` | blocked on credentials |

### 2.10 Workflows

`daily-brief` push trigger removed; `pip install -e ".[ci]"`; preflight before
the crawl; blobsync restore/persist. `ukraine-hourly` shallow, correct FIRMS
key name, scoped subtree, converged dependency path. `render-briefings`'
`changed` gate derives from rendered bytes, and its `pip install -e . || true`
is now unconditional.

Ten workflow-invariant tests make each of these a red test if reverted.

### 2.11 Pre-registration and multiple-testing control (Phase 4)

A rule's identity is its content: name, canonical params, and a sha256 of the
implementing module. Registration is `INSERT OR IGNORE` on that key, so
re-registering cannot move the timestamp — **backdating a rule to cover a
position already taken is impossible by construction, not by policy**.
`trial_count()` is what `n_trials` means in the deflated Sharpe, and it is not
recoverable after the fact, which is the entire reason the registry exists.

`worldscope/scoring/multiple_testing.py` implements Deflated Sharpe (Bailey &
López de Prado 2014), PBO via CSCV (Bailey, Borwein, López de Prado & Zhu
2015), and Hansen's SPA (2005) with a stationary bootstrap — numpy-only. Each
test is paired with a deliberately-broken baseline, so an estimator that
always passes would be visible as such.

**Three things measurement caught that reasoning did not:**

- **SPA over-rejected.** Measured size at nominal 5% was 0.115 (k=10) and
  0.145 (k=50) — the exact error the test exists to prevent. Cause: a fixed
  bootstrap block length applied to near-independent differentials. Block
  length is now chosen from observed autocorrelation; size returned to
  0.068/0.062 (MC se 0.011) with power 1.000.
- **The residual distortion is intrinsic.** Under real autocorrelation no
  block length gives nominal size at n=300 (0.10 at τ≈2, 0.14 at τ≈4.5, 0.26
  at τ≈20), and Newey-West moved it only 0.136 → 0.120. `SPAResult` therefore
  reports `effective_n` and `reliable`, and states that an unreliable p-value
  is a **lower bound** — it errs toward declaring skill that is not there.
- **A units bug would have inverted the verdict.** The scorecard passed
  `var(returns)/n` as `var_sharpe` — the variance of the *mean*, not of the
  *Sharpe*. That overstated the deflation threshold ~66× and scored a
  genuinely skilled 60-bet record at DSR 0.60 instead of 0.96: the difference
  between keeping a rule and discarding it.

PBO and SPA need ≥2 rules with overlapping live history, so they switch on by
themselves the first day that exists. SPA's benchmark is **doing nothing**,
not the best rule.

### 2.12 Verification

**501 tests. One failure**, `test_political_figures.py::test_at_least_ten_figures_have_nonzero_score`,
which reads `lake/sections/*` artifacts excluded by this clone's sparse
checkout — environmental, not a regression.

A real end-to-end run produced **6 charts, 4 maps, 4 Ukraine maps** (none had
ever rendered in production), `run_report.json`, and `scorecard.json`.

---

## 3. Runbook — what only Ian can do

Nothing is pushed. Local commits: `749edb90`, `f470ea60`, `a2cbf439`,
`cf513042`, `509980a6`.

**1. Set the missing secrets.** `ANTHROPIC_API_KEY` is the one that unblocks
paper-bet placement, brief prose, and all three cross-language sections.

```bash
gh secret set ANTHROPIC_API_KEY --repo ihelfrich/worldscope
```

Then, in rough order of value: `FIRMS_MAP_KEY` (thermal anomalies — never
worked), `GOVINFO_API_KEY` (30% of the figure score), `OPENFEC_API_KEY` (FEC is
rate-limited on DEMO_KEY), `MEDIACLOUD_API_KEY`, `ALERTS_IN_UA_TOKEN`,
`RELIEFWEB_APPNAME`.

**2. Check preflight before pushing**, to see exactly what the next run will
report:

```bash
cd ~/Developer/worldscope && uv run python -m worldscope.preflight
```

**3. Disable the two duplicate notifier routines** (they double every Pushover
and hold your Pushover tokens in plaintext). Not done automatically — deleting
cloud routines is not trivially reversible:

- `trig_01Rinspcu1prbctKAyGGvx14` — Worldscope morning brief (daily 06:00 ET)
- `trig_01L3eDwFZUpioBUAmH4yuefp` — Worldscope weekly brief (Friday 16:00 ET)

**4. Push, then watch the first run.** The first `daily-brief` will seed the
data release from git history. Expect the run to be slower than usual once, and
expect preflight to fail the run if a required secret is still missing.

**5. Retire STEP 3 of the `WORLDSCOPE morning brief` routine prompt** once you
have seen charts appear from `graphics.py`. It currently tells the agent to
redraw them.

---

## 4. Not built

- **Phase 5 remainder.** The three sections the registry sketches but never
  built (Maritime/AISStream, Elections, Anomaly); new surface — EDGAR
  full-text, AIS chokepoint transits, night-lights, commodity flows,
  central-bank speech corpora. `BEA_API_KEY`, `BLS_API_KEY` and
  `CONGRESS_GOV_API_KEY` are already configured and read by nothing.
- **History purge.** Repository stays ~1.36 GB and stops growing. A purge needs
  its own runbook and approval: freeze order, dry-run object counts, rollback
  mirror.
- **ProMED archive endpoint.** Not found unauthenticated; the section fails
  visibly with the list of what was tried.
