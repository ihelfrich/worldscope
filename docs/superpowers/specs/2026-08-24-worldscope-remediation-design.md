# Worldscope remediation — Phase 1 & 2 design

**Date:** 2026-08-24
**Author:** Ian Helfrich (with Claude)
**Status:** awaiting review
**Scope:** Phase 1 (restore the instrument) + Phase 2 (immutable, point-in-time record)

---

## 1. Purpose

Worldscope's goal is an ever-expanding open-source intelligence system that surfaces
what is actually happening in the world ahead of consensus coverage, converts that
into investable hypotheses, tests them against prediction markets, and revises its
method until it demonstrates durable edge.

That goal imposes two requirements the system does not currently meet:

1. **The instrument must work.** Five of eleven post-section stages fail silently
   every run; eight upstream feeds return nothing; the database is a git repository.
2. **The record must be immutable.** A track record that can be rewritten by a later
   run is not evidence. Every backtest run against it inherits look-ahead
   contamination.

Phase 1 fixes (1). Phase 2 fixes (2). Phase 3+ (honest scorecard, pre-registration
and multiple-testing control, coverage expansion) are out of scope here and are
sketched in §11.

---

## 2. Evidence

All references are to `origin/main` at `4eaf0a4b` (2026-08-24).

### 2.1 The Aug 5–6 migration was never pushed

Local `main` was *ahead 2, behind 140*. The two local commits — `8f46374f`
(Supabase blobstore, 650 LOC) and `f9cf9508` (watchdog-alert fail-loud) — have no
history on `origin/main`. Production ran the pre-migration code for 19 days.

Both commits are preserved on branch `archive/supabase-migration`. The local clone
has been reset to `origin/main` with a sparse checkout excluding `lake/`, `dist/`,
`data/`.

### 2.2 The lake is still committed to git, and `.gitignore` could not have stopped it

`.github/workflows/daily-brief.yml:85` runs:

```
git add -f dist/ data/store.sqlite lake/ || true
```

The `-f` overrides ignore rules. Even if the migration had been pushed, its
`.gitignore` additions would have been inert.

Tracked footprint on `origin/main`:

| path | size | share |
|---|---|---|
| `lake/` | 563.5 MB | 57% |
| `dist/` | 309.6 MB | 31% |
| `data/` | 74.6 MB | 8% |
| everything else (briefs, PNGs, code, figures, reports) | ~40 MB | 4% |
| **total tracked** | **988 MB** | |

Packed history is 1.36 GB; the repository is public. Growth is ~35 MB/day.

### 2.3 The pipeline runs twice a day

`daily-brief.yml:3-5` triggers on `push: branches: [main]`. The `WORLDSCOPE morning
brief` routine commits with a PAT, which triggers workflows. Actions history for
2026-08-24 shows `Daily briefing / schedule 07:50` followed by `Daily briefing /
push 09:20` — two complete runs, two full crawls of ~41 upstream sources, two lake
commits, for one brief.

This is also the mechanism by which every prediction and paper bet written by the
07:50 run is overwritten by the 09:20 run (see §2.6).

### 2.4 Two of four cloud routines are redundant notifiers

| routine | id | cron (UTC) | role |
|---|---|---|---|
| WORLDSCOPE morning brief | `trig_01VX1WpvjRnV2Tz6Qf7TC3Ue` | `0 9 * * *` | **producer** — commits `briefings/<date>.md` |
| WORLDSCOPE weekly brief | `trig_01SPNJ1AtKKEkiXEYQFeT1US` | `0 20 * * 5` | **producer** — commits `weekly_briefings/<week>.md` |
| Worldscope morning brief (daily 06:00 ET) | `trig_01Rinspcu1prbctKAyGGvx14` | `0 10 * * *` | notifier — sends Pushover itself |
| Worldscope weekly brief (Friday 16:00 ET) | `trig_01L3eDwFZUpioBUAmH4yuefp` | `0 20 * * 5` | notifier — sends Pushover itself |

`pushover-brief.yml` already delivers both daily and weekly
(`pushover-brief.yml:66` selects across `briefings/*.md` and
`weekly_briefings/*.md`) and is idempotent via `.pushover-sent.json`. The two
notifier routines therefore duplicate it, producing two pushes per brief. Both
weekly entries fire in the same minute.

Both notifier prompts contain the Pushover application token and user key in
plaintext in the routine body.

### 2.5 Five of eleven stages cannot run in CI

