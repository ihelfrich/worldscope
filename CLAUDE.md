# WORLDSCOPE — project rules for contributors and agents

This file is read by every Claude session that touches the repo. Follow these
invariants; they exist because they were violated and the brief got it wrong.

## STANDING RULE — Fact-check every number before publication

> Every number, every fact, every figure must be checked against the
> lake's structured source data before being published in the daily report.

The narrative layer of the brief (`briefings/<date>.md`, composed by an
external desk-officer Claude session) has hallucinated specific numbers
even when the truth was in the items handed to it (real example: claimed
Bitcoin at $104,000 when the lake had $74,816 from CoinGecko spot).

Enforcement:

  - `worldscope/fact_check.py` validates every asset-price claim in
    `briefings/*.md` against same-day records in `lake/db/worldscope.sqlite`.
    Per-asset tolerances: crypto 5%, gold 3%, equity indices 2%,
    commodities 5%, FX skipped (multiple conventions). Forecast-context
    phrases ("$150k by June 30", "Polymarket contract on …") are
    skipped because they're titles, not assertions.

  - `.github/workflows/render-briefings.yml` runs the fact-checker
    BEFORE the markdown-to-HTML renderer:
      1. `--annotate` rewrites the `.md` in place with
         `[⚠ actual ≈ $X.XX]` markers inline after each diverged claim,
         so the warning is visible to readers even if the build is
         later overridden.
      2. `--fail-on FAIL` exits non-zero on any divergence. Build
         fails. HTML is not rendered. The brief does not ship until
         the desk-officer reconciles the diverged figures.

  - Run manually: `python -m worldscope.fact_check briefings/<date>.md`

If the fact-checker doesn't cover a number type yet (text dates, statute
citations, population counts), the rule still applies — extend the
validator or surface the unverified claim. Do not silently ship.

## Section state machine — when a card says "no items"

Each section resolves to one of five states (`worldscope/sections/__init__.py`):

  | State                  | Meaning |
  |------------------------|---------|
  | `fresh`                | pull succeeded today, has items |
  | `fresh_empty`          | pull succeeded today, returned zero items |
  | `carry_forward`        | `WORLDSCOPE_SKIP` set, prior snapshot reused |
  | `stale_after_failure`  | pull threw, prior snapshot carried with stale marker |
  | `no_data`              | pull failed AND no prior snapshot to fall back on |

`fresh_empty` means the upstream API answered cleanly with no data —
this is normal for sections like `acled` (only fires when there are
ACLED events in our watch areas), `firms` (only when geofences see
fires), `macro` (FRED releases are sparse and weekday-only). It does
NOT mean the pull failed.

`WORLDSCOPE_SKIP: sanctions,people` is intentional in CI because those
sections need a 2.6 GB OpenSanctions corpus only available on the
maintainer's machine. They carry forward from local pushes.

## Other rules

  - Never invent figures. If you cannot ground a sentence in the
    provided items, omit the sentence. (Also enforced by `synth.py`
    system prompt for per-section synthesis.)

  - Content filter (`worldscope/lib/content_filter.py`) is on by
    default for every section. Sections that legitimately surface
    OnlyFans / crypto-scam terms (e.g. an OFAC action against an
    adult platform) opt out via `FILTER_ADULT_SCAM = False` on the
    subclass — do not disable globally.

  - The MCP server (`mcp-server/worldscope_mcp.py`) is read-only by
    design. If a write tool is ever needed, it goes in a separate
    explicitly-authorized server, not folded into this one.

  - Render layer (`worldscope/render.py`, `worldscope/lib/page_chrome.py`)
    is the only place that produces the homepage. `site_builder.py`
    is for per-section drill-down pages. `tools/render_brief.py` is
    for the desk-officer-composed `briefings/*.md`. These three must
    not diverge in chrome — share `page_shell()` from `lib/page_chrome`.
