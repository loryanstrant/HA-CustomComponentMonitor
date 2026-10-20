"""New in HACS (#95): the dismiss / restore / categorise services.

These three services are the only way the review card's buttons change
anything — the card itself only ever reads ``sensor.hacs_new_items``.
``dismiss_new_items`` and ``restore_new_items`` share one selector contract
(exactly one of ``repositories`` / ``category`` / ``all``); ``categorise_new_items``
mirrors ``generate_summaries``'s shape but must invoke the coordinator's
existing AI batch runner (``async_generate_new_item_categories``) rather than
reimplement it. Every AI call here is mocked — this file never talks to a
real model.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.custom_component_monitor.const import (
    CONF_AI_CATEGORIZATION_ENABLED,
    CONF_AI_TASK_ENTITY,
    DOMAIN,
    NEW_ITEM_CATEGORY_FALLBACK,
    NEW_ITEM_CATEGORY_OPTIONS,
    SERVICE_CATEGORISE_NEW_ITEMS,
    SERVICE_DISMISS_NEW_ITEMS,
    SERVICE_RESTORE_NEW_ITEMS,
)

ALPHA = "owner/alpha"
BETA = "owner/beta"
GAMMA = "owner/gamma"
HIDDEN_ONE = "owner/hidden-one"


def new_item(full_name: str, *, category: str = NEW_ITEM_CATEGORY_FALLBACK, **extra) -> dict:
    base = {
        "name": full_name.split("/")[-1],
        "full_name": full_name,
        "repository": f"https://github.com/{full_name}",
        "kind": "integration",
        "description": "A thing.",
        "topics": [],
        "stars": 1,
        "downloads": 1,
        "last_updated": None,
        "first_seen": dt_util.utcnow().isoformat(),
        "age_days": 1,
        "category": category,
        "ai_pending": False,
    }
    base.update(extra)
    return base


def _items_snapshot() -> list[dict]:
    """A fresh copy of the "currently visible" items, every scan."""
    return [
        new_item(ALPHA, category="Climate & heating"),
        new_item(BETA, category="Lighting"),
        new_item(GAMMA, category="Climate & heating"),
    ]


def _hidden_snapshot() -> list[dict]:
    return [
        {
            **new_item(HIDDEN_ONE, category="Lighting"),
            "reason": 'Built for "SE".',
            "country": "SE",
            "term": "Sweden",
            "field": "description",
            "source": "rules",
        }
    ]


async def _scan_new_items(*_args, **_kwargs) -> dict:
    return {
        "items": _items_snapshot(),
        "hidden": _hidden_snapshot(),
        "dismissed_count": 0,
        "total_new": 4,
        "known_full_names": {ALPHA, BETA, GAMMA, HIDDEN_ONE},
    }


@pytest.fixture
async def setup_integration(hass: HomeAssistant):
    """Set the integration up with new items enabled and a fixed scan result."""
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
        patch(
            "custom_components.custom_component_monitor.sensor.ComponentScanner"
            ".scan_new_items",
            AsyncMock(side_effect=_scan_new_items),
        ),
        # Don't let background AI enrichment (either pipeline) race the
        # assertions below.
        patch(
            "custom_components.custom_component_monitor.sensor"
            ".CustomComponentMonitorCoordinator._schedule_ai_run"
        ),
        patch(
            "custom_components.custom_component_monitor.sensor"
            ".CustomComponentMonitorCoordinator._schedule_new_items_ai_run"
        ),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        yield hass, entry


def _coordinator(hass: HomeAssistant):
    return hass.data[DOMAIN]["coordinator"]


# ---------------------------------------------------------------------------
# dismiss_new_items
# ---------------------------------------------------------------------------


async def test_dismiss_by_repositories(setup_integration):
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISMISS_NEW_ITEMS,
        {"repositories": [ALPHA]},
        blocking=True,
    )

    assert registry.is_dismissed(ALPHA) is True
    assert registry.is_dismissed(BETA) is False


async def test_dismiss_by_category(setup_integration):
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISMISS_NEW_ITEMS,
        {"category": "Climate & heating"},
        blocking=True,
    )

    assert registry.is_dismissed(ALPHA) is True
    assert registry.is_dismissed(GAMMA) is True
    assert registry.is_dismissed(BETA) is False


async def test_dismiss_all_dismisses_only_currently_visible_items(setup_integration):
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry

    await hass.services.async_call(
        DOMAIN, SERVICE_DISMISS_NEW_ITEMS, {"all": True}, blocking=True
    )

    assert registry.is_dismissed(ALPHA) is True
    assert registry.is_dismissed(BETA) is True
    assert registry.is_dismissed(GAMMA) is True
    # "all" is scoped to what sensor.hacs_new_items shows -- hidden items are
    # not touched.
    assert registry.is_dismissed(HIDDEN_ONE) is False


async def test_dismiss_persists_to_the_registry(setup_integration):
    """A dismissal must be saved, not just held in memory."""
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISMISS_NEW_ITEMS,
        {"repositories": [ALPHA]},
        blocking=True,
    )

    # dismiss() marks the registry dirty; a completed async_save() clears it
    # again, so a clean flag here means the save actually ran.
    assert registry._dirty is False
    assert registry.is_dismissed(ALPHA) is True


async def test_dismiss_unknown_repository_is_skipped_silently(setup_integration):
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry

    await hass.services.async_call(
        DOMAIN,
        SERVICE_DISMISS_NEW_ITEMS,
        {"repositories": ["someone/does-not-exist"]},
        blocking=True,
    )

    assert registry.is_dismissed("someone/does-not-exist") is False
    assert registry.is_dismissed(ALPHA) is False


async def test_dismiss_with_no_selector_raises(setup_integration):
    hass, _entry = setup_integration

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_DISMISS_NEW_ITEMS, {}, blocking=True
        )


async def test_dismiss_with_two_selectors_raises(setup_integration):
    hass, _entry = setup_integration

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DISMISS_NEW_ITEMS,
            {"repositories": [ALPHA], "all": True},
            blocking=True,
        )


async def test_dismiss_with_a_bad_category_raises(setup_integration):
    hass, _entry = setup_integration

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_DISMISS_NEW_ITEMS,
            {"category": "Not A Real Category"},
            blocking=True,
        )


# ---------------------------------------------------------------------------
# restore_new_items
# ---------------------------------------------------------------------------


async def test_restore_by_repository_clears_a_dismissal(setup_integration):
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry
    registry.dismiss("owner/already-dismissed")
    assert registry.is_dismissed("owner/already-dismissed") is True

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESTORE_NEW_ITEMS,
        {"repositories": ["owner/already-dismissed"]},
        blocking=True,
    )

    assert registry.is_dismissed("owner/already-dismissed") is False


async def test_restore_by_repository_clears_auto_hide_and_sets_never_hide(
    setup_integration,
):
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry
    registry.record_hidden(
        "owner/was-hidden",
        country="SE",
        term="Sweden",
        field="description",
        source="rules",
    )
    assert registry.hidden_record("owner/was-hidden") is not None
    assert registry.is_never_hide("owner/was-hidden") is False

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESTORE_NEW_ITEMS,
        {"repositories": ["owner/was-hidden"]},
        blocking=True,
    )

    assert registry.hidden_record("owner/was-hidden") is None
    assert registry.is_never_hide("owner/was-hidden") is True


async def test_restore_by_category_reaches_the_hidden_set(setup_integration):
    hass, _entry = setup_integration
    registry = _coordinator(hass).new_items_registry
    # The fixture's mocked scan_new_items never runs the real scanner, so it
    # never calls registry.record_hidden() itself -- seed it here the way a
    # real scan would have, so the registry agrees with coordinator.data.
    registry.record_hidden(
        HIDDEN_ONE, country="SE", term="Sweden", field="description", source="rules"
    )

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESTORE_NEW_ITEMS,
        {"category": "Lighting"},
        blocking=True,
    )

    # HIDDEN_ONE is categorised "Lighting" in the fixture scan result.
    assert registry.hidden_record(HIDDEN_ONE) is None
    assert registry.is_never_hide(HIDDEN_ONE) is True


async def test_restore_unknown_repository_does_not_raise(setup_integration):
    hass, _entry = setup_integration

    await hass.services.async_call(
        DOMAIN,
        SERVICE_RESTORE_NEW_ITEMS,
        {"repositories": ["someone/never-dismissed-or-hidden"]},
        blocking=True,
    )


async def test_restore_with_no_selector_raises(setup_integration):
    hass, _entry = setup_integration

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, SERVICE_RESTORE_NEW_ITEMS, {}, blocking=True
        )


async def test_restore_with_two_selectors_raises(setup_integration):
    hass, _entry = setup_integration

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_NEW_ITEMS,
            {"category": "Lighting", "all": True},
            blocking=True,
        )


async def test_restore_with_a_bad_category_raises(setup_integration):
    hass, _entry = setup_integration

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_RESTORE_NEW_ITEMS,
            {"category": "Not A Real Category"},
            blocking=True,
        )


# ---------------------------------------------------------------------------
# categorise_new_items
# ---------------------------------------------------------------------------


async def test_categorise_invokes_the_batch_runner_with_max_items(setup_integration):
    hass, _entry = setup_integration
    coordinator = _coordinator(hass)
    runner = AsyncMock(
        return_value={"generated": 1, "failed": 0, "skipped": 0, "remaining": 0}
    )

    with patch.object(coordinator, "async_generate_new_item_categories", runner):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_CATEGORISE_NEW_ITEMS,
            {"max_items": 7},
            blocking=True,
            return_response=True,
        )

    runner.assert_awaited_once_with(limit=7, max_seconds=None)
    assert response == {"generated": 1, "failed": 0, "skipped": 0, "remaining": 0}


async def test_categorise_without_arguments_uses_the_configured_time_budget(
    setup_integration,
):
    hass, _entry = setup_integration
    coordinator = _coordinator(hass)
    runner = AsyncMock(
        return_value={"generated": 0, "failed": 0, "skipped": 0, "remaining": 0}
    )

    with patch.object(coordinator, "async_generate_new_item_categories", runner):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CATEGORISE_NEW_ITEMS,
            {},
            blocking=True,
            return_response=True,
        )

    runner.assert_awaited_once_with(limit=None, max_seconds=None)


async def test_categorise_force_marks_every_visible_item_ai_pending_first(
    setup_integration,
):
    """force=True must re-queue every item before the (mocked) AI call."""
    hass, _entry = setup_integration
    coordinator = _coordinator(hass)

    # The fixture scan result has ai_pending=False for everything.
    assert all(
        item["ai_pending"] is False
        for item in coordinator.data["new_items"]["items"]
    )

    captured: dict[str, list[bool]] = {}

    async def _capture(*, limit=None, max_seconds=None):
        captured["pending"] = [
            item["ai_pending"] for item in coordinator.data["new_items"]["items"]
        ]
        return {"generated": 0, "failed": 0, "skipped": 0, "remaining": 0}

    with patch.object(
        coordinator, "async_generate_new_item_categories", AsyncMock(side_effect=_capture)
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CATEGORISE_NEW_ITEMS,
            {"force": True},
            blocking=True,
            return_response=True,
        )

    assert captured["pending"] == [True, True, True]


async def test_categorise_force_clears_the_cached_ai_verdict(setup_integration):
    """force=True must drop the cached verdict, not only re-queue the item.

    The batch runner asks the registry before it asks the model, so a cached
    verdict left in place means a forced run hands back the very category the
    user asked to replace — and a forced call that then fails reverts to it
    silently, looking like a fresh answer.
    """
    hass, _entry = setup_integration
    coordinator = _coordinator(hass)
    registry = coordinator.new_items_registry
    registry.set_ai(ALPHA, category="Lighting", country_scope="global", model="test")
    assert registry.get_ai(ALPHA) is not None

    with patch.object(
        coordinator,
        "async_generate_new_item_categories",
        AsyncMock(
            return_value={"generated": 0, "failed": 0, "skipped": 0, "remaining": 0}
        ),
    ):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_CATEGORISE_NEW_ITEMS,
            {"force": True},
            blocking=True,
            return_response=True,
        )

    assert registry.get_ai(ALPHA) is None


# ---------------------------------------------------------------------------
# The category list is duplicated in services.yaml — keep it honest
# ---------------------------------------------------------------------------


def test_services_yaml_category_options_match_the_canonical_list() -> None:
    """``services.yaml`` repeats the category list; nothing else enforces it.

    The ``category`` selector on both dismiss and restore has to offer exactly
    the categories the AI can assign, because ``_new_items_selected_field``
    rejects anything outside :data:`NEW_ITEM_CATEGORY_OPTIONS` — so a category
    added to the constant and forgotten here is unreachable from the UI, and
    one removed from the constant but left here is an option that always
    raises. Order is asserted too: both are the card's own alphabetical order
    with "Other" last, and a reordered copy is the first sign of a hand edit.
    """
    path = Path("custom_components/custom_component_monitor/services.yaml")
    services = yaml.safe_load(path.read_text(encoding="utf-8"))

    for service in (SERVICE_DISMISS_NEW_ITEMS, SERVICE_RESTORE_NEW_ITEMS):
        options = services[service]["fields"]["category"]["selector"]["select"][
            "options"
        ]
        assert options == NEW_ITEM_CATEGORY_OPTIONS, service


# ---------------------------------------------------------------------------
# Unload
# ---------------------------------------------------------------------------


async def test_services_are_removed_on_unload(setup_integration):
    hass, entry = setup_integration
    for service in (
        SERVICE_DISMISS_NEW_ITEMS,
        SERVICE_RESTORE_NEW_ITEMS,
        SERVICE_CATEGORISE_NEW_ITEMS,
    ):
        assert hass.services.has_service(DOMAIN, service), service

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    for service in (
        SERVICE_DISMISS_NEW_ITEMS,
        SERVICE_RESTORE_NEW_ITEMS,
        SERVICE_CATEGORISE_NEW_ITEMS,
    ):
        assert not hass.services.has_service(DOMAIN, service), service
