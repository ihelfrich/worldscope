# Legal & financial filings index — Dialog network

*Public records only: SEC filings, court dockets/opinions, DOJ/AG releases,
bankruptcy filings, OGE disclosures, and nonprofit Form 990s. Research date
2026-06-28.*

> **Neutral records index.** Being named in a filing is **not** an implication of
> guilt or wrongdoing unless a court so found. Investigations, disclosures, and
> ordinary insider-transaction reports are routine and lawful. Aggregator-derived
> figures (not read off the primary EDGAR document) are marked **(unverified)**.
>
> **On tax records:** individual income-tax returns are confidential under
> **26 U.S.C. §6103** and are *not* sought here. The only tax data below is the
> **public** layer — nonprofit **Form 990s** (public by statute) and tax-related
> *litigation* (e.g., the Goldstein case).

Raw per-entity pulls (EDGAR / CourtListener / Federal Register) for ~170 entities
live under `entities/<slug>/raw/`; foundation 990s under `foundations/<slug>/raw/`.
This file is the analyst synthesis of the notable, verified items.

---

## 1. Investment & ownership records (SEC)

### Insider transactions / beneficial ownership (Form 4 · Schedule 13D/G)

**Peter Thiel** — reporting owner CIK **0001211060**; subject **Palantir (PLTR)**
CIK 0001321655. Shares held indirectly via Rivendell 7 LLC, PLTR Holdings LLC, STS
Holdings II LLC, Rivendell 25 LLC.
- Form 4 **2026-03-04** — sold **2,000,000** Class A (~$144.85; ≈$289.7M) under a Nov-2025 10b5-1 plan (`0001211060-26-000007`).
- Form 4 **2024-10-01** — sold **12,412,322** (~$36.85; ≈$457.4M) (`0001321655-24-000183`).
- Form 4 **2024-09-26** — sold **16,178,415** (~$36.90; ≈$597.0M) (`0001321655-24-000181`).
- Form 4 **2024-05-10** — sold **12,955,244** (~$21.11; ≈$273.5M, 10b5-1) (`0001415889-24-012934`).
- Form 4 **2024-03-12** — sold **7,044,756** (~$24.79; ≈$174.6M) (`0001415889-24-007747`).
- **Founders Fund** group Form 4s (as 10% owner) on portfolio cos incl. **Affirm** (`0001209191-21-004575`, 2021-01-20) and **Airbnb** (`0001209191-20-063866`, 2020-12-16). A dedicated Founders Fund / Mithril / Clarium **13F or 13D** under Thiel's name was **not located (unverified)**.

**Elon Musk** — reporting owner CIK **0001494730**; subject **Tesla (TSLA)** CIK
0001318605. 117 Form 4s total on file; clusters in 2022 (option exercises/sales) and
a large **2024-12-31** option exercise (~303.96M shares @ $23.34, shares withheld for
tax; `0000950170-24-141705`); recent Form 4s through 2025-12-31 (`0001104659-25-125703`).
- **Twitter (TWTR)** CIK 0001418091: **SC 13G 2022-04-04** (`0001104659-22-041911`) disclosing a **9.2% stake** (73,486,938 shares via the Musk Revocable Trust); converted to **SC 13D 2022-04-05** (`0001104659-22-042863`); ~16 13D/A amendments through 2022. *(The SEC later sued Musk alleging the stake was disclosed ~11 days late — see §2.)*

