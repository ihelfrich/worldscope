# Gabi's replication project — analysis and recommendation

*Prepared 2026-06-10. Covers the state of the lab work with Katia, Liz, and Gabi, and recommends
the published paper Gabi should replicate as the next rung of her onboarding ladder.*

---

## 1. Where things stand

**The Lab** (HQ in Notion) is a four-person remote research group: Ian (PI), Liz (operations,
member experience), Katia Antunes (blended finance / the Portugal extension of the NMTC work,
in Portugal for the summer), and Gabriela "Gabi" Henderson (research associate, Claremont McKenna,
the student flagged in the June 4 meeting with Katia as able to help with data work).

**The research program.** How public money (tax credits, subsidies, development finance) pulls in
private money, and where it works better or worse. Two live threads:

- **NMTC: The Rural Mobilization Gap** — Ian's sole-authored paper decomposing the rural-vs-urban
  leverage gap in the US New Markets Tax Credit (~82% of the gap is between-CDE selection).
  Stage: Writing, SSRN-bound. Empirical base: the CDFI Fund transaction-level NMTC data.
- **Katia / Portugal** — qualitative-leaning blended-finance fieldwork (interviews start this
  month), bi-weekly journal updates, spatial data tracked alongside. The IMF paper this builds on
  is delayed ~2 years, so the lab has a window to publish first.

**Gabi's onboarding (the ladder).** Her project page sets a five-rung ladder: (1) map the
literature → (2) back out exactly how one paper was done → (3) reproduce a piece of it with data
we have → (4) find the holes → (5) propose an extension. She is on rung 1 now: a 10-paper
corpus-ranked starter list in the Literature DB, plus two worked-example rows (Schuetz & Talle
2001) showing what a finished card looks like. The replication worksheet ("Replicating a paper")
already defines rungs 2–3 as: pick ONE result, document data/sample/specification, run it in the
Bench, compare, reconcile.

**Data the lab actually holds** (Datasets DB, 12 entries): the directly relevant one is
**NMTC transactions (CDFI Fund)** — transaction-level allocations + QLICIs, ~2003–2021, tract-level,
open/public, status *Documented*, crosswalk keys: census tract GEOID, CDE, year. Its card already
says: *"The slice Gabi receives comes from here."* Supporting layers: BEA Regional (SAGDP), FRED,
GHS-SMOD and WorldPop (rural/urban classification). Nothing in the commons yet for HUD/LIHTC or
census-tract demographics, but both are free public downloads.

**Open action items** (from HQ "This week" + the June 4 Katia meeting):

| Owner | Item | Status |
|---|---|---|
| Katia | Send cleaned-up list of recommended blended-finance papers for Gabi | Not yet received by email as of 6/10 |
| Gabi | Start carding the starter list into the Literature DB | All 10 rows still "To read" |
| Ian | Share the NMTC data slice with Gabi | "Data Run-Through: Public Finance" was calendared for Jun 9, 9–10am CDT; no recording/summary surfaced — confirm it happened |
| Ian | Post the rural-gap draft on SSRN; notify Katia | Pending |
| Ian | Introduce Gabi ↔ Katia, arrange three-person meeting | Pending |
| Liz | React to charter + membership/expectations draft; set Gabi's check-in cadence | Pending |

## 2. What the replication paper must satisfy

