"""The enrichment worker: only updates missing a summary reach the model (#91)."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.util import dt as dt_util

from .test_ai_cache import update_item


@pytest.fixture
def loaded(coordinator):
    """A coordinator holding a scan result, with the AI call stubbed out."""
    powercalc = update_item(name="Powercalc", entity_id="update.powercalc_update")
    kiosk = update_item(
        name="Kiosk Mode",
        repo="nielsfaber/kiosk-mode",
        entity_id="update.kiosk_mode_update",
    )
    coordinator.data = {"updates": {"count": 2, "updates": [powercalc, kiosk]}}
    coordinator._build_update_entity_map = lambda: {"by_name": {}, "by_version": {}}
    return coordinator, powercalc, kiosk


def summarised(text="A summary.", categories=("Bug fixes",)):
    return {"categories": list(categories), "summary": text}


async def test_already_summarised_updates_are_not_re_sent(loaded):
    """The user's explicit ask — and the fix for the duplicate AI-log entries."""
    coordinator, powercalc, kiosk = loaded
    coordinator._ai_cache[coordinator._cache_key(powercalc)] = {
        "categories": ["Bug fixes"],
        "summary": "Already done.",
        "status": "ok",
        "attempts": 1,
        "updated": dt_util.utcnow().isoformat(),
    }
    call = AsyncMock(return_value=summarised())

    with patch.object(coordinator, "_async_categorise_one", call):
        counts = await coordinator.async_generate_summaries()

    assert call.await_count == 1
    assert call.await_args.args[0] is kiosk
    assert counts == {"generated": 1, "failed": 0, "skipped": 1, "remaining": 0}


async def test_a_cached_record_without_a_summary_is_re_sent(loaded):
    """Categories-only records used to be skipped forever."""
    coordinator, powercalc, _kiosk = loaded
    coordinator._ai_cache[coordinator._cache_key(powercalc)] = {
        "categories": ["Bug fixes"],
        "summary": "",
        "status": "partial",
        "attempts": 1,
        "last_attempt": (dt_util.utcnow() - timedelta(days=1)).isoformat(),
        "updated": dt_util.utcnow().isoformat(),
    }
    call = AsyncMock(return_value=summarised())

    with patch.object(coordinator, "_async_categorise_one", call):
        await coordinator.async_generate_summaries()

    assert call.await_count == 2
    assert powercalc["summary"] == "A summary."


async def test_backed_off_failures_are_skipped(loaded):
    coordinator, powercalc, _kiosk = loaded
    coordinator._ai_cache[coordinator._cache_key(powercalc)] = {
        "categories": [],
        "summary": "",
        "status": "failed",
        "attempts": 1,
        "last_attempt": dt_util.utcnow().isoformat(),
        "updated": dt_util.utcnow().isoformat(),
    }
    call = AsyncMock(return_value=summarised())

    with patch.object(coordinator, "_async_categorise_one", call):
        counts = await coordinator.async_generate_summaries()

    assert call.await_count == 1
    assert counts["skipped"] == 1


async def test_force_regenerates_everything(loaded):
    coordinator, powercalc, _kiosk = loaded
    coordinator._ai_cache[coordinator._cache_key(powercalc)] = {
        "categories": ["Bug fixes"],
        "summary": "Already done.",
        "status": "ok",
        "attempts": 1,
        "updated": dt_util.utcnow().isoformat(),
    }
    call = AsyncMock(return_value=summarised("Fresh."))

    with patch.object(coordinator, "_async_categorise_one", call):
        counts = await coordinator.async_generate_summaries(force=True)

    assert call.await_count == 2
    assert counts["generated"] == 2
    assert powercalc["summary"] == "Fresh."


async def test_entity_ids_limits_the_run(loaded):
    """What the card's button passes: only the rows it can see."""
    coordinator, _powercalc, kiosk = loaded
    call = AsyncMock(return_value=summarised())

    with patch.object(coordinator, "_async_categorise_one", call):
        counts = await coordinator.async_generate_summaries(
            entity_ids={"update.kiosk_mode_update"}
        )

    assert call.await_count == 1
    assert call.await_args.args[0] is kiosk
    assert counts["generated"] == 1


