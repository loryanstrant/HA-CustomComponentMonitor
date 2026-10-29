"""The Custom Component Monitor integration."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
import shutil
import time
from typing import Any

import voluptuous as vol

from homeassistant.components.todo import TodoItem, TodoItemStatus
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.typing import ConfigType
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    NEW_ITEM_CATEGORY_FALLBACK,
    NEW_ITEM_CATEGORY_OPTIONS,
    NEW_ITEMS_CARD_JS,
    NEW_ITEMS_CARD_BASE_PATH,
    SERVICE_CATEGORISE_NEW_ITEMS,
    SERVICE_DISMISS_NEW_ITEMS,
    SERVICE_GENERATE_SUMMARIES,
    SERVICE_RESTORE_NEW_ITEMS,
    SERVICE_UPDATE_AND_ACTION,
    SERVICE_UPDATE_ALL,
    UAT_CARD_JS,
    UAT_CARD_BASE_PATH,
    RIU_CARD_JS,
    RIU_CARD_BASE_PATH,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.TODO]

CARD_JS = "custom-component-monitor-card.js"
CARD_BASE_PATH = f"/local/{DOMAIN}/{CARD_JS}"

SERVICE_SCAN_NOW = "scan_now"

SERVICE_UPDATE_AND_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("entity_id"): cv.entity_id,
        vol.Optional("version"): cv.string,
    }
)

SERVICE_GENERATE_SUMMARIES_SCHEMA = vol.Schema(
    {
        vol.Optional("entity_ids", default=[]): cv.ensure_list,
        vol.Optional("force", default=False): cv.boolean,
        vol.Optional("max_items"): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
    }
)

# --- New in HACS (#95): dismiss / restore / categorise ---------------------

NEW_ITEMS_SELECTOR_SCHEMA = vol.Schema(
    {
        vol.Optional("repositories"): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional("category"): cv.string,
        vol.Optional("all"): cv.boolean,
    }
)

# Both caps are optional and both are escape hatches rather than settings
# (v1.15.1): left alone, the service runs one pass at the configured time
# budget and then keeps going in the background until the backlog is clear,
# which is what the automatic path does too. ``max_items`` exists for
# "categorise just these few now"; ``max_seconds`` for "don't tie up the AI
# for longer than this".
SERVICE_CATEGORISE_NEW_ITEMS_SCHEMA = vol.Schema(
    {
        vol.Optional("max_items"): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=2000)
        ),
        vol.Optional("max_seconds"): vol.All(
            vol.Coerce(float), vol.Range(min=1, max=600)
        ),
        vol.Optional("force", default=False): cv.boolean,
    }
)


def _new_items_selected_field(call_data: dict[str, Any], service: str) -> str:
    """Validate the mutually-exclusive dismiss/restore selectors.

    Exactly one of ``repositories`` / ``category`` / ``all`` must be given.
    Returns the name of the one that was. Raises :class:`ServiceValidationError`
    for zero, more than one, or (for ``category``) a name that isn't one of
    :data:`NEW_ITEM_CATEGORY_OPTIONS`.
    """
    present = [
        field
        for field in ("repositories", "category", "all")
        if call_data.get(field)
    ]
    if not present:
        raise ServiceValidationError(
            f"{service} needs exactly one of: repositories, category, all."
        )
    if len(present) > 1:
        raise ServiceValidationError(
            f"{service} takes exactly one of repositories, category or all — "
            f"got {', '.join(present)}."
        )
    field = present[0]
    if field == "category" and call_data["category"] not in NEW_ITEM_CATEGORY_OPTIONS:
        raise ServiceValidationError(
            f"Unknown category '{call_data['category']}'. Must be one of: "
            f"{', '.join(NEW_ITEM_CATEGORY_OPTIONS)}."
        )
    return field


CONFIG_SCHEMA = cv.empty_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Custom Component Monitor integration (platform-level).

    Copies both card JS files to www/ so they are served via /local/ and
    registers them as Lovelace module resources with cache-busting tags.
    """
    hass.data.setdefault(DOMAIN, {})

    www_dir = Path(hass.config.path()) / "www" / DOMAIN

    src_ccm = Path(__file__).parent / "www" / CARD_JS
    dest_ccm = www_dir / CARD_JS

    src_uat = Path(__file__).parent / "www" / UAT_CARD_JS
    dest_uat = www_dir / UAT_CARD_JS

    src_riu = Path(__file__).parent / "www" / RIU_CARD_JS
    dest_riu = www_dir / RIU_CARD_JS

    src_hni = Path(__file__).parent / "www" / NEW_ITEMS_CARD_JS
    dest_hni = www_dir / NEW_ITEMS_CARD_JS

    def _copy_cards():
        www_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src_ccm), str(dest_ccm))
        shutil.copy2(str(src_uat), str(dest_uat))
        shutil.copy2(str(src_riu), str(dest_riu))
        shutil.copy2(str(src_hni), str(dest_hni))

    await hass.async_add_executor_job(_copy_cards)

    async def _deferred_register(_event):
        await _register_lovelace_resource(hass, CARD_BASE_PATH, CARD_JS)
        await _register_lovelace_resource(hass, UAT_CARD_BASE_PATH, UAT_CARD_JS)
        await _register_lovelace_resource(hass, RIU_CARD_BASE_PATH, RIU_CARD_JS)
        await _register_lovelace_resource(
            hass, NEW_ITEMS_CARD_BASE_PATH, NEW_ITEMS_CARD_JS
        )

    hass.bus.async_listen_once("homeassistant_started", _deferred_register)

    _LOGGER.debug("Card JS files staged in %s", www_dir)
    return True


