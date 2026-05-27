# Ukraine Theater Section

**Status:** v1 binding for the worldscope pipeline. Last updated 2026-05-27.

This section exists for one reason: Ian has friends and family in Ukraine, especially in the Kyiv region, and wants a continuous total-war monitoring layer that an open-source pipeline can plausibly maintain. It is not a battle map. It is a context layer.

The section conforms to the Section Adapter Contract (`docs/SECTION_ADAPTER_CONTRACT.md`). Every record carries two contract-additional fields:

- `geo_resolution_m` (integer meters): the upper bound on what the record's geometry can resolve
- `latency_hours` (float): the typical time from real-world event to public visibility

Both fields are populated per source. They are surfaced in `raw.jsonl` and in the cartography attribution boxes so a reader can honestly say what they are and are not looking at.

---

## Sources

| # | Source | Endpoint | Geo res (m) | Latency | Auth |
|---|---|---|---|---|---|
| 1 | ACLED Ukraine events | `acleddata.com/api/acled/read` | 1000 | 24-72h | `ACLED_EMAIL` + `ACLED_PASSWORD` |
| 2 | NASA FIRMS NRT (VIIRS Europe 24h) | `firms.modaps.eosdis.nasa.gov/data/active_fire/.../SUOMI_VIIRS_C2_Europe_24h.csv` | 375 | 3-6h | none |
| 3 | DeepStateMap frontline | `deepstatemap.live/api/history/last` | 1000 | 24h | none |
| 4 | ISW campaign assessment | `understandingwar.org/backgrounder/...` (HTML scrape) | 1000 | 24h | none |
| 5 | UA Air Force air alerts | `api.alerts.in.ua/v1/alerts/active.json` | 50000 (oblast) | ~30s | none |
| 6 | Liveuamap | `liveuamap.com/en/feed` (RSS) | 1000 | 1-2h | none |
| 7 | UA official channels (ZSU, MoD, KCS) | various `*.gov.ua/rss` | 50000 | ~1h | none (Google News fallback) |
| 8 | OSINT Telegram via RSSHub | `rsshub.app/telegram/channel/<name>` | 5000 | 1-6h | none (rate-limited) |
| 9 | BlueSky tag search | `public.api.bsky.app/xrpc/app.bsky.feed.searchPosts` | 50000 | <1h | none |
| 10 | UNOSAT damage products | `unosat.org/products?country=ukraine` | 1 (at activation) | 24-96h | none |
| 11 | Copernicus EMS | `emergency.copernicus.eu/mapping/...` | 1-10 | 6-72h | none |
| 12 | Sentinel-2 STAC | `earth-search.aws.element84.com/v1/search` | 10 | 5-day revisit | none (SKIPPED in v1) |
| 13 | Kontur HRSL population baseline | bundled | 30 (covered) / 100 (WorldPop) | static | none |
| 14 | web-intel BFS-2 crawl | `~/go/bin/web-intel` over editorial sites | 50000 | 12h | none |

Sentinel-2 STAC is sketched but deferred: parsing change-detection deltas on a 5-day revisit cycle without a backing geoserver is too much for the first cut. Marked as TODO in the section module.

---

## The ZSU-protection rule (NON-NEGOTIABLE)

Even if a source publishes one, we do NOT ingest a record geocoded inside Ukrainian-claimed territory that explicitly identifies a current ZSU / territorial-defense / National Guard unit position.

### Heuristic (implemented in `_is_zsu_active_position`)

A record is dropped if ALL FOUR of the following hold:

1. It has `latitude` + `longitude` inside the Ukrainian-claimed bbox `(22.0, 44.0, 40.3, 52.4)`.
2. Its text (title + summary + notes + text fields, concatenated and lowercased) contains a ZSU / territorial-defense / National Guard / brigade / battalion / regiment marker, in English, Ukrainian, or transliterated Russian. The marker list is in `ZSU_UNIT_TOKENS`.
3. Its text also contains a position-indicating word: position, stronghold, fortified, dug in, deployed, stationed, garrisoned, concentrated, field HQ, command post. Ukrainian and Russian variants included. The list is in `POSITION_TOKENS`.
4. Its text does NOT contain any of the non-position markers in `NON_POSITION_TOKENS`: destroyed, knocked out, neutralized, killed, casualties, losses, strike on, attack on, shelling of, missile hit, moving, moved, redeployed, withdrew, withdrawal, wounded, captured, POW.

