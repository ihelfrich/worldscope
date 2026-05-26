# WORLDSCOPE spiders

The daily section adapters (Python) make ~1 HTTP call per source per day.
That's the right cadence for top-of-funnel news APIs (GDELT, MediaCloud,
ReliefWeb) but it's far too shallow for the kinds of primary-source dives
that distinguish a real intelligence product from a news roundup.

This directory holds the deeper-crawl spiders that run on a different
schedule (slower, more polite, much more thorough). They write to the
same SQLite snapshot store that the daily run reads from, so the morning
brief can cite directly.

## Stack

- **Go** via the existing `web-intel` binary at `~/go/bin/web-intel`
  (~2,300 URLs/s on sitemap flatten, JSONL + SHA1-sharded HTML archive).
  This is the workhorse for fan-out crawls.
- **Python** orchestration for the watch-area dispatcher (`dispatch.py`),
  since the watchareas.yaml config and the SQLite store are Python-native.
- **Rust** is unused for now. The right place to add Rust would be a
  high-volume telegram/Mastodon firehose consumer; not yet needed.

## Watch-area dispatcher

`dispatch.py` reads `watchareas.yaml`, expands each area into a target list
of publications, government press pages, regulator dockets, and court
filings, then hands the list to `web-intel crawl` with appropriate depth,
politeness, and content extraction. Results land in
`~/.worldscope/spider.sqlite` indexed by (watch_area, url, fetched_at).

Run nightly at 04:00 UTC (cron job in repo-level workflow). The morning
brief reads the previous night's haul as a section called `spider_dive`.

## Target catalogs (per area)

For each watch area, the dispatcher knows three kinds of targets:

1. **Authoritative publications** — country-specific newspapers in
   the local language: Kommersant + Vedomosti for Russia, Haaretz +
   Yedioth for Israel, Al Jazeera Arabic, Caixin + Yicai for China,
   Le Monde + Les Échos for France, FAZ + Handelsblatt for Germany,
   Folha + O Globo for Brazil, La Nación + Clarín for Argentina,
   Daily Maverick + Mail & Guardian for South Africa.
2. **Government / regulator pages** — Treasury OFAC, EU Council
   regulations, UK FCDO sanctions notices, MOFCOM (China), MEA (India),
   MOFA (Japan), Bundesbank, Banque de France, central bank statements.
3. **Court / docket pages** — CIT (USITC), SCOTUS, federal circuits,
   EU CJEU, UK High Court.

The target lists live in `tools/spider/targets/<area-slug>.yaml`. Adding
a target is a one-line YAML edit. The dispatcher picks up changes on the
next run, no code change needed.

## Why this is separate from the daily Python sections

The daily Python sections are designed to fail fast and shallow: one HTTP
call per source, hard timeout, fail-soft on error. They run in a 5-minute
window on a free CI runner. They cover breadth.

The spider runs in a 20-minute window on the user's machine (or on a
dedicated GH Actions schedule). It covers depth. The two are
complementary; the morning brief reads from both.

## Status

- [ ] `dispatch.py` — Python orchestrator, reads watchareas.yaml, fans out
- [ ] `targets/*.yaml` — per-area target catalogs (~6 high-priority areas to seed)
- [ ] `web-intel` wrapper — invokes the Go binary with right flags
- [ ] `sections/spider_dive.py` — reads spider.sqlite, surfaces in daily brief
- [ ] nightly workflow — `.github/workflows/spider-nightly.yml`

This is Phase 2. The daily sections shipped first because they're what
the morning brief currently depends on.