async def _register_lovelace_resource(
    hass: HomeAssistant, card_base_path: str, card_js: str
) -> None:
    """Add/update a card JS file in lovelace_resources with a cache-bust tag."""
    from homeassistant.components.lovelace import DOMAIN as LL_DOMAIN
    from homeassistant.components.lovelace.resources import ResourceStorageCollection

    if LL_DOMAIN not in hass.data:
        _LOGGER.debug("Lovelace not ready, skipping resource registration for %s", card_js)
        return

    ll_data = hass.data[LL_DOMAIN]
    resources = getattr(ll_data, "resources", None)
    if resources is None:
        _LOGGER.debug("Lovelace resources collection not available for %s", card_js)
        return

    if not isinstance(resources, ResourceStorageCollection):
        _LOGGER.info(
            "Lovelace is in YAML mode. Add manually: url: %s, type: module",
            card_base_path,
        )
        return

    if not resources.loaded:
        await resources.async_load()

    www_path = Path(hass.config.path()) / "www" / DOMAIN / card_js
    try:
        mtime = int(www_path.stat().st_mtime * 10)
    except OSError:
        mtime = int(time.time() * 10)
    card_url = f"{card_base_path}?v={mtime}"

    for item in resources.async_items():
        url = item.get("url", "")
        if card_base_path in url:
            if url != card_url:
                await resources.async_update_item(
                    item["id"], {"res_type": "module", "url": card_url}
                )
                _LOGGER.info("Updated lovelace resource: %s", card_url)
            else:
                _LOGGER.debug("Lovelace resource already current: %s", card_base_path)
            return

    await resources.async_create_item({"res_type": "module", "url": card_url})
    _LOGGER.info("Registered lovelace resource: %s", card_url)


