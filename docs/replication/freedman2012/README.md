# Freedman (2012) first-stage replication — Gabi's project folder

This folder is rungs 2–3 of the 5-step plan (see
[`docs/gabi_replication_project.md`](../../gabi_replication_project.md) and the
Notion worksheet "Replicating a paper"). Everything needed to do the replication is
committed here: papers, data, and the notebook. Open
**`freedman_first_stage.Rmd`** in RStudio and work top to bottom.

## What's in the folder

```
freedman_first_stage.Rmd     the notebook — start here
papers/                      both papers, PDF
data/                        three committed datasets + DATA_DICTIONARY.md
data/raw/                    (git-ignored) your scratch space for downloads
```

## The papers

Verified byte-for-byte against their sources (SHA-256: Freedman `564c9d58…f7d6`,
Baum-Snow & Marion `9bc256d6…acb8`):

- `papers/freedman_2012_teaching_new_markets_old_tricks_wp.pdf` — working-paper
  version, Cornell eCommons ([handle 1813/89085](https://ecommons.cornell.edu/handle/1813/89085)).
  Published version: [*J. Public Economics* 96(11–12)](https://www.sciencedirect.com/science/article/abs/pii/S0047272712000886)
  (paywalled; that's the one to cite, and the one whose first-stage table the
  Part 4 comparison should use). [SSRN copy](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2180227)
  if you want a second ungated source.
- `papers/baum_snow_marion_2009_lihtc_wp.pdf` — the backup paper, Brown WP 2007-5
  ([direct PDF](https://economics.brown.edu/sites/default/files/papers/2007-5_paper.pdf);
  [near-final Novoco copy](https://www.novoco.com/public-media/documents/baumsnow-marion_lihtc-dec08.pdf);
  [published version](https://www.sciencedirect.com/science/article/abs/pii/S0047272709000024)).

## The data

Three files in `data/`, all built from public sources and documented in
[`data/DATA_DICTIONARY.md`](data/DATA_DICTIONARY.md) — read that file first:

| file | what it is |
|---|---|
| `eligibility_2000.csv` | every census-2000 tract (65,443) with the NMTC running variable: tract MFI / area MFI, plus poverty rate and metro status. Built from Census 2000 SF3 and the 1999 OMB metro definitions. |
| `nmtc_transactions_fy2003_2017.csv` | the CDFI Fund's FY2017 public release, 13,880 transactions. The cleaning exercise. |
| `nmtc_tract_qlici_2003_2007.csv` | 2003–2007 deals aggregated by tract and mapped from 2010 to 2000 census boundaries. The merge exercise checks against this. |

One warning that applies beyond this project: every CDFI public release re-reports
historical deals on the *current* census geography. The file in the lab Drive
(FY2022 release) is on 2020 tracts and will not merge with year-2000 income data.
The dictionary explains the workaround used here.

The notebook's checkpoint numbers (N = 15,582 in the window; a +1.7 point jump in
the probability of any deal at the cutoff, t ≈ 4.5) were produced from these exact
files, so a clean run reproduces them to the digit.

## House rules

- Stuck more than 30 minutes, ask in the pod channel. Sitting on a bug in silence
  is the only way to fail here.
- Don't skip the fake-data rehearsal in Part 2; it's the template for everything after.
- Write down every choice the paper doesn't pin down. That list *is* the
  reconciliation section of the worksheet.
