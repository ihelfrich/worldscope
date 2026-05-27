# political_figures section

Per-figure tracking of acting US political figures, surfaced as the top-10
most-anomalous figures each day in the Worldscope brief. The section lives at
`worldscope/sections/political_figures.py` and writes to
`lake/sections/political_figures/<YYYY-MM-DD>/`.

## What this section does

For each of ~600 figures on the watch list, the section computes a composite
anomaly score in `[0, 1]` based on six weighted components, then ranks the
list and surfaces the top 10 in the daily summary. The score is meant to
catch unusual stock activity, off-baseline speech, tone shifts in coverage,
new disclosures, and enforcement mentions, all relative to that same
figure's own trailing history.

## Watch list composition

Registry: `worldscope/figures_registry.yaml` (regenerate with
`python3 tools/figures/build_registry.py > worldscope/figures_registry.yaml`).

Current breakdown (build date 2026-05-27):

| Tier | Source | Count | TODO entries |
|---|---|---|---|
| Senate | senate.gov contact_information XML | 100 | 0 |
| House (voting + delegates) | clerk.house.gov MemberData.xml | 441 | 0 |
| Cabinet + Cabinet-rank | Wikipedia Second_cabinet_of_Donald_Trump bolded table | 24 | 0 |
| WH senior / Cabinet-rank slots not single-source verified | hand-curated slot list | 12 | 12 |
| SCOTUS | supremecourt.gov composition | 9 | 0 |
| Federal Reserve Board | federalreserve.gov bios/board | 7 | 0 |
| Federal Reserve Bank presidents | slot list | 12 | 12 |
| Independent agency chairs (SEC, CFTC, FDIC, OCC, FTC, FCC, NLRB, NCUA) | slot list | 8 | 8 |
| Total | | **613** | **32** |

The TODO entries are real slots that exist in the US executive structure but
whose current incumbent could not be verified against a single primary
source on the build date. Rather than fabricate, the registry keeps the slot
with `name: TODO` and a `source: stub: incumbent not verified against single source`
marker. Ian provisions verified names by hand and re-runs the builder.

## Per-figure schema

```yaml
- id: senator-warren-elizabeth-ma
  name: Elizabeth Warren
  role: Senator
  jurisdiction: MA
  party: Democratic
  bioguide_id: W000817
  congress_chamber: senate
  committees: []                  # populate via api.congress.gov when CONGRESS_GOV_API_KEY provisioned
  twitter: TODO
  bluesky: TODO
  ogeid: TODO
  cspan_person_id: TODO
  watchlist_tags: [senate]
  source: senate.gov contact_information XML
```

The `bioguide_id` is the unique key for cross-source joins to Quiver
Quantitative STOCK Act PTRs and to the Library of Congress API
(api.congress.gov). ProPublica's Congress API was retired in 2024; the
`propublica_id` registry field is kept as a stable historical identifier
but is no longer queried.

## Composite score

Six components, each in `[0, 1]`, weighted, summed (weights total 1.0):

| Component | Weight | What it measures |
|---|---|---|
| `stock_activity` | 0.25 | PTR count in trailing 30d vs 90d baseline rate, plus max abs ExcessReturn vs SPY over the same window |
| `speech_volume` | 0.15 | Words on the floor or press in last 7d vs 90d weekly baseline (z-score, saturated) |
| `speech_topic_drift` | 0.15 | Cosine distance from the figure's 90d speech-topic centroid |
| `gdelt_tone` | 0.15 | Standardized 24h tone delta vs 30d baseline |
| `new_filings` | 0.10 | Count of new PTR / Form 4 / OGE-278 entries in last 7d |
| `enforcement_hits` | 0.20 | DOJ + OIG + CourtListener mentions in last 7d, binned 0 / 1 / 2+ |

Each component degrades to 0 when its input is missing, so a fresh install
with an empty lake still returns valid (zero) scores rather than crashing.

The recent window for `stock_activity` was widened from the spec's 7d to 30d
because the STOCK Act gives members up to 45 days between trade date and
disclosure date and Quiver returns `transaction_date`, so a strict 7d window
always scores zero in normal operation.

## Signal sources and current status

Verified live on 2026-05-27 (HTTP status, record counts after one real
section run):