`brief.py:131` `_run_stage` catches every exception and prints. `daily-brief.yml:49`
installs `pip install -e .` — base dependencies only. The consequence, for the
eleven stages registered at `brief.py:372`:

| stage | needs | extra | status in CI |
|---|---|---|---|
| embeddings | `sentence-transformers` | `analytics` | **ImportError, swallowed** |
| graphics | `matplotlib` | `graphics` | **ImportError, swallowed** |
| maps | `fiona`, `matplotlib` | `graphics` | **ImportError, swallowed** |
| ukraine-maps | `fiona`, `matplotlib` | `graphics` | **ImportError, swallowed** |
| cross-section | stdlib | — | runs |
| integrity | stdlib | — | runs |
| signals | stdlib | — | runs |
| radar | stdlib | — | runs |
| claims | stdlib | — | runs |
| stories | stdlib | — | runs |
| site-builder | stdlib | — | runs |

The DuckDB warehouse (`worldscope/lib/warehouse.py`) degrades to `duckdb = None` on
import for the same reason. Last 100 Actions runs: **100 success, 0 failure.**

Direct consequence: `worldscope/graphics.py` is 1,330 lines — the largest module in
the repository — and has never produced a chart in production. `tests/test_calibration_graphic.py`
passes against a graphic that has never rendered. Meanwhile STEP 3 of the
`WORLDSCOPE morning brief` routine prompt instructs a cloud agent to redraw
`yield_curve.png`, `fx_oil.png`, `gdelt_tone_heatmap.png` and `watchareas_volume.png`
in matplotlib from scratch, daily (commit `3874110b` is exactly those four files).

### 2.6 The forecast record is mutable

`worldscope/lake/__init__.py` writes:

| line | statement | table |
|---|---|---|
| 604 | `INSERT OR REPLACE INTO predictions` | forecasts |
| 652 | `INSERT OR REPLACE INTO paper_bets` | simulated trades |
| 689 | `INSERT OR REPLACE INTO paper_bet_marks` | mark-to-market |
| 722 | `INSERT OR REPLACE INTO paper_bet_resolutions` | realized P&L |
| 735 | `INSERT OR REPLACE INTO anomalies` | statistical alerts |
| 764 | `INSERT OR REPLACE INTO claims` | evidence graph |
| 792 | `INSERT OR REPLACE INTO claim_evidence` | evidence links |

Each of these tables keys on `id TEXT PRIMARY KEY` (DDL at :211, :231, :252, :264,
:273, :171, :196). `INSERT OR REPLACE` on a matching primary key is a full-row
overwrite. A prediction's `confidence`, or a bet's `price_at_bet` and `side`, can be
silently rewritten by any later run.

Combined with §2.3, this is not a latent risk: it happens twice daily by
construction.

### 2.7 Eight feeds are dark, and they are the differentiated ones

Per-section record counts for 2026-08-24:

- **0 records:** `conflict`, `firms`, `mediacloud`, `wikidata_changes`
- **no lake output at all:** `acled`, `promed`, `reliefweb`, `gdacs`
- **0 new on a business day, needs diagnosis:** `federal_register`, `form4`,
  `who_don`, `cisa_kev`
- **skipped by design** (`WORLDSCOPE_SKIP`, no OpenSanctions corpus in CI):
  `sanctions`, `people`

`markets` and `macro` are **not** stale: on a Monday, Friday's close is correct, and
`new_count: 0` reflects stable per-ticker record IDs, not a failed pull.

The dark feeds are disproportionately the physical and alt-data sources — thermal
anomalies, conflict events, disaster feeds — that are not on a standard terminal.
The feeds working correctly are the English-language news sources with the least
differentiated signal.

### 2.8 Smaller defects found in passing

- `pushover-brief.yml:66` selects the brief to notify with `ls -t`, which sorts by
  mtime. In a fresh Actions checkout every file carries the checkout timestamp, so
  the selection is effectively arbitrary.
- `ukraine-hourly.yml:40` uses `fetch-depth: 0` — a full-history clone of a 1.36 GB
  repository, 24 times per day.
- `ukraine-hourly.yml:51` installs via `requirements.txt` while `daily-brief.yml:49`
  uses `pip install -e .`. Two drifting dependency paths.
- `ukraine-hourly.yml:55` installs a Go crawler with
  `|| echo "web-intel install skipped; section will fall back"` — another silent
  degradation.
