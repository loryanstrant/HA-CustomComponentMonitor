"""Reconciling the HACS storage snapshot against the live update entities (#91).

``.storage/hacs.repositories`` is a snapshot HACS writes periodically and it
lags reality in both directions — it misses updates HACS has already surfaced
as an entity, and keeps listing components the user updated hours ago. The
first kind could never be summarised at all, because the AI enrichment only
ever saw the snapshot.
"""
from __future__ import annotations

import pytest

from homeassistant.core import HomeAssistant

from custom_components.custom_component_monitor.sensor import ComponentScanner


def storage_item(name: str, repo: str, current: str, available: str, type_: str = "Integration"):
    """An entry shaped like the ones ``scan_updates`` builds from storage."""
    return {
        "name": name,
        "type": type_,
        "repository": f"https://github.com/{repo}",
        "current_version": current,
        "available_version": available,
    }


HACS_REPOS = {
    "1": {
        "installed": True,
        "full_name": "ganhammar/hass-mcp-server",
        "category": "integration",
    },
    "2": {
        "installed": True,
        "full_name": "bramstroker/homeassistant-powercalc",
        "category": "integration",
    },
    "3": {"installed": True, "full_name": "nielsfaber/kiosk-mode", "category": "plugin"},
}


@pytest.fixture
def scanner(hass: HomeAssistant) -> ComponentScanner:
    return ComponentScanner(hass)


def test_drops_updates_the_entity_says_are_done(scanner, hacs_update_entity):
    """A snapshot row contradicted by an `off` entity is a phantom."""
    hacs_update_entity(
        "update.kiosk_mode_update",
        "off",
        friendly_name="Kiosk Mode Update",
        installed_version="v14.0.2",
        latest_version="v14.0.2",
        release_url="https://github.com/nielsfaber/kiosk-mode/releases/v14.0.2",
    )
    updates = [storage_item("Kiosk Mode", "nielsfaber/kiosk-mode", "v14.0.1", "v14.0.2")]

    result = scanner._reconcile_updates(
        updates, HACS_REPOS, scanner._live_hacs_updates()
    )

    assert result == []


def test_adds_updates_the_snapshot_missed(scanner, hacs_update_entity):
    """The MCP Server case: entity `on`, snapshot still says up to date."""
    hacs_update_entity(
        "update.mcp_server_http_transport_update",
        "on",
        friendly_name="MCP Server (HTTP Transport) Update",
        installed_version="v1.12.0",
        latest_version="v1.13.0",
        release_url="https://github.com/ganhammar/hass-mcp-server/releases/v1.13.0",
    )

    result = scanner._reconcile_updates([], HACS_REPOS, scanner._live_hacs_updates())

    assert len(result) == 1
    added = result[0]
    assert added["name"] == "MCP Server (HTTP Transport)"
    assert added["entity_id"] == "update.mcp_server_http_transport_update"
    assert added["current_version"] == "v1.12.0"
    assert added["available_version"] == "v1.13.0"
    assert added["repository"] == "https://github.com/ganhammar/hass-mcp-server"
    # Type resolved from the installed repo's HACS category, not left as "Other".
    assert added["type"] == "Integration"


def test_added_update_falls_back_to_other_when_repo_unknown(scanner, hacs_update_entity):
    hacs_update_entity(
        "update.mystery_update",
        "on",
        friendly_name="Mystery Update",
        installed_version="1.0.0",
        latest_version="1.1.0",
        release_url="https://github.com/someone/mystery/releases/1.1.0",
    )

    result = scanner._reconcile_updates([], HACS_REPOS, scanner._live_hacs_updates())

    assert result[0]["type"] == "Other"


def test_versions_come_from_the_entity(scanner, hacs_update_entity):
    """The entity is authoritative when both sources have the update."""
    hacs_update_entity(
        "update.powercalc_update",
        "on",
        friendly_name="Powercalc Update",
        installed_version="v1.23.0",
        latest_version="v1.23.5",
        release_url="https://github.com/bramstroker/homeassistant-powercalc/releases/v1.23.5",
    )
    updates = [
        storage_item(
            "Powercalc", "bramstroker/homeassistant-powercalc", "v1.22.0", "v1.23.1"
        )
    ]

    result = scanner._reconcile_updates(
        updates, HACS_REPOS, scanner._live_hacs_updates()
    )

    assert result[0]["current_version"] == "v1.23.0"
    assert result[0]["available_version"] == "v1.23.5"
    assert result[0]["entity_id"] == "update.powercalc_update"


