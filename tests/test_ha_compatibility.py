"""Does the offered update actually run on this Home Assistant? (#111)

A component declares its minimum Home Assistant version in ``hacs.json``, HACS
enforces it on install, and nothing showed it to the user beforehand — so an
install either failed for no visible reason or, as with Microsoft 365 Calendar,
went ahead and had to be rolled back by hand.

The one rule these tests exist to defend: *unknown is never folded into met*.
Most components declare nothing at all, so a quiet default of "compatible"
would be wrong far more often than it was right.
"""
from __future__ import annotations

import pytest

from custom_components.custom_component_monitor.compat import (
    REASON_HA_UNREADABLE,
    REASON_NOT_DECLARED,
    REASON_UNREADABLE,
    STATE_BLOCKED,
    STATE_MET,
    STATE_UNKNOWN,
    block_sentence,
    declared_min_ha_version,
    ha_requirement,
    is_blocked,
    strictest,
)


def test_declared_minimum_is_met():
    result = ha_requirement({"homeassistant": "2025.6.0"}, "2026.10.4")

    assert result["state"] == STATE_MET
    assert result["required"] == "2025.6.0"
    assert result["installed"] == "2026.10.4"
    assert result["reason"] is None


def test_declared_minimum_is_not_met():
    """The Microsoft 365 Calendar case: a real, readable, unmet requirement."""
    result = ha_requirement({"homeassistant": "2026.11.0"}, "2026.10.4")

    assert result["state"] == STATE_BLOCKED
    assert is_blocked(result)


def test_equal_versions_are_met():
    assert ha_requirement({"homeassistant": "2026.10.4"}, "2026.10.4")["state"] == STATE_MET


def test_a_coarser_declaration_still_compares():
    """`2026.11` and `2026.11.0` are the same release, written two ways."""
    assert ha_requirement({"homeassistant": "2026.11"}, "2026.11.0")["state"] == STATE_MET
    assert ha_requirement({"homeassistant": "2026.11"}, "2026.10.4")["state"] == STATE_BLOCKED


@pytest.mark.parametrize(
    "manifest",
    [
        {},
        {"homeassistant": ""},
        {"homeassistant": "   "},
        {"homeassistant": None},
        {"homeassistant": 2026.11},
        None,
        "not a manifest",
        [],
    ],
)
def test_nothing_usable_declared_is_unknown_not_met(manifest):
    result = ha_requirement(manifest, "2026.10.4")

    assert result["state"] == STATE_UNKNOWN
    assert result["reason"] == REASON_NOT_DECLARED


def test_a_range_expression_is_unknown_rather_than_a_crash():
    """`>=2026.11.0` parses, then raises the moment it is compared."""
    result = ha_requirement({"homeassistant": ">=2026.11.0"}, "2026.10.4")

    assert result["state"] == STATE_UNKNOWN
    assert result["reason"] == REASON_UNREADABLE
    # The declared string is still carried, so the card can quote it.
    assert result["required"] == ">=2026.11.0"


def test_a_container_tag_does_not_block_the_whole_house():
    """`latest` is a valid AwesomeVersion that compares above every release.

    Taken at face value it would mark every single update incompatible, which
    is the loudest possible way to be wrong.
    """
    result = ha_requirement({"homeassistant": "latest"}, "2026.10.4")

    assert result["state"] == STATE_UNKNOWN
    assert result["reason"] == REASON_UNREADABLE
    assert not is_blocked(result)


@pytest.mark.parametrize("installed", ["", None, "   ", "unknown"])
def test_an_unreadable_home_assistant_version_is_unknown(installed):
    result = ha_requirement({"homeassistant": "2026.11.0"}, installed)

    assert result["state"] == STATE_UNKNOWN
    assert result["reason"] == REASON_HA_UNREADABLE


@pytest.mark.parametrize("required", ["0.118.0b3", "1.4", "0.110"])
def test_an_ancient_minimum_is_met(required):
    """A pre-CalVer minimum is genuinely satisfied, not a comparison failure.

    Plenty of long-lived components still declare ``0.118.0b3``-era minimums.
    Those orders fine against a CalVer version, so the honest answer is ``met``
    — this test pins that rather than accepting either answer.
    """
    result = ha_requirement({"homeassistant": required}, "2026.10.4")

    assert result["state"] == STATE_MET
    assert result["reason"] is None


def test_a_prerelease_minimum_still_blocks():
    """``2026.11.0b2`` is a real minimum and a real block on 2026.10.4."""
    result = ha_requirement({"homeassistant": "2026.11.0b2"}, "2026.10.4")

    assert result["state"] == STATE_BLOCKED


def test_strictest_prefers_a_block_over_a_clearance():
    """Two rows claiming one entity: the restrictive answer wins (#111).

    The reconciler's name/slug fallback can hand one ``update.*`` entity to two
    snapshot rows. Believing the permissive one would install exactly what this
    feature exists to hold back, so the strict one is believed instead.
    """
    met = {"state": STATE_MET, "required": "2025.1.0"}
    unknown = {"state": STATE_UNKNOWN, "reason": REASON_NOT_DECLARED}
    blocked = {"state": STATE_BLOCKED, "required": "2026.11.0"}

    assert strictest([met, blocked]) is blocked
    assert strictest([blocked, met]) is blocked
    assert strictest([met, unknown]) is unknown
    assert strictest([unknown, met]) is unknown
    assert strictest([unknown, blocked]) is blocked
    assert strictest([met]) is met


def test_strictest_handles_nothing_and_rubbish():
    assert strictest([]) is None
    assert strictest(None) is None
    assert strictest(["not a dict", None]) is None
    # An unrecognised state is treated as unknown, not as a clearance.
    odd = {"state": "something new"}
    assert strictest([{"state": STATE_MET}, odd]) is odd


def test_declared_min_ha_version_returns_the_string_verbatim():
    assert declared_min_ha_version({"homeassistant": " 2026.11.0 "}) == "2026.11.0"
    assert declared_min_ha_version({"homeassistant": ">=2026.11"}) == ">=2026.11"
    assert declared_min_ha_version({}) is None
    assert declared_min_ha_version(None) is None


def test_is_blocked_is_true_only_for_a_proven_block():
    assert is_blocked({"state": STATE_BLOCKED}) is True
    assert is_blocked({"state": STATE_UNKNOWN}) is False
    assert is_blocked({"state": STATE_MET}) is False
    assert is_blocked(None) is False
    assert is_blocked("blocked") is False


def test_block_sentence_names_both_versions():
    sentence = block_sentence(
        "Microsoft 365 — Calendar",
        {"state": STATE_BLOCKED, "required": "2026.11.0", "installed": "2026.10.4"},
    )

    assert "Microsoft 365 — Calendar" in sentence
    assert "2026.11.0" in sentence
    assert "2026.10.4" in sentence
    assert "Update Home Assistant first" in sentence


def test_block_sentence_survives_missing_versions():
    sentence = block_sentence("Thing", {"state": STATE_BLOCKED})

    assert "a newer version" in sentence
    assert "an older version" in sentence
