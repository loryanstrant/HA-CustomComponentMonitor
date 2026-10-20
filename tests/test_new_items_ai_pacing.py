"""New in HACS: the self-tuning batch-and-retry sorting loop (v1.15.1, #96).

v1.15.0 sent a fixed number of items (default 10) to the AI once per hourly
scan. Measured against a local Qwen3.5-9B each item took 0.40-0.48 s, so a
cycle did ~4.5 s of work and then waited 59 minutes 55 s; a 200-item HACS drop
took close to a day. Raising the number would only have moved the wrong
constant, because the right number depends on hardware nobody can know in
advance.

What replaced it, and what these tests pin down:

* a **time budget** per pass instead of an item count, checked *between*
  waves so the first wave always runs and a model slower than the whole
  budget still makes progress;
* a **wave size that tunes itself** on throughput measured *per wave size* —
  it tries any size it has never measured, moves to whichever neighbour
  measured clearly better, halves on any failed call in a wave, and
  periodically forgets its neighbours so a stale number cannot pin it. The
  per-size comparison is the point: a single best-ever bar can only ratchet
  up, so one failure would walk the size down to 1 with no way back;
* a **catch-up chain** that runs pass after pass until the backlog is clear,
  and stops rather than spinning when nothing is getting through;
* **measurements, not estimates**, on the sensor: the rate and the ETA are
  null until a wave has actually been timed.

Every AI call is mocked. Where a test needs time to pass it fakes the clock
rather than sleeping, so the suite stays fast.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.custom_component_monitor.const import (
    NEW_ITEMS_AI_CONCURRENCY_MAX,
    NEW_ITEMS_AI_CONCURRENCY_START,
    NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS,
    NEW_ITEMS_AI_MAX_FAILED_WAVES,
    NEW_ITEMS_AI_MAX_PASSES,
    NEW_ITEMS_AI_MAX_BUDGET_SECONDS,
    NEW_ITEMS_AI_MIN_BUDGET_SECONDS,
    NEW_ITEMS_AI_REPROBE_WAVES,
)
from custom_components.custom_component_monitor.sensor import (
    CustomComponentMonitorCoordinator,
)

from .test_new_items_scanner import make_entry, new_item

OK_RESULT = {"category": "Other", "country_scope": "global"}


@pytest.fixture
def coordinator(hass: HomeAssistant) -> CustomComponentMonitorCoordinator:
    """A coordinator with both registries in memory and nothing on disk."""
    coord = CustomComponentMonitorCoordinator(hass, make_entry())
    coord._ai_loaded = True
    coord._ai_store = None
    coord.new_items_registry._loaded = True
    coord.new_items_registry._store = None
    return coord


def seed(coord: CustomComponentMonitorCoordinator, count: int) -> list[dict]:
    items = [new_item(f"owner/repo-{i}") for i in range(count)]
    coord.data = {"new_items": {"items": items, "hidden": []}}
    return items


class FakeClock:
    """A monotonic clock that only moves when told to.

    The loop reads ``time.monotonic()`` for the budget check and for every
    wave's duration, so controlling it is the only way to test pacing without
    the suite actually waiting.
    """

    def __init__(self, step: float = 0.0) -> None:
        self.now = 1000.0
        self.step = step

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def tick(self) -> None:
        self.now += self.step


# ---------------------------------------------------------------------------
# The time budget
# ---------------------------------------------------------------------------


async def test_the_budget_stops_the_pass_and_leaves_the_rest_remaining(
    coordinator: CustomComponentMonitorCoordinator,
):
    items = seed(coordinator, 40)
    clock = FakeClock()

    async def slow(*args, **kwargs):
        # Three seconds of model time per call, charged to the fake clock.
        clock.advance(3.0)
        return OK_RESULT

    with patch(
        "custom_components.custom_component_monitor.sensor.time.monotonic", clock
    ), patch.object(coordinator, "_async_ai_call", AsyncMock(side_effect=slow)):
        counts = await coordinator.async_generate_new_item_categories(max_seconds=10)

    assert counts["generated"] < 40
    assert counts["remaining"] == 40 - counts["generated"]
    assert counts["remaining"] > 0
    # Everything it did get through is recorded, not thrown away.
    done = [item for item in items if not item["ai_pending"]]
    assert len(done) == counts["generated"]


async def test_the_first_wave_always_runs_even_when_slower_than_the_whole_budget(
    coordinator: CustomComponentMonitorCoordinator,
):
    """The reason the budget is checked between waves and never mid-call.

    A model slower than the entire budget would otherwise be cut off with
    nothing to show, every pass, forever — the backlog would never move. One
    wave of progress per pass is slow, but it finishes.
    """
    seed(coordinator, 10)
    clock = FakeClock()

    async def glacial(*args, **kwargs):
        clock.advance(600.0)  # ten minutes per item, against a 5 s budget
        return OK_RESULT

    with patch(
        "custom_components.custom_component_monitor.sensor.time.monotonic", clock
    ), patch.object(coordinator, "_async_ai_call", AsyncMock(side_effect=glacial)):
        counts = await coordinator.async_generate_new_item_categories(max_seconds=5)

    assert counts["generated"] == NEW_ITEMS_AI_CONCURRENCY_START
    assert counts["remaining"] == 10 - NEW_ITEMS_AI_CONCURRENCY_START


async def test_the_budget_option_is_clamped_rather_than_trusted(
    hass: HomeAssistant,
):
    """A hand-edited config entry must not be able to stall the loop."""
    low = CustomComponentMonitorCoordinator(hass, make_entry(ai_budget_seconds=1))
    high = CustomComponentMonitorCoordinator(
        hass, make_entry(ai_budget_seconds=99999)
    )

    assert low._new_items_settings()["ai_budget_seconds"] == (
        NEW_ITEMS_AI_MIN_BUDGET_SECONDS
    )
    assert high._new_items_settings()["ai_budget_seconds"] == (
        NEW_ITEMS_AI_MAX_BUDGET_SECONDS
    )


async def test_an_empty_or_zero_budget_option_means_unset_not_zero(
    hass: HomeAssistant,
):
    """Zero is how an un-filled form arrives, not a request for no budget."""
    for value in (0, None, ""):
        coord = CustomComponentMonitorCoordinator(
            hass, make_entry(ai_budget_seconds=value)
        )
        assert coord._new_items_settings()["ai_budget_seconds"] == (
            NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS
        )


async def test_a_nonsense_budget_option_falls_back_to_the_default(
    hass: HomeAssistant,
):
    coord = CustomComponentMonitorCoordinator(
        hass, make_entry(ai_budget_seconds="twenty")
    )

    budget = coord._new_items_settings()["ai_budget_seconds"]
    assert NEW_ITEMS_AI_MIN_BUDGET_SECONDS <= budget <= NEW_ITEMS_AI_MAX_BUDGET_SECONDS


# ---------------------------------------------------------------------------
# Wave sizing
# ---------------------------------------------------------------------------


def test_the_wave_explores_every_size_it_has_never_measured(
    coordinator: CustomComponentMonitorCoordinator,
):
    """An unmeasured size is always worth one wave.

    Never exploring upwards is how a 16 GB GPU ends up sorting two items at a
    time forever; never exploring downwards means one call at a time is never
    measured at all, so a backend that is genuinely fastest that way sits on
    the start size. Either way the cost of being wrong is one wave.
    """
    coordinator._new_items_ai_wave = 2
    coordinator._adapt_new_items_ai_wave(sent=2, ok=2, elapsed=1.0)  # 2/s
    # The floor first — it is below the start size, so nothing else would ever
    # go there on its own.
    assert coordinator._new_items_ai_wave == 1

    # One item at a time is slower here, so it goes back up and keeps climbing.
    coordinator._adapt_new_items_ai_wave(sent=1, ok=1, elapsed=1.0)  # 1/s
    assert coordinator._new_items_ai_wave == 2

    coordinator._adapt_new_items_ai_wave(sent=2, ok=2, elapsed=1.0)
    assert coordinator._new_items_ai_wave == 4

    coordinator._adapt_new_items_ai_wave(sent=4, ok=4, elapsed=1.0)  # 4/s
    assert coordinator._new_items_ai_wave == 8


def test_one_call_at_a_time_wins_on_a_backend_that_serialises(
    coordinator: CustomComponentMonitorCoordinator,
):
    """The reason the floor is probed at all.

    A backend that handles one request well and queues the rest never *fails*,
    so nothing halves the wave — the only thing that can find the truth is
    having measured a single call.
    """
    coordinator._new_items_ai_wave = 2
    # Both neighbours already measured, so there is nothing left to explore and
    # the only question is which of the three is worth most.
    coordinator._new_items_ai_wave_rates = {1: 4.0, 4: 0.5}  # 4/s one at a time

    coordinator._adapt_new_items_ai_wave(sent=2, ok=2, elapsed=2.0)  # 1/s

    assert coordinator._new_items_ai_wave == 1


def test_the_wave_holds_once_throughput_flattens(
    coordinator: CustomComponentMonitorCoordinator,
):
    """The hardware's ceiling. Holding here is what stops it oscillating."""
    coordinator._new_items_ai_wave = 4
    coordinator._new_items_ai_wave_rates = {2: 2.0}  # floor already probed
    coordinator._adapt_new_items_ai_wave(sent=4, ok=4, elapsed=1.0)  # 4/s
    assert coordinator._new_items_ai_wave == 8

    # Twice the concurrency, twice the latency: exactly the same throughput.
    # No clear margin either way, so there is nothing to be gained by moving.
    coordinator._adapt_new_items_ai_wave(sent=8, ok=8, elapsed=2.0)  # 4/s again
    assert coordinator._new_items_ai_wave == 8


