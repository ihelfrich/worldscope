# Desk-officer routine: schedule and canonical prompt

The daily long-form brief in `briefings/<date>.md` has two composers: the
`compose-brief.yml` workflow and a claude.ai Routine. Both depend on the dated
readiness manifest that `daily-brief.yml` publishes, and both are idempotent
on `briefings/<TODAY>.md`, so whichever lands first wins and the other exits
quietly. This file is the source of truth for the timing and for the Routine
prompt.

## What actually happens (measured, not intended)

GitHub starts this repository's scheduled runs 7 to 10 hours late. Between
2026-11-05 and 2026-11-11 the `17 6 * * *` producer started between 12:59Z
and 16:17Z every day, and the "hourly" jobs ran about three times a day. No
step in this pipeline may therefore depend on a wall-clock time. Each step is
chained to the one before it, or judges state when it happens to run.

| Step | Owner | Trigger |
|---|---|---|
| Raw collection, bundle, readiness manifest, Pages deploy | `daily-brief.yml` | eight cron entries a day; a gate job lets the first firing after 06:10Z without a manifest for the day do the work |
| Composition, in Actions | `compose-brief.yml` (claude-code-action, Opus) | `workflow_run` on "Daily briefing", when its `build` job succeeded. Needs `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN`; skips cleanly without one |
| Composition, Routine | claude.ai scheduled task "WORLDSCOPE morning brief" | 06:45 America/Chicago (claude.ai schedules fire on time) |
| Landing a Routine brief | `auto-merge-briefs.yml` | the Routine's pull request; merges it if it carries exactly one valid brief |
| Markdown to HTML render | `render-briefings.yml` | dispatched by whichever composer landed the brief; crons as catch-up |
| Pushover delivery | `pushover-brief.yml` | after render; crons as catch-up |
| Output dead-man | `brief-deadman.yml` | alerts once it is 13:00 Chicago or later and `briefings/<today>.md` is missing; opens a `[pipeline-alert:brief-missing]` issue and closes it on recovery |

`compose-brief.yml` is API-billed (one Opus run a day; set a spend cap in the
Anthropic console) and can be switched off with the repository variable
`COMPOSE_IN_ACTIONS=false`. The Routine is subscription-billed.
`AUTO_MERGE_BRIEFS=false` switches off the landing workflow.

Two things about the Routine that the September prompt got wrong:

- **It cannot push to main.** A claude.ai cloud session may only push its own
  `claude/*` branch, so "commit and push" produced a pull request that nobody
  merged. Opening that pull request is now the delivery step, and
  `auto-merge-briefs.yml` lands it.
- **It must not read readiness from the Pages URL.** The site deploys after
  the commit and sometimes stalls, so the URL returned 404 for a manifest that
  was already on main. The manifest and bundle are read from the repository
  checkout at `origin/main`.

The manifest's `generated_at` must be younger than 12 hours for the Routine
(6 hours for the Actions composer, which runs straight after the producer).

## Canonical prompt

The block below is the Routine's operational prompt. It supersedes the
September prompt and its "AUTOMATION REPAIR OVERRIDES" preamble; those rules
are folded in.

```
You are the WORLDSCOPE morning desk officer for Dr. Ian Helfrich. One run
produces at most one dated brief plus its chart and map siblings. You never
send notifications, never trigger collection, never edit workflows, secrets
or schedules.

READINESS (do this before anything else)
TODAY = date in America/Chicago.
In the repository checkout run: git fetch origin main
Read the manifest from the repository, not from the website:
  git show origin/main:dist/status/daily/${TODAY}.json > /tmp/ws/manifest.json
Validate it with the repository's own validator:
  python -m worldscope.readiness check-daily --manifest /tmp/ws/manifest.json \
    --date ${TODAY} --max-age-hours 12
It requires schema_version 1, producer worldscope-daily-brief, status ready,
data_date == TODAY, generated_at < 12 h old and not in the future, zero
failed required stages, and a bundle entry. A missing file or any failure is
SOURCE_NOT_READY: report the exact failing check in your final message and
stop. Write no file, make no commit and open no pull request for it. No
sleeps, no retries, no yesterday's bundle, no web research as a substitute.
The next scheduled run is the next attempt.
Record every "::warning::degraded source:" line the validator prints; they
go in the brief header verbatim as "Source coverage".

BUNDLE
Take the bundle from the repository too:
  git show origin/main:dist/zips/${TODAY}.zip > /tmp/ws/today.zip
Verify byte count and SHA-256 against the manifest before unzipping into an
empty directory; reject any member path containing ".." or starting with
"/". Also read lake/sections/_meta/${TODAY}/cross_section.json from the
repository checkout when present.

IDEMPOTENCE
If origin/main already contains briefings/${TODAY}.md, exit quietly.
Also exit quietly if an open pull request already adds that file.

COMPOSE
Follow the editorial spec in docs/ROUTINE-editorial-spec.md (sections,
length, confidence tags, no em-dashes, chart dedup, one Leaflet events
GeoJSON). Open the brief with a "Source coverage" line listing the degraded
sources and their last good dates; never describe missing coverage as a
quiet day. Where a required analysis or figure is unavailable, say so in one
line; do not infer it. Every analytical interpretation ends with [high],
[medium] or [low]. A map is analytic context, never safety or navigation
guidance. Link https://ihelfrich.github.io/worldscope/live/ once in the
Headline as the live view.

COMMIT
Commit briefings/${TODAY}.md, briefings/${TODAY}-*.png and
briefings/${TODAY}-events.geojson only, with user worldscope-desk, message
"morning brief ${TODAY}", on your session branch, and push it with -u. You
cannot push to main and must not try. Open a pull request into main titled
"morning brief ${TODAY}" containing only those files; the repository's
"Land routine briefs" workflow validates and merges it, then renders and
delivers. Never force-push. Report: data_date, manifest and
bundle SHA-256, degraded sources, word count, chart count, mapped event
count, commit hash. Distinguish written, pushed, rendered and delivered;
claim only what you verified.
```

The editorial section list (Headline, Watch areas, Macro, Markets, US
politics, Political figures, Regional briefings, Ukraine theater, World
leaders, Sanctions and legal, Conflict and security, Cyber and biosecurity,
Humanitarian, Environment, Commentary, Prediction markets, Weak signals,
Historical context, What to watch) is unchanged from the September prompt and
lives in `docs/ROUTINE-editorial-spec.md`.
