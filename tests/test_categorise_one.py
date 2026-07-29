"""Per-update AI calls: how many, and what a failure costs (#91).

The ai_task path used to make two generations per update — a structured-output
call and a plain-text fallback — every single time, for backends that can't do
structured output. It now probes once and remembers.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from .test_ai_cache import update_item

EMPTY_MAP = {"by_name": {}, "by_version": {}}


@pytest.fixture
def no_notes(coordinator):
    """Release-note lookup stubbed out so prompts stay deterministic."""
    with patch.object(coordinator, "_async_release_notes", AsyncMock(return_value="")):
        yield coordinator


async def test_structured_success_costs_one_call(no_notes):
    call = AsyncMock(return_value={"categories": ["Bug fixes"], "summary": "Fixes."})

    with patch.object(no_notes, "_async_ai_call", call):
        result = await no_notes._async_categorise_one(
            update_item(), "ai_task.test", EMPTY_MAP
        )

    assert call.await_count == 1
    assert result["summary"] == "Fixes."
    assert no_notes._ai_structured_ok is True


async def test_a_backend_without_structured_output_is_probed_only_once(no_notes):
    """Two calls for the first update, one for every update after it."""
    call = AsyncMock(
        side_effect=[
            RuntimeError("structure not supported"),
            '{"categories": ["Bug fixes"], "summary": "Fixes."}',
            '{"categories": ["New features"], "summary": "Adds things."}',
        ]
    )

    with patch.object(no_notes, "_async_ai_call", call):
        first = await no_notes._async_categorise_one(
            update_item(), "ai_task.test", EMPTY_MAP
        )
        second = await no_notes._async_categorise_one(
            update_item(name="Kiosk Mode", repo="nielsfaber/kiosk-mode"),
            "ai_task.test",
            EMPTY_MAP,
        )

    assert no_notes._ai_structured_ok is False
    assert call.await_count == 3  # 2 for the probe, 1 for the second update
    assert first["summary"] == "Fixes."
    assert second["summary"] == "Adds things."


async def test_a_timeout_does_not_disable_structured_output(no_notes):
    """One slow response says nothing about the backend's capabilities."""
    call = AsyncMock(
        side_effect=[
            TimeoutError(),
            '{"categories": [], "summary": "From the fallback."}',
            {"categories": ["Bug fixes"], "summary": "Structured again."},
        ]
    )

    with patch.object(no_notes, "_async_ai_call", call):
        await no_notes._async_categorise_one(update_item(), "ai_task.test", EMPTY_MAP)
        second = await no_notes._async_categorise_one(
            update_item(name="Kiosk Mode", repo="nielsfaber/kiosk-mode"),
            "ai_task.test",
            EMPTY_MAP,
        )

    assert no_notes._ai_structured_ok is not False
    assert second["summary"] == "Structured again."


async def test_categories_from_the_structured_call_survive_the_fallback(no_notes):
    """A structured reply with categories but no summary must not be wasted."""
    call = AsyncMock(
        side_effect=[
            {"categories": ["Breaking changes"], "summary": ""},
            '{"summary": "Renames an option."}',
        ]
    )

    with patch.object(no_notes, "_async_ai_call", call):
        result = await no_notes._async_categorise_one(
            update_item(), "ai_task.test", EMPTY_MAP
        )

    assert result["summary"] == "Renames an option."
    assert "Breaking changes" in result["categories"]


async def test_both_calls_failing_returns_none(no_notes):
    call = AsyncMock(side_effect=RuntimeError("provider down"))

    with patch.object(no_notes, "_async_ai_call", call):
        result = await no_notes._async_categorise_one(
            update_item(), "ai_task.test", EMPTY_MAP
        )

    assert result is None


async def test_conversation_agent_path_makes_a_single_call(no_notes):
    call = AsyncMock(return_value='{"categories": ["Bug fixes"], "summary": "Fixes."}')

    with patch.object(no_notes, "_async_conversation_call", call):
        result = await no_notes._async_categorise_one(
            update_item(), "conversation.test", EMPTY_MAP
        )

    assert call.await_count == 1
    assert result["summary"] == "Fixes."


async def test_release_notes_use_the_resolved_entity(coordinator):
    """The reconcile now hands us the entity, so don't guess from the name."""
    notes = AsyncMock(return_value="### Bug Fixes\n* fix: a crash")
    call = AsyncMock(return_value={"categories": [], "summary": "Fixes."})

    with (
        patch.object(coordinator, "_async_release_notes", notes),
        patch.object(coordinator, "_async_ai_call", call),
    ):
        result = await coordinator._async_categorise_one(
            update_item(entity_id="update.powercalc_update"), "ai_task.test", EMPTY_MAP
        )

    notes.assert_awaited_once_with("update.powercalc_update")
    # Categories detected from the notes are unioned in even when the model
    # returns none.
    assert "Bug fixes" in result["categories"]
