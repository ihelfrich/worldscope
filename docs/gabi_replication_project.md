# Gabi's replication project — analysis and recommendation (v2)

*Prepared 2026-06-10, revised same day after reviewing the QSS Lab Drive folders (group + private)
and the prior session's 5-step plan. Covers the state of the work with Katia, Liz, and Gabi, and
recommends the published paper Gabi should replicate.*

---

## 1. Framing correction from v1

QSS Lab is **method-led, not topic-led**: a distributed, project-based group run by Ian Helfrich
and Dr. Elizaveta Gonchar, working across the social sciences with quantitative and computational
methods. NMTC is *one project* (Ian's rural mobilization-gap paper), not the lab's identity. The
replication project below is therefore chosen as a **training instrument** — practicing the full
empirical pipeline on free data — that happens to also feed the blended-finance thread Gabi sits
in, not as a commitment to public finance as the lab's direction.

## 2. Where things stand (current as of the June 9 Drive setup)

**The pods.** The live pod is **Portugal — Blended Finance & COMPETE2020**: Katia Antunes leads
day-to-day (researcher, in Portugal for summer fieldwork; interviews underway), Gabi is the
**data apprentice**, Ian and Liz advise. The pod extends the US blended-finance/NMTC work to
Portugal's COMPETE2020 structural-funds program. This is the template pod per `LAB_STRUCTURE.md`.

**Gabi's state.** She is brand new (apprentice tier), wants hands-on experience with real
research, and needs training before she can meaningfully help Katia. Her **first task is already
assigned**: COMPETE2020 source recon (find the program portals, the Lista de Operações beneficiary
lists, the EU Cohesion Open Data Platform; build a source table and a Portuguese-English
glossary), 4–6 hours, **due Monday June 16**, first weekly check-in then. Her Portuguese is the
asset there. The replication project is the *next* thing, and it should not collide with the
recon deadline.

**What Katia has found.** Her source list has been delivered and processed into the Drive:
"Portugal Project — Literature Map (triaged)" (A/B/C source-authority tiers) and an annotated
bibliography of 97 sources. Reviewed for replication potential: it is overwhelmingly
**institutional reports and case studies** (OECD, IFC, World Bank, IMF, EIB, Convergence) plus a
few SSRN/journal papers, mostly qualitative or descriptive. **Nothing in that corpus is a clean
quantitative replication target with free data.** It is the right reading base for the pod, but
the replication paper has to come from the US place-based-policy literature, where the lab
already holds the data.

**Data the lab holds or can get free** (Datasets DB + Drive): the CDFI Fund NMTC transaction
file (~2003–2022, tract-level, documented — the working base of Ian's paper and the April 22
pilot brief for Katia), BEA Regional, FRED, GHS-SMOD/WorldPop, and anything public —
BEA/BLS/ACS/CPS/Census downloads are all in scope per Ian's direction.

**The 5-step plan (the reference Ian asked for).** The prior session's plan survives as the
Notion page [Gabi · First project: reading the literature, then backing out a paper](https://app.notion.com/p/3750847164058160903de5e2b1f1e763)
(the raw session transcript itself isn't accessible, but the artifacts are). The five rungs:

1. **Read and map the literature** — see the machinery inside a paper (question, method, data, finding).
2. **Pick one paper we have data for and back out exactly how they did it.**
3. **Try to reproduce a piece of it** with data we have.
4. **Find the holes** — what didn't they check?
5. **Propose an extension** — a small new question; her first research idea.

The companion page [Replicating a paper: a worksheet](https://app.notion.com/p/37608471640581869db4cc68f77b5ac4)
operationalizes rungs 2–3 (one result; data → sample → specification → run → compare → reconcile).
The replication project below is rungs 2–3 of that plan, and the paper is chosen so rungs 4–5
point somewhere real.

## 3. What the replication paper must satisfy

1. **Published**, so Gabi dissects a finished, refereed paper.
2. **Easy**: methods at the OLS / fixed-effects / threshold-comparison level.
3. **Data we hold or that is free** (BEA/BLS/ACS/CPS/NMTC/FRED/HUD).
4. **Exercises the training arc Ian specified**: cleaning raw data, sorting/filtering into an
   analysis sample, building the model, and seeing how tests and robustness take shape in
   empirical economics.
5. **Connects forward** to the Portugal pod, so the skills transfer directly to helping Katia.

## 4. Candidates considered

| Paper | Venue | Data | Verdict |
|---|---|---|---|
| **Freedman (2012), "Teaching new markets old tricks"** | J. Public Economics | CDFI Fund NMTC file (we hold it) + Census 2000/ACS tract data (free) | **Recommended** |
| Baum-Snow & Marion (2009), LIHTC developments and neighborhoods | J. Public Economics | HUD LIHTC database + census (all free); already flagged "Replication candidate" in the Lit DB | Strong backup / second replication |
| Busso, Gregory & Kline (2013), Empowerment Zones | AER | Restricted Census microdata for core results | Too heavy; read-only |
| Harger & Ross (2016), NMTC and new businesses | J. Regional Science | Proprietary Dun & Bradstreet | Ruled out |
| Anything from Katia's blended-finance corpus | various | Mostly reports/case studies, qualitative | No clean quantitative target |

## 5. Recommendation: Freedman (2012), first stage, as the training vehicle

Freedman, M. (2012), ["Teaching new markets old tricks: The effects of subsidized investment on
low-income neighborhoods"](https://www.sciencedirect.com/science/article/abs/pii/S0047272712000886),
*Journal of Public Economics* 96(11) ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2180227)).

**The one result (worksheet step 0):** the first stage — census tracts just below the
80%-of-area-median-income eligibility threshold receive discontinuously more NMTC investment
than tracts just above. Not the full RD with outcomes; the engine of the paper.

**Why it fits the training goals, step by step:**

- **Cleaning:** the raw CDFI Fund release is a real, messy administrative file (tract GEOIDs to
  standardize, multi-CDE deals, amounts to deflate). Exactly the "get your hands dirty" material.
- **Sorting/filtering:** building the analysis sample — which years, which tract vintage (2000
  boundaries), how eligibility is defined (median family income ratio; the poverty-rate OR-clause
  is the classic trap) — is where the worksheet says replications quietly go wrong, and where the
  learning is.
- **Building the model:** a merge of two datasets (NMTC + census tract income), then one OLS with
  a threshold dummy; richer local-linear RD only as a stretch goal.
- **Tests taking shape:** sign/magnitude comparison against a published JPubE number, then
  reconciliation (data vintage, sample rules, clustering) — the real anatomy of empirical work.

**Why it connects forward rather than dead-ending:** the NMTC eligibility margin is the US
benchmark for the very question the Portugal pod asks of COMPETE2020 (does public money pull in
private money, and where?). Rung 4 has a natural answer waiting (*does the eligibility
discontinuity in mobilized investment differ in rural tracts?* — feeding Ian's rural-gap paper),
and rung 5 can become *what's the COMPETE2020 analogue?* — which is precisely Katia's project.
Per the April 22 pilot brief, the LIC-eligibility RDD is already the lab's named identification
strategy, so Gabi's replication doubles as groundwork the pod will actually use.

**Stretch goals, in order:** (a) one reduced-form outcome around the cutoff (e.g., tract poverty
rate or home values, ACS); (b) split the first stage by rural/non-metro tracts; (c) write it up
on a duplicated worksheet page.

## 6. Sequencing (so nothing collides)

1. **Now → June 16:** Gabi finishes the COMPETE2020 recon (her current deliverable). No new
   assignments before then.
2. **Ian, this week:** cut Gabi's NMTC slice (tract GEOID, CDE, year, QLICI amount, total project
   cost) with a one-page data dictionary; drop it in the Portugal pod's `Data/` folder. Add a
   pointer to the Census 2000 tract income tables (NHGIS/IPUMS or Census FTP — free).
3. **June 16 check-in (Ian/Liz + Gabi):** debrief recon; introduce the replication as rung 2–3 of
   the 5-step plan; hand over the Freedman PDF and a duplicated worksheet page with step 0
   pre-filled. Liz sets the weekly cadence and the "stuck >30 min → ask" rule.
4. **Weeks of June 16–July:** Gabi cards Freedman first (rung 2), then builds the replication
   (rung 3) in the Bench, with the worksheet as the spine.
5. **Katia:** stays focused on interviews; Gabi's recon table + glossary flow to her. The
   three-way intro meeting (outstanding from June 4) happens once the recon is in.
6. **Backup:** if the NMTC slice is delayed, Baum-Snow & Marion (2009) on the public HUD LIHTC
   database is the fallback, already flagged in the Literature DB.

## Sources

- QSS Lab Drive (group): Member Handbook; Projects / "Portugal — Blended Finance & COMPETE2020"
  (Welcome doc, First Task — COMPETE2020 Source Recon, Project Brief — Blended Finance (NMTC
  context), Literature Map (triaged), SSRN/paywalled resolved list); Library (97-source annotated
  bibliography).
- QSS Lab Drive (private, Ian & Liz): `LAB_STRUCTURE.md` (roles, pods, the ladder, Stage 0 plan);
  `FOUNDING_BRIEF.md` (not summarized here).
- Notion (prior session's artifacts): "Gabi · First project" (the 5-step plan); "Replicating a
  paper: a worksheet"; Literature DB; Datasets DB; The Lab — HQ.
- Zoom summaries: June 4 (Katia — lab plans, Gabi intro), May 18 (Katia — NMTC regressions).
- Freedman (2012), *J. Public Economics* ([ScienceDirect](https://www.sciencedirect.com/science/article/abs/pii/S0047272712000886), [SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2180227));
  Harger & Ross (2016) ([WVU WP](https://researchrepository.wvu.edu/econ_working-papers/107/));
  [CDFI Fund NMTC program](https://www.cdfifund.gov/programs-training/programs/new-markets-tax-credit).
