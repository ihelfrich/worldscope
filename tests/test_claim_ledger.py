"""Contract tests for the typed claim ledger."""
from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

from worldscope.fact_check import build_claim_ledger, extract_claims


BRIEF_DAY = date(2026, 5, 28)


def _make_lake(path: Path) -> None:
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
    day_iso = BRIEF_DAY.isoformat()
    today_at = f"{day_iso}T03:00:00Z"
    rows = [
        (
            "m-btc", "coingecko", "markets_global",
            "[crypto] BTC: $74,816.50 (24h: -1.49%)",
            "https://coingecko.com/coins/bitcoin",
            day_iso, today_at, "public",
            json.dumps({
                "symbol": "BTC",
                "name": "bitcoin",
                "asset_class": "crypto",
                "close": 74816.50,
                "chg24": -1.49,
            }),
        ),
        (
            "macro-dgs10", "fred", "macro",
            "[Rates] 10-Year Treasury (DGS10) -- latest: 4.32 as of 2026-05-28",
            "https://fred.stlouisfed.org/series/DGS10",
            day_iso, today_at, "public",
            json.dumps({"series": "DGS10", "value": 4.32}),
        ),
    ]
    conn.executemany("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


class ClaimLedgerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lake = self.root / "worldscope.sqlite"
        _make_lake(self.lake)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ledger_for(self, text: str) -> dict:
        md = self.root / "2026-05-28.md"
        md.write_text(text, encoding="utf-8")
        ledger, _verdicts, _report = build_claim_ledger(md, self.lake, day=BRIEF_DAY)
        return ledger

    def _claim(self, ledger: dict, claim_type: str) -> dict:
        matches = [c for c in ledger["claims"] if c["claim_type"] == claim_type]
        self.assertTrue(matches, f"missing {claim_type} claim in {ledger['claims']}")
        return matches[0]

    def test_asset_price_verified_and_divergent(self) -> None:
        verified = self._claim(self._ledger_for("Bitcoin at $74,800 today."), "asset_price")
        divergent = self._claim(self._ledger_for("Bitcoin at approximately $104,000."), "asset_price")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["evidence_record_ids"], ["m-btc"])
        self.assertEqual(divergent["status"], "divergent")
        self.assertGreater(divergent["divergence_pct"], 0.30)

    def test_percentage_verified_divergent_and_skipped_unknown_subject(self) -> None:
        verified = self._claim(self._ledger_for("Bitcoin 24h change -1.49%."), "percentage")
        divergent = self._claim(self._ledger_for("Bitcoin 24h change 3.00%."), "percentage")
        skipped = self._claim(self._ledger_for("Unemployment rate at 4.1 percent."), "percentage")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["actual_value"], -1.49)
        self.assertEqual(divergent["status"], "divergent")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["skip_reason"], "no_validator_for_subject")

    def test_yield_rate_verified_and_divergent(self) -> None:
        verified = self._claim(self._ledger_for("The 10-year Treasury yield at 4.32%."), "yield_rate")
        divergent = self._claim(self._ledger_for("The 10-year Treasury yield at 5.00%."), "yield_rate")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(verified["evidence_record_ids"], ["macro-dgs10"])
        self.assertEqual(divergent["status"], "divergent")

    def test_population_count_is_surfaced_unverified(self) -> None:
        claim = self._claim(self._ledger_for("The filing listed 126,237 declarations."), "population")
        self.assertEqual(claim["status"], "unverified")
        self.assertEqual(claim["claimed_value"], 126237)

    def test_named_entity_count_is_surfaced_unverified(self) -> None:
        claim = self._claim(self._ledger_for("The alert spans 37 sections."), "named_entity_count")
        self.assertEqual(claim["status"], "unverified")
        self.assertEqual(claim["claimed_value"], 37)

    def test_statute_citation_is_surfaced_unverified(self) -> None:
        claim = self._claim(
            self._ledger_for("The order cites Section 122 of the Trade Act of 1974."),
            "statute_citation",
        )
        self.assertEqual(claim["status"], "unverified")
        self.assertIn("Trade Act", claim["raw_text"])

    def test_calendar_date_verified_and_divergent_when_past_context_future_date(self) -> None:
        verified = self._claim(self._ledger_for("The ministry reported on May 11."), "calendar_date")
        divergent = self._claim(self._ledger_for("The ministry reported on June 30, 2026."), "calendar_date")
        self.assertEqual(verified["status"], "verified")
        self.assertEqual(divergent["status"], "divergent")

    def test_fx_rate_is_skipped_until_convention_is_normalized(self) -> None:
        claim = self._claim(self._ledger_for("EUR/USD at 1.163 in morning trade."), "fx_rate")
        self.assertEqual(claim["status"], "skipped")
        self.assertEqual(claim["skip_reason"], "fx_convention_ambiguous")

    def test_forecast_context_filter_still_skips_contract_strikes(self) -> None:
        text = "The Polymarket contract on Bitcoin hitting $150k by June 30 trades at 1%."
        self.assertEqual(extract_claims(text), [])
        ledger = self._ledger_for(text)
        self.assertTrue(ledger["claims"])
        self.assertTrue(all(c["status"] == "skipped" for c in ledger["claims"]))
        self.assertTrue(any(c["skip_reason"] == "forecast_context" for c in ledger["claims"]))

    def test_claims_json_shape_matches_contract(self) -> None:
        ledger = self._ledger_for("Bitcoin at $74,800 today. The 10-year Treasury yield at 4.32%.")
        self.assertEqual(set(ledger), {"brief_date", "generated_at", "generator_version", "summary", "claims"})
        self.assertEqual(set(ledger["summary"]), {"total", "verified", "divergent", "unverified", "skipped"})
        self.assertIsInstance(ledger["brief_date"], str)
        self.assertIsInstance(ledger["generated_at"], str)
        self.assertIsInstance(ledger["generator_version"], str)
        self.assertIsInstance(ledger["summary"]["total"], int)
        self.assertIsInstance(ledger["claims"], list)
        claim = ledger["claims"][0]
        self.assertEqual(set(claim), {
            "id",
            "brief_date",
            "paragraph_offset",
            "raw_text",
            "claim_type",
            "subject",
            "claimed_value",
            "unit",
            "status",
            "evidence_record_ids",
            "actual_value",
            "tolerance",
            "divergence_pct",
            "validator",
            "skip_reason",
        })
        self.assertIsInstance(claim["id"], str)
        self.assertIsInstance(claim["paragraph_offset"], int)
        self.assertIsInstance(claim["evidence_record_ids"], list)

    def test_stable_claim_id_for_same_markdown(self) -> None:
        first = self._ledger_for("Bitcoin at $74,800 today.")
        second = self._ledger_for("Bitcoin at $74,800 today.")
        self.assertEqual(first["claims"][0]["id"], second["claims"][0]["id"])


if __name__ == "__main__":
    unittest.main()
