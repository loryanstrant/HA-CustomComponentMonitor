"""Targeted HACS review synchronization: persistence, backfill and races."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.custom_component_monitor.const import (
    CONF_AUTO_HIDE_FOREIGN,
    CONF_NEW_ITEMS_ENABLED,
    DOMAIN,
    NEW_ITEMS_STORAGE_KEY,
    NEW_ITEMS_STORAGE_VERSION,
    SERVICE_DISMISS_NEW_ITEMS,
    SERVICE_RESTORE_NEW_ITEMS,
)
from custom_components.custom_component_monitor.hacs_sync import async_sync_hacs_new
from custom_components.custom_component_monitor.new_items import NewItemRegistry
from custom_components.custom_component_monitor.sensor import ComponentScanner
from tests import test_new_items_services as service_tests

REAL_SCAN_NEW_ITEMS = ComponentScanner.scan_new_items
setup_integration = service_tests.setup_integration


def make_hacs(hass, names, *, installed=(), category="integration"):
    """HACS-shaped runtime whose writer stores sparse snapshots, like upstream."""
    repos = {
        name.lower(): SimpleNamespace(data=SimpleNamespace(
            full_name=name, new=True, category=category,
            installed=name in installed, description="unchanged",
        )) for name in names
    }
    persisted = {}

    async def write():
        persisted.clear()
        for name, repo in repos.items():
            # HACS omits False and restore defaults it to False.
            persisted[name] = {key: value for key, value in vars(repo.data).items()
                               if key != "new" or value}

    hacs = SimpleNamespace(
        repositories=SimpleNamespace(get_by_full_name=lambda name: repos.get(name.lower())),
        data=SimpleNamespace(async_write=AsyncMock(side_effect=write)),
        system=SimpleNamespace(disabled=False), status=SimpleNamespace(startup=False),
    )
    hass.data["hacs"] = hacs
    return hacs, repos, persisted


async def test_targeted_clear_one_write_and_unrelated_snapshot_unchanged(hass):
    hacs, repos, persisted = make_hacs(hass, ["Owner/Alpha", "owner/beta", "owner/keep"])
    await hacs.data.async_write()
    untouched = deepcopy(persisted["owner/keep"])
    hacs.data.async_write.reset_mock()
    result = await async_sync_hacs_new(hass, {" Owner/Alpha ": False, "owner/alpha": False, "owner/beta": False})
    assert result.changed == 2
    assert result.error is None
    hacs.data.async_write.assert_awaited_once_with()
    assert repos["owner/keep"].data.new is True
    assert persisted["owner/keep"] == untouched
    assert persisted["owner/alpha"].get("new", False) is False


async def test_already_clear_unknown_installed_and_wrong_category_are_noops(hass):
    hacs, repos, _ = make_hacs(hass, ["owner/clear", "owner/installed", "owner/theme"], installed=["owner/installed"])
    repos["owner/clear"].data.new = False
    repos["owner/theme"].data.category = "theme"
    result = await async_sync_hacs_new(hass, {name: False for name in [*repos, "missing/repo"]})
    assert result.changed == 0
    assert result.completed == {*repos, "missing/repo"}
    hacs.data.async_write.assert_not_awaited()
    assert repos["owner/installed"].data.new is True
    assert repos["owner/theme"].data.new is True


@pytest.mark.parametrize("condition", ["missing", "incompatible", "disabled", "starting"])
async def test_unavailable_hacs_is_retryable_without_mutation(hass, condition):
    hacs, repos, _ = make_hacs(hass, ["owner/alpha"])
    if condition == "missing":
        hass.data.pop("hacs")
    elif condition == "incompatible":
        hacs.data.async_write = None
    elif condition == "disabled":
        hacs.system.disabled = True
    else:
        hacs.status.startup = True
    result = await async_sync_hacs_new(hass, {"owner/alpha": False})
    assert result.error
    assert not result.completed
    assert repos["owner/alpha"].data.new is True


@pytest.mark.parametrize("failure", [RuntimeError("save failed"), asyncio.CancelledError()])
async def test_failed_or_cancelled_write_rolls_back_and_keeps_intent(hass, failure):
    hacs, repos, _ = make_hacs(hass, ["owner/alpha", "owner/beta"])
    hacs.data.async_write.side_effect = failure
    desired = {name: False for name in repos}
    if isinstance(failure, asyncio.CancelledError):
        with pytest.raises(asyncio.CancelledError):
            await async_sync_hacs_new(hass, desired)
    else:
        result = await async_sync_hacs_new(hass, desired)
        assert result.error
        assert result.changed == 0
        assert not result.completed
    assert all(repo.data.new for repo in repos.values())
    hacs.data.async_write.side_effect = None
    assert (await async_sync_hacs_new(hass, desired)).changed == 2


async def test_identity_mismatch_rolls_back_earlier_targets(hass):
    hacs, repos, _ = make_hacs(hass, ["owner/alpha", "owner/beta"])
    repos["owner/beta"].data.full_name = "different/repo"
    result = await async_sync_hacs_new(hass, {name: False for name in repos})
    assert result.error
    assert all(repo.data.new for repo in repos.values())
    hacs.data.async_write.assert_not_awaited()


async def test_backfill_uses_all_rejections_not_capped_sensor_attributes(coordinator):
    coord = coordinator
    registry = coord.new_items_registry
    registry._loaded = True
    registry._store = None
    dismissed = [f"owner/dismissed-{i}" for i in range(806)]
    hidden = [f"owner/hidden-{i}" for i in range(119)]
    keep = [f"owner/keep-{i}" for i in range(28)]
    for name in dismissed:
        registry.dismiss(name)
    # Simulate old v1 data: no pending operations were ever queued.
    registry._hacs_pending.clear()
    hacs, repos, _ = make_hacs(coord.hass, dismissed + hidden + keep)
    async with coord.new_items_review_lock:
        await coord.async_sync_new_items_hacs({"hidden": [{"full_name": name} for name in hidden]})
    assert all(not repos[name].data.new for name in dismissed + hidden)
    assert all(repos[name].data.new for name in keep)
    assert registry.hacs_pending == {}
    hacs.data.async_write.assert_awaited_once()


async def test_pending_intent_saved_before_hacs_write_and_retries_after_reload(coordinator, hass_storage):
    coord = coordinator
    registry = coord.new_items_registry
    await registry.async_load()
    registry.dismiss("owner/alpha")
    hacs, repos, _ = make_hacs(coord.hass, ["owner/alpha"])

    async def fail():
        assert hass_storage[NEW_ITEMS_STORAGE_KEY]["data"]["hacs_pending"] == {"owner/alpha": False}
        raise OSError("disk full")

    hacs.data.async_write.side_effect = fail
    async with coord.new_items_review_lock:
        await coord.async_sync_new_items_hacs({"hidden": []})
    assert registry.hacs_pending == {"owner/alpha": False}
    assert coord.hacs_sync_last_error
    reloaded = NewItemRegistry(coord.hass)
    await reloaded.async_load()
    coord.new_items_registry = reloaded
    hacs.data.async_write.side_effect = None
    async with coord.new_items_review_lock:
        await coord.async_sync_new_items_hacs({"hidden": []})
    assert reloaded.hacs_pending == {}
    assert repos["owner/alpha"].data.new is False
    assert coord.hacs_sync_last_error is None


async def test_restore_overtakes_pending_clear_and_unknown_restore_does_not_mark_new(coordinator):
    coord = coordinator
    reg = coord.new_items_registry
    reg._loaded = True
    reg._store = None
    reg.dismiss("owner/alpha")
    reg.restore("owner/alpha")
    assert reg.restore("owner/keep") is False
    _hacs, repos, _ = make_hacs(coord.hass, ["owner/alpha", "owner/keep"])
    for repo in repos.values():
        repo.data.new = False
    async with coord.new_items_review_lock:
        await coord.async_sync_new_items_hacs({"hidden": []})
    assert repos["owner/alpha"].data.new is True
    assert repos["owner/keep"].data.new is False
    assert reg.hacs_pending == {}


async def test_disabled_country_filter_cancels_pending_hide_without_marking_new(coordinator):
    coord = coordinator
    reg = coord.new_items_registry
    reg._loaded = True
    reg._store = None
    reg.record_hidden("owner/alpha", country="SE", term="Sweden", field="description", source="rules")
    coord.entry.options[CONF_AUTO_HIDE_FOREIGN] = False
    hacs, repos, _ = make_hacs(coord.hass, ["owner/alpha"])
    async with coord.new_items_review_lock:
        await coord.async_sync_new_items_hacs({"hidden": [{"full_name": "owner/alpha"}]})
    assert repos["owner/alpha"].data.new is True
    assert reg.hacs_pending == {}
    hacs.data.async_write.assert_not_awaited()


async def test_disabled_review_never_mutates_hacs(coordinator):
    coord = coordinator
    coord.new_items_registry._loaded = True
    coord.new_items_registry._store = None
    coord.new_items_registry.dismiss("owner/alpha")
    coord.entry.options[CONF_NEW_ITEMS_ENABLED] = False
    hacs, repos, _ = make_hacs(coord.hass, ["owner/alpha"])
    async with coord.new_items_review_lock:
        await coord.async_sync_new_items_hacs({"hidden": []})
    assert repos["owner/alpha"].data.new is True
    assert coord.new_items_registry.hacs_pending
    hacs.data.async_write.assert_not_awaited()


@pytest.mark.parametrize("selector, expected", [
    ({"repositories": ["OWNER/ALPHA"]}, {"owner/alpha"}),
    ({"category": "Climate & heating"}, {"owner/alpha", "owner/gamma"}),
    ({"all": True}, {"owner/alpha", "owner/beta", "owner/gamma"}),
])
async def test_real_dismiss_service_syncs_only_selected_items(setup_integration, selector, expected):
    hass, _ = setup_integration
    _, repos, _ = make_hacs(hass, ["owner/alpha", "owner/beta", "owner/gamma", "owner/hidden-one", "owner/keep"])
    await hass.services.async_call(DOMAIN, SERVICE_DISMISS_NEW_ITEMS, selector, blocking=True)
    assert {name for name, repo in repos.items() if not repo.data.new} == expected | {"owner/hidden-one"}


async def test_real_restore_service_marks_hacs_new_not_old_hidden_snapshot(setup_integration):
    hass, _ = setup_integration
    coord = hass.data[DOMAIN]["coordinator"]
    reg = coord.new_items_registry
    reg.record_hidden("owner/hidden-one", country="SE", term="Sweden", field="description", source="rules")
    _, repos, _ = make_hacs(hass, ["owner/hidden-one", "owner/keep"])
    repos["owner/hidden-one"].data.new = False
    # The service fixture uses a fixed hidden snapshot. Use the real scanner
    # (stub its disk input) here so a restored exemption is respected.
    snapshot = {str(i): {"full_name": name, "new": repo.data.new, "category": "integration"}
                for i, (name, repo) in enumerate(repos.items())}
    with patch.object(coord.scanner, "_load_hacs_repositories", AsyncMock(return_value=snapshot)), patch.object(
        coord.scanner, "scan_new_items", wraps=REAL_SCAN_NEW_ITEMS.__get__(coord.scanner)
    ):
        await hass.services.async_call(DOMAIN, SERVICE_RESTORE_NEW_ITEMS, {"repositories": ["owner/hidden-one"]}, blocking=True)
    assert repos["owner/hidden-one"].data.new is True
    assert reg.is_never_hide("owner/hidden-one")
    assert reg.is_restored("owner/hidden-one")
    assert reg.hacs_pending == {}


async def test_old_v1_registry_keeps_audit_and_dismissals_when_loading(hass, hass_storage):
    from homeassistant.util import dt as dt_util
    timestamp = dt_util.utcnow().isoformat()
    hass_storage[NEW_ITEMS_STORAGE_KEY] = {"version": NEW_ITEMS_STORAGE_VERSION, "data": {
        "dismissed": {"owner/alpha": timestamp}, "seen": {"owner/alpha": timestamp},
        "auto_hidden": {}, "never_hide": [], "ai": {},
    }}
    registry = NewItemRegistry(hass)
    await registry.async_load()
    assert registry.is_dismissed("owner/alpha")
    assert registry.first_seen("owner/alpha") == timestamp
    assert registry.hacs_pending == {}


async def test_superseded_ai_batch_does_not_sync_an_obsolete_hidden_list(coordinator):
    coord = coordinator
    reg = coord.new_items_registry
    reg._loaded = True
    reg._store = None
    item = service_tests.new_item("owner/alpha", ai_pending=True)
    hidden = service_tests.new_item("owner/previously-hidden")
    coord.data = {"new_items": {"items": [item], "hidden": [hidden]}}
    reg.record_hidden(hidden["full_name"], country="SE", term="Sweden",
                      field="description", source="rules")
    hacs, repos, _ = make_hacs(coord.hass, [item["full_name"], hidden["full_name"]])

    async def newer_scan(*_args):
        # A newer scan no longer finds foreign evidence before the old AI
        # response arrives. The previous clear was never committed to HACS.
        async with coord.new_items_review_lock:
            reg.clear_hidden(hidden["full_name"])
            coord.data = {"new_items": {"items": [hidden], "hidden": []}}
            await coord.async_sync_new_items_hacs(coord.data["new_items"])
        return [{"category": "Lighting", "country_scope": "global"}]

    with patch.object(coord, "_async_run_new_items_ai_wave", AsyncMock(side_effect=newer_scan)):
        counts = await coord.async_generate_new_item_categories()
    assert counts["skipped"] == 1
    assert repos[hidden["full_name"]].data.new is True
    assert reg.hacs_pending == {}
    hacs.data.async_write.assert_not_awaited()


@pytest.mark.parametrize('service', [SERVICE_DISMISS_NEW_ITEMS, SERVICE_RESTORE_NEW_ITEMS])
async def test_review_actions_force_immediate_refresh_not_throttled_request(setup_integration, service):
    hass, _ = setup_integration
    coord = hass.data[DOMAIN]['coordinator']
    if service == SERVICE_RESTORE_NEW_ITEMS:
        coord.new_items_registry.dismiss('owner/alpha')
    with patch.object(coord, 'async_refresh', AsyncMock()) as refresh, patch.object(
        coord, 'async_request_refresh', AsyncMock()
    ) as throttled:
        await hass.services.async_call(DOMAIN, service, {'repositories': ['owner/alpha']}, blocking=True)
    refresh.assert_awaited_once_with()
    throttled.assert_not_awaited()