**Marc Andreessen** — reporting owner CIK **0001160077**.
- **Coinbase (COIN)** director Form 4s — **2026-06-18** (`0001679788-26-000067`); **2025-06-23** RSU vesting; reported ~**1,150,028** COIN shares (2024-03-26).
- **Meta (META)** director since 2008 — 2026 RSU-vesting Form 4s (`0000950103-26-007470`, `-009172`); shares held via the LAMA Community Trust (~49,253 Class A indirect).
- a16z funds run by **AH Capital Management** (adviser firm #160489); a consolidated **13F-HR** with top-holdings value **not confirmed (unverified)** — venture structure limits reportable public holdings.

**Strauss Zelnick** — reporting owner CIK **0001223489**; files Form 4s for **two**
issuers: **Take-Two (TTWO)** (Chairman/CEO) and **Starwood Property Trust (STWD)**
(director). Recent: TTWO Form 4 **2025-06-03** (May-30 open-market sales of
50,935 / 157,749 / 7,977 sh @ ~$225, a 372,577-sh award, 135,985-sh gifts; ending
direct holding 1,279,802). **ZMC** has no separate Section 16 / 13D filings — its
TTWO reporting flows through Zelnick's personal CIK.

**Reid Hoffman** — reporting owner CIK **0001519339**; **Microsoft (MSFT)** director
Form 4s 2018–2026 (e.g., 2026-02-03, 15,905 sh via living trust) plus a 2013 **Zynga**
Form 4. No separate LinkedIn-era or Greylock 13D/G surfaced *(unverified)*.

**Adam D'Angelo** — reporting owner CIK **0001823077**; **all** Section 16 filings
are for **Asana (ASAN)** (director; 29 filings), **not Meta** — no Meta Form 4s exist
for him (he is a non-officer Meta director). *(Corrects an earlier premise.)*

**Greg Brockman** — **no personal SEC filings** (OpenAI/Stripe are private); his name
appears only inside third-party documents (e.g., the **Cerebras** S-1, as a reported
investor), never as a Section 16 filer.

**Henry Kravis / KKR** — KKR's 13F filer is *Kohlberg Kravis Roberts & Co. L.P.*
(CIK **0001399770**), distinct from public-co KKR & Co. Inc. (CIK 0001404912).
Q1 2026 13F-HR (filed 2026-05-15): **$5.35B, 96 positions** (top: BrightSpring
$1.78B, Henry Schein $1.15B, BridgeBio $985M). **Kravis** files Form 4s personally
(CIK **0001081714**; 360 lifetime) — latest 2026-06-05 sold **14,669,771
BrightSpring shares @ $58.45**.

**Joe Lonsdale / 8VC** — 8VC *does* file a 13F (8VC GP I LLC, CIK **0001667766**):
Q1 2026 = **$39.6M, a single position** (Joby Aviation, JOBY). Lonsdale (CIK
0001832823) filed **SC 13G/A** on **Senti Biosciences** (~1.0%, joint with 8VC
funds). No Palantir 13D/G under his CIK.

**Barry Silbert / DCG** — DCG files **no** 13F/13D/13G under its own name. Silbert
(CIK **0001976415**) files **Form 144** (proposed restricted-stock sales) — **119+**
on file, all on Grayscale crypto trusts (GTAO/FILG/GSOL/GDLC…), latest 2026-05-07.

**Jim Breyer / Breyer Capital** (CIK **0001537061**) — files 13G, not 13F. **Circle
(CRCL)** post-IPO: **SC 13G 2025-08-13 = ~6.3% (13,331,954 sh)**, reduced to **~3.0%**
(13G/A 2026-02-17). Reporting persons: James Breyer, Breyer Capital, the Breyer 2005
Trust.

**Micky Malka / Ribbit** — Ribbit Management Co. (CIK **0001836733**) Q1 2026 13F =
**$1.68B, 16 positions**: Nu Holdings $424M, Figure $382M, **Robinhood $225M**,
**Coinbase $129M**, Block $86M. **Robinhood (HOOD) SC 13G** via affiliated *Bullfrog
Capital* (Malka a reporting person): 13G/A 2024-11-14 = **14,284,835 sh, 1.9%**.

### Institutional holdings (Form 13F-HR)

| Manager (roster figure) | CIK | Latest 13F | Portfolio value | Holdings | Notable |
|---|---|---|---|---|---|
| **Millennium Management** (Bob Jain, co-CIO) | 0001273087 | Q1 2026 | ~$240.3B *(unverified)* | ~5,622 | ETF/index-option heavy |
| **Renaissance Technologies** (Peter Brown, CEO) | 0001037389 | Q4 2025 | ~$64.5B *(unverified)* | ~3,185 | **top holding: Palantir (PLTR ~2.4%)** |
| **Bridgewater Associates** (Karen Karniol-Tambour, co-CIO) | 0001350694 | Q1 2026 | ~$22.4B *(unverified)* | ~993 | SPY/IVV/AMZN/NVDA |
| **Key Square** (Scott Bessent, pre-Treasury) | 0001662970 | Q4 2024 | ~$0 (wound down) | 1 | book unwound into 2025 Treasury role |

**Social Capital (Chamath Palihapitiya)** — no recurring 13F; public footprint is the
**SPAC S-1/424B** series: IPOA→**Virgin Galactic** (CIK 1706946), IPOB→**Opendoor**
(1801169), IPOC→**Clover Health** (1801170), IPOE→**SoFi** (1818874), IPOD/IPOF
liquidated; Suvretta I–IV → ProKidney, Akili.

---

## 2. Litigation & regulatory filings

### Convictions / verdicts
- **Tom Goldstein** — *United States v. Goldstein*, D. Md. **8:25-cr-00006-LKG**. Indicted Jan 16, 2025; **CONVICTED at jury trial Feb 26, 2026 (12 of 16 counts)** — tax evasion, false returns, false statements to mortgage lenders. Sentencing TBD. ([DOJ](https://www.justice.gov/usao-md/pr/prominent-lawyer-thomas-goldstein-convicted-tax-evasion-and-mortgage-fraud) · [CourtListener](https://www.courtlistener.com/docket/69552592/united-states-v-goldstein/))
- **Musk v. Altman / OpenAI** — N.D. Cal. **4:24-cv-04722** (Gonzalez Rogers). Filed Feb 2024 (state) → refiled federal Aug 2024; **defense verdict May 18, 2026** (jury for OpenAI/Altman; statute of limitations). Musk vowed to appeal *(status unverified)*. ([NPR](https://www.npr.org/2026/05/18/nx-s1-5822366/musk-altman-openai-jury-verdict-claims-dismissed))

### Active / settled civil & regulatory
- **Barry Silbert / DCG / Genesis** — three matters: (1) **NY AG v. Gemini/Genesis/DCG/Moro/Silbert**, NY Sup. Ct. Index **452784/2023** — Genesis **settled up to $2B**; **proceeding vs. DCG & Silbert** (MTD largely denied Apr 9, 2025). (2) **In re Genesis Global Holdco**, SDNY Bankr. **23-10063 (SHL)** — Chapter 11, plan confirmed. (3) **McGreevy v. DCG**, D. Conn. **3:23-cv-00082** — securities class action, **MTD denied Feb 24, 2026** (proceeding).
- **Mike Novogratz / Galaxy Digital** — NY AG **Assurance of Discontinuance** (Terra/LUNA), Mar 2025 — **$200M** over ~3 years, no admission.
- **Leonard Leo / Marble Freedom Trust** — **DC Attorney General civil investigation** (reported Aug 2023, ongoing) into Leo-affiliated nonprofits. **No charges or findings** — an open inquiry, not an adjudication.

### Government-contract protests
- **Palantir USG v. United States** — COFC **1:16-cv-00784C**; **Fed. Cir. affirmed injunction Sept 13, 2018 (904 F.3d 980)** — Army violated the commercial-items preference (10 U.S.C. §2377). **Palantir won.** Later GAO **B-423684.1** (DIA sole-source) dismissed Jul 30, 2025.
- **Anduril** — GAO **B-419420** (ABMS), **denied** Feb 22, 2021.
- **OpenAI** — filed no protest itself; **Ask Sage v. GSA** (GAO **B-423827**) challenged the $1 OneGov AI deals routed to OpenAI/Anthropic — **dismissed** ~Dec 2025 on jurisdiction.

### Ethics disclosures (OGE Form 278e) — routine records, not allegations
- **Scott Bessent** — ethics agreement Jan 2025; **OGE non-compliance letter Aug 11, 2025** (failed to timely complete certain divestitures; pledged completion by Dec 15, 2025).
- **Jim O'Neill** (HHS) — 278e signed Mar 13, 2025, OGE-certified Mar 31, 2025.
- **Will Scharf** (WH Staff Sec.) — 278e signed Apr 6, 2025, certified Jun 12, 2025.
- **Jared Kushner** — CY2018/CY2019 annual 278e + 2017 ethics agreement; no verified 2025 re-filing.

### Congressional document release — records event only
- **Lawrence Summers** — named in the **House Oversight** Epstein-estate document release (**Nov 12, 2025**, 20,000+ pages). Reported here strictly as a records event; content/allegations not characterized. Summers stepped back from public roles (incl. the **OpenAI board**) thereafter.

---

## 3. Public tax layer — nonprofit Form 990s

*Foundations and nonprofits in the network file public Form 990s (revenue, assets,
officers). Pulled via ProPublica Nonprofit Explorer into `foundations/<slug>/raw/`.
Table generated below once the harvest completes.*

| Organization | EIN | Latest FY | Revenue | Expenses | Assets (EOY) | 990s |
|---|---|---|---|---|---|---|
| Americans For Tax Reform | 521403587 | 2023 | $7,834,355 | $4,785,028 | $37,618,709 | 5 |
| Anti Defamation League | 131818723 | 2023 | $38,294,911 | $57,939,187 | $67,064,785 | 5 |
| atlantic-council | ? | ? | n/a | n/a | n/a | 1 |
| Berggruen Institute | 465602320 | 2023 | $23,706,328 | $18,826,624 | $10,516,992 | 5 |
| Cato Institute | 237432162 | 2024 | $71,927,807 | $41,834,120 | $172,218,520 | 5 |
| Charles Koch Foundation | 480918408 | 2023 | $1,315,532 | $83,011,998 | $748,223,343 | 5 |
| Council On Foreign Relations Inc | 131628168 | 2023 | $106,930,600 | $83,166,400 | $719,707,600 | 5 |
| federalist-society | ? | ? | n/a | n/a | n/a | 1 |
| Heterodox Academy | 822903153 | 2023 | $2,980,573 | $2,395,669 | $2,673,674 | 5 |
| Hudson Institute Inc | 131945157 | 2023 | $24,783,916 | $24,891,108 | $117,371,037 | 5 |
| Human Rights Foundation Inc | 202669700 | 2023 | $17,478,062 | $25,327,045 | $58,286,837 | 5 |
| Knight Foundation | 911791788 | 2023 | $1,730,391,596 | $244,066,344 | $4,230,106,111 | 5 |
| Latino Community Foundation | 810564400 | 2023 | $24,197,968 | $12,554,062 | $54,554,268 | 5 |
| Lifebox Foundation Inc | 462266526 | 2024 | $3,737,135 | $3,248,479 | $2,342,079 | 5 |
| marble-freedom-trust | ? | ? | n/a | n/a | n/a | 1 |
| Math For America Inc | 200651886 | 2023 | $24,004,679 | $23,566,565 | $31,310,984 | 5 |
| New America Foundation | 522096845 | 2023 | $46,779,045 | $40,824,022 | $88,026,169 | 5 |
| Renew Democracy Initiative Inc | 822547275 | 2023 | $5,607,953 | $7,276,370 | $2,825,452 | 5 |
| Robin Hood Foundation | 133441066 | 2023 | $134,455,897 | $150,625,376 | $308,801,119 | 5 |
| Saisei Foundation | 832599773 | 2023 | $3,887,002 | $2,434,486 | $3,493,360 | 5 |
| Simons Foundation Inc | 133794889 | 2023 | $124,058,660 | $565,004,000 | $4,587,102,476 | 5 |
| Stand Together Foundation | 273197768 | 2023 | $12,881,613 | $62,388,783 | $312,915,750 | 5 |
| The Thiel Foundation | 203846597 | 2023 | $8,240,688 | $3,316,210 | $44,447,969 | 5 |

*23 foundations with machine-readable 990 data. Source: ProPublica Nonprofit Explorer (public Form 990 filings). Full per-org filing history in `foundations/<slug>/raw/form990.json`.*

---

## Sources & method
Per-entity raw pulls under `entities/` and `foundations/` (SEC EDGAR full-text,
CourtListener, Federal Register, OpenSanctions, ProPublica 990). Form 4/13D
accession numbers verified against `data.sec.gov/submissions/` and EDGAR; some
13F portfolio totals are aggregator-derived (13f.info / WhaleWisdom) and tagged
**(unverified)** pending a primary-document read. Litigation items cite the court
docket, DOJ/AG release, or GAO decision number.