- `paper_bets` pools Polymarket, Kalshi, PredictIt and Manifold into one edge
  calculation. Manifold is play money; PredictIt is wound down. (Deferred to Phase 3.)
- `paper_bet_placement.py:39-52` applies an 8% raw edge threshold and
  `min(edge * 5, 1.0)` sizing with no spread, fee, or depth model. (Deferred to Phase 3.)

---

## 3. Non-goals

- No git history rewrite. Repository stays ~1.36 GB and stops growing. A purge is a
  separate, separately-approved pass.
- No Supabase. `archive/supabase-migration` is retained for reference only.
- No change to how briefs are composed, to the routine prompts' analytical content,
  or to the published site's appearance.
- No new alerting channel. `watchdog-deadman.yml` remains the only alarm.
- Phase 3+ items (venue separation, cost model, pre-registration, multiple-testing
  control, new sections) are explicitly out of scope.

---

## 4. Phase 1 — restore the instrument

### 4.1 Clone reconciliation *(complete)*

`archive/supabase-migration` pins `f9cf9508`. Local `main` reset to `origin/main`
under a sparse checkout excluding `lake/`, `dist/`, `data/`.

The watchdog fix in `f9cf9508` is cherry-picked forward: `watchdog-alert.yml` must
exit non-zero when `PUSHOVER_USER_KEY` or `PUSHOVER_APP_TOKEN` is absent, rather
than exiting 0 having delivered nothing. That job *is* the alert channel; a silent
success there is invisible to the dead-man switch, which watches the heartbeat
commit rather than delivery.

### 4.2 One Pushover path

Disable both notifier routines and rename them
`[superseded by pushover-brief.yml] <original name>`, matching the precedent set for
`trig_01UQqKswHV7PbJEkSP5AdMaS`. Not deleted — the voice rules in those prompts are
hand-tuned and worth retaining as reference.

This removes the Pushover token and user key from two plaintext cloud routine
bodies.

`pushover-brief.yml` survives as the single delivery path. Fix its selection bug:
replace `ls -t` with selection by the ISO date embedded in the filename, preferring
`briefings/` over `weekly_briefings/` on a tie, so the choice is deterministic and
independent of checkout mtimes.

**Acceptance:** exactly one Pushover message per composed brief; zero credentials in
any routine prompt.

### 4.3 One pipeline run per day

Remove the `push:` trigger from `daily-brief.yml:3-5`. Retain `schedule` and
`workflow_dispatch`.

The `briefings/**.md` → `render-briefings.yml` → `pushover-brief.yml` chain is
unaffected; `render-briefings.yml` keeps its own path-filtered push trigger.

**Acceptance:** one `Daily briefing` run per calendar day in Actions history;
`predictions` and `paper_bets` receive exactly one write per bet per day.

### 4.4 Data out of git

**Untrack.** `git rm -r --cached lake dist data` in a single commit (working tree
untouched). Restore the ignore rules from `archive/supabase-migration`'s `.gitignore`
— they were correct; the `-f` was defeating them. Remove `-f` from
`daily-brief.yml:85`.

`ukraine-hourly.yml`'s add step has no `-f`, so it stops committing lake artifacts
automatically once the ignore rules take effect; it continues to commit
`briefings/<date>-ukraine*.png`, which is intended.

**`dist/` needs no replacement mechanism.** It already reaches Pages via
`upload-pages-artifact` with `path: dist`, which does not read git. Untracking it is
a no-op for the published site and eliminates the 4–6 daily "rendered briefs"
commits that churn 97–135 files for a single regenerated timestamp line each.

**`lake/` and `data/store.sqlite` get two tiers.**

- *Hot tier:* `actions/cache`, keyed by date, restore-keys falling back to the most
  recent prior day. Fast, but evicts after 7 idle days.
- *Durable tier:* a GitHub Release per day, tagged `data-YYYY-MM-DD`, carrying
  `lake.tar.zst`, `store.sqlite.zst`, `worldscope.sqlite.zst`. Individual Release
  assets cap at 2 GB; a 563 MB lake compresses well below that.

Restore order at job start: cache → most recent `data-*` Release → cold start.
Persist at job end: write cache, publish Release.

**Concurrency.** `daily-brief` and `ukraine-hourly` both write the lake. Today the
`git rebase -X theirs` retry loop mediates this by accident. Under the new scheme
they get separate cache-key namespaces, with `ukraine-hourly` owning
`lake/sections/ukraine_theater/**` and `daily-brief` treating that subtree as
read-only. `ukraine-hourly` restores only that subtree, not the whole lake, and
drops `fetch-depth: 0`.

