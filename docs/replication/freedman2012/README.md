# Freedman (2012) first-stage replication — Gabi's project folder

This folder is the working home for rungs 2–3 of the 5-step plan (see
[`docs/gabi_replication_project.md`](../../gabi_replication_project.md) and the Notion
worksheet "Replicating a paper"). The spine of the project is the R notebook:

- **`freedman_first_stage.Rmd`** — open it in RStudio and work top to bottom.

## The papers

Both PDFs are committed in [`papers/`](papers/), verified byte-for-byte against their
sources (SHA-256: Freedman `564c9d58…f7d6`, Baum-Snow & Marion `9bc256d6…acb8`):

- `papers/freedman_2012_teaching_new_markets_old_tricks_wp.pdf` — the working-paper
  version, from Cornell eCommons (45 pp.)
- `papers/baum_snow_marion_2009_lihtc_wp.pdf` — the working-paper version, from
  Brown Economics (46 pp.)

These are the author-posted, ungated working-paper versions — fine for reading and
carding. **Cite the published versions** (links below), and pull the comparison
numbers for Part 4 of the notebook from the published first-stage table if you have
library access, since tables can shift between working paper and journal.

**Main paper — the one being replicated:**

- Freedman, Matthew (2012). "Teaching new markets old tricks: The effects of subsidized
  investment on low-income neighborhoods." *Journal of Public Economics* 96(11–12): 1000–1014.
  - Ungated working-paper PDF: Cornell eCommons, [handle 1813/89085](https://ecommons.cornell.edu/handle/1813/89085)
    (file `Freedman_NMTC_Web.pdf` — click Download on that page).
  - [SSRN page](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2180227) (alternate ungated copy).
  - [Published version](https://www.sciencedirect.com/science/article/abs/pii/S0047272712000886)
    (paywalled — use a library proxy; the published version is the one to cite and to
    pull the comparison numbers from, the working paper is fine for reading).

**Backup / second replication candidate:**

- Baum-Snow, Nathaniel and Justin Marion (2009). "The effects of low income housing tax
  credit developments on neighborhoods." *Journal of Public Economics* 93(5–6): 654–666.
  - Direct working-paper PDF: [Brown Economics WP 2007-5](https://economics.brown.edu/sites/default/files/papers/2007-5_paper.pdf)
  - Direct near-final PDF: [Novoco mirror (Dec 2008)](https://www.novoco.com/public-media/documents/baumsnow-marion_lihtc-dec08.pdf)
  - [Published version](https://www.sciencedirect.com/science/article/abs/pii/S0047272709000024) (paywalled).

## The data

Nothing in `data/` is committed to git. The notebook explains each file when it's needed:

| File (put in `data/raw/`) | What it is | Where it comes from |
|---|---|---|
| `nmtc_slice.csv` | The lab's cut of the CDFI Fund NMTC transaction file (tract GEOID, CDE, year, QLICI amount, total project cost) | Ian drops it in the Portugal pod's `Data/` folder in Drive (per the memo). |
| CDFI Fund public release (fallback) | The full public NMTC transaction spreadsheet, FY 2003–2022 | [CDFI Fund data releases](https://www.cdfifund.gov/documents/data-releases) |
| 2000-census tract eligibility file | All ~65k census-2000 tracts with median-family-income ratio, poverty rate, and NMTC eligibility flags — the *running variable* | CDFI Fund eligibility data (same page as above) or [Novoco's NMTC mapping tools](https://www.novoco.com/resource-centers/new-markets-tax-credits) |
| Census 2000 SF3 tract income (stretch) | Build the running variable yourself: median family income 1999 (table P077) | Census API / `tidycensus` — covered in the notebook's stretch section |

## House rules (from the lab handbook)

- Stuck more than 30 minutes → ask in the pod channel. Being stuck silently is the only way to fail.
- Work top to bottom in the notebook; don't skip the synthetic warm-up — it's the template
  for everything after it.
- Write down every choice you make that the paper doesn't pin down. That list *is* the
  reconciliation section of the worksheet.
