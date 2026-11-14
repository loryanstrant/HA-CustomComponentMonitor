"""Every scanned update carries its Home Assistant requirement (#111).

Two paths produce update rows and both have to answer the question, because a
row that simply omits the requirement reads as "checked and fine":

* the storage snapshot (``scan_updates``), where ``repository_manifest`` is
  right there on the repository, and
* the live-entity reconciliation, where HACS has surfaced an update the
  snapshot hasn't caught up with yet — the row is built from the entity, so the
  manifest has to be looked up separately.

⚠ Every test here hands the scanner a ``repository_manifest`` and an
``available_version`` that describe the same release, which is exactly the
assumption production violates — see ``test_verify_update_requirements.py``
(#116). What these tests pin is that ``scan_updates`` stays offline and gives
every row *an* answer; the answer itself is corrected afterwards by
``ComponentScanner.async_verify_update_requirements``.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant

from custom_components.custom_component_monitor.const import ATTR_HA_REQUIREMENT
from custom_components.custom_component_monitor.sensor import ComponentScanner

# Older and newer than whatever Home Assistant the suite runs on, so these
# tests keep meaning the same thing in 2027.
OLDER = "2000.1.0"
NEWER = "2999.12.0"


@pytest.fixture
def scanner(hass: HomeAssistant) -> ComponentScanner:
    return ComponentScanner(hass)


def repo(full_name: str, *, manifest=None, category: str = "integration", pending=True):
    entry = {
        "installed": True,
        "full_name": full_name,
        "category": category,
        "pending_update": pending,
        "current_version": "1.0.0",
        "available_version": "1.1.0",
        "name": full_name.split("/")[-1],
    }
    if manifest is not None:
        entry["repository_manifest"] = manifest
    return entry


async def scan(scanner: ComponentScanner, repos: dict):
    with patch.object(
        ComponentScanner, "_load_hacs_repositories", AsyncMock(return_value=repos)
    ):
        return await scanner.scan_updates()


async def test_snapshot_rows_state_met_blocked_and_unknown(scanner):
    result = await scan(
        scanner,
        {
            "1": repo("someone/needs-newer", manifest={"homeassistant": NEWER}),
            "2": repo("someone/happy", manifest={"homeassistant": OLDER}),
            "3": repo("someone/silent", manifest={}),
        },
    )

    by_name = {item["name"]: item[ATTR_HA_REQUIREMENT] for item in result["updates"]}
    assert by_name["needs-newer"]["state"] == "blocked"
    assert by_name["needs-newer"]["required"] == NEWER
    assert by_name["needs-newer"]["installed"] == HA_VERSION
    assert by_name["happy"]["state"] == "met"
    assert by_name["silent"]["state"] == "unknown"
    assert by_name["silent"]["reason"] == "not_declared"


async def test_the_scan_counts_blocked_and_unknown_for_the_sensor(scanner):
    """Small recorded scalars, so an automation needn't parse the row list."""
    result = await scan(
        scanner,
        {
            "1": repo("someone/needs-newer", manifest={"homeassistant": NEWER}),
            "2": repo("someone/also-newer", manifest={"homeassistant": NEWER}),
            "3": repo("someone/happy", manifest={"homeassistant": OLDER}),
            "4": repo("someone/silent", manifest={}),
        },
    )

    assert result["count"] == 4
    assert result["blocked"] == 2
    assert result["unknown_requirement"] == 1


async def test_a_missing_manifest_never_reads_as_compatible(scanner):
    result = await scan(scanner, {"1": repo("someone/no-manifest-at-all")})

    assert result["updates"][0][ATTR_HA_REQUIREMENT]["state"] == "unknown"
    assert result["unknown_requirement"] == 1


async def test_a_live_only_row_gets_the_requirement_too(scanner, hacs_update_entity):
    """The hole this closes: an update HACS surfaced before the next snapshot.

    Without the manifest index these rows were built purely from the entity,
    so every one of them would have claimed "requirement unknown" — including
    the blocked ones this feature exists to catch.
    """
    hacs_update_entity(
        "update.needs_newer_update",
        "on",
        friendly_name="Needs Newer Update",
        installed_version="1.0.0",
        latest_version="1.1.0",
        release_url="https://github.com/someone/needs-newer/releases/1.1.0",
    )
    repos = {"1": repo("someone/needs-newer", manifest={"homeassistant": NEWER}, pending=False)}

    result = scanner._reconcile_updates([], repos, scanner._live_hacs_updates())

    assert len(result) == 1
    assert result[0]["entity_id"] == "update.needs_newer_update"
    requirement = result[0][ATTR_HA_REQUIREMENT]
    assert requirement["state"] == "blocked"
    assert requirement["required"] == NEWER


async def test_a_live_only_row_for_an_unknown_repo_is_unknown(scanner, hacs_update_entity):
    hacs_update_entity(
        "update.mystery_update",
        "on",
        friendly_name="Mystery Update",
        installed_version="1.0.0",
        latest_version="1.1.0",
        release_url="https://github.com/someone/mystery/releases/1.1.0",
    )

    result = scanner._reconcile_updates([], {}, scanner._live_hacs_updates())

    assert result[0][ATTR_HA_REQUIREMENT]["state"] == "unknown"


async def test_the_manifest_index_only_covers_installed_repos(scanner):
    index = ComponentScanner._installed_manifest_index(
        {
            "1": repo("someone/installed", manifest={"homeassistant": OLDER}),
            "2": dict(
                repo("someone/removed", manifest={"homeassistant": NEWER}),
                installed=False,
            ),
            "3": {"installed": True, "full_name": "someone/no-manifest"},
            "4": "not a repo",
        }
    )

    assert index == {"someone/installed": {"homeassistant": OLDER}}
