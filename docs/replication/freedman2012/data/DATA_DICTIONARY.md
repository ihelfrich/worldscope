# Data dictionary — Freedman (2012) first-stage replication

Three files, all committed to the repo. Everything here is built from public sources;
the build was done in June 2026 and the numbers below are what you should see when you
load each file. If you see something else, stop and figure out why before going on.

## eligibility_2000.csv

One row per census-2000 tract: 65,443 rows. This is the *universe* — every tract,
whether or not it ever saw a dollar of NMTC money — and it carries the running
variable for the RD.

| column | meaning |
|---|---|
| `tract_geoid` | 11-digit 2000-census tract id (state 2 + county 3 + tract 6). Text, zero-padded. |
| `tract_mfi` | Tract median family income, 1999 dollars (Census 2000 SF3, table P077). Blank for a handful of unpopulated tracts. |
| `poverty_rate` | Tract poverty rate (SF3 P087: below-poverty / universe). Proportion, not percent. |
| `metro` | 1 if the tract's county belonged to a metropolitan area under the June 1999 OMB definitions, else 0. |
| `metro_id` | Metro area code. `M` + PMSA code where a PMSA exists, otherwise `M` + MSA code. New England uses county-based NECMAs, prefixed `N`. Blank for non-metro. |
| `area_mfi` | The statutory "area" median: for metro tracts, the larger of the state median and the metro median; for non-metro tracts, the state median. |
| `mfi_ratio` | `tract_mfi / area_mfi`. **This is the running variable.** A tract is income-eligible when it is at or below 0.80. |

Checks: 39.0% of tracts are NMTC-eligible by income or poverty (the CDFI Fund's own
figure for the 2000-census era is "about 39%"); 74.7% of tracts are metro.

How it was built, in one paragraph: tract MFI and poverty come straight from the
Census 2000 SF3 API. State medians are the published SF3 numbers. Metro medians are
not published at the right geography by the API, so they are computed from county-level
family-income distributions (SF3 P076, 16 bins) aggregated to 1999 metro areas and
interpolated within the median bin — the same method the Census Bureau uses to compute
published medians. Validated against the 52 published state medians: mean absolute
error 0.38%, worst case 1.08%. County-to-metro assignment is the Census `99mfips.txt`
delineation (PMSA over MSA where both exist) and `99nfips.txt` NECMAs for New England.

## nmtc_transactions_fy2003_2017.csv

The CDFI Fund's FY2017 public data release, "Financial Notes" sheet, lightly renamed:
13,880 QLICI transactions, fiscal years 2003–2017. One row = one loan or investment.
This is the file for the cleaning exercise — it has everything a real administrative
file has, including a few surprises (look at the year column closely).

| column | meaning |
|---|---|
| `project_id`, `transaction_id` | CDFI Fund identifiers. Several transactions can share a project. |
| `tract_geoid_2010` | 11-digit tract id — **2010 census boundaries**, not 2000. This matters; see below. |
| `metro_2010` | CDFI's metro flag, 2010-census basis. |
| `year` | Origination year. |
| `cde_name` | The community development entity making the investment. |
| `qlici_amount` | Dollars. Not deflated. |
| remaining columns | Location and deal descriptors as released. |

Source: cdfifund.gov, `2019-nmtc-public-data-release_fy_17.xlsx`,
sha256 `dec4e55b…16ae`.

## nmtc_tract_qlici_2003_2007.csv

The worked aggregate: 2003–2007 transactions summed by tract, with each 2010 tract
mapped back to 2000 boundaries. 1,255 rows. You should be able to rebuild this file
yourself from the transactions file plus the mapping columns here — that's one of the
exercises.

| column | meaning |
|---|---|
| `tract_geoid_2010` | as above |
| `tract_geoid_2000` | the 2000-census tract that contributed the largest share of this 2010 tract's population (Census 2010 tract relationship file, `POPPCT10`) |
| `poppct10` | that share, in percent. 100 = the tract didn't change between censuses. Low values mean the mapping is genuinely uncertain — worth flagging in your write-up. |
| `n_deals` | transactions, FY2003–2007 |
| `total_qlici` | dollars, FY2003–2007 |

## The vintage problem, because you will hit it again

Every CDFI public release re-reports *all* historical deals on whatever census
geography is current: the FY2022 release uses 2020 tracts, FY2017 uses 2010 tracts.
None of the surviving public files report 2000 tracts, the boundaries the eligibility
rule actually used in 2003–2007 — the early tract-level releases that did are gone
from both cdfifund.gov and the Internet Archive. So deals here travel from the FY2017
release back to 2000 boundaries through the population crosswalk. Most tracts map
cleanly; the ones that don't add noise to where treatment "is," which works against
finding the jump. Freedman had the confidential transaction file on native 2000
tracts. Keep both facts in your reconciliation section.
