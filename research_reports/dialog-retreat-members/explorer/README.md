# Dialog — Investigative Explorer

A self-contained, interactive toolkit for researching the Dialog network across
three linked dimensions — **as a network, over time, and across geographic
space** — independently and interdependently.

## Open it

Just open `index.html` in a browser (double-click, or drag into a tab). No build
step or server required — the data is inlined (`data.js`) and the libraries
(D3, Leaflet) are vendored under `vendor/`. The only thing that needs internet is
the map *basemap tiles*; every marker and interaction works offline regardless.

Prefer a server? `cd explorer && python3 -m http.server` then visit
`http://localhost:8000`.

## The three views (and how they link)

| View | What it shows | Interact |
|---|---|---|
| **Network** (left) | the 325-node / 417-edge socioeconomic graph — people, companies, funds, PACs, orgs, media, gov, family. Node size = degree; color = type. | drag to reposition, scroll to zoom, **click a node** to focus its ego-network and load its detail. |
| **Geography** (center) | every entity placed at its HQ / jurisdiction (258 located) — incl. the Dublin retreat, DC officialdom, Gulf capital, the Pakistan delegation, NATO/Brussels. | **click a marker** to select; the network + detail follow. |
| **Timeline** (top) | 2,181 dated public records binned by year, stacked by type (SEC filings, court, Federal Register, Form 990) plus curated milestones (founding, the leak, lawsuits, the PAC). | **brush a time range** to filter the network and map to entities active in that window. |

**Everything is interdependent.** Selecting a node highlights it in all three
views and lists its connections + primary-source records in the Detail panel.
Brushing the timeline dims nodes/markers with no records in range. The type and
event-type **chips** filter globally. **Search** (top-left) jumps to any node.
**Reset** clears everything.

## Reading the data

- **Detail → Public records** links straight to the primary source (SEC EDGAR
  filing, CourtListener opinion, Federal Register notice, ProPublica 990).
- A node with "no dated public records" is a **structural/relationship node**
  (e.g., a board, a podcast, an abstract intermediary) or files under a different
  legal name — its evidence lives on the connected company/fund node.
- Geographic placement is by **primary affiliation**, not residence.

## Money-flow view (`money.html`)

A second page (linked top-left as **"Money flows →"**) traces **documented dollar
flows** as a Sankey, across four toggleable categories:

- **political** — donor → PAC → candidate (a16z/Brockman/Lonsdale → Leading the
  Future → Bores [opposed, lost] / Torres / Menendez …), FEC/reported.
- **investment** — LP → fund → portfolio (Soros → Key Square; PIF → Affinity;
  KKR/Ribbit/8VC/RenTech/Founders Fund → their holdings), from 13Fs & rounds.
- **philanthropy** — gift → trust → grantee (Barre Seid → Marble Freedom Trust;
  Thiel Foundation → Emergent Ventures).
- **enforcement / exits** — settlements (Galaxy → NY AG; Genesis → victims fund)
  and insider stock sales (Thiel/Kravis → public market), from Form 4/144.

Hover a flow for the **amount + the filing/report it comes from + a confidence
tag**; click a node to isolate its inflows/outflows with source links. **Dashed
links = amount not publicly disclosed** (rendered at uniform width, never
invented). Rebuild with `python tools/dialog_build_money.py`.

## Provenance & caveats

- **Public-record data only.** Built from `network.json`, the `entities/` and
  `foundations/` document bundles, and the curated milestone list — all in this
  report directory.
- **Appearing in this graph does not imply membership, agreement, or wrongdoing.**
  The leaked directory mixed members with past guests/speakers; several named
  people disputed involvement. Litigation/investigation markers are records, not
  findings of guilt.
- No private data: no individual tax returns, home addresses, or the leaked
  private fields (political-leaning scores, contact tokens, matchmaking answers).

## Regenerate

After updating the network or pulling more records:

```
python tools/dialog_build_network.py        # rebuild network.json
python tools/dialog_build_viz_data.py        # rebuild explorer/data.js (+ data/*.json)
```

`data/*.json` (`nodes`, `edges`, `events`, `meta`) are also emitted as plain JSON
for programmatic use (e.g., loading into a notebook or Gephi).