If all four conditions hold, the record is dropped at the section's `pull()` step before storage. The drop count is reported as a synthetic summary record (`zsu-filter-summary`) and surfaced in the anomalies array of `structured.json`, so the filter's activity is visible but no underlying coordinates are published.

### Why this works (and where it fails)

The heuristic is intentionally conservative on three axes:

- It uses an OR of a long ZSU-marker list rather than relying on precise unit identifiers. False positives (dropping benign mentions) are preferred over false negatives.
- It requires geographic confinement to Ukrainian-claimed territory. Russian-side positions are not subject to the rule; reporting Russian unit positions is standard open-source practice and we do not filter it.
- It releases the filter on any destruction / movement / strike marker. A strike against a ZSU unit is reportable, the existence of an attack against a position is already public the moment shells land, and movement of forces is widely tracked. We only refuse to publish present-tense resting positions.

Where the heuristic fails: a record that uses none of the ZSU markers (e.g. just a unit nickname), or one whose position-indicating word lives in a foreign-language synonym we did not list, can slip through. We mitigate this by also relying on upstream sources' own filters (DeepStateMap and major OSINT channels generally follow a similar norm) and by treating the worldscope pipeline as one layer in a defense-in-depth approach, not as the only line.

This is a structural protection, not a content guarantee. If a leaked position appears, we want to remove it and tighten the heuristic; we do not want to lecture the reader about why we cannot show it.

---

## Refresh cadence

The section is designed to run hourly. Heavier sources (UNOSAT, Copernicus EMS) are configured in `DAILY_PULLS` so they can be moved to a once-daily cadence when the hourly micro-refresh workflow lands in issue #93. Until then both lists run on each invocation; the section's full hourly budget is ~60 seconds in practice (most pulls complete in 1-5s; web-intel and RSSHub are the long tails).

`pull()` is idempotent: re-running the same date never double-counts because the contract's UPSERT dedup-key logic in `to_lake()` keys on the deterministic record IDs each source generates.

---

## Cartography outputs

Maps land under `figures/daily/<YYYY-MM-DD>/maps/`:

- `ukraine_theater_overview.png` (1400x1100): full theater bbox (22, 44, 40, 53) with DeepStateMap frontline, ACLED past-24h events, FIRMS thermal, and air-alert oblast fills
- `ukraine_kyiv_focus.png` (1200x1000): Kyiv city + oblast zoom with Kontur HRSL population underlay (gray), labeled districts, and the same activity layers
- `ukraine_damage_recent.png` (1400x1100): UNOSAT + Copernicus EMS product flags from past 7 days
- `ukraine_population_at_risk.png` (1400x1100): Kontur HRSL × past-24h activity envelope, heat colored only where activity overlaps inhabited cells

All maps use the heritage palette only and contain no em-dashes. Attribution boxes (bottom-left) carry per-source resolution.

The Kontur HRSL underlay is currently a synthetic Gaussian-bump proxy at major-city centroids. When the real 30m raster lands in `worldscope/cartography_data/`, the imshow call in `cartography_ukraine.py::render_kyiv_focus` and `render_population_at_risk` will pick it up.

---

## Files

- `worldscope/sections/ukraine_theater.py`: section adapter
- `worldscope/cartography_ukraine.py`: four-map renderer
- `worldscope/crawlers/__init__.py` plus `worldscope/crawlers/ukraine_osint.py`: web-intel wrapper
- `tests/test_ukraine_theater.py`: smoke + ZSU-filter tests
- `docs/ukraine_theater.md`: this file
