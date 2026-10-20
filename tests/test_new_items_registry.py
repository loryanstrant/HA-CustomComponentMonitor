"""The New-in-HACS registry (#95).

HACS flags a freshly indexed repository with ``new: true`` in its own
snapshot, but it also clears that flag itself (a panel action, some HACS
upgrades reset it wholesale), so the integration keeps its own record of
what it has already shown the user: when each repository was first seen,
what has been dismissed, what was auto-hidden as foreign to the configured
home country (and why), what must never be auto-hidden again, and the
cached AI category verdict for each.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.custom_component_monitor import new_items
from custom_components.custom_component_monitor.const import (
    NEW_ITEM_AI_PROMPT_VERSION,
    NEW_ITEMS_GRACE_DAYS,
    NEW_ITEMS_STORAGE_KEY,
    NEW_ITEMS_STORAGE_VERSION,
)
from custom_components.custom_component_monitor.new_items import (
    NewItemRegistry,
    NewItemStore,
    item_key,
)


@pytest.fixture
def registry(hass: HomeAssistant) -> NewItemRegistry:
    """A loaded new-items registry with an in-memory (never-written) store."""
    reg = NewItemRegistry(hass)
    reg._loaded = True
    reg._store = None  # async_save() becomes a no-op
    return reg


def at(days_ago: float):
    """An aware UTC datetime *days_ago* in the past (or future, if negative)."""
    return dt_util.utcnow() - timedelta(days=days_ago)


# -- first-seen ----------------------------------------------------------------


def test_note_seen_never_moves_first_seen_forward_but_last_present_moves(registry):
    later = at(5)

    first = registry.note_seen("Owner/Repo", now=at(10))
    second = registry.note_seen("Owner/Repo", now=later)

    assert second == first
    key = item_key("Owner/Repo")
    assert registry._seen[key]["last_present"] == later.isoformat()


def test_age_days_is_computed_from_first_seen(registry):
    registry.note_seen("Owner/Repo", now=at(10))

    assert registry.age_days("Owner/Repo", now=at(0)) == 10


def test_age_days_for_an_unseen_item_is_zero(registry):
    assert registry.age_days("Nobody/Home") == 0


# -- dismissal -----------------------------------------------------------------


def test_dismiss_then_is_dismissed(registry):
    assert registry.is_dismissed("Owner/Repo") is False

    assert registry.dismiss("Owner/Repo") is True

    assert registry.is_dismissed("Owner/Repo") is True
    assert registry.dismissed_count == 1


def test_dismissing_twice_reports_no_further_change(registry):
    registry.dismiss("Owner/Repo")

    assert registry.dismiss("Owner/Repo") is False


async def test_dismissal_survives_a_save_and_reload(hass, hass_storage):
    reg = NewItemRegistry(hass)
    await reg.async_load()
    reg.dismiss("Owner/Repo")
    await reg.async_save()

    reloaded = NewItemRegistry(hass)
    await reloaded.async_load()

    assert reloaded.is_dismissed("owner/repo") is True
    assert reloaded.dismissed_count == 1


# -- restore ---------------------------------------------------------------------


def test_restore_clears_a_dismissal(registry):
    registry.dismiss("Owner/Repo")

    assert registry.restore("Owner/Repo") is True

    assert registry.is_dismissed("Owner/Repo") is False


def test_a_second_restore_reports_no_change(registry):
    registry.dismiss("Owner/Repo")
    registry.restore("Owner/Repo")

    assert registry.restore("Owner/Repo") is False


def test_restore_of_an_untouched_item_reports_no_change(registry):
    assert registry.restore("Never/Touched") is False


def test_restore_clears_an_auto_hide_and_sets_never_hide(registry):
    registry.record_hidden(
        "Owner/Repo", country="SE", term="Sweden", field="description", source="rules"
    )

    assert registry.is_never_hide("Owner/Repo") is False

    assert registry.restore("Owner/Repo") is True

    assert registry.hidden_record("Owner/Repo") is None
    assert registry.is_never_hide("Owner/Repo") is True


# -- auto-hide audit -----------------------------------------------------------


def test_record_hidden_and_hidden_record_round_trip_including_source(registry):
    registry.record_hidden(
        "Owner/Repo",
        country="DE",
        term="Deutschland",
        field="topics",
        source="ai",
    )

    record = registry.hidden_record("Owner/Repo")

    assert record["country"] == "DE"
    assert record["term"] == "Deutschland"
    assert record["field"] == "topics"
    assert record["source"] == "ai"
    assert record["at"] is not None


def test_clear_hidden_removes_the_record(registry):
    registry.record_hidden(
        "Owner/Repo", country="SE", term="Sweden", field="description", source="rules"
    )

    registry.clear_hidden("Owner/Repo")

    assert registry.hidden_record("Owner/Repo") is None


def test_hidden_record_absent_is_none(registry):
    assert registry.hidden_record("Nobody/Home") is None


# -- AI verdict cache -----------------------------------------------------------


def test_get_ai_returns_the_record_when_the_prompt_version_matches(registry):
    registry.set_ai(
        "Owner/Repo", category="Energy & solar", country_scope="global", model="test-model"
    )

    record = registry.get_ai("Owner/Repo")

    assert record["category"] == "Energy & solar"
    assert record["country_scope"] == "global"
    assert record["model"] == "test-model"
    assert record["prompt_version"] == NEW_ITEM_AI_PROMPT_VERSION


def test_get_ai_returns_none_when_the_prompt_version_has_moved_on(registry):
    registry.set_ai("Owner/Repo", category="Energy & solar", country_scope=None, model=None)
    key = item_key("Owner/Repo")
    registry._ai[key]["prompt_version"] = NEW_ITEM_AI_PROMPT_VERSION - 1

    assert registry.get_ai("Owner/Repo") is None


def test_get_ai_absent_is_none(registry):
    assert registry.get_ai("Nobody/Home") is None


# -- pruning ---------------------------------------------------------------------


def test_prune_drops_a_record_absent_beyond_the_grace_period(registry):
    registry.note_seen("Gone/Forever", now=at(200))
    registry._seen[item_key("Gone/Forever")]["last_present"] = at(
        NEW_ITEMS_GRACE_DAYS + 1
    ).isoformat()

    dropped = registry.prune([])

    assert dropped == 1
    assert item_key("Gone/Forever") not in registry._seen


def test_prune_keeps_a_record_absent_for_less_than_the_grace_period(registry):
    registry.note_seen("Still/Recent", now=at(200))
    registry._seen[item_key("Still/Recent")]["last_present"] = at(
        NEW_ITEMS_GRACE_DAYS - 1
    ).isoformat()

    dropped = registry.prune([])

    assert dropped == 0
    assert item_key("Still/Recent") in registry._seen


def test_prune_never_drops_dismissed_or_never_hide_even_long_vanished(registry):
    registry.note_seen("Dismissed/Repo", now=at(200))
    registry.dismiss("Dismissed/Repo")
    registry._seen[item_key("Dismissed/Repo")]["last_present"] = at(
        NEW_ITEMS_GRACE_DAYS + 500
    ).isoformat()

    registry.record_hidden(
        "NeverHide/Repo", country="SE", term="Sweden", field="description", source="rules"
    )
    registry.restore("NeverHide/Repo")  # sets never_hide
    registry.note_seen("NeverHide/Repo", now=at(200))
    registry._seen[item_key("NeverHide/Repo")]["last_present"] = at(
        NEW_ITEMS_GRACE_DAYS + 500
    ).isoformat()

    dropped = registry.prune([])

    assert dropped == 0
    assert registry.is_dismissed("Dismissed/Repo") is True
    assert registry.is_never_hide("NeverHide/Repo") is True


def test_prune_enforces_max_entries_dropping_oldest_first_seen_first(registry, monkeypatch):
    monkeypatch.setattr(new_items, "NEW_ITEMS_MAX_ENTRIES", 2)
    registry.note_seen("Oldest/Repo", now=at(30))
    registry.note_seen("Middle/Repo", now=at(20))
    registry.note_seen("Newest/Repo", now=at(10))

    dropped = registry.prune(
        ["Oldest/Repo", "Middle/Repo", "Newest/Repo"], now=at(0)
    )

    assert dropped == 1
    assert item_key("Oldest/Repo") not in registry._seen
    assert item_key("Middle/Repo") in registry._seen
    assert item_key("Newest/Repo") in registry._seen


def test_prune_known_full_names_are_never_dropped_regardless_of_age(registry):
    registry.note_seen("Still/Present", now=at(500))
    registry._seen[item_key("Still/Present")]["last_present"] = at(
        NEW_ITEMS_GRACE_DAYS + 500
    ).isoformat()

    dropped = registry.prune(["Still/Present"])

    assert dropped == 0
    assert item_key("Still/Present") in registry._seen


# -- identity ------------------------------------------------------------------


def test_item_key_normalises_case_and_whitespace():
    assert item_key(" Owner/Repo ") == item_key("owner/repo")
    assert item_key("owner/repo") == "owner/repo"


def test_item_key_of_nothing_is_empty():
    assert item_key("") == ""
    assert item_key(None) == ""  # type: ignore[arg-type]


# -- defensive -------------------------------------------------------------------


async def test_a_corrupt_store_loads_without_raising_and_behaves_sanely(
    hass, hass_storage
):
    hass_storage[NEW_ITEMS_STORAGE_KEY] = {
        "version": NEW_ITEMS_STORAGE_VERSION,
        "data": {
            "seen": {
                "good/one": dt_util.utcnow().isoformat(),  # bare string: first_seen
                "bad/notadict": 12345,
            },
            "dismissed": {
                "junk/timestamp": "not-a-real-timestamp",
                "good/dismissed": dt_util.utcnow().isoformat(),
            },
            "auto_hidden": {
                "bad/hidden": "nonsense",
            },
            # "never_hide" and "ai" are missing entirely.
        },
    }
    reg = NewItemRegistry(hass)

    await reg.async_load()

    assert set(reg._seen) == {"good/one"}
    assert set(reg._dismissed) == {"good/dismissed"}
    assert reg._auto_hidden == {}
    assert reg._never_hide == set()
    assert reg._ai == {}
    # And the registry is still usable.
    assert reg.is_dismissed("good/dismissed") is True
    assert reg.is_dismissed("junk/timestamp") is False
    assert reg.first_seen("good/one") is not None
    assert reg.get_ai("anything") is None


async def test_async_load_tolerates_a_raising_store(hass):
    reg = NewItemRegistry(hass)
    with patch.object(NewItemStore, "async_load", side_effect=ValueError("boom")):
        await reg.async_load()

    assert reg._seen == {}
    assert reg._dismissed == {}
    assert reg._auto_hidden == {}
    assert reg._never_hide == set()
    assert reg._ai == {}


async def test_async_save_is_a_no_op_when_nothing_changed(hass, hass_storage):
    reg = NewItemRegistry(hass)
    await reg.async_load()

    await reg.async_save()

    assert NEW_ITEMS_STORAGE_KEY not in hass_storage
