"""The AI summary cache: what gets re-sent to the model, and what doesn't (#91).

v1.11.0 keyed the cache on the display name, hard-pruned it to the currently
pending set on every scan, never recorded failures, and treated any cached
record as "done". Between them those meant the same updates were generated
over and over while some never got a summary at all.
"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.util import dt as dt_util


def update_item(
    name: str = "Powercalc",
    repo: str = "bramstroker/homeassistant-powercalc",
    available: str = "v1.23.1",
    entity_id: str | None = None,
):
    item = {
        "name": name,
        "type": "Integration",
        "repository": f"https://github.com/{repo}",
        "current_version": "v1.23.0",
        "available_version": available,
    }
    if entity_id:
        item["entity_id"] = entity_id
    return item


# -- keying -----------------------------------------------------------------


def test_key_survives_a_rename(coordinator):
    """A HACS manifest rename must not throw away a good summary."""
    before = coordinator._cache_key(update_item(name="Powercalc"))
    after = coordinator._cache_key(update_item(name="PowerCalc (renamed)"))

    assert before == after


def test_key_ignores_the_v_prefix(coordinator):
    assert coordinator._cache_key(
        update_item(available="v1.23.1")
    ) == coordinator._cache_key(update_item(available="1.23.1"))


def test_key_changes_with_the_target_version(coordinator):
    assert coordinator._cache_key(update_item(available="v1.23.1")) != (
        coordinator._cache_key(update_item(available="v1.24.0"))
    )


def test_key_falls_back_to_the_name_without_a_repository(coordinator):
    item = {"name": "Mystery Card", "repository": "", "available_version": "1.0"}

    assert coordinator._cache_key(item) == "name:mysterycard|1.0"


# -- completeness -----------------------------------------------------------


def test_categories_without_a_summary_is_not_complete(coordinator):
    """The bug that made some updates never get a summary, ever.

    ``_parse_ai_*`` succeed on categories alone, so this record is real; the old
    truthiness check treated it as finished and skipped it forever.
    """
    record = {"categories": ["Bug fixes"], "summary": "", "status": "partial"}

    assert coordinator._record_is_complete(record) is False


def test_whitespace_summary_is_not_complete(coordinator):
    assert coordinator._record_is_complete({"summary": "   "}) is False


def test_a_real_summary_is_complete(coordinator):
    assert coordinator._record_is_complete({"summary": "Fixes a crash."}) is True


def test_missing_record_is_not_complete(coordinator):
    assert coordinator._record_is_complete(None) is False


# -- legacy migration -------------------------------------------------------


async def test_v1_migration_wraps_the_old_dict(hass):
    from custom_components.custom_component_monitor.const import (
        AI_CACHE_STORAGE_KEY,
        AI_CACHE_STORAGE_VERSION,
    )
    from custom_components.custom_component_monitor.sensor import AiCacheStore

    store = AiCacheStore(hass, AI_CACHE_STORAGE_VERSION, AI_CACHE_STORAGE_KEY)
    old = {"Powercalc|v1.23.1": {"categories": ["Bug fixes"], "summary": "Fixes."}}

    migrated = await store._async_migrate_func(1, 0, old)

    assert migrated == {"entries": {}, "legacy": old}


def test_legacy_record_is_promoted_not_regenerated(coordinator):
    """Upgrading must not re-run every summary the user already paid for."""
    coordinator._ai_legacy = {
        "Powercalc|v1.23.1": {"categories": ["Bug fixes"], "summary": "Fixes a crash."}
    }
    item = update_item()

    record = coordinator._cache_get(item)

    assert coordinator._record_is_complete(record)
    assert record["status"] == "ok"
    # Promoted under the new key and taken off the legacy pile.
    assert coordinator._cache_key(item) in coordinator._ai_cache
    assert coordinator._ai_legacy == {}


def test_legacy_record_without_a_summary_stays_incomplete(coordinator):
    coordinator._ai_legacy = {
        "Powercalc|v1.23.1": {"categories": ["Bug fixes"], "summary": ""}
    }

    record = coordinator._cache_get(update_item())

    assert record["status"] == "partial"
    assert coordinator._record_is_complete(record) is False


# -- retries and backoff ----------------------------------------------------


def test_unknown_update_is_retried(coordinator):
    assert coordinator._should_retry(None, force=False) is True


def test_recent_failure_is_not_retried_immediately(coordinator):
    record = {
        "status": "failed",
        "attempts": 1,
        "last_attempt": dt_util.utcnow().isoformat(),
    }

    assert coordinator._should_retry(record, force=False) is False


def test_failure_is_retried_once_the_backoff_expires(coordinator):
    record = {
        "status": "failed",
        "attempts": 1,
        "last_attempt": (dt_util.utcnow() - timedelta(hours=2)).isoformat(),
    }

    assert coordinator._should_retry(record, force=False) is True


def test_retries_stop_after_the_attempt_cap(coordinator):
    record = {
        "status": "failed",
        "attempts": 5,
        "last_attempt": (dt_util.utcnow() - timedelta(days=30)).isoformat(),
    }

    assert coordinator._should_retry(record, force=False) is False


def test_force_beats_backoff_and_the_attempt_cap(coordinator):
    record = {
        "status": "failed",
        "attempts": 99,
        "last_attempt": dt_util.utcnow().isoformat(),
    }

    assert coordinator._should_retry(record, force=True) is True


# -- recording an attempt ---------------------------------------------------


def test_failure_is_recorded_so_it_can_back_off(coordinator):
    """v1.11.0 discarded failures, so they were re-sent every single scan."""
    item = update_item()

    coordinator._record_result(item, None)

    record = coordinator._ai_cache[coordinator._cache_key(item)]
    assert record["status"] == "failed"
    assert record["attempts"] == 1
    assert record["last_attempt"]


def test_repeated_failures_accumulate_attempts(coordinator):
    item = update_item()

    coordinator._record_result(item, None)
    coordinator._record_result(item, None)

    assert coordinator._ai_cache[coordinator._cache_key(item)]["attempts"] == 2


def test_a_later_blank_never_overwrites_a_good_summary(coordinator):
    item = update_item()
    coordinator._record_result(item, {"categories": ["Bug fixes"], "summary": "Fixes."})

    coordinator._record_result(item, {"categories": [], "summary": ""})

    record = coordinator._ai_cache[coordinator._cache_key(item)]
    assert record["summary"] == "Fixes."
    assert record["categories"] == ["Bug fixes"]
    assert record["status"] == "ok"


def test_result_is_stamped_on_the_item(coordinator):
    item = update_item()

    coordinator._record_result(item, {"categories": ["Bug fixes"], "summary": "Fixes."})

    assert item["summary"] == "Fixes."
    assert item["categories"] == ["Bug fixes"]


# -- eviction ---------------------------------------------------------------


def test_a_scan_that_omits_a_key_does_not_delete_it(coordinator):
    """The HACS snapshot flaps; a brief disappearance must not cost a summary."""
    item = update_item()
    key = coordinator._cache_key(item)
    coordinator._ai_cache[key] = {
        "summary": "Fixes.",
        "categories": [],
        "updated": dt_util.utcnow().isoformat(),
    }

    coordinator._evict_ai_cache(set())  # nothing pending this scan

    assert key in coordinator._ai_cache


def test_stale_records_are_evicted(coordinator):
    coordinator._ai_cache["old|1.0"] = {
        "summary": "Ancient.",
        "updated": (dt_util.utcnow() - timedelta(days=90)).isoformat(),
    }

    coordinator._evict_ai_cache(set())

    assert "old|1.0" not in coordinator._ai_cache


def test_a_stale_record_that_is_still_pending_is_kept(coordinator):
    coordinator._ai_cache["old|1.0"] = {
        "summary": "Ancient.",
        "updated": (dt_util.utcnow() - timedelta(days=90)).isoformat(),
    }

    coordinator._evict_ai_cache({"old|1.0"})

    assert "old|1.0" in coordinator._ai_cache


# -- applying the cache to a scan ------------------------------------------


def test_apply_cached_ai_counts_the_backlog(coordinator):
    done = update_item(name="Powercalc")
    todo = update_item(name="Kiosk Mode", repo="nielsfaber/kiosk-mode")
    coordinator._ai_cache[coordinator._cache_key(done)] = {
        "categories": ["Bug fixes"],
        "summary": "Fixes a crash.",
        "updated": dt_util.utcnow().isoformat(),
    }
    updates = {"count": 2, "updates": [done, todo]}

    coordinator._apply_cached_ai(updates)

    assert updates["ai_pending"] == 1
    assert done["summary"] == "Fixes a crash."


def test_exhausted_retries_stop_waking_the_worker(coordinator):
    """A permanently-failing update must not re-spawn a no-op task every hour.

    It still counts as pending (the user should see it has no summary) but it
    is no longer *retryable*, which is what drives scheduling.
    """
    item = update_item()
    coordinator._ai_cache[coordinator._cache_key(item)] = {
        "categories": [],
        "summary": "",
        "status": "failed",
        "attempts": 5,
        "last_attempt": (dt_util.utcnow() - timedelta(days=7)).isoformat(),
        "updated": dt_util.utcnow().isoformat(),
    }
    updates = {"count": 1, "updates": [item]}

    coordinator._apply_cached_ai(updates)

    assert updates["ai_pending"] == 1
    assert updates["ai_retryable"] == 0


def test_a_backed_off_update_is_pending_but_not_retryable(coordinator):
    item = update_item()
    coordinator._ai_cache[coordinator._cache_key(item)] = {
        "categories": [],
        "summary": "",
        "status": "failed",
        "attempts": 1,
        "last_attempt": dt_util.utcnow().isoformat(),
        "updated": dt_util.utcnow().isoformat(),
    }
    updates = {"count": 1, "updates": [item]}

    coordinator._apply_cached_ai(updates)

    assert (updates["ai_pending"], updates["ai_retryable"]) == (1, 0)


def test_a_never_seen_update_is_both_pending_and_retryable(coordinator):
    updates = {"count": 1, "updates": [update_item()]}

    coordinator._apply_cached_ai(updates)

    assert (updates["ai_pending"], updates["ai_retryable"]) == (1, 1)


def test_apply_cached_ai_is_a_no_op_when_disabled(no_ai_coordinator):
    item = update_item()
    updates = {"count": 1, "updates": [item]}

    no_ai_coordinator._apply_cached_ai(updates)

    assert updates["ai_pending"] == 0
    assert "summary" not in item