def test_the_wave_shrinks_when_a_smaller_size_measured_better(
    coordinator: CustomComponentMonitorCoordinator,
):
    """A backend that serialises internally, or throttles concurrent callers.

    The comparison is against what the *smaller size actually achieved*, not
    against the best figure ever seen — see the recovery test below for why
    that distinction is load-bearing.
    """
    coordinator._new_items_ai_wave = 8
    coordinator._new_items_ai_wave_rates = {4: 4.0}

    coordinator._adapt_new_items_ai_wave(sent=8, ok=8, elapsed=8.0)  # 1/s

    assert coordinator._new_items_ai_wave == 4


def test_any_failure_in_a_wave_halves_it(
    coordinator: CustomComponentMonitorCoordinator,
):
    """A refused concurrent call is not a speed signal — back off hard."""
    coordinator._new_items_ai_wave = 8
    coordinator._adapt_new_items_ai_wave(sent=8, ok=7, elapsed=0.1)

    assert coordinator._new_items_ai_wave == 4
    # Recorded as worth nothing rather than forgotten: a forgotten size reads
    # as "never tried" and would be explored again on the very next wave.
    assert coordinator._new_items_ai_wave_rates[8] == 0.0


def test_the_wave_recovers_after_a_failure_instead_of_pinning_at_one(
    coordinator: CustomComponentMonitorCoordinator,
):
    """The bug this controller was rewritten to remove.

    With a single best-ever throughput bar, one transient failure at the top
    of the ramp halved the wave and left the bar at the top size's rate. Every
    smaller — and therefore legitimately slower — wave then read as a
    regression, so the size walked down to one item at a time and stayed there
    for the life of the coordinator: exactly the hourly trickle of v1.15.0,
    reinstated by a single flaky call.
    """
    coordinator._new_items_ai_wave = 2
    coordinator._new_items_ai_wave_rates = {1: 1.0}  # floor already probed
    sizes: list[int] = []

    def wave(ok_offset: int = 0) -> None:
        sent = coordinator._new_items_ai_wave
        # Throughput is proportional to the wave size: the hardware really can
        # use the concurrency, so the right answer is always the ceiling.
        coordinator._adapt_new_items_ai_wave(
            sent=sent, ok=sent - ok_offset, elapsed=1.0
        )
        sizes.append(coordinator._new_items_ai_wave)

    wave()  # 2 -> 4
    wave()  # 4 -> 8
    assert coordinator._new_items_ai_wave == NEW_ITEMS_AI_CONCURRENCY_MAX

    wave(ok_offset=1)  # one call in the wave fails
    assert coordinator._new_items_ai_wave < NEW_ITEMS_AI_CONCURRENCY_MAX

    # Nothing fails from here on. The size must climb back by itself.
    for _ in range(NEW_ITEMS_AI_REPROBE_WAVES + 2):
        wave()

    assert coordinator._new_items_ai_wave == NEW_ITEMS_AI_CONCURRENCY_MAX
    assert 1 not in sizes, f"collapsed to one item at a time: {sizes}"