1. **Published** (a real venue, so Gabi sees the full anatomy of a finished paper).
2. **Uses the NMTC data we hold** (or data adjacent to Katia's blended-finance corpus).
3. **One headline result reproducible with free data** — no proprietary inputs.
4. **Methods at the OLS / fixed-effects / simple-RD level**, matching the Methods menu in the
   Literature DB and Gabi's current skills.
5. **Feeds the lab's own agenda**, so rungs 4–5 (find the holes, propose an extension) lead
   somewhere real rather than to a dead end.

## 3. Candidates considered

| Paper | Venue | Fit | Verdict |
|---|---|---|---|
| **Freedman (2012), "Teaching new markets old tricks: The effects of subsidized investment on low-income neighborhoods"** | J. Public Economics 96(11) | Uses the *same CDFI Fund NMTC transaction data we hold* + free Census/ACS tract outcomes. Identification is a transparent eligibility cutoff (tract median income ≤ 80% of area median). First-stage result is reproducible with our data alone. | **Recommended** |
| Baum-Snow & Marion (2009), "The effects of LIHTC developments on neighborhoods" | J. Public Economics 93(5–6) | Already flagged "Replication candidate" in the Lit DB ("often flagged as a clean candidate"). Data (HUD LIHTC database, decennial census) is public but we don't hold it yet; LIHTC rather than NMTC. | Strong backup / second replication |
| Busso, Gregory & Kline (2013), "Assessing the incidence and efficiency of a prominent place-based policy" | AER 103(2) | Flagged in Lit DB, but core results need restricted Census microdata; methods (propensity reweighting) beyond the menu. | Too heavy for rung 3; keep as a "read deeply" paper |
| Harger & Ross (2016), "Do capital tax incentives attract new businesses?" | J. Regional Science | NMTC eligibility cutoff design, but outcomes are proprietary Dun & Bradstreet files. | Ruled out (data) |
| Gurley-Calvez et al. (2009) | JPAM | NMTC, but built on confidential IRS SOI microdata. | Ruled out (data) |

## 4. Recommendation: Freedman (2012), one result

**The target (worksheet step 0).** Not the full RD. The one result to reproduce is the
**first stage**: census tracts just below the 80%-of-area-median-income eligibility threshold
receive discontinuously more NMTC investment than tracts just above it. This is the paper's
engine, and it can be rebuilt almost entirely from the lab's own documented dataset.

**Why this one is right for Gabi:**

- **Data we have = the treatment variable.** QLICI dollars by tract-year come straight from the
  CDFI Fund file (`ds_nmtc`). The only additions are free: Census 2000 tract median family income
  and area (MSA/state) medians to construct the eligibility ratio.
- **The method is one regression.** A local comparison of mean investment on either side of a
  cutoff — OLS with a threshold dummy, then richer RD as stretch goals. Every concept maps to
  tags already in the Methods menu (OLS, Fixed Effects, RDD).
- **It teaches the most transferable lesson** of the worksheet: step 2 (the sample) is where
  replications quietly go wrong — tract vintages (2000 boundaries), eligibility definitions, and
  which program years to include all have to be pinned down. Reconciling a gap against a published
  JPubE number is exactly the skill the ladder is designed to teach.
- **It bridges straight into the lab's own work.** Freedman's eligibility margin is the urban/rural-
  blind version of what the rural mobilization-gap paper decomposes. Rung 4 ("find the holes")
  has a natural answer waiting: *does the discontinuity in mobilized investment look different in
  rural tracts?* — which extends Ian's paper rather than competing with it, and gives Katia's
  Portugal thread a US benchmark. Rung 5 (the extension proposal) is then already in sight.

**Stretch goals, in order:** (a) replicate one reduced-form outcome (e.g., poverty rate or median
home value around the cutoff, ACS vs. his census outcomes); (b) re-run the first stage splitting
by GHS-SMOD/USDA rural definition; (c) write the comparison up on the replication worksheet page.

## 5. What we need to do (sequenced)

1. **Ian:** confirm the Jun 9 data run-through happened; deliver Gabi's NMTC slice (tract GEOID,
   CDE, year, QLICI amount) plus a short data dictionary. Pull or point her to Census 2000 tract
   median income + area medians (one afternoon; add as a Datasets DB row, "Acquiring").
2. **Ian/Liz:** add Freedman (2012) to the Literature DB, flag it `Replication candidate`,
   relate it to a new Projects row (or fold into the existing "Literature mapping & replication
   track"), and duplicate the replication worksheet page for it with step 0 pre-filled (the
   first-stage target).
3. **Gabi:** card Freedman (2012) *first* among her 10 starter papers (it is the one she will
   rebuild), then continue the starter list. Reading it with the worksheet's questions in hand is
   rung 2 of the ladder.
4. **Katia:** send the blended-finance paper list (outstanding from Jun 4); anything on it that
   uses public data becomes the candidate pool for a *second* replication later in the summer.
5. **Liz:** set the check-in cadence (bi-weekly, mirroring Katia's), with the worksheet's
   "stuck >30 min → Help & Review board" rule made explicit at the first check-in.
6. **Backup path:** if the NMTC slice is delayed, Baum-Snow & Marion (2009) with the public HUD
   LIHTC database is the fallback — already flagged in the Lit DB — so Gabi is never blocked on us.

## Sources

- Lab Notion workspace: The Lab — HQ; "Gabi · First project"; "Replicating a paper: a worksheet";
  Literature DB; Datasets DB; project pages for "NMTC: The Rural Mobilization Gap" and
  "Literature mapping & replication track".
- Zoom meeting summaries (Apr–Jun 2026), esp. Jun 4 (Katia: lab plans, Gabi intro, paper list) and
  May 18 (Katia: NMTC regression analysis).
- Freedman, M. (2012). [Teaching new markets old tricks: The effects of subsidized investment on
  low-income neighborhoods](https://www.sciencedirect.com/science/article/abs/pii/S0047272712000886).
  *Journal of Public Economics*, 96(11), 1000–1014. ([SSRN](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2180227))
- Harger, K., & Ross, A. (2016). [Do capital tax incentives attract new businesses? Evidence across
  industries from the New Markets Tax Credit](https://ideas.repec.org/p/wvu/wpaper/14-14.html).
  *Journal of Regional Science*.
- [CDFI Fund, New Markets Tax Credit program](https://www.cdfifund.gov/programs-training/programs/new-markets-tax-credit).
