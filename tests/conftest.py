"""Shared fixtures for the Custom Component Monitor test suite."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pycares
import pytest

from homeassistant.core import HomeAssistant

from custom_components.custom_component_monitor.const import (
    CONF_AI_CATEGORIZATION_ENABLED,
    CONF_AI_TASK_ENTITY,
)
from custom_components.custom_component_monitor.install_dates import (
    InstallDateRegistry,
)
from custom_components.custom_component_monitor.sensor import (
    ComponentScanner,
    CustomComponentMonitorCoordinator,
)

# aiohttp resolves DNS through pycares, which starts one daemon thread for the
# whole process the first time it tears a channel down. Home Assistant's test
# teardown fails any test that leaves a new thread behind, so whichever test
# happens to open the first client session gets blamed for a process-wide
# one-off. Start it here, before the first test takes its thread census.
#
# Guarded because this is pycares' own private machinery and may be renamed or
# removed by a version bump. If that happens the right outcome is one test
# failing on a lingering thread — not the whole suite failing to collect.
_pycares_shutdown = getattr(pycares, "_shutdown_manager", None)
if _pycares_shutdown is not None and hasattr(_pycares_shutdown, "start"):
    _pycares_shutdown.start()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Let Home Assistant load this custom integration in every test."""
    return


@pytest.fixture(autouse=True)
def offline_target_manifests(request):
    """No test reaches GitHub unless it has said how it wants to be answered.

    Verifying a pending update's requirement reads the offered release's own
    ``hacs.json`` over the network (#115), and that now happens on every
    coordinator refresh — so without this, most of this suite would quietly
    make real requests and its results would depend on GitHub being up.

    A test that asks for ``aioclient_mock`` — directly, or through another
    fixture that does — is opting in: it gets the real method, and the mocker
    fails loudly on any URL it hasn't been told about.
    Every other test gets a fetch that finds nothing, which the production
    code already has to handle.
    """
    if "aioclient_mock" in request.fixturenames:
        yield
        return
    with patch.object(
        ComponentScanner, "_fetch_target_manifests", AsyncMock(return_value={})
    ):
        yield


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
def registry(hass: HomeAssistant) -> InstallDateRegistry:
    """A loaded install-date registry with an in-memory (never-written) store."""
    reg = InstallDateRegistry(hass)
    reg._loaded = True
    reg._store = None  # end_scan()'s save becomes a no-op
    return reg


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