async def _async_get_release_notes(hass: HomeAssistant, entity_id: str) -> str | None:
    """Fetch release notes from a Home Assistant update entity."""
    entity_comp = hass.data.get("entity_components", {}).get("update")
    if entity_comp is None:
        return None
    entity = entity_comp.get_entity(entity_id)
    if entity is None:
        return None
    if hasattr(entity, "async_release_notes"):
        try:
            return await entity.async_release_notes()
        except Exception:
            _LOGGER.debug("Failed to fetch release notes for %s", entity_id, exc_info=True)
    return None


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry so an exclusion-list change re-runs the scan (#70)."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Custom Component Monitor from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {}

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Re-scan when the exclusion list (options) changes (#70).
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    # --- scan_now service ---
    if not hass.services.has_service(DOMAIN, SERVICE_SCAN_NOW):

        async def handle_scan_now(call: ServiceCall) -> None:
            coordinator = hass.data[DOMAIN].get("coordinator")
            if coordinator is not None:
                _LOGGER.info("Manual scan triggered via service call")
                await coordinator.async_request_refresh()
            else:
                _LOGGER.warning("No coordinator available for manual scan")

        hass.services.async_register(
            DOMAIN, SERVICE_SCAN_NOW, handle_scan_now, schema=vol.Schema({})
        )

    # --- update_and_action service ---
    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_AND_ACTION):

        async def handle_update_and_action(call: ServiceCall) -> None:
            entity_id: str = call.data["entity_id"]
            version: str | None = call.data.get("version")

            state = hass.states.get(entity_id)
            if state is None:
                raise ValueError(f"Entity {entity_id} not found")

            friendly_name = state.attributes.get("friendly_name", entity_id)
            latest_version = version or state.attributes.get("latest_version", "unknown")
            release_url = state.attributes.get("release_url", "")

            release_notes = await _async_get_release_notes(hass, entity_id)

            service_data: dict[str, Any] = {"entity_id": entity_id}
            if version:
                service_data["version"] = version
            await hass.services.async_call("update", "install", service_data, blocking=True)

            todo_entity = hass.data[DOMAIN].get("todo_entity")
            if todo_entity is None:
                _LOGGER.error("Todo entity not available — cannot create action item")
                return

            today = date.today().isoformat()
            description_parts = [f"Updated: {today}"]
            if release_url:
                description_parts.append(f"Release: {release_url}")
            description_parts.append(f"Entity: {entity_id}")
            if release_notes:
                description_parts.append(f"\n---\n{release_notes}")

            item = TodoItem(
                summary=f"{friendly_name} updated to {latest_version} ({today})",
                status=TodoItemStatus.NEEDS_ACTION,
                description="\n".join(description_parts),
            )
            await todo_entity.async_create_todo_item(item)
            _LOGGER.info("Updated %s to %s and created action item", friendly_name, latest_version)

        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_AND_ACTION,
            handle_update_and_action,
            schema=SERVICE_UPDATE_AND_ACTION_SCHEMA,
        )

    # --- update_all service: install every available HACS update at once ---
    if not hass.services.has_service(DOMAIN, SERVICE_UPDATE_ALL):

        async def handle_update_all(call: ServiceCall) -> None:
            create_actions: bool = call.data.get("create_actions", False)
            # Optional subset: only update these update entities (the card passes
            # the currently-filtered set for "Update selected"). Empty = all.
            selected: set[str] = set(call.data.get("entity_ids", []) or [])
            registry = er.async_get(hass)

            targets: list[str] = []
            for state in hass.states.async_all("update"):
                if state.state != "on":
                    continue
                if state.attributes.get("in_progress"):
                    continue
                if selected and state.entity_id not in selected:
                    continue
                entry_re = registry.async_get(state.entity_id)
                if entry_re is None or entry_re.platform != "hacs":
                    continue
                targets.append(state.entity_id)

            _LOGGER.info(
                "update_all: %d HACS component(s) with available updates%s",
                len(targets),
                " (selected subset)" if selected else "",
            )
            for entity_id in targets:
                try:
                    if create_actions:
                        await hass.services.async_call(
                            DOMAIN,
                            SERVICE_UPDATE_AND_ACTION,
                            {"entity_id": entity_id},
                            blocking=True,
                        )
                    else:
                        await hass.services.async_call(
                            "update",
                            "install",
                            {"entity_id": entity_id},
                            blocking=True,
                        )
                except Exception as err:  # noqa: BLE001
                    _LOGGER.error("update_all: failed to update %s: %s", entity_id, err)

        hass.services.async_register(
            DOMAIN,
            SERVICE_UPDATE_ALL,
            handle_update_all,
            schema=vol.Schema(
                {
                    vol.Optional("create_actions", default=False): cv.boolean,
                    vol.Optional("entity_ids", default=[]): cv.ensure_list,
                }
            ),
        )

    # --- generate_summaries service: fill in any missing AI summaries (#91) ---
    if not hass.services.has_service(DOMAIN, SERVICE_GENERATE_SUMMARIES):

        async def handle_generate_summaries(call: ServiceCall) -> dict[str, int]:
            coordinator = hass.data[DOMAIN].get("coordinator")
            if coordinator is None:
                raise HomeAssistantError(
                    "Custom Component Monitor is still starting up — no scan "
                    "coordinator is available yet."
                )
            if not coordinator.ai_enabled:
                raise HomeAssistantError(
                    "AI summaries are switched off. Enable them in the Custom "
                    "Component Monitor options and pick an AI Task or "
                    "conversation entity first."
                )
            entity_ids = set(call.data.get("entity_ids") or [])
            # The card renders live update.* entities, so it can legitimately
            # know about an update the last hourly scan didn't. Re-scan rather
            # than shrug (#91).
            if entity_ids and not entity_ids <= coordinator.known_update_entity_ids():
                await coordinator.async_refresh()
            return await coordinator.async_generate_summaries(
                entity_ids=entity_ids or None,
                force=call.data.get("force", False),
                limit=call.data.get("max_items"),
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_GENERATE_SUMMARIES,
            handle_generate_summaries,
            schema=SERVICE_GENERATE_SUMMARIES_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    # --- dismiss_new_items service (#95) ---
    if not hass.services.has_service(DOMAIN, SERVICE_DISMISS_NEW_ITEMS):

        async def handle_dismiss_new_items(call: ServiceCall) -> None:
            coordinator = hass.data[DOMAIN].get("coordinator")
            if coordinator is None:
                raise HomeAssistantError(
                    "Custom Component Monitor is still starting up — no scan "
                    "coordinator is available yet."
                )
            field = _new_items_selected_field(call.data, SERVICE_DISMISS_NEW_ITEMS)
            registry = coordinator.new_items_registry

            # "Currently visible" is exactly what sensor.hacs_new_items shows:
            # the last scan's new, not-installed, not-dismissed, not-hidden
            # items. Read it from there rather than re-scanning.
            visible: list[dict[str, Any]] = (
                (coordinator.data or {}).get("new_items") or {}
            ).get("items", []) or []

            if field == "all":
                targets = [item["full_name"] for item in visible]
            elif field == "category":
                wanted = call.data["category"]
                targets = [
                    item["full_name"]
                    for item in visible
                    if item.get("category") == wanted
                ]
            else:  # repositories — unknown/not-currently-visible names are
                # skipped silently rather than dismissing an arbitrary string.
                wanted_repos = {
                    str(r).strip().lower() for r in call.data["repositories"]
                }
                targets = [
                    item["full_name"]
                    for item in visible
                    if item["full_name"].strip().lower() in wanted_repos
                ]

            for full_name in targets:
                registry.dismiss(full_name)
            await registry.async_save()
            await coordinator.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_DISMISS_NEW_ITEMS,
            handle_dismiss_new_items,
            schema=NEW_ITEMS_SELECTOR_SCHEMA,
        )

    # --- restore_new_items service (#95) ---
    if not hass.services.has_service(DOMAIN, SERVICE_RESTORE_NEW_ITEMS):

        async def handle_restore_new_items(call: ServiceCall) -> None:
            coordinator = hass.data[DOMAIN].get("coordinator")
            if coordinator is None:
                raise HomeAssistantError(
                    "Custom Component Monitor is still starting up — no scan "
                    "coordinator is available yet."
                )
            field = _new_items_selected_field(call.data, SERVICE_RESTORE_NEW_ITEMS)
            registry = coordinator.new_items_registry

            if field == "repositories":
                # registry.restore() is a no-op (returns False) for a name
                # that was never dismissed or auto-hidden, so an unknown
                # repository is skipped without needing a membership check.
                targets = {str(r).strip() for r in call.data["repositories"]}
            else:
                # category/all reach the hidden AND dismissed sets. The
                # auto-hidden items come straight off the last scan (they
                # carry a category); the dismissed ones are by definition not
                # in the sensor's visible list at all, so they come from the
                # registry's own listing.
                hidden: list[dict[str, Any]] = (
                    (coordinator.data or {}).get("new_items") or {}
                ).get("hidden", []) or []
                dismissed_keys = registry.dismissed_keys

                if field == "all":
                    targets = {item["full_name"] for item in hidden}
                    targets.update(dismissed_keys)
                else:  # category
                    wanted = call.data["category"]
                    targets = {
                        item["full_name"]
                        for item in hidden
                        if item.get("category") == wanted
                    }
                    for key in dismissed_keys:
                        # include_stale: a dismissed item is never re-sent to
                        # the AI, so after a NEW_ITEM_AI_PROMPT_VERSION bump
                        # the plain call returns None for every one of them and
                        # they all collapse onto the fallback category. See
                        # NewItemRegistry.get_ai.
                        ai_record = registry.get_ai(key, include_stale=True)
                        category = (
                            ai_record["category"]
                            if ai_record
                            else NEW_ITEM_CATEGORY_FALLBACK
                        )
                        if category == wanted:
                            targets.add(key)

            for full_name in targets:
                registry.restore(full_name)
            await registry.async_save()
            await coordinator.async_request_refresh()

        hass.services.async_register(
            DOMAIN,
            SERVICE_RESTORE_NEW_ITEMS,
            handle_restore_new_items,
            schema=NEW_ITEMS_SELECTOR_SCHEMA,
        )

    # --- categorise_new_items service (#95) ---
    if not hass.services.has_service(DOMAIN, SERVICE_CATEGORISE_NEW_ITEMS):

        async def handle_categorise_new_items(call: ServiceCall) -> dict[str, int]:
            coordinator = hass.data[DOMAIN].get("coordinator")
            if coordinator is None:
                raise HomeAssistantError(
                    "Custom Component Monitor is still starting up — no scan "
                    "coordinator is available yet."
                )
            if not coordinator.ai_enabled:
                raise HomeAssistantError(
                    "AI summaries are switched off. Enable them in the Custom "
                    "Component Monitor options and pick an AI Task or "
                    "conversation entity first."
                )
            force = call.data.get("force", False)
            if force:
                # Done by the coordinator, under its categorisation lock, so a
                # forced call that lands while the background catch-up chain
                # is mid-pass waits for that pass instead of reverting items
                # it has already finished. See
                # ``async_reset_new_items_ai_cache``.
                await coordinator.async_reset_new_items_ai_cache()
            counts = await coordinator.async_generate_new_item_categories(
                limit=call.data.get("max_items"),
                max_seconds=call.data.get("max_seconds"),
            )
            # One pass is bounded by the time budget, so a big backlog will
            # still have items left when it returns. Hand the rest to the
            # background catch-up chain instead of making the caller loop
            # (v1.15.1) — unless they asked for a bounded slice, in which case
            # finishing the whole backlog is not what they asked for.
            if (
                counts.get("remaining", 0) > 0
                and counts.get("generated", 0) > 0
                and call.data.get("max_items") is None
            ):
                coordinator.async_schedule_new_items_ai_run()
            return counts

        hass.services.async_register(
            DOMAIN,
            SERVICE_CATEGORISE_NEW_ITEMS,
            handle_categorise_new_items,
            schema=SERVICE_CATEGORISE_NEW_ITEMS_SCHEMA,
            supports_response=SupportsResponse.OPTIONAL,
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator = hass.data.get(DOMAIN, {}).get("coordinator")
    if coordinator is not None:
        coordinator.async_cancel_ai_run()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        hass.data[DOMAIN].pop("todo_entity", None)
        hass.data[DOMAIN].pop("coordinator", None)
        if hass.services.has_service(DOMAIN, SERVICE_SCAN_NOW):
            hass.services.async_remove(DOMAIN, SERVICE_SCAN_NOW)
        if hass.services.has_service(DOMAIN, SERVICE_UPDATE_AND_ACTION):
            hass.services.async_remove(DOMAIN, SERVICE_UPDATE_AND_ACTION)
        if hass.services.has_service(DOMAIN, SERVICE_UPDATE_ALL):
            hass.services.async_remove(DOMAIN, SERVICE_UPDATE_ALL)
        if hass.services.has_service(DOMAIN, SERVICE_GENERATE_SUMMARIES):
            hass.services.async_remove(DOMAIN, SERVICE_GENERATE_SUMMARIES)
        if hass.services.has_service(DOMAIN, SERVICE_DISMISS_NEW_ITEMS):
            hass.services.async_remove(DOMAIN, SERVICE_DISMISS_NEW_ITEMS)
        if hass.services.has_service(DOMAIN, SERVICE_RESTORE_NEW_ITEMS):
            hass.services.async_remove(DOMAIN, SERVICE_RESTORE_NEW_ITEMS)
        if hass.services.has_service(DOMAIN, SERVICE_CATEGORISE_NEW_ITEMS):
            hass.services.async_remove(DOMAIN, SERVICE_CATEGORISE_NEW_ITEMS)
    return unload_ok
