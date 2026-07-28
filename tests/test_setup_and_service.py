"""End-to-end setup: services register, and setup makes no AI calls (#91).

The first refresh used to run the whole serial AI loop inside the sensor
platform's ``async_setup_entry``. With a cold cache and a slow local model that
blew Home Assistant's platform-setup timeout, and because the cache was only
written after the loop finished, every summary generated up to that point was
lost — then regenerated on the next attempt.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.custom_component_monitor.const import (
    CONF_AI_CATEGORIZATION_ENABLED,
    CONF_AI_TASK_ENTITY,
    DOMAIN,
    SERVICE_GENERATE_SUMMARIES,
    SERVICE_UPDATE_ALL,
    SERVICE_UPDATE_AND_ACTION,
)

SCAN_RESULT = {
    "count": 1,
    "updates": [
        {
            "name": "Powercalc",
            "type": "Integration",
            "repository": "https://github.com/bramstroker/homeassistant-powercalc",
            "current_version": "v1.23.0",
            "available_version": "v1.23.1",
            "entity_id": "update.powercalc_update",
        }
    ],
}


@pytest.fixture
async def setup_integration(hass: HomeAssistant):
    """Set the integration up with AI enabled and one pending update."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Custom Component Monitor",
        data={},
        options={
            CONF_AI_CATEGORIZATION_ENABLED: True,
            CONF_AI_TASK_ENTITY: "ai_task.test",
        },
    )
    entry.add_to_hass(hass)

    categorise = AsyncMock(return_value={"categories": ["Bug fixes"], "summary": "Ok."})
    with (
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_updates",
            return_value=dict(SCAN_RESULT, updates=[dict(SCAN_RESULT["updates"][0])]),
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_all_hacs_components",
            return_value=[],
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_themes",
            return_value={"total": 0, "used": [], "unused": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_frontend",
            return_value={"total": 0, "used": [], "unused": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_integrations",
            return_value={"total": 0, "used": [], "unused": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor"
            ".CustomComponentMonitorCoordinator._async_categorise_one",
            categorise,
        ),
        # Don't let the background enrichment race the assertions.
        patch(
            "custom_components.custom_component_monitor.sensor"
            ".CustomComponentMonitorCoordinator._schedule_ai_run"
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield hass, entry, categorise


async def test_setup_makes_no_ai_calls(setup_integration):
    """A cold cache must not turn platform setup into an AI batch job."""
    _hass, _entry, categorise = setup_integration

    assert categorise.await_count == 0


async def test_setup_records_the_ai_backlog(setup_integration):
    hass, _entry, _categorise = setup_integration
    state = hass.states.get("sensor.hacs_updates")

    assert state is not None
    assert state.attributes["ai_enabled"] is True
    assert state.attributes["ai_pending"] == 1
    assert state.attributes["ai_in_progress"] is False


async def test_services_are_registered(setup_integration):
    hass, _entry, _categorise = setup_integration

    for service in (
        "scan_now",
        SERVICE_UPDATE_AND_ACTION,
        SERVICE_UPDATE_ALL,
        SERVICE_GENERATE_SUMMARIES,
    ):
        assert hass.services.has_service(DOMAIN, service), service


async def test_generate_summaries_service_returns_counts(setup_integration):
    hass, _entry, categorise = setup_integration

    response = await hass.services.async_call(
        DOMAIN,
        SERVICE_GENERATE_SUMMARIES,
        {"entity_ids": ["update.powercalc_update"]},
        blocking=True,
        return_response=True,
    )

    assert categorise.await_count == 1
    assert response == {"generated": 1, "failed": 0, "skipped": 0, "remaining": 0}
    assert hass.states.get("sensor.hacs_updates").attributes["ai_pending"] == 0


async def test_generate_summaries_service_is_idempotent(setup_integration):
    """Running it twice must not pay for the same summary twice."""
    hass, _entry, categorise = setup_integration

    await hass.services.async_call(
        DOMAIN, SERVICE_GENERATE_SUMMARIES, {}, blocking=True, return_response=True
    )
    response = await hass.services.async_call(
        DOMAIN, SERVICE_GENERATE_SUMMARIES, {}, blocking=True, return_response=True
    )

    assert categorise.await_count == 1
    assert response == {"generated": 0, "failed": 0, "skipped": 1, "remaining": 0}


async def test_generate_summaries_rejects_a_bad_max_items(setup_integration):
    hass, _entry, _categorise = setup_integration

    with pytest.raises(Exception):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_GENERATE_SUMMARIES,
            {"max_items": 0},
            blocking=True,
            return_response=True,
        )


async def test_generate_summaries_errors_when_ai_is_off(hass: HomeAssistant):
    """A silent no-op would look like the feature is simply broken."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, options={})
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_updates",
            return_value={"count": 0, "updates": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_all_hacs_components",
            return_value=[],
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_themes",
            return_value={"total": 0, "used": [], "unused": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_frontend",
            return_value={"total": 0, "used": [], "unused": []},
        ),
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_integrations",
            return_value={"total": 0, "used": [], "unused": []},
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with pytest.raises(HomeAssistantError, match="switched off"):
            await hass.services.async_call(
                DOMAIN,
                SERVICE_GENERATE_SUMMARIES,
                {},
                blocking=True,
                return_response=True,
            )
