"""Narrow, guarded compatibility boundary for HACS and HA Marketplace.

Marketplace runtime and review writes are private contracts, checked against
HA 2026.11. No newer HA module is imported, so legacy HACS still works.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant

UPDATE_PROVIDERS = frozenset({"hacs", "marketplace"})


class StoreUnavailable(Exception):
    """The selected store cannot safely provide a complete snapshot."""


@dataclass(frozen=True)
class Provider:
    """The active owner, never a fallback to a retired owner's stale data."""

    domain: str
    manager: Any = None

    def require_ready(self) -> None:
        """Fail immediately instead of waiting inside HA platform setup."""
        if self.manager is None:
            raise StoreUnavailable(f"{self.domain.title()} is not loaded; check its integration and retry Scan now.")
        system = getattr(self.manager, "system", None)
        if getattr(system, "disabled", False):
            raise StoreUnavailable(f"{self.domain.title()} is disabled; check its integration and retry Scan now.")
        status = getattr(self.manager, "status", None)
        stage = getattr(self.manager, "stage", None)
        if getattr(status, "startup", False) or stage in ("setup", "startup"):
            raise StoreUnavailable(f"{self.domain.title()} is starting; component data will retry automatically.")


def resolve_provider(hass: HomeAssistant) -> Provider:
    """Resolve each time, including across provider reloads and migration."""
    entries = hass.config_entries.async_entries("marketplace")
    if entries:
        entry = entries[0]
        manager = getattr(entry, "runtime_data", None) if entry.state is ConfigEntryState.LOADED else None
        return Provider("marketplace", manager)
    entries = hass.config_entries.async_entries("hacs")
    if entries and entries[0].state is not ConfigEntryState.LOADED:
        return Provider("hacs")
    return Provider("hacs", hass.data.get("hacs"))


# Fields used by the existing scanner. Keep storage spelling at this boundary.
_REPOSITORY_FIELDS = (
    "full_name", "category", "installed", "new", "domain", "description",
    "topics", "stargazers_count", "downloads", "last_updated", "manifest_name",
    "installed_commit", "last_commit", "last_version", "releases", "directory",
    "file_name", "default_branch", "selected_tag",
)


def _marketplace_snapshot(manager: Any) -> dict[str, dict[str, Any]]:
    repositories = getattr(getattr(manager, "repositories", None), "list_all", None)
    if not isinstance(repositories, (list, tuple)):
        raise StoreUnavailable("Marketplace repository interface is unavailable; check for a compatible monitor update.")
    result = {}
    for repository in repositories:
        data = repository.data
        row = {key: deepcopy(getattr(data, key, None)) for key in _REPOSITORY_FIELDS}
        row["version_installed"] = data.installed_version
        row["repository_manifest"] = deepcopy(repository.repository_manifest.manifest)
        row["name"] = repository.display_name
        row["local_path"] = repository.content.path.local
        row["pending_update"] = repository.pending_update
        # The manager's decision includes pre-releases and branch tracking.
        branch = data.selected_tag is not None and data.selected_tag == data.default_branch
        row["current_version"] = data.installed_commit if branch else repository.display_installed_version
        row["available_version"] = data.last_commit if branch else repository.display_available_version
        result[str(data.id)] = row
    return _validate_snapshot(result)


def _validate_snapshot(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        raise StoreUnavailable("Component repository data is incompatible; restore the store or update the monitor.")
    for row in raw.values():
        if not isinstance(row, dict) or not isinstance(row.get("full_name"), str) or not isinstance(row.get("category"), str):
            raise StoreUnavailable("Component repository data is incomplete; restore the store or retry after startup.")
    return raw


async def async_repository_snapshot(hass: HomeAssistant) -> tuple[str, dict[str, Any]]:
    """One detached snapshot per scan; absence/corruption is never empty."""
    provider = resolve_provider(hass)
    if provider.domain == "marketplace":
        provider.require_ready()
        try:
            return provider.domain, _marketplace_snapshot(provider.manager)
        except (AttributeError, TypeError, ValueError, KeyError) as err:
            raise StoreUnavailable("Marketplace repository interface changed; check for a compatible monitor update.") from err

    # Legacy HACS keeps its established on-disk format. A configured but
    # unloaded HACS is not a licence to scan its last saved file.
    if provider.manager is not None or hass.config_entries.async_entries("hacs"):
        provider.require_ready()
    path = Path(hass.config.path(".storage/hacs.repositories"))

    def read() -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") != 1 or "data" not in raw:
                raise StoreUnavailable("HACS repository storage format is incompatible; check for a compatible monitor update.")
            return _validate_snapshot(raw["data"])
        except (OSError, ValueError) as err:
            raise StoreUnavailable("HACS repository data cannot be read; check HACS is loaded and retry Scan now.") from err

    return provider.domain, await hass.async_add_executor_job(read)