def test_a_converged_size_is_periodically_re_probed(
    coordinator: CustomComponentMonitorCoordinator,
):
    """Measurements go stale — the GPU was busy, and then it wasn't.

    Without this, a number taken once pins the wave size for as long as Home
    Assistant stays up.
    """
    coordinator._new_items_ai_wave = 4
    coordinator._new_items_ai_wave_rates = {2: 1.0, 4: 4.0, 8: 0.0}

    for _ in range(NEW_ITEMS_AI_REPROBE_WAVES):
        coordinator._adapt_new_items_ai_wave(sent=4, ok=4, elapsed=1.0)
        assert coordinator._new_items_ai_wave == 4

    # The neighbours' stale records have been dropped, so the next wave
    # explores upwards again rather than trusting them forever.
    assert 8 not in coordinator._new_items_ai_wave_rates
    coordinator._adapt_new_items_ai_wave(sent=4, ok=4, elapsed=1.0)
    assert coordinator._new_items_ai_wave == 8


def test_the_wave_never_goes_below_one_or_above_the_ceiling(
    coordinator: CustomComponentMonitorCoordinator,
):
    coordinator._new_items_ai_wave = 1
    coordinator._adapt_new_items_ai_wave(sent=1, ok=0, elapsed=0.1)
    assert coordinator._new_items_ai_wave == 1

    coordinator._new_items_ai_wave = NEW_ITEMS_AI_CONCURRENCY_MAX
    # Half the ceiling already measured, and slower, so there is nothing left
    # to explore in either direction.
    coordinator._new_items_ai_wave_rates = {NEW_ITEMS_AI_CONCURRENCY_MAX // 2: 1.0}
    coordinator._adapt_new_items_ai_wave(
        sent=NEW_ITEMS_AI_CONCURRENCY_MAX, ok=NEW_ITEMS_AI_CONCURRENCY_MAX, elapsed=0.1
    )
    assert coordinator._new_items_ai_wave == NEW_ITEMS_AI_CONCURRENCY_MAX


def test_a_truncated_tail_wave_is_not_filed_as_the_configured_size(
    coordinator: CustomComponentMonitorCoordinator,
):
    """Most backlogs don't divide exactly by the wave size.

    The last wave of a pass is routinely smaller than the size that was asked
    for. Recording that measurement under the configured size would fill the
    table with figures taken at a concurrency the key doesn't describe — and
    the table is the only thing the grow/shrink decisions stand on.
    """
    coordinator._new_items_ai_wave = 8
    coordinator._new_items_ai_wave_rates = {4: 4.0}

    # Only two items were left, so two is what actually ran.
    coordinator._adapt_new_items_ai_wave(sent=2, ok=2, elapsed=1.0)

    assert coordinator._new_items_ai_wave_rates[2] == 2.0
    assert 8 not in coordinator._new_items_ai_wave_rates
    # And it says nothing about eight, so it must not retune it.
    assert coordinator._new_items_ai_wave == 8


def test_a_failed_size_is_measured_afresh_rather_than_averaged_with_its_zero(
    coordinator: CustomComponentMonitorCoordinator,
):
    """The zero is a sentinel, not a throughput reading.

    Blending into it would halve the recorded rate for a size that has just
    proved it works, and that wrong number is what later decisions compare
    against.
    """
    coordinator._new_items_ai_wave = 8
    coordinator._adapt_new_items_ai_wave(sent=8, ok=7, elapsed=1.0)  # fails
    assert coordinator._new_items_ai_wave_rates[8] == 0.0

    coordinator._new_items_ai_wave = 8
    coordinator._adapt_new_items_ai_wave(sent=8, ok=8, elapsed=1.0)  # 8/s

    assert coordinator._new_items_ai_wave_rates[8] == 8.0


async def test_a_wave_really_does_run_its_calls_concurrently(
    coordinator: CustomComponentMonitorCoordinator,
):
    """Not a style point: serial calls are the whole bug being fixed.

    Each mocked call records how many are in flight when it starts. If the
    wave ran serially that peak would be 1 and the hour-long backlog would
    survive the rewrite intact.
    """
    seed(coordinator, 8)
    in_flight = 0
    peak = 0

    async def concurrent(*args, **kwargs):
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return OK_RESULT

    with patch.object(coordinator, "_async_ai_call", AsyncMock(side_effect=concurrent)):
        await coordinator.async_generate_new_item_categories()

    assert peak >= NEW_ITEMS_AI_CONCURRENCY_START


# ---------------------------------------------------------------------------
# Giving up rather than spinning
# ---------------------------------------------------------------------------


async def test_a_pass_gives_up_after_the_allowed_run_of_failed_waves(
    coordinator: CustomComponentMonitorCoordinator,
):
    """A dead AI entity must not be hammered for the whole budget."""
    seed(coordinator, 50)
    call = AsyncMock(side_effect=RuntimeError("provider down"))

    with patch.object(coordinator, "_async_ai_call", call):
        counts = await coordinator.async_generate_new_item_categories()

    assert counts["generated"] == 0
    assert counts["failed"] > 0
    # Three waves, each halving: 2 + 1 + 1 with the default settings. The
    # exact figure is not the point — that it is a handful, not fifty, is.
    assert call.await_count <= NEW_ITEMS_AI_CONCURRENCY_START * (
        NEW_ITEMS_AI_MAX_FAILED_WAVES + 1
    )
    assert counts["remaining"] == 50


# ---------------------------------------------------------------------------
# The catch-up chain
# ---------------------------------------------------------------------------


async def test_the_chain_runs_passes_until_the_backlog_is_clear(
    coordinator: CustomComponentMonitorCoordinator,
):
    seed(coordinator, 12)
    passes = 0
    real = coordinator.async_generate_new_item_categories

    async def counting_pass(**kwargs):
        nonlocal passes
        passes += 1
        # Four items per pass, as if the budget ran out there.
        return await real(limit=4, **kwargs)

    with patch.object(
        coordinator, "_async_ai_call", AsyncMock(return_value=OK_RESULT)
    ), patch.object(
        coordinator, "async_generate_new_item_categories", counting_pass
    ), patch(
        "custom_components.custom_component_monitor.sensor.asyncio.sleep", AsyncMock()
    ):
        await coordinator._async_deferred_new_items_ai_run()

    assert passes == 3
    assert all(not item["ai_pending"] for item in coordinator.data["new_items"]["items"])


async def test_the_chain_stops_when_a_pass_makes_no_progress(
    coordinator: CustomComponentMonitorCoordinator,
):
    """``generated == 0`` ends the chain.

    The next hourly scan is the back-off, so a broken AI entity costs one
    short pass an hour rather than a tight loop — and no extra timer.
    """
    seed(coordinator, 20)
    passes = 0
    real = coordinator.async_generate_new_item_categories

    async def counting_pass(**kwargs):
        nonlocal passes
        passes += 1
        return await real(**kwargs)

    with patch.object(
        coordinator, "_async_ai_call", AsyncMock(side_effect=RuntimeError("down"))
    ), patch.object(
        coordinator, "async_generate_new_item_categories", counting_pass
    ), patch(
        "custom_components.custom_component_monitor.sensor.asyncio.sleep", AsyncMock()
    ):
        await coordinator._async_deferred_new_items_ai_run()

    assert passes == 1


async def test_the_chain_is_hard_capped(
    coordinator: CustomComponentMonitorCoordinator,
):
    """A backstop against a logic error, not a pacing control."""
    seed(coordinator, 1)
    passes = 0

    async def never_finishes(**kwargs):
        nonlocal passes
        passes += 1
        return {"generated": 1, "failed": 0, "skipped": 0, "remaining": 1}

    with patch.object(
        coordinator, "async_generate_new_item_categories", never_finishes
    ), patch(
        "custom_components.custom_component_monitor.sensor.asyncio.sleep", AsyncMock()
    ):
        await coordinator._async_deferred_new_items_ai_run()

    assert passes == NEW_ITEMS_AI_MAX_PASSES


async def test_the_sorting_flag_stays_set_across_the_whole_chain(
    coordinator: CustomComponentMonitorCoordinator,
):
    """Otherwise the card flickers "done" between every pass."""
    seed(coordinator, 12)
    seen: list[bool] = []
    real = coordinator.async_generate_new_item_categories

    async def recording_pass(**kwargs):
        counts = await real(limit=4, **kwargs)
        # Read *after* the pass has finished and restored the flag.
        seen.append(coordinator.new_items_ai_in_progress)
        return counts

    with patch.object(
        coordinator, "_async_ai_call", AsyncMock(return_value=OK_RESULT)
    ), patch.object(
        coordinator, "async_generate_new_item_categories", recording_pass
    ), patch(
        "custom_components.custom_component_monitor.sensor.asyncio.sleep", AsyncMock()
    ):
        await coordinator._async_deferred_new_items_ai_run()

    assert seen and all(seen), "in_progress dropped between passes"
    assert coordinator.new_items_ai_in_progress is False


# ---------------------------------------------------------------------------
# Re-sorting everything by hand, while the chain is running
# ---------------------------------------------------------------------------


async def test_a_forced_resort_waits_for_the_pass_it_lands_in(
    coordinator: CustomComponentMonitorCoordinator,
):
    """The catch-up chain now runs for minutes, so a forced re-sort meets it.

    Flipping every item back to pending from outside the pass would discard
    work the pass had already finished *and* wipe its fresh records, while the
    pass's own cursor had moved past those items — so they would end up
    pending with nothing to fill them until the next hourly scan. Taking the
    same lock the pass holds makes the forced call queue behind it instead.
    """
    items = seed(coordinator, 4)
    for item in items:
        item["ai_pending"] = False

    async with coordinator._new_items_ai_lock:
        reset = asyncio.create_task(coordinator.async_reset_new_items_ai_cache())
        await asyncio.sleep(0)

        assert not reset.done(), "reset ran while a pass held the lock"
        assert all(not item["ai_pending"] for item in items)

    assert await reset == 4
    assert all(item["ai_pending"] for item in items)


# ---------------------------------------------------------------------------
# What the sensor reports
# ---------------------------------------------------------------------------


def test_the_rate_and_eta_are_null_before_anything_has_been_measured(
    coordinator: CustomComponentMonitorCoordinator,
):
    assert coordinator.new_items_ai_rate_per_minute is None
    assert coordinator.new_items_ai_eta_seconds(100) is None


def test_the_rate_is_reported_per_minute_after_a_wave(
    coordinator: CustomComponentMonitorCoordinator,
):
    coordinator._record_new_items_ai_rate(sent=4, ok=4, elapsed=2.0)  # 2/s

    assert coordinator.new_items_ai_rate_per_minute == 120.0


def test_a_failed_wave_does_not_poison_the_rate(
    coordinator: CustomComponentMonitorCoordinator,
):
    coordinator._record_new_items_ai_rate(sent=2, ok=0, elapsed=30.0)

    assert coordinator._new_items_ai_rate is None


def test_a_part_failed_wave_does_not_poison_the_rate_either(
    coordinator: CustomComponentMonitorCoordinator,
):
    """Half a wave answering is a measurement of the failure, not the hardware.

    A wave with one dead call spends most of its time waiting on a call that
    never answers, so its items-per-second describes the outage. Folding that
    into the reported rate would drop the figure on the card — and the ETA
    built on it — on one flaky wave.
    """
    coordinator._record_new_items_ai_rate(sent=8, ok=8, elapsed=2.0)  # 4/s
    good = coordinator._new_items_ai_rate

    coordinator._record_new_items_ai_rate(sent=8, ok=4, elapsed=30.0)  # 0.13/s

    assert coordinator._new_items_ai_rate == good


def test_the_eta_allows_for_the_gap_between_passes(
    coordinator: CustomComponentMonitorCoordinator,
):
    """A pure inference-rate ETA would promise a finish the clock never keeps.

    At 2 items/second, 120 items is 60 s of model time — but the chain also
    sleeps between passes, so the honest answer is longer than that.
    """
    coordinator._new_items_ai_rate = 2.0

    eta = coordinator.new_items_ai_eta_seconds(120)

    assert eta is not None
    assert eta > 60
    assert eta < 120


def test_the_eta_prefers_the_measured_duty_cycle_over_the_budget(
    coordinator: CustomComponentMonitorCoordinator,
):
    """The budget and the real work phase disagree where it matters.

    A backend slower than the whole budget runs one long wave and overruns it,
    so its work phase is far longer than the budget says and its real duty
    cycle is close to 1. Assuming the budget there quotes a backlog as taking
    longer than it will.
    """
    coordinator._new_items_ai_rate = 2.0
    assumed = coordinator.new_items_ai_eta_seconds(120)

    coordinator._new_items_ai_duty = 1.0  # a pass that never idled
    measured = coordinator.new_items_ai_eta_seconds(120)

    assert measured == 60  # 120 items at 2/s, no gap to allow for
    assert assumed is not None and assumed > measured


def test_the_eta_is_null_with_nothing_pending(
    coordinator: CustomComponentMonitorCoordinator,
):
    coordinator._new_items_ai_rate = 2.0

    assert coordinator.new_items_ai_eta_seconds(0) is None
