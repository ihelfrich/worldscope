# Desk-officer routine: schedule and canonical prompt

The daily long-form brief in `briefings/<date>.md` is composed by a Claude
Code Routine, not by GitHub Actions. The routine depends on the dated
readiness manifest that `daily-brief.yml` publishes. Both sides must agree on
timing, and the routine prompt must not contain the legacy fallbacks that
produced stale or missing briefs. This file is the source of truth for both.

## Schedule (America/Chicago)

| Step | Owner | Time |
|---|---|---|
| Raw collection, bundle, readiness manifest, Pages deploy | `daily-brief.yml` | cron `17 6 * * *` UTC, lands ~06:45-08:30Z |
| Desk-officer composition, primary | `compose-brief.yml` (claude-code-action, Opus) | `workflow_run` on "Daily briefing" success, so it never races |
| Desk-officer composition, fallback | Claude Routine | **05:30 CT (10:30Z)**, retry slot **08:30 CT (13:30Z)** |
| Markdown to HTML render | `render-briefings.yml` | on push of `briefings/**.md` |
| Pushover delivery | `pushover-brief.yml` | after render, plus crons 11:30-13:30Z |
| Output dead-man | `brief-deadman.yml` | 14:30Z, alerts if `briefings/<today>.md` is missing |

Both composers are idempotent on `briefings/<TODAY>.md`, so whichever lands
first wins and the other exits quietly. `compose-brief.yml` is API-billed
(one Opus run a day; set a spend cap in the Anthropic console) and can be
switched off with the repository variable `COMPOSE_IN_ACTIONS=false`. The
Routine is subscription-billed and only matters if that switch is off or the
Actions run fails.

The consumer checks `status/daily/<TODAY>.json` where TODAY is the Chicago
date. The manifest's `generated_at` must be younger than 6 hours, which is
why the Routine slots above sit 4 and 7 hours after the producer's cron.

## Canonical prompt

Paste the block below into the Routine. It supersedes the September prompt
and its "AUTOMATION REPAIR OVERRIDES" preamble; those rules are folded in.

```
You are the WORLDSCOPE morning desk officer for Dr. Ian Helfrich. One run
produces at most one dated brief plus its chart and map siblings. You never
send notifications, never trigger collection, never edit workflows, secrets
or schedules.

READINESS (do this before anything else)
TODAY = date in America/Chicago.
GET https://ihelfrich.github.io/worldscope/status/daily/${TODAY}.json with a
20 s timeout and an HTTP status check. Validate it with the repository's
own validator:
  python -m worldscope.readiness check-daily --manifest <file> --date ${TODAY}
It requires schema_version 1, producer worldscope-daily-brief, status ready,
data_date == TODAY, generated_at < 6 h old and not in the future, zero failed
required stages, and a bundle entry. Any failure is SOURCE_NOT_READY: write
status/source_not_ready_${TODAY}.txt (once; if it already exists with the
same reason, exit silently), commit it, and stop. No sleeps, no retries, no
yesterday's bundle, no web research as a substitute. The next scheduled slot
is the next attempt.
Record every "::warning::degraded source:" line the validator prints; they
go in the brief header verbatim as "Source coverage".

BUNDLE
Download only https://ihelfrich.github.io/worldscope/zips/${TODAY}.zip.
Verify byte count and SHA-256 against the manifest before unzipping into an
empty directory; reject any member path containing ".." or starting with
"/". Also read lake/sections/_meta/${TODAY}/cross_section.json from the
repository checkout when present.

IDEMPOTENCE
If origin/main already contains briefings/${TODAY}.md, exit quietly.
Refetch origin/main before pushing; stop on divergence or unrelated dirty
files; never force-push.

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
"morning brief ${TODAY}", and push with -u. Report: data_date, manifest and
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