**Retention.** Keep the last 60 daily Releases plus a monthly archive Release.
`worldscope/lake_maintenance.py` currently prunes and VACUUMs to stay under
GitHub's 100 MB per-file limit; that constraint disappears, but its retention role
remains and its thresholds should be re-derived rather than deleted.

**Acceptance:** `git ls-tree -r origin/main` reports zero paths under `lake/`,
`dist/`, `data/`; the published site is byte-identical; a cold run with an empty
cache reconstructs the lake from Releases; repository growth falls to ~1–2 MB/day.

### 4.5 Fail loudly

**Tier `_run_stage` (`brief.py:131`).**

- `ImportError` / `ModuleNotFoundError` → **hard fail immediately.** A missing
  dependency is a deployment defect, not a runtime hiccup. This single rule catches
  all five silent no-ops in §2.5.
- Any other exception → record and continue, so a flaky upstream never costs the
  day's brief. Fail the job at the end if any stage marked *required* failed.

Stages are classified required/optional in the registry at `brief.py:372`. Initial
classification: `graphics`, `maps`, `cross-section`, `signals`, `claims`,
`site-builder` required; `embeddings`, `ukraine-maps`, `integrity`, `radar`,
`stories` optional pending a week of observed behaviour.

**Install the extras.** `daily-brief.yml` moves to `pip install -e ".[graphics,sources]"`.
`analytics` (`duckdb`, `sentence-transformers`) is heavy; measure install time before
adding it to the daily job, and gate `embeddings` on it explicitly rather than by
accident. `ukraine-hourly.yml` converges onto the same `pip install -e .` path and
`requirements.txt` is either regenerated from `pyproject.toml` or removed.

**Emit `dist/run_report.json`** per run: schema version, run id, UTC timestamps,
per-stage `{status, duration_ms, error_type, error_message}`, per-section
`{record_count, new_count, state, error}`. Surface a compact pipeline-health block
at the foot of the rendered brief. No new notification channel.

**Acceptance:** a deliberately uninstalled `matplotlib` reddens the workflow; a
simulated upstream 500 does not; `run_report.json` is present and complete for every
run; the calibration graphic renders.

### 4.6 Retire the routine-drawn charts

Once §4.5 restores the graphics stage, `brief.py`'s mirror step writes
`briefings/<date>-<name>.png` from `worldscope/graphics.py`. Replace STEP 3 of the
`WORLDSCOPE morning brief` routine prompt with an instruction to reference the
already-written charts and not regenerate them. Verify whether the pipeline also
emits the STEP 4 events GeoJSON (`worldscope/theater_map.py`,
`worldscope/cartography.py`); if so, retire that instruction too.

**Acceptance:** no matplotlib code executes inside the routine; charts in the
published brief are produced by versioned repository code.

---

## 5. Phase 2 — immutable, point-in-time record

### 5.1 Versioned tables

Applies to `predictions`, `paper_bets`, `paper_bet_marks`, `paper_bet_resolutions`,
`anomalies`, `claims`, `claim_evidence`.

For each table `t`:

1. Rename the physical table to `t_versions`.
2. Add columns: `row_uid INTEGER PRIMARY KEY AUTOINCREMENT`, `as_of TEXT NOT NULL`,
   `run_id TEXT`, `revision INTEGER NOT NULL DEFAULT 1`, `superseded_at TEXT`.
   The former `id` becomes a logical key, not a primary key, with
   `UNIQUE (id, revision)`.
3. Create a view named `t` — the *old* name — defined as
   `SELECT * FROM t_versions WHERE superseded_at IS NULL`.

Point 3 is what keeps the change cheap: every existing reader
(`track_record.py`, `signals.py`, `claims.py`, `graphics.py`, the MCP server,
`site_builder.py`) continues to issue `SELECT ... FROM predictions` and continues to
see current rows. No reader changes.

Writes are the only thing that moves. `Lake.add_prediction` and siblings target
`t_versions` and become: within one transaction, set `superseded_at` on the current
revision for that logical `id`, then insert a new row at `revision + 1`. SQLite
views are not writable without `INSTEAD OF` triggers, which is acceptable because
every write already goes through a `Lake` method.

### 5.2 `knowledge_as_of(t)`

Add a query surface answering "what did the system hold to be true at time *t*":

