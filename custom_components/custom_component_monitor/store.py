"""Narrow, guarded compatibility boundary for HACS and HA Marketplace.

Marketplace runtime and review writes are private contracts, checked against
HA 2026.11. No newer HA module is imported, so legacy HACS still works.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
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

    @property
    def label(self) -> str:
        """How this provider is spelled to a person.

        ``domain.title()`` renders "hacs" as "Hacs", which 1.20.0 put in front
        of people for the first time by quoting the scan error on the cards
        (#114). Keep the storage spelling at the boundary and the brand's own
        spelling in the message.
        """
        return {"hacs": "HACS", "marketplace": "Marketplace"}.get(self.domain, self.domain.title())

    @property
    def starting(self) -> bool:
        """Whether the provider still claims to be starting up.

        Read, never trusted as a verdict about the *data*. HACS 2.0.5 can
        leave this true for the whole uptime of Home Assistant: a duplicate
        repository row makes ``set_repository_id`` raise out of its startup
        tasks between clearing ``status.startup`` and setting the running
        stage, so ``stage`` stays "startup" permanently while its storage file
        on disk is complete and correct (#114).
        """
        status = getattr(self.manager, "status", None)
        stage = getattr(self.manager, "stage", None)
        return bool(getattr(status, "startup", False)) or stage in ("setup", "startup")

    def require_readable(self) -> None:
        """Loaded, intact and not disabled — the floor for reading anything.

        Everything :meth:`require_ready` checks *except* whether startup has
        finished. Reading an on-disk snapshot only needs the owner to exist
        and be enabled; waiting for its stage to advance is a separate, and
        stricter, question (#114).
        """
        if self.manager is None:
            raise StoreUnavailable(f"{self.label} is not loaded; check its integration and retry Scan now.")
        system = getattr(self.manager, "system", None)
        status = getattr(self.manager, "status", None)
        if self.domain == "marketplace" and (
            not isinstance(getattr(system, "disabled", None), bool)
            or not isinstance(getattr(status, "startup", None), bool)
        ):
            raise StoreUnavailable("Marketplace readiness interface changed; check for a compatible monitor update.")
        if getattr(system, "disabled", False):
            raise StoreUnavailable(f"{self.label} is disabled; check its integration and retry Scan now.")

    def require_ready(self) -> None:
        """Fail immediately instead of waiting inside HA platform setup.

        The strict gate: readable *and* finished starting. Required before
        reading a provider's live in-memory objects or writing to them, where
        a half-built index really is unusable.
        """
        self.require_readable()
        if self.starting:
            raise StoreUnavailable(f"{self.label} is starting; component data will retry automatically.")


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
    # unloaded HACS is not a licence to scan its last saved file — but a
    # loaded one that never finished starting is a different case, and
    # refusing it was what broke every card in 1.19.0 (#114). HACS writes
    # that file itself and only writes a complete one, so the strict gate is
    # the wrong question to ask of a file: read it, and judge the *data*
    # below.
    if provider.manager is not None or hass.config_entries.async_entries("hacs"):
        provider.require_readable()
    path = Path(hass.config.path(".storage/hacs.repositories"))

    def read() -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict) or raw.get("version") not in ("6", 6) or "data" not in raw:
                raise StoreUnavailable("HACS repository storage format is incompatible; check for a compatible monitor update.")
            return _validate_snapshot(raw["data"])
        except (OSError, ValueError) as err:
            raise StoreUnavailable("HACS repository data cannot be read; check HACS is loaded and retry Scan now.") from err

    snapshot = await hass.async_add_executor_job(read)
    # The one case where "still starting" does decide the answer: an empty
    # store while HACS is mid-startup is a file it has not finished filling
    # in, and publishing zero components from it would read as "everything
    # was uninstalled". A readable stale file is worse than an honest
    # unavailable state (DECISIONS.md, 2026-11-09) — and so is a readable
    # empty one.
    if not snapshot and provider.starting:
        raise StoreUnavailable(f"{provider.label} is starting; component data will retry automatically.")
    return provider.domain, snapshot


def store_status(hass: HomeAssistant) -> dict[str, Any]:
    """Describe the active provider without reading any repository data.

    Separate from :func:`async_repository_snapshot` rather than folded into
    its return value, so that function's ``(domain, snapshot)`` contract is
    unchanged. Published on the sensors so "the monitor is stuck" can be
    diagnosed from the entity attributes instead of the log: a provider
    reporting ``starting`` long after Home Assistant came up is HACS's
    startup having failed, not the monitor's (#114).
    """
    provider = resolve_provider(hass)
    manager = provider.manager
    stage = getattr(manager, "stage", None)
    return {
        "provider": provider.domain,
        "loaded": manager is not None,
        "disabled": bool(getattr(getattr(manager, "system", None), "disabled", False)),
        "stage": stage if isinstance(stage, str) else None,
        "starting": provider.starting,
    }
