"""HACS/Marketplace compatibility without importing newer HA internals."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.custom_component_monitor.const import DOMAIN
from custom_components.custom_component_monitor.hacs_sync import async_sync_hacs_new
from custom_components.custom_component_monitor.sensor import (
    SCAN_INTERVAL,
    ComponentScanner,
)
from custom_components.custom_component_monitor.store import (
    StoreUnavailable,
    async_repository_snapshot,
    resolve_provider,
)


def repository(**overrides):
    data = {'id': '1', 'full_name': 'owner/component', 'category': 'integration',
            'installed': True, 'new': False, 'domain': 'component', 'installed_version': '1.0',
            'installed_commit': 'old', 'last_commit': 'new', 'selected_tag': None,
            'default_branch': 'main', 'releases': True, 'topics': ['lighting']}
    data.update(overrides)
    return NS(data=NS(**data), repository_manifest=NS(manifest={'name': 'Component'}),
              display_name='Component', content=NS(path=NS(local=None)),
              pending_update=True, display_installed_version='1.0',
              display_available_version='2.0')


def manager(*repos):
    return NS(repositories=NS(list_all=list(repos), get_by_full_name=lambda name: next(
        (r for r in repos if r.data.full_name.lower() == name), None)),
        data=NS(async_write=AsyncMock()), status=NS(startup=False),
        system=NS(disabled=False), stage='running')


def entries(hass, marketplace=None, hacs=None):
    return patch.object(hass.config_entries, 'async_entries', side_effect=lambda domain: {
        'marketplace': [marketplace] if marketplace else [],
        'hacs': [hacs] if hacs else [],
    }[domain])


async def test_marketplace_wins_over_stale_hacs_and_returns_detached_snapshot(hass):
    repo = repository()
    current = manager(repo)
    hass.data['hacs'] = manager(repository(full_name='stale/repo'))
    with entries(hass, NS(state=ConfigEntryState.LOADED, runtime_data=current)):
        provider, snapshot = await async_repository_snapshot(hass)
    assert provider == 'marketplace'
    assert snapshot['1']['full_name'] == 'owner/component'
    assert snapshot['1']['version_installed'] == '1.0'
    assert snapshot['1']['name'] == 'Component'
    snapshot['1']['topics'].append('changed')
    snapshot['1']['repository_manifest']['name'] = 'changed'
    assert repo.data.topics == ['lighting']
    assert repo.repository_manifest.manifest == {'name': 'Component'}


@pytest.mark.parametrize('state', [ConfigEntryState.NOT_LOADED, ConfigEntryState.SETUP_RETRY])
async def test_unloaded_marketplace_never_falls_back_to_hacs(hass, state):
    hass.data['hacs'] = manager(repository())
    with entries(hass, NS(state=state, runtime_data=manager(repository()))):
        assert resolve_provider(hass).manager is None
        with pytest.raises(StoreUnavailable, match='not loaded'):
            await async_repository_snapshot(hass)
        result = await async_sync_hacs_new(hass, {'owner/component': False})
    assert result.error and not result.completed
    hass.data['hacs'].data.async_write.assert_not_awaited()


@pytest.mark.parametrize('condition', ['startup', 'disabled', 'interface', 'bad-row', 'no-readiness'])
async def test_marketplace_bad_or_starting_runtime_is_not_empty(hass, condition):
    repo = repository()
    current = manager(repo)
    if condition == 'startup':
        current.status.startup = True
    elif condition == 'disabled':
        current.system.disabled = True
    elif condition == 'interface':
        current.repositories.list_all = None
    elif condition == 'no-readiness':
        del current.status
    else:
        repo.data.category = None
    with (entries(hass, NS(state=ConfigEntryState.LOADED, runtime_data=current)),
          pytest.raises(StoreUnavailable)):
        await async_repository_snapshot(hass)


async def test_ready_empty_marketplace_is_a_valid_snapshot(hass):
    with entries(hass, NS(state=ConfigEntryState.LOADED, runtime_data=manager())):
        assert await async_repository_snapshot(hass) == ('marketplace', {})


async def test_reload_resolves_new_runtime_instead_of_cached_manager(hass):
    entry = NS(state=ConfigEntryState.LOADED, runtime_data=manager(repository()))
    with entries(hass, entry):
        scanner = ComponentScanner(hass)
        await scanner._load_hacs_repositories()
        entry.runtime_data = manager(repository(full_name='owner/reloaded'))
        scanner._invalidate_cache()
        assert (await scanner._load_hacs_repositories())['1']['full_name'] == 'owner/reloaded'


@pytest.mark.parametrize('branch', [False, True])
async def test_marketplace_version_pair_and_pending_decision_are_authoritative(hass, branch):
    repo = repository(selected_tag='main' if branch else None)
    current = manager(repo)
    repo.display_available_version = '2.1-beta'
    with entries(hass, NS(state=ConfigEntryState.LOADED, runtime_data=current)):
        _, snapshot = await async_repository_snapshot(hass)
        assert ComponentScanner._pending_update_versions(snapshot['1']) == (
            ('old', 'new') if branch else ('1.0', '2.1-beta'))
        repo.pending_update = False
        _, snapshot = await async_repository_snapshot(hass)
        assert ComponentScanner._pending_update_versions(snapshot['1']) is None


@pytest.mark.parametrize('version', ['6', 6])
async def test_legacy_hacs_snapshot_still_scans(hass, version):
    path = Path(hass.config.path('.storage/hacs.repositories'))
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = {'1': {'full_name': 'owner/legacy', 'category': 'integration', 'installed': True}}
    await hass.async_add_executor_job(path.write_text, json.dumps({'version': version, 'data': raw}))
    with entries(hass):
        assert await async_repository_snapshot(hass) == ('hacs', raw)


@pytest.mark.parametrize('content', [None, '{', '{"version":2,"data":{}}', '{"version":"6"}',
                                     '{"version":"6","data":[]}', '{"version":"6","data":{"1":{}}}'])
async def test_missing_corrupt_or_incompatible_hacs_is_not_empty(hass, content):
    path = Path(hass.config.path('.storage/hacs.repositories'))
    if content is None:
        await hass.async_add_executor_job(lambda: path.unlink(missing_ok=True))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        await hass.async_add_executor_job(path.write_text, content)
    with entries(hass), pytest.raises(StoreUnavailable):
        await async_repository_snapshot(hass)


async def test_configured_unloaded_hacs_never_reads_stale_file(hass):
    with (entries(hass, hacs=NS(state=ConfigEntryState.NOT_LOADED)),
          pytest.raises(StoreUnavailable, match='not loaded')):
        await async_repository_snapshot(hass)


async def test_marketplace_review_targets_one_repo_and_rolls_back_failed_save(hass):
    target = repository(installed=False, new=True)
    keep = repository(id='2', full_name='owner/keep', installed=False, new=True)
    current = manager(target, keep)
    with entries(hass, NS(state=ConfigEntryState.LOADED, runtime_data=current)):
        result = await async_sync_hacs_new(hass, {'owner/component': False})
        assert result.changed == 1 and not result.error
        assert not target.data.new and keep.data.new
        current.data.async_write.assert_awaited_once_with()
        current.data.async_write.side_effect = OSError('save failed')
        result = await async_sync_hacs_new(hass, {'owner/component': True})
        assert result.error and not result.completed
        assert not target.data.new and keep.data.new


async def test_unavailable_scan_retains_dates_reviews_and_last_good_data(coordinator):
    coord = coordinator
    coord.install_dates._loaded = True
    coord.install_dates._store = None
    coord.install_dates._components = {'owner/component': {'first_seen': '2025-01-01T00:00:00+00:00'}}
    coord.new_items_registry._loaded = True
    coord.new_items_registry._store = None
    coord.new_items_registry.dismiss('owner/reviewed')
    old = {'provider': 'hacs', 'all_components': [{'name': 'Keep'}]}
    coord.data = old
    dates = deepcopy(coord.install_dates._components)
    with patch('custom_components.custom_component_monitor.sensor.async_repository_snapshot',
               side_effect=StoreUnavailable('Starting')):
        await coord.async_refresh()
    assert not coord.last_update_success
    assert coord.data is old
    assert coord.install_dates._components == dates
    assert coord.new_items_registry.is_dismissed('owner/reviewed')
    assert coord.update_interval == timedelta(seconds=30)


async def test_initial_unavailable_setup_attaches_sensors_and_recovers_without_reload(hass):
    entry = MockConfigEntry(domain=DOMAIN, title='Monitor', data={})
    entry.add_to_hass(hass)
    with patch('custom_components.custom_component_monitor.sensor.async_repository_snapshot',
               side_effect=StoreUnavailable('Starting')) as snapshot:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        sensor_ids = ('sensor.hacs_installed_components', 'sensor.unused_custom_integrations',
                      'sensor.unused_custom_themes', 'sensor.unused_frontend_resources',
                      'sensor.hacs_updates', 'sensor.hacs_new_items')
        assert all(hass.states.get(eid).state == 'unavailable' for eid in sensor_ids)
        assert hass.data[DOMAIN]['todo_entity'] is not None
        assert hass.services.has_service(DOMAIN, 'scan_now')
        snapshot.side_effect = None
        snapshot.return_value = ('marketplace', {})
        coord = hass.data[DOMAIN]['coordinator']
        await coord.async_refresh()
        await hass.async_block_till_done()
        state = hass.states.get('sensor.hacs_installed_components')
        assert state.state == '0'
        assert state.attributes['component_provider'] == 'marketplace'
        assert all(hass.states.get(eid).state == '0' for eid in sensor_ids)
        assert coord.update_interval == SCAN_INTERVAL
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


async def test_marketplace_update_entities_are_owned_not_core(hass, coordinator):
    registry = NS(async_get=lambda eid: NS(platform={
        'update.hacs': 'hacs', 'update.market': 'marketplace',
        'update.core': 'homeassistant',
    }[eid]))
    for eid in ('update.hacs', 'update.market', 'update.core'):
        hass.states.async_set(eid, 'on', {'title': eid, 'latest_version': '2.0',
                                         'installed_version': '1.0'})
    with patch('custom_components.custom_component_monitor.sensor.er.async_get', return_value=registry):
        live = ComponentScanner(hass)._live_hacs_updates()
        assert {u['entity_id'] for u in live} == {'update.hacs', 'update.market'}
        assert coordinator._build_update_entity_map()['by_version'] == {}


async def test_bulk_update_only_installs_owned_pending_selected_entities(hass):
    entry = MockConfigEntry(domain=DOMAIN, title='Monitor', data={})
    entry.add_to_hass(hass)
    with patch('custom_components.custom_component_monitor.sensor.async_repository_snapshot',
               return_value=('marketplace', {})):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        targets = []
        async def install(call):
            targets.append(call.data['entity_id'])
        hass.services.async_register('update', 'install', install)
        platforms = {'update.legacy': 'hacs', 'update.market': 'marketplace',
                     'update.core': 'homeassistant', 'update.busy': 'marketplace',
                     'update.done': 'marketplace'}
        for eid in platforms:
            hass.states.async_set(eid, 'off' if eid == 'update.done' else 'on',
                                  {'in_progress': eid == 'update.busy'})
        registry = NS(async_get=lambda eid: NS(platform=platforms[eid]))
        with patch('custom_components.custom_component_monitor.er.async_get', return_value=registry):
            await hass.services.async_call(DOMAIN, 'update_all', {}, blocking=True)
            assert targets == ['update.legacy', 'update.market']
            targets.clear()
            await hass.services.async_call(DOMAIN, 'update_all',
                {'entity_ids': ['update.market', 'update.core']}, blocking=True)
            assert targets == ['update.market']
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
