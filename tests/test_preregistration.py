"""Pre-registration of decision rules.

Every multiple-testing correction needs to know how many things were tried.
That number cannot be recovered after the fact — if the rule is edited in
place and its record follows it, there is no way to distinguish "one rule that
worked" from "the eighteenth variant of a rule that did not".

So a rule's identity is derived from its content: name, parameters, and a
fingerprint of the code that implements it. Change any of them and you get a
different rule_id, a fresh registration timestamp, and a record that starts
from zero. The old version keeps its own record.

The property that matters is that registration cannot be backdated. Two things
enforce it: the id is content-derived (you cannot re-register the same rule
with an earlier timestamp), and any forecast referencing a rule registered
after the forecast was written is reported as a violation.
"""
from __future__ import annotations

import pytest

from worldscope.lake import Lake


@pytest.fixture
def lake(tmp_path):
    lk = Lake.open(tmp_path / "w.sqlite")
    yield lk
    lk.close()


PARAMS = {"edge_threshold": 0.08, "min_net_edge": 0.02, "max_bets": 8}


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #

def test_registering_returns_a_stable_content_derived_id(lake):
    a = lake.register_rule(name="paper-bet-placement", params=PARAMS)
    b = lake.register_rule(name="paper-bet-placement", params=PARAMS)
    assert a == b, "the same rule content must map to the same id"


def test_parameter_order_does_not_change_identity(lake):
    a = lake.register_rule(name="r", params={"x": 1, "y": 2})
    b = lake.register_rule(name="r", params={"y": 2, "x": 1})
    assert a == b


def test_changing_a_parameter_creates_a_new_rule(lake):
    a = lake.register_rule(name="paper-bet-placement", params=PARAMS)
    b = lake.register_rule(name="paper-bet-placement",
                           params={**PARAMS, "edge_threshold": 0.05})
    assert a != b, "a tuned threshold is a different hypothesis"


def test_changing_the_code_fingerprint_creates_a_new_rule(lake):
    a = lake.register_rule(name="r", params=PARAMS, code_fingerprint="v1")
    b = lake.register_rule(name="r", params=PARAMS, code_fingerprint="v2")
    assert a != b, "same params, different implementation, different rule"


def test_a_different_name_is_a_different_rule(lake):
    a = lake.register_rule(name="alpha", params=PARAMS)
    b = lake.register_rule(name="beta", params=PARAMS)
    assert a != b


# --------------------------------------------------------------------------- #
# Registration cannot be backdated
# --------------------------------------------------------------------------- #

def test_re_registering_preserves_the_original_timestamp(lake):
    rid = lake.register_rule(name="r", params=PARAMS)
    first = lake.get_rule(rid)["registered_at"]
    lake.register_rule(name="r", params=PARAMS)
    assert lake.get_rule(rid)["registered_at"] == first, \
        "re-registration must not move the clock"


def test_registration_records_the_run_that_did_it(lake):
    from worldscope.lake import process_run_id
    rid = lake.register_rule(name="r", params=PARAMS)
    assert lake.get_rule(rid)["registered_by_run"] == process_run_id()


def test_get_rule_returns_none_for_an_unknown_id(lake):
    assert lake.get_rule("nonexistent") is None


def test_params_round_trip(lake):
    rid = lake.register_rule(name="r", params=PARAMS)
    assert lake.get_rule(rid)["params"] == PARAMS


# --------------------------------------------------------------------------- #
# Trial counting — the input to every correction
# --------------------------------------------------------------------------- #

def test_rule_versions_counts_every_variant_tried(lake):
    for t in (0.05, 0.06, 0.07, 0.08):
        lake.register_rule(name="placement", params={**PARAMS, "edge_threshold": t})
    versions = lake.rule_versions("placement")
    assert len(versions) == 4, \
        "n_trials for the deflated Sharpe is exactly this count"


def test_rule_versions_are_ordered_oldest_first(lake):
    ids = [lake.register_rule(name="p", params={"i": i}) for i in range(3)]
    assert [v["rule_id"] for v in lake.rule_versions("p")] == ids


def test_rule_versions_of_an_unknown_name_is_empty(lake):
    assert lake.rule_versions("never-registered") == []


def test_trial_count_spans_all_names_when_asked(lake):
    lake.register_rule(name="a", params={"x": 1})
    lake.register_rule(name="a", params={"x": 2})
    lake.register_rule(name="b", params={"x": 1})
    assert lake.trial_count() == 3
    assert lake.trial_count(name="a") == 2


# --------------------------------------------------------------------------- #
# Violations
# --------------------------------------------------------------------------- #

def _bet(lake, bet_id, rule_id):
    lake.add_paper_bet(
        bet_id=bet_id, market_platform="polymarket", market_id="m",
        market_url=None, market_question="q?", market_resolves_at=None,
        side="YES", size_usd=100.0, price_at_bet=0.4, rationale="r",
        evidence=[], model_version="v1", confidence_band="medium",
        section_id="paper_bet_placement", rule_id=rule_id,
    )


def test_a_bet_placed_under_a_registered_rule_is_clean(lake):
    rid = lake.register_rule(name="placement", params=PARAMS)
    _bet(lake, "b1", rid)
    assert lake.preregistration_violations() == []


def test_a_bet_referencing_an_unregistered_rule_is_a_violation(lake):
    _bet(lake, "b1", "made-up-rule-id")
    v = lake.preregistration_violations()
    assert len(v) == 1
    assert v[0]["bet_id"] == "b1"
    assert v[0]["reason"] == "unregistered_rule"


def test_a_bet_with_no_rule_at_all_is_a_violation(lake):
    _bet(lake, "b1", None)
    v = lake.preregistration_violations()
    assert len(v) == 1
    assert v[0]["reason"] == "no_rule"


def test_a_bet_predating_its_rule_registration_is_a_violation(lake):
    """The backdating case: a rule invented to explain a trade already made."""
    rid = lake.register_rule(name="placement", params=PARAMS)
    _bet(lake, "b1", rid)
    conn = lake._ensure_open()
    # Move the registration to after the bet, simulating a rule written later.
    conn.execute("UPDATE rule_registry SET registered_at = '2999-01-01T00:00:00Z' "
                 "WHERE rule_id = ?", (rid,))
    v = lake.preregistration_violations()
    assert len(v) == 1
    assert v[0]["reason"] == "rule_registered_after_bet"


def test_violations_are_empty_with_no_bets(lake):
    assert lake.preregistration_violations() == []
