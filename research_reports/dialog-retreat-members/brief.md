# Research brief: "Dialog" retreat — member network dossier

*type: network-dossier · built by WORLDSCOPE research pipeline + analyst agents*

## What this is

A satirical news-style video by **@thomasvangenderen** ("Duke Silver",
aimoneywatch.org in bio) claims a leaked member directory for **"Dialog,"** an
invite-only, off-the-record retreat associated with Peter Thiel was exposed in
the retreat website's source code. This report **evaluates every named person**,
their **companies owned / founded / heavily invested in**, and their **known
associates**, then assembles the **socioeconomic network** that connects them —
including the intermediary entities (funds, PACs, boards, media, institutions)
that form the actual connective tissue.

> **Ground truth & guardrails.** All subjects are **public figures**; everything
> here is **public-record / public-reporting OSINT** (corporate filings, board
> rosters, FEC disclosures, court opinions, the Federal Register, published
> reporting). No private/personal data. **"Dialog" is a real retreat** —
> co-founded 2006 by **Peter Thiel and Auren Hoffman** — and **the data leak is
> real too**: in June 2026 hacktivist **maia arson crimew** exposed an open
> directory in dialog.org's source code; **WIRED verified** it. The actual
> released roster (the **113-name Directory List** + a **~222-person 2026 retreat
> registration**) is documented in **`LEAK_actual_names.md`**. What remains the
> *video's* satirical dramatization are the specific dollar figures and
> "roundtable topics"; those stay flagged. Other independently-real elements (the
> **Leading the Future** super PAC, the **Alex Bores** race, the **Epstein/Lisa
> Randall** email motif) are labeled as such.

## How it was built

1. **Seven analyst agents** profiled the ~55 named people in parallel clusters
   (tech/PayPal, politicians, finance, corporate, academics, money-and-politics,
   and uncertain overlay reads) → `dossiers/01..07`.
2. **A document harvester** (`tools/dialog_pull_documents.py`) ran the WORLDSCOPE
   research pipeline over **66 entities** (every principal + their key
   companies/funds/PACs), pulling real public records into
   `entities/<slug>/` — **1,058 documents**: **796 SEC EDGAR filings**, **167
   court opinions** (CourtListener), **95 Federal Register actions**.
3. **A network builder** (`tools/dialog_build_network.py`) encoded the documented
   ties as a typed graph → `network.json` (166 nodes, 184 edges), `network.md`
   (adjacency + hub ranking), and `network.dot` (Graphviz).

## Files

| Path | Contents |
|---|---|
| **`LEAK_actual_names.md`** | **the real leak: full 113-name Directory List + 222-registrant names** |
| `dossiers/01-tech-paypal-ai.md` | Thiel, Musk, Lonsdale, Hoffman, Brockman, Kwon, Teller, Akhund, Songhurst, Bronstein |
| `dossiers/02-politicians-officials.md` | Cruz, Booker, Himes, Bessent, Monaco, Moore, Norquist, Leo, Brand, Slaughter, Reema Al-Saud |
| `dossiers/03-finance-pe-crypto.md` | Novogratz, Sternlicht, Rubin, Galperin, Chamath, Bryan Johnson, Berggruen, Silbert, Casares, Kapadia |
| `dossiers/04-corporate-ceos.md` | Mohan, Narasimhan, Mutlu, Schlosser, Cook, Cannon-Brookes, McChrystal, Hamburg |
| `dossiers/05-academics-authors.md` | Cowen, Grant, Athey, Haidt, Klein, Harris, Cialdini, K. Scott, Stephens, Levin, Thompson, Warren |
| `dossiers/06-money-politics-ltf.md` | Leading the Future PAC, aimoneywatch.org, Bores + endorsed candidates |
| `dossiers/07-directory-uncertain-names.md` | Cochran (Oklo), Kapadia (XN), and unresolved overlay fragments |
| **`dossiers/08-leak-roster-expansion.md`** | **second-degree web: ~80 leak-roster names not in the video — finance, tech, politics/law, foreign, academics, media** |
| `network.json` / `network.md` / `network.dot` | the socioeconomic graph (**325 nodes, 417 edges**) |
| **`LEGAL_AND_FINANCIAL_FILINGS.md`** | **SEC investment/ownership filings, litigation index, OGE disclosures, and public Form 990 financials** |
| `foundations/<slug>/` | nonprofit Form 990 bundles (revenue/assets/officers) for ~25 network foundations |
| `entities/<slug>/` | per-entity public-record document bundles (brief + raw JSON) |