def test_matches_by_name_when_release_url_is_missing(scanner, hacs_update_entity):
    """HACS entities don't always carry a usable release_url."""
    hacs_update_entity(
        "update.powercalc_update",
        "on",
        friendly_name="Powercalc Update",
        installed_version="v1.23.0",
        latest_version="v1.23.1",
    )
    updates = [
        storage_item(
            "Powercalc", "bramstroker/homeassistant-powercalc", "v1.23.0", "v1.23.1"
        )
    ]

    result = scanner._reconcile_updates(
        updates, HACS_REPOS, scanner._live_hacs_updates()
    )

    assert result[0]["entity_id"] == "update.powercalc_update"


def test_matches_by_entity_id_when_the_friendly_name_drifted(scanner, hacs_update_entity):
    """The "Jokes Update" vs HACS "Dad Jokes" case, with no release_url."""
    hacs_update_entity(
        "update.dad_jokes_update",
        "on",
        friendly_name="Jokes Update",
        installed_version="v1.4.0",
        latest_version="v1.6.0",
    )
    updates = [storage_item("Dad Jokes", "loryanstrant/ha-jokes", "v1.4.0", "v1.6.0")]

    result = scanner._reconcile_updates(
        updates, HACS_REPOS, scanner._live_hacs_updates()
    )

    # One row, not two — the snapshot row matched the entity via its id.
    assert len(result) == 1
    assert result[0]["name"] == "Dad Jokes"
    assert result[0]["entity_id"] == "update.dad_jokes_update"


def test_a_shared_version_never_binds_unrelated_components(scanner, hacs_update_entity):
    """Matching on version alone would delete a real pending update."""
    hacs_update_entity(
        "update.unrelated_thing_update",
        "off",
        friendly_name="Unrelated Thing Update",
        installed_version="1.0.0",
        latest_version="1.0.0",
    )
    # Same version string, completely different component, and nothing else to
    # match on (no release_url on the entity, different name).
    updates = [storage_item("Some Card", "someone/some-card", "0.9.0", "1.0.0", "Theme")]

    result = scanner._reconcile_updates(
        updates, HACS_REPOS, scanner._live_hacs_updates()
    )

    assert [item["name"] for item in result] == ["Some Card"]
    assert "entity_id" not in result[0]


def test_no_live_entities_leaves_the_snapshot_alone(scanner):
    """At boot HACS may not have added its entities yet — don't flap to zero."""
    updates = [storage_item("Powercalc", "bramstroker/homeassistant-powercalc", "1", "2")]

    result = scanner._reconcile_updates(updates, HACS_REPOS, [])

    assert result == updates


def test_unavailable_entity_does_not_drop_a_pending_update(scanner, hacs_update_entity):
    """A flapping entity must not wipe a genuine pending update."""
    hacs_update_entity(
        "update.powercalc_update",
        "unavailable",
        friendly_name="Powercalc Update",
    )
    hacs_update_entity(
        "update.kiosk_mode_update",
        "on",
        friendly_name="Kiosk Mode Update",
        installed_version="v14.0.1",
        latest_version="v14.0.2",
    )
    updates = [
        storage_item(
            "Powercalc", "bramstroker/homeassistant-powercalc", "v1.23.0", "v1.23.1"
        )
    ]

    result = scanner._reconcile_updates(
        updates, HACS_REPOS, scanner._live_hacs_updates()
    )

    names = {item["name"] for item in result}
    assert "Powercalc" in names


def test_non_hacs_update_entities_are_ignored(scanner, hass: HomeAssistant):
    """Core/other-integration update entities are none of our business."""
    hass.states.async_set(
        "update.home_assistant_core_update",
        "on",
        {"friendly_name": "Home Assistant Core Update", "latest_version": "2026.8.0"},
    )

    # No entity-registry patch here, so nothing resolves to the hacs platform.
    assert scanner._live_hacs_updates() == []


def test_snapshot_row_with_no_entity_is_kept(scanner, hacs_update_entity):
    """Nothing contradicts it, so the snapshot keeps its say."""
    hacs_update_entity(
        "update.kiosk_mode_update",
        "on",
        friendly_name="Kiosk Mode Update",
        installed_version="v14.0.1",
        latest_version="v14.0.2",
    )
    updates = [
        storage_item("Some Theme", "someone/some-theme", "1.0.0", "1.1.0", "Theme")
    ]

    result = scanner._reconcile_updates(
        updates, HACS_REPOS, scanner._live_hacs_updates()
    )

    assert any(item["name"] == "Some Theme" for item in result)
