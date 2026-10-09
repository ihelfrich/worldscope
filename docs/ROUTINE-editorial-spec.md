# Desk-officer brief: editorial specification

Register: Setser meets Tooze meets Stratfor. Declarative, specific, sourced.
Target 4,000-6,000 words with no filler; a short honest brief beats a long
padded one. Every major claim carries an inline link to a primary source.

## Inputs

- `bundle/raw/<section>.json` for every section in the verified bundle, plus
  `manifest.json`, `trends.json` (anomaly z-scores), `calendar.json`.
- `lake/sections/_meta/<date>/cross_section.json`: entities in 3+ sections
  (`by_confidence.high`, `by_confidence.medium`).
- `watchareas.yaml`.
- Up to 12 primary-source follow-ups (WebFetch/WebSearch), in this order:
  watch areas with `alert_fired`, anomalies with z > 2, ACLED events with
  20+ fatalities, FIRMS clusters > 100 MW near named infrastructure, any
  SCOTUS or CIT opinion, any sanctions designation of a listed entity,
  Polymarket moves > 25 points, the top-3 political-figure anomaly drivers,
  high-confidence cross-section recurrences, USGS M6+ or GDACS Red, a
  head-of-state speech in the last 24 h.
- Free live supplements (all optional, 20 s timeouts): USGS significant-day
  feed, GDACS RSS, NASA EONET, ReliefWeb v2, Congress.gov, CBR and SAFE
  fixings.

## Charts (matplotlib, Agg, dpi 150, 8x4, no top/right spines)

`yield_curve`, `anomaly_screen`, `fx_oil`, `gdelt_tone_heatmap`,
`watchareas_volume`, `conflict_fatalities`. Accent `#1F3864`, divergence
`#B45309`, bars beyond 2σ `#B91C1C`. Skip any chart whose data is missing
and say so in the section where it would have appeared. **Each PNG appears
at most once in the brief.**

## Map

`briefings/<date>-events.geojson`: one Point Feature per ACLED event, FIRMS
anomaly, USGS M5+ quake and GDACS Orange/Red, with properties `title`,
`summary`, `date`, `severity` (1-10), `source`. The renderer embeds it as a
Leaflet map. The live hourly view is `/live/`; link it once from the
Headline.

## Structure

`# Morning brief - <Weekday DD Month YYYY>`

Then, directly under the title, one line: `**Source coverage:** ...`
listing the degraded sources and last good dates from the readiness
validator, or "all required sources fresh".

1. **Headline**: one paragraph; dominant arc, three strongest signals, watch
   areas that fired.
2. **Watch areas**: one paragraph per area with items (count vs 14-day
   median, top 3 items with links, alert status). Areas with no items go in
   one closing line, "Quiet today: ...".
3. **Macro situation** (5-7 paragraphs; `yield_curve`, `anomaly_screen`
   here only). Link every FRED series. Say why a z-score matters.
4. **Markets** (4-6; `fx_oil` here only).
5. **United States politics & policy** (4-6).
6. **Political figures watchlist** (3-5): top 10 by `anomaly_score`, the one
   dominant driver per figure, one sentence of meaning for the top 3,
   cross-references to other sections. If the file is empty, one line.
7. **Regional briefings**, every continent, mandatory: North America, South
   America, Europe, Russia & post-Soviet, Middle East (4-6), Africa, East
   Asia, South & SE Asia, Oceania (1-2).
8. **Ukraine theater** (4-7): frontline delta vs 7 days ago from
   DeepStateMap (~1 km), 24 h strike density from ACLED (~1 km) and FIRMS
   (375 m) with the three most populous oblasts named, air-alert oblasts,
   damage layers if present, the three theater maps if present. State the
   resolution of every layer. Never claim meter-level unit positions. Never
   include or speculate about Ukrainian unit or territorial-defense
   positions.
9. **World leaders**: cite and link every speech.
10. **Sanctions, designations, legal** (3-4).
11. **Conflict & security signals** (3-5; `conflict_fatalities` here only).
12. **Cyber & biosecurity** (2-3). First-of-kind KEV entries get a two-sentence
    callout.
13. **Humanitarian** (2).
14. **Environment, disasters, climate** (2-4).
15. **Speeches & op-eds by major critics** (2-4): commentary.json plus a 48 h
    search of the named columnists.
16. **Prediction markets** (1-3).
17. **Weak signals** (4-7, highest value; `watchareas_volume` here only).
    Open with the top three cross-section recurrences and what the
    convergence means.
18. **Historical context** (2-3; `gdelt_tone_heatmap` here only).
19. **What to watch, next 14 days** (2-3) with specific dates.

## Style rules

- No paragraph over 180 words. Vary section shape: short paragraphs, framing
  plus bullets, or lede plus H3s.
- Blockquote at most one verbatim quote per section.
- Bold named entities on first mention.
- Every analytical interpretation ends with `[high]`, `[medium]` or `[low]`.
  Sourced facts carry no tag.
- No em-dashes. No filler. No "at its core", "delve", "underscoring",
  "serves as", "Indeed,", "Notably,". No parallel triplets for effect.
- Never invent dates, numbers, names, quotes or statute references.
- Disproportionate effort on Watch areas, Political figures, Ukraine
  theater and Weak signals.
