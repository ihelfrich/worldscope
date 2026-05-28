"""Tests for worldscope.fact_check — price-claim validator for the
desk-officer-written briefing markdown."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.fact_check import (
    annotate_markdown,
    check_brief,
    extract_claims,
    verify_claims,
)


def _make_lake(path: Path, day_iso: str = "2026-05-28") -> None:
    """Build a minimal lake with markets records carrying spot prices."""
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE records(
            id TEXT PRIMARY KEY,
            source_id TEXT, section_id TEXT,
            original_text TEXT, original_url TEXT,
            record_date TEXT, ingested_at TEXT,
            license TEXT, extra_json TEXT
        );
    """)
    today_at = f"{day_iso}T03:00:00Z"
    items = [
        ("m-btc",  "coingecko", "markets_global",
         "[crypto] BTC: $74,816.50  (24h: -1.49%)", "https://coingecko.com/coins/bitcoin",
         day_iso, today_at, "public",
         json.dumps({"symbol":"BTC","name":"bitcoin","asset_class":"crypto","close":74816.50,"chg24":-1.49})),
        ("m-eth",  "coingecko", "markets_global",
         "[crypto] ETH: $2,580.12 (24h: +0.74%)", "https://coingecko.com/coins/ethereum",
         day_iso, today_at, "public",
         json.dumps({"symbol":"ETH","name":"ethereum","asset_class":"crypto","close":2580.12,"chg24":0.74})),
        ("m-gold", "stooq", "markets_global",
         "[commodity] Gold: 4421.45", "https://stooq.com/q/?s=xauusd",
         day_iso, today_at, "public",
         json.dumps({"symbol":"XAU","name":"Gold","asset_class":"commodity","close":4421.45})),
        ("m-spy",  "finnhub", "markets",
         "[US equities] S&P 500 (SPY): 745.64", "https://finance.yahoo.com/quote/SPY",
         day_iso, today_at, "public",
         json.dumps({"symbol":"SPY","name":"S&P 500","asset_class":"equity_index","close":745.64})),
    ]
    conn.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)", items)
    conn.commit(); conn.close()


class TestExtractClaims(unittest.TestCase):
    def test_extracts_bitcoin_price_claim(self) -> None:
        text = "Bitcoin at approximately $104,000 (flat 24h)."
        claims = extract_claims(text)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].asset_canonical, "bitcoin")
        self.assertEqual(claims[0].claimed_value, 104000.0)

    def test_extracts_gold_price_claim(self) -> None:
        text = "Gold at $2,415/oz."
        claims = extract_claims(text)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].asset_canonical, "gold")
        self.assertEqual(claims[0].claimed_value, 2415.0)

    def test_skips_polymarket_contract_references(self) -> None:
        """Regression: 'Bitcoin $150k by June 30' is a contract title,
        not a spot price assertion — must not be flagged."""
        text = "The Polymarket contract on Bitcoin hitting $150k by June 30 trades at 1%."
        claims = extract_claims(text)
        self.assertEqual(claims, [], "forecast-context phrase incorrectly extracted")

    def test_skips_hit_phrasing(self) -> None:
        text = "Will Bitcoin hit $150k by year-end?"
        claims = extract_claims(text)
        self.assertEqual(claims, [])

    def test_does_not_skip_past_tense_hit(self) -> None:
        """Regression for gemini Pass B finding: 'hit $X' alone matched
        FORECAST_CONTEXT, so past-tense statements like 'Apple hit
        $150 yesterday' were skipped from validation. Now requires an
        explicit future modifier OR a forecast-context cue elsewhere
        in the window."""
        text = "Apple reported earnings and the stock hit $150 yesterday."
        claims = extract_claims(text)
        # Should NOT be empty — this is a past-event price claim worth
        # checking. (No "Apple" in the asset registry yet, so it'd be
        # unverified rather than verified, but extraction must happen.)
        # The claim_type would be "percentage" matching nothing useful,
        # so we just assert the past-tense phrase wasn't treated as a
        # forecast skip.
        text_with_asset = "Bitcoin hit $74,816 yesterday."
        claims2 = extract_claims(text_with_asset)
        self.assertTrue(any(c.subject == "bitcoin" for c in claims2),
                        "past-tense bitcoin price claim was incorrectly "
                        "skipped as forecast context")

    def test_extracts_multiple_claims(self) -> None:
        text = "Bitcoin at $74,816 (down 1.49% 24h). Gold at $4,421. S&P 500 at 745.64."
        claims = extract_claims(text)
        # S&P 500 doesn't have a $ prefix in the example so won't match.
        names = [c.asset_canonical for c in claims]
        self.assertIn("bitcoin", names)
        self.assertIn("gold", names)


class TestVerifyClaims(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.lake = Path(self.tmp.name) / "lake.sqlite"
        _make_lake(self.lake)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pass_when_within_tolerance(self) -> None:
        # 5% tol for crypto; $74,000 vs $74,816 = -1.1%, should PASS
        text = "Bitcoin at $74,000 (down 1.49% 24h)."
        vs = verify_claims(extract_claims(text), self.lake, date(2026, 5, 28))
        self.assertEqual(vs[0].status, "PASS")

    def test_fail_when_way_off(self) -> None:
        text = "Bitcoin at approximately $104,000."
        vs = verify_claims(extract_claims(text), self.lake, date(2026, 5, 28))
        self.assertEqual(vs[0].status, "FAIL")
        self.assertAlmostEqual(vs[0].actual_value, 74816.50)
        self.assertGreater(vs[0].delta_pct, 0.30)

    def test_unverified_when_no_record(self) -> None:
        text = "WTI Crude at approximately $77/barrel."
        vs = verify_claims(extract_claims(text), self.lake, date(2026, 5, 28))
        self.assertEqual(vs[0].status, "UNVERIFIED")
        self.assertIn("no", vs[0].note.lower())

    def test_gold_fail(self) -> None:
        text = "Gold at $2,415/oz today."
        vs = verify_claims(extract_claims(text), self.lake, date(2026, 5, 28))
        self.assertEqual(vs[0].status, "FAIL")
        self.assertAlmostEqual(vs[0].actual_value, 4421.45)


class TestAnnotateMarkdown(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.lake = Path(self.tmp.name) / "lake.sqlite"
        _make_lake(self.lake)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_annotation_inserts_warning_after_fail(self) -> None:
        text = "Bitcoin at approximately $104,000 (flat 24h)."
        vs = verify_claims(extract_claims(text), self.lake, date(2026, 5, 28))
        out = annotate_markdown(text, vs)
        self.assertIn("⚠", out)
        self.assertIn("74,816.50", out)

    def test_annotation_skips_pass_claims(self) -> None:
        text = "Bitcoin at $74,800 today."
        vs = verify_claims(extract_claims(text), self.lake, date(2026, 5, 28))
        out = annotate_markdown(text, vs)
        self.assertNotIn("⚠", out)

    def test_check_brief_e2e(self) -> None:
        md = Path(self.tmp.name) / "2026-05-28.md"
        md.write_text("Bitcoin at approximately $104,000 has surprised. Gold at $4,420 closed flat.")
        verdicts, report = check_brief(md, self.lake)
        # Two claims; BTC FAIL, Gold PASS (within tol)
        statuses = sorted(v.status for v in verdicts)
        self.assertEqual(statuses, ["FAIL", "PASS"])
        self.assertIn("FAIL", report)


if __name__ == "__main__":
    unittest.main()
