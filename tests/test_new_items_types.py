"""Themes and templates use the existing discovery and review pipeline."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from custom_components.custom_component_monitor.const import (
    DOMAIN,
    SERVICE_DISMISS_NEW_ITEMS,
    SERVICE_RESTORE_NEW_ITEMS,
)
from custom_components.custom_component_monitor.new_items import NewItemRegistry
from custom_components.custom_component_monitor.sensor import ComponentScanner
from tests import test_hacs_sync
from tests.test_hacs_sync import REAL_SCAN_NEW_ITEMS, make_hacs

setup_integration = test_hacs_sync.setup_integration


@pytest.mark.parametrize("category", ["theme", "template"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_new_type_dismiss_or_country_hide_restore_and_reload(
    setup_integration, category, foreign,
):
    """Real scanner/services + HACS-shaped writer, including persisted restore."""
    hass, _ = setup_integration
    coord = hass.data[DOMAIN]["coordinator"]
    name = "owner/review-me"
    _, repos, persisted = make_hacs(hass, [name, "owner/keep"], category=category)
    if foreign:
        repos[name].data.description = "Electricity helpers for Sweden."

    async def snapshot():
        return {str(i): vars(repo.data).copy() for i, repo in enumerate(repos.values())}

    with patch.object(coord.scanner, "_load_hacs_repositories", AsyncMock(side_effect=snapshot)), patch.object(
        coord.scanner, "scan_new_items", wraps=REAL_SCAN_NEW_ITEMS.__get__(coord.scanner)
    ):
        await coord.async_refresh()
        if not foreign:
            assert name in {item["full_name"] for item in coord.data["new_items"]["items"]}
            await hass.services.async_call(
                DOMAIN, SERVICE_DISMISS_NEW_ITEMS, {"repositories": [name]}, blocking=True,
            )
        else:
            assert name in {item["full_name"] for item in coord.data["new_items"]["hidden"]}
        assert not repos[name].data.new
        assert persisted[name].get("new", False) is False
        assert repos["owner/keep"].data.new
        assert coord.new_items_registry.hacs_pending == {}

        await hass.services.async_call(
            DOMAIN, SERVICE_RESTORE_NEW_ITEMS, {"repositories": [name]}, blocking=True,
        )
        assert repos[name].data.new
        assert persisted[name]["new"] is True
        assert name in {item["full_name"] for item in coord.data["new_items"]["items"]}
        if foreign:
            assert coord.new_items_registry.is_never_hide(name)

        reloaded = NewItemRegistry(hass)
        await reloaded.async_load()
        coord.new_items_registry = reloaded
        await coord.async_refresh()
        assert name in {item["full_name"] for item in coord.data["new_items"]["items"]}
        assert repos[name].data.new
        assert repos["owner/keep"].data.new
        assert reloaded.hacs_pending == {}


@pytest.mark.parametrize("category", ["theme", "template"])
async def test_new_type_selection_keeps_installed_old_and_unknown_categories_out(hass, category):
    registry = NewItemRegistry(hass)
    registry._loaded = True
    registry._store = None
    scanner = ComponentScanner(hass, None)
    snapshot = {
        "1": {"full_name": "owner/new", "category": category, "new": True},
        "2": {"full_name": "owner/installed", "category": category, "new": True, "installed": True},
        "3": {"full_name": "owner/old", "category": category, "new": False},
        "4": {"full_name": "owner/unsupported", "category": "python_script", "new": True},
    }
    with patch.object(scanner, "_load_hacs_repositories", AsyncMock(return_value=snapshot)):
        result = await scanner.scan_new_items(registry)
    assert [item["full_name"] for item in result["items"]] == ["owner/new"]
    assert result["items"][0]["kind"] == category
    assert registry.first_seen("owner/old") is None
    assert registry.first_seen("owner/unsupported") is None


@pytest.mark.parametrize("hacs_category,expected_kind", [("plugin", "card"), ("template", "template")])
async def test_pending_cards_and_templates_keep_existing_other_fallback(hass, hacs_category, expected_kind):
    reg = NewItemRegistry(hass)
    reg._loaded = True
    reg._store = None
    scanner = ComponentScanner(hass, None)
    with patch.object(scanner, "_load_hacs_repositories", AsyncMock(return_value={
        "1": {"full_name": "owner/pending", "category": hacs_category, "new": True}
    })):
        result = await scanner.scan_new_items(reg)
    item = result["items"][0]
    assert item["kind"] == expected_kind
    assert item["category"] == "Other"
    assert item["ai_pending"]
