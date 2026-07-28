"""Shared fixtures for the Custom Component Monitor test suite."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from homeassistant.core import HomeAssistant

from custom_components.custom_component_monitor.const import (
    CONF_AI_CATEGORIZATION_ENABLED,
    CONF_AI_TASK_ENTITY,
)
from custom_components.custom_component_monitor.sensor import (
    CustomComponentMonitorCoordinator,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load this custom integration in every test."""
    return


def make_entry(*, ai_enabled: bool = True, ai_entity: str = "ai_task.test") -> MagicMock:
    """A stand-in config entry carrying only the options the code reads."""
    entry = MagicMock()
    entry.options = {
        CONF_AI_CATEGORIZATION_ENABLED: ai_enabled,
        CONF_AI_TASK_ENTITY: ai_entity,
    }
    return entry


@pytest.fixture
def coordinator(hass: HomeAssistant) -> CustomComponentMonitorCoordinator:
    """A coordinator with AI enabled and an in-memory (never-written) store."""
    coord = CustomComponentMonitorCoordinator(hass, make_entry())
    coord._ai_loaded = True
    coord._ai_store = None  # _save_ai_cache() becomes a no-op
    return coord


@pytest.fixture
def no_ai_coordinator(hass: HomeAssistant) -> CustomComponentMonitorCoordinator:
    """A coordinator with AI switched off."""
    coord = CustomComponentMonitorCoordinator(hass, make_entry(ai_enabled=False))
    coord._ai_loaded = True
    return coord


@pytest.fixture
def hacs_update_entity(hass: HomeAssistant):
    """Register ``update.*`` states that look like they came from HACS.

    Returns a callable ``(entity_id, state, **attributes)``. The entity
    registry is patched rather than populated because the scanner only cares
    that ``entry.platform == "hacs"``.
    """
    hacs_entities: set[str] = set()

    def _add(entity_id: str, state: str, **attributes) -> None:
        hacs_entities.add(entity_id)
        hass.states.async_set(entity_id, state, attributes)

    def _registry_get(entity_id: str):
        if entity_id not in hacs_entities:
            return None
        entry = MagicMock()
        entry.platform = "hacs"
        return entry

    registry = MagicMock()
    registry.async_get.side_effect = _registry_get
    with patch(
        "custom_components.custom_component_monitor.sensor.er.async_get",
        return_value=registry,
    ):
        yield _add