```sql
SELECT * FROM predictions_versions
 WHERE as_of <= :t
   AND (superseded_at IS NULL OR superseded_at > :t)
```

Exposed as `Lake.knowledge_as_of(table, t)` and as an MCP tool. This is the
precondition for any Phase 4 backtest that is not contaminated by look-ahead, and
it is the reason Phase 2 blocks Phase 3's scorecard work.

### 5.3 What deliberately stays upsert, and why

`records` (`lake/__init__.py:527-531`, `INSERT INTO records ... ON CONFLICT(id) DO
UPDATE`) is **not** versioned.
Volume is ~5,000 rows/day and versioning multiplies storage without adding
information: the authoritative vintage of raw evidence is already the on-disk
`lake/sections/<id>/<date>/raw.jsonl` snapshot, which the `records` table indexes for
convenience.

This holds only if those snapshots are themselves immutable per day. `ukraine-hourly`
overwrites `lake/sections/ukraine_theater/<date>/` 24 times a day; the workflow
comment asserts that "the lake's upsert semantics keep prior intra-day refreshes
addressable by run-id." **That claim is unverified and must be verified during
implementation.** If it is false, `ukraine_theater` snapshots become run-stamped
(`<date>/<run_id>/`) with a `latest` symlink, or the assertion is removed from the
comment and the limitation documented.

`briefs`, `quarantine`, `source_runs`, `sources`, `source_health` stay upsert —
operational bookkeeping, not forecast record.

### 5.4 Migration

One idempotent migration in `worldscope/lake/`, applied on open, guarded by a
`schema_version` row in the existing `meta` table (`lake/__init__.py:67`).

Backfill for existing rows: `revision = 1`, `superseded_at = NULL`, `run_id = NULL`,
and `as_of` derived per table — `predictions.made_at`, `paper_bets.timestamp_bet`,
`paper_bet_marks.mark_date`, `paper_bet_resolutions.resolved_at`,
`anomalies.detected_at`, `claims`/`claim_evidence` from their existing timestamp
column.

Backfilled `as_of` values are recovered timestamps, not observed ones. Rows migrated
this way carry `run_id = NULL`, and that is the marker: **any row with a null
`run_id` predates immutability and must not be treated as a pre-registered call.**
The honest track record starts at the migration boundary. This must be stated in the
scorecard rather than buried.

**Acceptance:** migration is idempotent across repeated opens; a re-run of the same
day produces revision 2 with revision 1 intact and correctly superseded;
`knowledge_as_of` reproduces the pre-rerun state exactly; all existing readers pass
unchanged against the views.

---

## 6. Component boundaries

| unit | responsibility | depends on |
|---|---|---|
| `worldscope.lake` | versioned persistence, `knowledge_as_of` | sqlite3 |
| `worldscope.blobsync` *(new)* | restore/persist lake + stores via cache and Releases | `gh` CLI, tar, zstd |
| `worldscope.brief` | stage orchestration, required/optional policy, `run_report.json` | lake, stages |
| `worldscope.graphics` | chart rendering from the lake | matplotlib, lake |
| `.github/workflows/daily-brief.yml` | one scheduled run; no push trigger; no `-f` add | blobsync |
| `.github/workflows/pushover-brief.yml` | sole delivery path, deterministic selection | — |
| cloud routines | compose prose; never draw charts, never send Pushover | Pages zip |

`blobsync` is new and deliberately small: `restore(paths) -> Source` and
`persist(paths) -> ReleaseRef`, with the cache/Release/cold-start decision internal.
It must be testable against a local fake without network.

---

## 7. Data flow

**Before**

```
07:50 cron ─→ daily-brief (full crawl) ─→ git add -f lake dist data ─→ push ─┐
                                                                             │ push trigger
09:00 routine ─→ compose brief ─→ commit briefings/<date>.md ────────────────┤
                                                                             ▼
09:20 daily-brief AGAIN (full crawl, overwrites predictions + paper_bets)
   ↓
render-briefings ─→ commit dist/ ─→ pushover-brief ─→ Pushover #1
10:00 notifier routine ─→ refetch published HTML ────→ Pushover #2
hourly ×24 ─→ ukraine-hourly (fetch-depth 0 on 1.36 GB) ─→ commit lake subtree
```

**After**

