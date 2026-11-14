"""Guarded, targeted access to HACS's private review-status contract.

Mirror HACS's own clear-new handler: mutate resolved repository.data.new,
then await data.async_write() once for the batch. Never edit HACS's store or
clear a whole category. HACS owns serialization and frontend dispatch.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from homeassistant.core import HomeAssistant

from .const import NEW_ITEM_HACS_KINDS
from .new_items import item_key
from .store import StoreUnavailable, resolve_provider


@dataclass
class HacsSyncResult:
    """Completed targets (including safe no-ops) and a retryable error."""

    completed: set[str] = field(default_factory=set)
    changed: int = 0
    error: str | None = None


async def async_sync_hacs_new(
    hass: HomeAssistant, desired: Mapping[str, bool]
) -> HacsSyncResult:
    """Set only selected, uninstalled supported repositories' New flags.

    Callers serialize monitor decisions and this operation with their own
    lock. Missing/installed targets are safe no-ops, not arbitrary new repos
    to create. Unavailable HACS and failed writes leave work retryable.
    """
    result = HacsSyncResult()
    if not desired:
        return result
    provider = resolve_provider(hass)
    try:
        provider.require_ready()
    except StoreUnavailable as err:
        result.error = str(err)
        return result
    hacs = provider.manager
    label = provider.label
    repositories = getattr(hacs, "repositories", None)
    lookup = getattr(repositories, "get_by_full_name", None)
    writer = getattr(getattr(hacs, "data", None), "async_write", None)
    if not callable(lookup) or not callable(writer):
        result.error = f"{label} is not ready or its review-status interface is unavailable."
        return result
    if getattr(getattr(hacs, "system", None), "disabled", False) or getattr(
        getattr(hacs, "status", None), "startup", False
    ):
        result.error = f"{label} is starting or disabled; review syncing will retry on the next scan."
        return result

    changes: list[tuple[object, bool]] = []
    try:
        normalized = {item_key(name): new for name, new in desired.items()}
        for full_name, new in normalized.items():
            key = item_key(full_name)
            repository = lookup(key)
            data = getattr(repository, "data", None)
            if repository is None:
                result.completed.add(key)
                continue
            if (
                item_key(getattr(data, "full_name", "")) != key
                or not isinstance(getattr(data, "new", None), bool)
            ):
                raise ValueError("HACS repository identity or New flag does not match the expected contract")
            if getattr(data, "installed", False) or getattr(data, "category", None) not in NEW_ITEM_HACS_KINDS:
                result.completed.add(key)
                continue
            if data.new != new:
                changes.append((data, data.new))
                data.new = new
            result.completed.add(key)
        if changes:
            # Without force: never bypass disabled HACS. Its writer also
            # sends the normal repository/config dispatcher notifications.
            await writer()
        result.changed = len(changes)
    except (Exception, asyncio.CancelledError) as err:
        for data, previous in changes:
            data.new = previous
        result.completed.clear()
        if isinstance(err, asyncio.CancelledError):
            raise
        # No exception text: upstream errors can contain tokens or URLs.
        result.error = f"Could not save review status in {label}; syncing will retry on the next scan."
    return result
