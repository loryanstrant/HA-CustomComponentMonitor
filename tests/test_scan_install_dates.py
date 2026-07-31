"""The scanners publish install dates from the registry, not from mtime (#93).

The four scan methods each used to carry their own copy of the same
filesystem-timestamp arithmetic. They now share ``_install_info`` and pass the
result through the registry, so these tests are the guard that no call site was
missed by that de-duplication.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.custom_component_monitor.const import (
    DOMAIN,
    INSTALL_DATES_STORAGE_KEY,
    INSTALL_DATES_STORAGE_VERSION,
)
from custom_components.custom_component_monitor.install_dates import (
    InstallDateRegistry,
)
from custom_components.custom_component_monitor.sensor import ComponentScanner

FULL_NAME = "bramstroker/homeassistant-powercalc"

REPOS = {
    "1": {
        "full_name": FULL_NAME,
        "category": "integration",
        "installed": True,
        "domain": "powercalc",
        "manifest_name": "Powercalc",
        "version_installed": "v1.23.1",
    },
    "2": {
        "full_name": "someone/pretty-theme",
        "category": "theme",
        "installed": True,
        "manifest_name": "Pretty",
        "version_installed": "v2.0",
    },
    "3": {
        "full_name": "someone/fancy-card",
        "category": "plugin",
        "installed": True,
        "manifest_name": "Fancy Card",
        "version_installed": "v3.0",
    },
}


def days_ago_ts(days: float) -> float:
    return (dt_util.utcnow() - timedelta(days=days)).timestamp()


def build_scanner(hass, registry, timestamp):
    """A scanner wired to *registry*, with every disk read stubbed out."""
    scanner = ComponentScanner(hass, registry)
    patches = [
        patch.object(
            ComponentScanner, "_load_hacs_repositories", AsyncMock(return_value=REPOS)
        ),
        # The scanners resolve nothing on disk here; the timestamp is supplied
        # directly, exactly as _get_install_timestamp would have returned it.
        patch.object(
            ComponentScanner,
            "_resolve_install_path_and_timestamp",
            return_value=(None, timestamp),
        ),
        patch.object(
            ComponentScanner, "_collect_used_theme_names", AsyncMock(return_value=set())
        ),
        patch.object(
            ComponentScanner, "_collect_used_card_types", AsyncMock(return_value=set())
        ),
        patch.object(
            ComponentScanner,
            "_collect_used_icon_prefixes",
            AsyncMock(return_value=set()),
        ),
        patch.object(ComponentScanner, "_derive_card_types", return_value=[]),
        patch.object(
            ComponentScanner, "_get_configured_domains", AsyncMock(return_value=set())
        ),
    ]
    return scanner, patches


async def run_scan(hass, registry, method, timestamp):
    scanner, patches = build_scanner(hass, registry, timestamp)
    for p in patches:
        p.start()
    try:
        registry.begin_scan()
        result = await getattr(scanner, method)()
        registry.end_scan()
    finally:
        for p in patches:
            p.stop()
    if isinstance(result, dict):
        return result["used"] + result["unused"]
    return result


@pytest.mark.parametrize(
    "method",
    ["scan_themes", "scan_frontend", "scan_integrations", "scan_all_hacs_components"],
)
async def test_every_scan_publishes_an_install_date_and_its_confidence(
    hass: HomeAssistant, registry: InstallDateRegistry, method: str
):
    entries = await run_scan(hass, registry, method, days_ago_ts(40))

    assert entries, f"{method} produced no entries"
    for entry in entries:
        assert entry["days_installed"] == 40
        assert entry["install_date_estimated"] is True


@pytest.mark.parametrize(
    "method",
    ["scan_themes", "scan_frontend", "scan_integrations", "scan_all_hacs_components"],
)
async def test_the_recorded_date_wins_over_the_files_on_disk(
    hass: HomeAssistant, registry: InstallDateRegistry, method: str
):
    """A HACS update rewrites the directory; the published age must not move."""
    await run_scan(hass, registry, method, days_ago_ts(120))

    entries = await run_scan(hass, registry, method, days_ago_ts(0))

    for entry in entries:
        assert entry["days_installed"] == 120


async def test_scan_themes_still_reads_variants_from_the_resolved_path(
    hass: HomeAssistant, registry: InstallDateRegistry
):
    """`scan_themes` is the one scanner that also uses the install path.

    The de-duplication moved that path behind `_install_info`, so this is the
    guard that it is still threaded through to `_get_theme_variants`.
    """
    theme_dir = Path(hass.config.config_dir) / "themes" / "pretty"
    variants = AsyncMock(return_value=["Pretty Dark", "Pretty Light"])
    scanner, patches = build_scanner(hass, registry, days_ago_ts(5))
    patches = [p for p in patches if p.attribute != "_resolve_install_path_and_timestamp"]
    patches.append(
        patch.object(
            ComponentScanner,
            "_resolve_install_path_and_timestamp",
            return_value=(theme_dir, days_ago_ts(5)),
        )
    )
    patches.append(patch.object(ComponentScanner, "_get_theme_variants", variants))
    for p in patches:
        p.start()
    try:
        registry.begin_scan()
        result = await scanner.scan_themes()
        registry.end_scan()
    finally:
        for p in patches:
            p.stop()

    variants.assert_awaited_once_with(theme_dir)
    entry = (result["used"] + result["unused"])[0]
    assert entry["variants"] == 2
    assert entry["days_installed"] == 5


async def test_a_scanner_without_a_registry_still_reports_an_age(
    hass: HomeAssistant,
):
    """The scanner stays independently constructible."""
    scanner, patches = build_scanner(hass, None, days_ago_ts(9))
    scanner._install_dates = None
    for p in patches:
        p.start()
    try:
        entries = await scanner.scan_all_hacs_components()
    finally:
        for p in patches:
            p.stop()

    assert entries
    for entry in entries:
        assert entry["days_installed"] == 9
        assert entry["install_date_estimated"] is True


async def test_setup_keeps_the_dates_already_on_disk(hass: HomeAssistant, hass_storage):
    """The first refresh must not re-seed over a registry that already exists.

    If the store were loaded after the first scan, every recorded date would be
    overwritten with a fresh filesystem estimate on every restart.
    """
    seeded_at = (dt_util.utcnow() - timedelta(days=200)).isoformat()
    first_seen = (dt_util.utcnow() - timedelta(days=150)).isoformat()
    hass_storage[INSTALL_DATES_STORAGE_KEY] = {
        "version": INSTALL_DATES_STORAGE_VERSION,
        "data": {
            "seeded_at": seeded_at,
            "components": {
                FULL_NAME: {
                    "first_seen": first_seen,
                    "last_seen": first_seen,
                    "estimated": False,
                }
            },
        },
    }
    entry = MockConfigEntry(domain=DOMAIN, title="Custom Component Monitor", data={})
    entry.add_to_hass(hass)

    with (
        patch.object(
            ComponentScanner, "_load_hacs_repositories", AsyncMock(return_value=REPOS)
        ),
        patch.object(
            ComponentScanner,
            "_resolve_install_path_and_timestamp",
            return_value=(None, days_ago_ts(0)),  # freshly updated by HACS
        ),
        patch.object(
            ComponentScanner, "_collect_used_theme_names", AsyncMock(return_value=set())
        ),
        patch.object(
            ComponentScanner, "_collect_used_card_types", AsyncMock(return_value=set())
        ),
        patch.object(
            ComponentScanner,
            "_collect_used_icon_prefixes",
            AsyncMock(return_value=set()),
        ),
        patch.object(ComponentScanner, "_derive_card_types", return_value=[]),
        patch.object(
            ComponentScanner, "_get_configured_domains", AsyncMock(return_value=set())
        ),
        patch.object(
            ComponentScanner, "scan_updates", AsyncMock(return_value={"count": 0, "updates": []})
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.hacs_installed_components")
    assert state is not None
    assert state.attributes["install_dates_since"] == seeded_at
    powercalc = next(
        c for c in state.attributes["components"] if c["name"] == "Powercalc"
    )
    assert powercalc["days_installed"] == 150
    assert powercalc["install_date_estimated"] is False