| Source | Endpoint | Status | Records this run | Notes |
|---|---|---|---|---|
| Quiver Quantitative PTRs (via lake) | reuses `congressional_trades` section | OK | 66 PTRs across 14 unique members | No refetch; we read the most recent date subfolder of `lake/sections/congressional_trades/` |
| GDELT GKG (via lake) | reuses `gdelt_gkg` section | OK (empty today) | 0 rows | GKG section pulled today but stored 0 rows; tone signal therefore contributes 0 |
| SEC Form 4 (via lake) | reuses `form4` section | OK | 40 filings | Loose surname match on `filer_name`; produces false positives for common surnames like Barr |
| Senate.gov member XML | https://www.senate.gov/general/contact_information/senators_cfm.xml | 200 OK, 52,587 bytes | 100 senators | Used at registry-build time only |
| Clerk.house.gov MemberData.xml | https://clerk.house.gov/xml/lists/MemberData.xml | 200 OK, 555,044 bytes | 441 members | Used at registry-build time only |
| DOJ press release RSS | https://www.justice.gov/news/rss | 200 OK | up to 75 items | Pulled live on every section run; figure-name substring match |
| CourtListener RECAP search | https://www.courtlistener.com/api/rest/v4/search/?type=r | 200 OK | up to 5 per figure | Only the top-K pre-ranked figures get queried (K=25); throttle-aware |
| Library of Congress API | https://api.congress.gov/v3/ | 403 (no key) | 0 | ProPublica's Congress API was retired in 2024 ("ProPublica's Congress API is no longer available", per their docs). The replacement is api.congress.gov, free key signup at https://api.congress.gov. Once `CONGRESS_GOV_API_KEY` is provisioned, covers member metadata, sponsored bills, votes, committee assignments, and the Congressional Record |
| GovInfo Congressional Record | https://api.govinfo.gov/collections/CREC | 401 (no key) | 0 | `GOVINFO_API_KEY` not yet provisioned. Speech volume / topic drift contributes 0 until then |
| Senate EFD search | https://efdsearch.senate.gov/search/home/ | 200 OK | not yet wired | CSRF token + session needed; sketched, not implemented |
| House EFD | https://disclosures-clerk.house.gov/PublicDisclosure/FinancialDisclosure | 200 OK (after redirect) | not yet wired | PDF-based; sketched, not implemented |
| OGE-278 | https://efile.oge.gov/ | connection timeout | not yet wired | API endpoint pattern not stable; sketched, not implemented |
| IGNet OIG aggregator | https://www.oversight.gov/rss/reports | 404 | not yet wired | Endpoint shape moved; needs investigation |
| White House feed | https://www.whitehouse.gov/feed/ | 404 | not yet wired | Site has no public RSS; would need browser-emulating scrape |
| C-SPAN per-person | https://www.c-span.org/person/<id> | not tested | not yet wired | No API; scrape responsibly |

## Env-var secrets Ian still needs to provision

| Env var | Purpose | Affected component |
|---|---|---|
| `CONGRESS_GOV_API_KEY` | Library of Congress API (api.congress.gov) | speech_volume, speech_topic_drift, committees field on registry. Replaces retired ProPublica Congress API |
| `GOVINFO_API_KEY` | GovInfo Congressional Record bulk API | speech_volume, speech_topic_drift |
| `COURTLISTENER_API_TOKEN` (or `COURTLISTENER_API_KEY`) | CourtListener RECAP search | enforcement_hits (already partially live without key, but key lifts rate limits) |

Until those keys land, the section runs with `speech_volume` and
`speech_topic_drift` permanently at 0, and `enforcement_hits` capped by the
free-tier CourtListener rate limit.

## Section contract artifacts

Per the section-adapter contract, each run emits three files under
`lake/sections/political_figures/<YYYY-MM-DD>/`:

`raw.jsonl`: one row per figure (active or stub), with `figure_id`,
`figure_name`, `figure_role`, `anomaly_score`, all six `components`, the
hit-counts for each signal source, and `evidence_record_ids` pointing back
to lake records.

`summary.md`: top-10 ranked figures with composite scores and per-driver
breakdowns, plus evidence cite-bracket links.

`structured.json`: Person entities (one per active figure), plus an
`anomalies[]` list of the top-10 ranked items with their drill-down
components.

The `entities_added` payload populates the lake's `entities` table so the
MCP server's `lookup_entity` / `query_relationships` tools can answer "what
do we know about <figure>?" queries.

## Operational notes

- The section depends on `congressional_trades`, `gdelt_gkg`, and `form4`
  having already run today. The brief registry orders them earlier.
- CourtListener calls are throttled to the top-K pre-ranked figures (K=25)
  to stay within the free tier.
- Stub entries (name = TODO) are kept in the registry so the count is
  honest, but they always score 0 and never appear in the top-10.
- Surname-only matching for Form 4 produces false positives for common
  surnames (Barr, Smith, etc.). The next iteration should require firm-name
  + figure-name co-occurrence, or restrict to Form 4 filers whose `filer_name`
  includes a figure's full name token sequence.
- The composite score is a screen, not a verdict. A high score flags "this
  figure is unusual today on at least one axis", not "this figure did
  something wrong". The brief composer is responsible for the human-read
  interpretation.
