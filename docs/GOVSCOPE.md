# GovScope — total U.S. government coverage for WorldScope

GovScope answers two needs:

1. **"Give me any public government document, now."** — a query tool over every
   branch and department (`worldscope.gov.query`).
2. **"Tell me everything the U.S. government did today."** — a WorldScope
   Section (`gov_us`) that lands a *U.S. Government Daily* block in the brief,
   diffed against yesterday.

Plus a **positions ledger** (`worldscope.gov.positions`) that accumulates *who
is on what side of every issue* from the structured record (roll-call votes
today; sponsorship and document-derived stances slot into the same shape).

## Why it's built this way

| Layer | What it covers | How |
|---|---|---|
| **Federal Register API** | *Every* executive department + agency (Energy, Transportation, DHS, Education, HHS, Treasury, EPA, …) and **all presidential documents** (EOs, memos, proclamations) | one key-free call, `fetch.fetch_federal_register` |
| **RSS registry** (`sources.py`) | White House press, Defense, Treasury press, **Federal Reserve**, **DOJ/Attorney General**, State, **ODNI/CIA/FBI/CISA**, CBO/GAO, the courts, and a seed of **state AGs** | parallel defensive fetch, `fetch.fetch_rss_sources` |
| **Congress.gov API** | Bills, laws, actions | `fetch.fetch_congress` (needs `CONGRESS_API_KEY`) |
| **CourtListener API** | **SCOTUS** opinions | `fetch.fetch_courtlistener` (needs `COURTLISTENER_API_TOKEN`) |

The Federal Register is the backbone because a single reliable endpoint already
gives exhaustive, authoritative coverage of the entire executive branch — so the
RSS registry only has to cover the *other* branches and the press layer. Every
source is **defensive**: a dead feed is skipped, a missing key is a no-op, and a
total blackout raises a typed error so the trust layer flags it (it never
silently reports an empty government).

## Daily briefing

`gov_us` is registered in `worldscope/brief.py` and runs every build like any
other section. It pulls the last 2 days across all sources, dedups, diffs vs.
yesterday's snapshot, writes records + a branch-grouped summary into the lake,
and renders a section grouped Executive / Legislative / Judicial / Independent /
State. Entities (issuing org, president) and `issued-by` / `signed-by`
relationships flow into the graph, so `lookup_entity` / `query_relationships`
work over government activity too.

```bash
python -m worldscope.brief --section gov_us      # run just this section
```

## Query any document, at a moment's notice

```bash
# from the lake (fast, offline, full history)
python -m worldscope.gov.query --org "Department of Energy" --since 2026-06-10
python -m worldscope.gov.query --branch judicial --json
python -m worldscope.gov.query --doc-type "Presidential Document" --limit 20

# live across all branches right now
python -m worldscope.gov.query --live --query tariff
```

Filters: `--query` (title+summary substring), `--branch`, `--org`,
`--doc-type`, `--since`, `--limit`, `--live`, `--json`.

## Positions ledger — who's on what

```bash
# ingest recent House/Senate roll-call votes (needs CONGRESS_API_KEY)
python -m worldscope.gov.positions populate --congress 119 --days 7

# query the accumulated ledger
python -m worldscope.gov.positions query --issue agriculture --stance support
python -m worldscope.gov.positions query --entity Thompson --json
```

Each datapoint is a `Position(entity, role, party, state, issue, subject_id,
stance, value, date, source, evidence_url)`. The store is append-only JSONL at
`lake/gov/positions/positions.jsonl`, deduped on (entity, subject, date), so the
history of a member's stances accumulates and can be tracked as it shifts.

## Keys (all optional)

| Env var | Unlocks |
|---|---|
| `CONGRESS_API_KEY` | bills/laws + positions from roll-call votes (free: api.congress.gov) |
| `COURTLISTENER_API_TOKEN` | SCOTUS opinions (already used by the `courtlistener` section) |

Without any key, GovScope still covers the entire executive branch (Federal
Register) and every RSS source in the registry.

## Network note

In Anthropic's sandboxed sessions, outbound access is governed by the
environment's egress allowlist; `*.gov` hosts must be allowed for **live**
fetches. The daily brief runs in CI / a network-enabled session where this is
configured. The query tool's **lake** mode and the positions store are fully
offline. All fetchers degrade gracefully when a host is unreachable.

## Tests

`tests/test_gov.py` proves the whole pipeline offline (pure mappers, recency,
dedup, the Section pull contract incl. total-failure, the query filter, and the
positions ledger roundtrip):

```bash
python -m pytest tests/test_gov.py -q
```

## Extending

- **More sources:** append a `GovSource(...)` line to `sources.py`.
- **More position signals:** write a populator that emits `Position` rows
  (sponsorship, amicus briefs, agency rule stances) and call `record_positions`.
- **State legislatures / governors:** `state_bills` already covers legislation
  via OpenStates; add governor press feeds to the `state` branch of the registry.
