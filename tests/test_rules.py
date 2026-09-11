"""Tests for per-account switching rules (rules.py): storage, parsing, and the
capped-headroom math the engine decides with."""

from __future__ import annotations

import pytest

from claude_swap.rules import (
    KEEP,
    AccountRule,
    apply_rule,
    cap_headroom,
    effective_threshold,
    parse_hard_limit,
    parse_priority,
    parse_swap_limit,
    rule_from_record,
)


class TestRecordRoundTrip:
    def test_missing_keys_are_the_default_rule(self):
        assert rule_from_record({"email": "a@x"}) == AccountRule()
        assert rule_from_record(None) == AccountRule()
        assert AccountRule().is_default

    def test_reads_and_clamps(self):
        rule = rule_from_record({"swapLimit": 95, "hardLimit": 50, "priority": 2})
        assert rule == AccountRule(swap_limit=95.0, hard_limit=50.0, priority=2)
        # garbage → default per key; out-of-range → clamped
        rule = rule_from_record({"swapLimit": "95", "hardLimit": 500, "priority": True})
        assert rule == AccountRule(swap_limit=None, hard_limit=100.0, priority=1)
        assert rule_from_record({"priority": 0}).priority == 1

    def test_apply_writes_only_non_defaults_and_keeps_other_fields(self):
        record = {"email": "a@x", "alias": "main"}
        rule = apply_rule(record, priority=2, hard_limit=50.0)
        assert rule == AccountRule(hard_limit=50.0, priority=2)
        assert record == {"email": "a@x", "alias": "main", "hardLimit": 50.0, "priority": 2}
        # KEEP leaves fields alone; an explicit default removes the key
        apply_rule(record, swap_limit=95.0)
        assert record["swapLimit"] == 95.0 and record["priority"] == 2
        apply_rule(record, priority=1, hard_limit=KEEP)
        assert "priority" not in record and record["hardLimit"] == 50.0
        apply_rule(record, reset=True)
        assert record == {"email": "a@x", "alias": "main"}

    def test_reset_then_set_in_one_call(self):
        record = {"swapLimit": 60.0, "priority": 3}
        rule = apply_rule(record, reset=True, hard_limit=40.0)
        assert rule == AccountRule(hard_limit=40.0)
        assert record == {"hardLimit": 40.0}


class TestParsing:
    def test_swap_limit(self):
        assert parse_swap_limit("95") == 95.0
        assert parse_swap_limit("95%") == 95.0
        assert parse_swap_limit(" 80.5 ") == 80.5
        for cleared in ("", "off", "default", None):
            assert parse_swap_limit(cleared) is None
        with pytest.raises(ValueError, match="between 1 and 100"):
            parse_swap_limit("0")
        with pytest.raises(ValueError, match="must be a number"):
            parse_swap_limit("high")

    def test_hard_limit(self):
        assert parse_hard_limit("50") == 50.0
        assert parse_hard_limit("off") == 100.0
        with pytest.raises(ValueError, match="between 1 and 100"):
            parse_hard_limit("150")

    def test_priority(self):
        assert parse_priority("2") == 2
        assert parse_priority("off") == 1
        with pytest.raises(ValueError, match="between 1 and 99"):
            parse_priority("0")
        with pytest.raises(ValueError, match="whole number"):
            parse_priority("2.5")


class TestEngineMath:
    def test_cap_headroom(self):
        assert cap_headroom(60.0, AccountRule()) == 60.0  # default: unchanged
        assert cap_headroom(60.0, AccountRule(hard_limit=50.0)) == 10.0  # 40% used
        assert cap_headroom(50.0, AccountRule(hard_limit=50.0)) == 0.0  # at the cap
        assert cap_headroom(40.0, AccountRule(hard_limit=50.0)) == -10.0  # over it
        assert cap_headroom(None, AccountRule(hard_limit=50.0)) is None

    def test_effective_threshold(self):
        # default rule: the global threshold, untouched
        assert effective_threshold(AccountRule(), 90.0) == 90.0
        # own swap limit, no cap: itself
        assert effective_threshold(AccountRule(swap_limit=95.0), 90.0) == 95.0
        # swap 40 under a 50 cap: 40% used is 10 pts of capped headroom → 90
        assert effective_threshold(AccountRule(swap_limit=40.0, hard_limit=50.0), 90.0) == 90.0
        # swap at/past the cap: never proactive, only the hard-limit escape
        assert effective_threshold(AccountRule(swap_limit=95.0, hard_limit=50.0), 90.0) == 100.0

    def test_summary_and_leave_at(self):
        rule = AccountRule(swap_limit=95.0, hard_limit=50.0, priority=2)
        assert rule.summary() == "p2 · swap 95% · hard 50%"
        assert rule.summary(threshold=95.0) == "p2 · hard 50%"  # swap == global: elided
        assert rule.leave_at(90.0) == 50.0
        assert AccountRule(swap_limit=60.0).leave_at(90.0) == 60.0
        assert AccountRule().leave_at(90.0) == 90.0