```
07:50 cron ─→ blobsync.restore ─→ daily-brief (full crawl) ─→ blobsync.persist
                                        ↓
                          dist/ ─→ Pages artifact (never git)
                          run_report.json ─→ brief health block
09:00 routine ─→ compose brief (references pipeline charts) ─→ commit briefings/<date>.md
   ↓
render-briefings ─→ Pages artifact ─→ pushover-brief ─→ Pushover (only)
hourly ×24 ─→ ukraine-hourly (shallow, ukraine subtree only) ─→ commit PNGs only
```

---

## 8. Error handling

| failure | behaviour |
|---|---|
| stage `ImportError` | hard fail, red workflow |
| required stage runtime error | recorded, run continues, job fails at end |
| optional stage runtime error | recorded, job succeeds |
| upstream section 4xx/5xx | existing carry-forward, recorded in `run_report.json` |
| cache miss | fall back to latest `data-*` Release |
| Release download fails | cold start, logged as degraded, job fails |
| Release publish fails | job fails; cache still holds the day |
| migration failure | abort open, do not write |
| Pushover credentials missing | non-zero exit (§4.1) |

---

## 9. Testing

TDD throughout; tests precede implementation.

- `_run_stage` tiering: `ImportError` propagates; other exceptions are captured;
  required-vs-optional determines final exit status.
- `run_report.json` schema, including the failure path.
- Versioning: re-writing a logical `id` yields revision 2 with revision 1 superseded
  and intact; `knowledge_as_of` returns the pre-rerun row; readers see only current
  rows through the views.
- Migration idempotency across repeated opens; backfill correctness per table;
  `run_id IS NULL` marks every pre-migration row.
- `blobsync` restore/persist round-trip against a local fake; cache-miss path;
  cold-start path.
- Workflow lint: `daily-brief.yml` has no `push` trigger; no `git add -f` anywhere;
  `ukraine-hourly.yml` does not use `fetch-depth: 0`.
- `pushover-brief.yml` selection is deterministic under uniform mtimes.
- CI must install the extras, or §4.5's hard-fail rule reddens CI itself. This is a
  required first step of implementation, not a follow-up.

---

## 10. Risks and rollback

| risk | mitigation |
|---|---|
| Untracking `lake/` loses history | History is untouched; every past commit still contains it. `archive/supabase-migration` and the Release assets are independent copies. |
| Release/cache path fails on first live run | Land §4.4 last, after §4.3 and §4.5, so a failure is loud and the day's lake still exists in the prior commit. |
| Hard-failing on `ImportError` breaks the daily brief | Install the extras (§4.5) *before* enabling the hard-fail rule. Verify with one manual `workflow_dispatch`. |
| Ukraine hourly and daily race on the lake | Disjoint cache namespaces plus read-only treatment of the ukraine subtree; verify over a full 24h cycle. |
| Migration corrupts the existing record | Migration is guarded by `schema_version` and runs against a Release-restored copy first; the pre-migration DB is retained as a dated Release asset. |
| Routine prompt edits degrade brief quality | Change STEP 3 only; leave analytical structure and voice rules untouched; compare one day's output before and after. |

Every Phase 1 change is individually revertible by a single git revert. Phase 2's
migration is forward-only, which is why §5.4 requires a retained pre-migration copy.

---

## 11. Deferred (Phase 3+)

Recorded here so they are not lost, explicitly not designed in this spec.

- **Phase 3 — honest scorecard.** Separate real-money (Polymarket, Kalshi) from
  play-money (Manifold) and wound-down (PredictIt) venues in every edge computation;
  demote Manifold to a sentiment feed. Add a spread/fee/depth cost model and report
  net edge. Report `signals.py`'s self-graded salience-persistence separately and
  label it explicitly as not forecasting skill — the lake produces both the
  prediction and its resolution, so that Brier score measures the persistence of the
  system's own attention. Wire `track_record` in as a daily stage.
- **Phase 4 — honest search.** Pre-register each decision rule with a hash,
  timestamp and frozen parameters written before it trades; a changed rule becomes a
  new version and the prior version keeps accruing its own out-of-sample record.
  Then deflated Sharpe, probability of backtest overfitting via CSCV, and Hansen's
  SPA across the live rule set.
- **Phase 5 — coverage.** Resurrect the eight dark feeds first (they carry the
  differentiated signal). Then the three sections the registry sketches but never
  built: Maritime/AISStream, Elections, Anomaly. Then new surface: EDGAR full-text,
  AIS chokepoint transits, night-lights, commodity flows, central-bank speech
  corpora.
- **Repository history purge.** Separate runbook, separately approved: freeze order,
  dry-run object counts, rollback mirror.