async def test_limit_caps_the_run_and_reports_the_remainder(loaded):
    coordinator, _powercalc, _kiosk = loaded
    call = AsyncMock(return_value=summarised())

    with patch.object(coordinator, "_async_categorise_one", call):
        counts = await coordinator.async_generate_summaries(limit=1)

    assert call.await_count == 1
    assert counts == {"generated": 1, "failed": 0, "skipped": 0, "remaining": 1}


async def test_failures_are_counted_and_recorded(loaded):
    coordinator, powercalc, _kiosk = loaded
    call = AsyncMock(return_value=None)

    with patch.object(coordinator, "_async_categorise_one", call):
        counts = await coordinator.async_generate_summaries()

    assert counts["failed"] == 2
    record = coordinator._ai_cache[coordinator._cache_key(powercalc)]
    assert record["status"] == "failed"
    assert record["attempts"] == 1


async def test_pending_count_is_refreshed_after_a_run(loaded):
    coordinator, _powercalc, _kiosk = loaded
    call = AsyncMock(side_effect=[summarised(), None])

    with patch.object(coordinator, "_async_categorise_one", call):
        await coordinator.async_generate_summaries()

    assert coordinator.data["updates"]["ai_pending"] == 1


async def test_in_progress_flag_is_cleared_even_when_a_call_explodes(loaded):
    coordinator, _powercalc, _kiosk = loaded
    call = AsyncMock(side_effect=RuntimeError("provider down"))

    with patch.object(coordinator, "_async_categorise_one", call):
        with pytest.raises(RuntimeError):
            await coordinator.async_generate_summaries()

    assert coordinator.ai_in_progress is False


async def test_unknown_entity_ids_are_reported_not_silently_dropped(loaded, caplog):
    """The card can know about an update the last scan didn't."""
    coordinator, _powercalc, _kiosk = loaded
    call = AsyncMock(return_value=summarised())

    with patch.object(coordinator, "_async_categorise_one", call):
        counts = await coordinator.async_generate_summaries(
            entity_ids={"update.brand_new_thing"}
        )

    assert call.await_count == 0
    assert counts["skipped"] == 1
    assert "update.brand_new_thing" in caplog.text


async def test_known_update_entity_ids(loaded):
    coordinator, _powercalc, _kiosk = loaded

    assert coordinator.known_update_entity_ids() == {
        "update.powercalc_update",
        "update.kiosk_mode_update",
    }


async def test_deferred_run_waits_for_the_coordinator_to_publish(coordinator):
    """Background tasks start eagerly; the pass must not run before `data` is set.

    Without the yield the enrichment ran inside `_async_update_data`, saw
    `self.data` as None on the first refresh and the *previous* scan's items on
    every later one — stamping summaries onto dicts about to be discarded.
    """
    coordinator.data = None
    seen: list = []

    async def _capture(**kwargs):
        seen.append(coordinator.data)
        return {"generated": 0, "failed": 0, "skipped": 0, "remaining": 0}

    with patch.object(coordinator, "async_generate_summaries", _capture):
        task = coordinator.hass.async_create_background_task(
            coordinator._async_deferred_ai_run(), name="test"
        )
        # Eager start would have run it by now; the yield defers it.
        assert seen == []
        coordinator.data = {"updates": {"count": 0, "updates": []}}
        await task

    assert seen == [{"updates": {"count": 0, "updates": []}}]


async def test_disabled_ai_generates_nothing(no_ai_coordinator):
    no_ai_coordinator.data = {"updates": {"count": 1, "updates": [update_item()]}}
    call = AsyncMock(return_value=summarised())

    with patch.object(no_ai_coordinator, "_async_categorise_one", call):
        counts = await no_ai_coordinator.async_generate_summaries()

    assert call.await_count == 0
    assert counts["generated"] == 0