## The network in one paragraph

*(Updated after the second-degree expansion: the graph now holds **325 nodes and
417 edges**. Thiel's degree rose to **23**; new connector nodes emerged —
**Micky Malka** (Ribbit) bridging the a16z and Brex board interlocks, **Matt
Cohler** linking early-Facebook to the LinkedIn/PayPal-Mafia founding team, and
the **Meta board** (Arnold + Andreessen) and **Brex board** (Malka + Duckett) as
direct intra-roster pairings.)*

The graph has a single dominant human hub: **Peter Thiel** (degree 23, second
only to the Dialog node itself), radiating through three structures he built —
**PayPal** (the "Mafia": Musk, Hoffman, Sacks, and Thiel himself), **Founders
Fund / Clarium / Thiel Capital** (which reach Oscar Health, Stripe, Facebook,
Oklo via Altman's Thiel-LP'd **Hydrazine Capital**, and Eric Weinstein's bridge
to Sam Harris), and **Palantir** (with protégé **Joe Lonsdale**, whose **8VC**
spins out its own defense-tech orbit). The **OpenAI** cluster (Brockman, Altman,
Kwon, Bret Taylor → Bronstein) is the second pole, fused to the first by the
**Leading the Future** super PAC, to which both **Lonsdale and Brockman** are
mega-donors alongside **a16z** — the money node that ties the tech principals to
sitting politicians (**Cruz**, a longtime Thiel donee who chairs the Senate's AI
jurisdiction; **Himes**, who *oversees* Palantir/OpenAI contracting; **Bessent**,
whose Treasury writes the financial-data rules). Secondary clusters — Wall
Street/crypto (Novogratz, Silbert, Casares, Berggruen, Chamath), the Stanford
GSB economics axis (Athey ↔ Levin), NYT/Atlantic/Free Press media, and the
conservative-money network (Leo, Norquist) — attach through shared boards (CFR,
Aspen), shared platforms (All-In, Conversations with Tyler), and shared capital
(Ezetap co-investors Thiel + Chamath + Berggruen). See `network.md` for the full
hub ranking and per-person adjacency.

## Top hubs (degree centrality)

| Rank | Node | Type | Degree |
|---|---|---|---|
| 1 | Dialog retreat (roster hub) | org | 101 |
| 2 | **Peter Thiel** | person | 23 |
| 3 | OpenAI | company | 15 |
| 4 | Leading the Future (PAC) | pac | 11 |
| 5 | Joe Lonsdale / Elon Musk / Founders Fund | — | 9 |
| 8 | Reid Hoffman | person | 8 |
| 9 | PayPal / **Micky Malka** | — | 7 |

*(The Dialog node's degree ≈ the number of named roster members; it is the
roster index, not evidence of coordination. Thiel is the dominant **human** hub.)*

## Reading guide & caveats

- **Verified vs. claimed.** The strongest *documented* Thiel-orbit ties are
  Lonsdale (protégé + Palantir + 8VC), Brockman (OpenAI + LTF donor), Schlosser
  (Founders Fund-backed Oscar), Cochran (Oklo via Hydrazine), Cruz (donee),
  Cowen (Thiel-Foundation-funded Emergent Ventures), and Berggruen/Chamath
  (Ezetap co-investors). The *weakest* / outliers: Rubin, Sternlicht, Warren,
  Cialdini, Mutlu, Teller — present in the satire but with little to no
  documented tie to the core network.
- **Several agents asserted a "WIRED-reported 222-name Dialog leak" as fact.**
  That corroboration could **not** be independently confirmed here and is treated
  as **unverified**; only the existence of the *Dialog retreat itself* is taken
  as real.
- **Transcript corrections surfaced:** Bret Stephens edits *SAPIR*, not *The Free
  Press* (Bari Weiss's); the overlay "Caroline Cochra—" is **Oklo's** Caroline
  Cochran (not Oxide); "Gourav Kapadia" is **Gaurav Kapadia** of XN. The
  "N.S.N. Al-Sabah", "K.K. Tamb—", and the "Henry/Jared/Scott" fragments could
  not be resolved and were deliberately not guessed.
- **Document bundles** under `entities/` are raw pulls keyed by name; common
  names (e.g., "Robert Rubin") may include same-name false positives — treat the
  raw JSON as leads, not confirmed filings, until the named party is verified in
  the document itself.
