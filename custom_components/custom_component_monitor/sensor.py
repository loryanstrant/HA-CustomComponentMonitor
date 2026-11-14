"""Sensor platform for Custom Component Monitor."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import yaml
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

from aiohttp import ClientError, ClientTimeout

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import __version__ as HA_VERSION
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    AI_CACHE_MAX_AGE_DAYS,
    AI_CACHE_MAX_ENTRIES,
    AI_CACHE_STORAGE_KEY,
    AI_CACHE_STORAGE_VERSION,
    AI_CALL_TIMEOUT,
    AI_CATEGORY_ALIASES,
    AI_CATEGORY_OPTIONS,
    AI_FAILURE_BACKOFF_MINUTES,
    AI_MAX_ATTEMPTS,
    AI_MAX_PER_SCAN,
    ATTR_AI_ENABLED,
    ATTR_AI_ETA_SECONDS,
    ATTR_AI_IN_PROGRESS,
    ATTR_AI_LAST_ERROR,
    ATTR_AI_LAST_RUN,
    ATTR_AI_PENDING,
    ATTR_AI_RATE_PER_MINUTE,
    ATTR_BY_CATEGORY,
    ATTR_CATEGORIES,
    ATTR_COMPONENTS,
    ATTR_DATA_STALE,
    ATTR_DISMISSED_COUNT,
    ATTR_ENTITY_ID,
    ATTR_EXCLUDED_COMPONENTS,
    ATTR_HA_REQUIREMENT,
    ATTR_HIDDEN,
    ATTR_HIDDEN_COUNT,
    ATTR_HIDDEN_TRUNCATED,
    ATTR_HOME_COUNTRY,
    ATTR_INSTALL_DATES_SINCE,
    ATTR_ITEMS,
    ATTR_ITEMS_TRUNCATED,
    ATTR_LAST_SUCCESSFUL_SCAN,
    ATTR_SCAN_ERROR,
    ATTR_STORE_STATUS,
    ATTR_SUMMARY,
    ATTR_TOTAL_COMPONENTS,
    ATTR_TOTAL_NEW,
    ATTR_UNUSED_COMPONENTS,
    ATTR_UPDATES,
    ATTR_UPDATES_BLOCKED,
    ATTR_UPDATES_UNKNOWN_REQUIREMENT,
    ATTR_USED_COMPONENTS,
    CATEGORY_MAP,
    CONF_AI_CATEGORIZATION_ENABLED,
    CONF_AI_TASK_ENTITY,
    CONF_AUTO_HIDE_FOREIGN,
    CONF_EXCLUDE,
    CONF_EXTRA_HOME_TERMS,
    CONF_HOME_COUNTRY,
    CONF_NEW_ITEMS_AI_BUDGET_SECONDS,
    CONF_NEW_ITEMS_ENABLED,
    DEFAULT_HOME_COUNTRY,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    NEW_ITEM_CATEGORY_ALIASES,
    NEW_ITEM_CATEGORY_DEFINITIONS,
    NEW_ITEM_CATEGORY_FALLBACK,
    NEW_ITEM_CATEGORY_OPTIONS,
    NEW_ITEM_HACS_KINDS,
    NEW_ITEMS_AI_CATCHUP_SECONDS,
    NEW_ITEMS_AI_CONCURRENCY_MAX,
    NEW_ITEMS_AI_CONCURRENCY_START,
    NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS,
    NEW_ITEMS_AI_GROW_GAIN,
    NEW_ITEMS_AI_MAX_BUDGET_SECONDS,
    NEW_ITEMS_AI_MAX_FAILED_WAVES,
    NEW_ITEMS_AI_MAX_PASSES,
    NEW_ITEMS_AI_MIN_BUDGET_SECONDS,
    NEW_ITEMS_AI_REPROBE_WAVES,
    NEW_ITEMS_MAX_EXPOSED,
    NEW_ITEMS_MAX_EXPOSED_HIDDEN,
    NEW_ITEMS_WINDOW_DAYS,
    SCAN_RETRY_INTERVAL,
    SCAN_RETRY_MAX_INTERVAL,
    SCAN_STALE_GRACE,
    SENSOR_ALL_COMPONENTS,
    SENSOR_HACS_UPDATES,
    SENSOR_NEW_ITEMS,
    SENSOR_UNUSED_FRONTEND,
    SENSOR_UNUSED_INTEGRATIONS,
    SENSOR_UNUSED_THEMES,
    TARGET_MANIFEST_BUDGET,
    TARGET_MANIFEST_CONCURRENCY,
    TARGET_MANIFEST_MAX_BYTES,
    TARGET_MANIFEST_TIMEOUT,
    TARGET_MANIFEST_URL,
)
from .compat import (
    block_sentence,
    ha_requirement,
    is_blocked,
    requirement_counts,
    strictest,
    target_unreadable_requirement,
)
from .countries import CountryVerdict, classify, scope_is_foreign
from .hacs_sync import async_sync_hacs_new
from .install_dates import InstallDateRegistry
from .new_items import NewItemRegistry, display_name_for
from .store import (
    UPDATE_PROVIDERS,
    StoreUnavailable,
    async_repository_snapshot,
    store_status,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

# The category catalogue handed to the model, built once (v1.17.0).
#
# Built at import rather than per item for two reasons. It is identical for
# every item in a run, so re-joining ~20 definitions a few hundred times is
# pure waste; and because it is identical it belongs at the *front* of the
# prompt, where a local backend's prefix cache can reuse it across the whole
# batch instead of re-reading it for each listing.
NEW_ITEM_CATEGORY_CATALOGUE = "\n".join(
    f"- {label}: {NEW_ITEM_CATEGORY_DEFINITIONS[label]}"
    for label in NEW_ITEM_CATEGORY_OPTIONS
)


# ---------------------------------------------------------------------------
# Matching helpers — shared by the scanner, the coordinator and the card
# ---------------------------------------------------------------------------


def norm_name(value: Any) -> str:
    """Normalise an update name for matching (drop ' update', punctuation)."""
    text = re.sub(r"\s+update$", "", str(value or ""), flags=re.IGNORECASE)
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def norm_version(value: Any) -> str:
    """Normalise a version string (strip a leading 'v', lower-case)."""
    return re.sub(r"^v", "", str(value or "").strip().lower())


def repo_key(url: Any) -> str:
    """Return ``owner/repo`` from a GitHub URL, lower-cased.

    Mirrors ``_repoKey`` in the cards so the backend and the frontend key the
    same update the same way — a release URL and a repository URL both reduce
    to the same identity (#91).
    """
    match = re.search(
        r"github\.com/([^/]+/[^/#?]+)", str(url or ""), flags=re.IGNORECASE
    )
    return re.sub(r"\.git$", "", match.group(1).lower()) if match else ""


class AiCacheStore(Store):
    """The AI summary cache store, with its v1 -> v2 migration (#91).

    v1 was a flat ``{name|version: {categories, summary}}`` dict. v2 keys on
    repo identity and carries retry bookkeeping, so the old rows are parked
    under ``legacy`` and promoted lazily as each update is next looked up —
    nobody pays twice for a summary they already have.
    """

    async def _async_migrate_func(
        self, old_major_version: int, old_minor_version: int, old_data: dict[str, Any]
    ) -> dict[str, Any]:
        if old_major_version == 1:
            return {"entries": {}, "legacy": old_data or {}}
        return old_data


# ---------------------------------------------------------------------------
# ComponentScanner — reads HACS data and enriches it
# ---------------------------------------------------------------------------


class InstallInfo(NamedTuple):
    """Where a component lives on disk and how long it has been installed."""

    path: Path | None
    days: int | None
    estimated: bool


class ComponentScanner:
    """Scanner for HACS-installed custom components."""

    def __init__(
        self,
        hass: HomeAssistant,
        install_dates: InstallDateRegistry | None = None,
    ) -> None:
        """Initialize the scanner.

        *install_dates* is optional so the scanner stays independently
        constructible; without it, install ages fall back to the filesystem
        estimate and are always reported as estimated.
        """
        self.hass = hass
        self.config_dir = Path(hass.config.config_dir)
        self._hacs_repositories: dict[str, Any] | None = None
        self.provider: str | None = None
        self._install_dates = install_dates
        # (owner/repo, tag) -> that tag's own hacs.json (#116). Successes only,
        # and only for tags still on offer — see
        # :meth:`async_verify_update_requirements`.
        self._target_manifest_cache: dict[tuple[str, str], dict[str, Any]] = {}

    # -- storage helpers -----------------------------------------------------

    async def _load_storage_file(self, filename: str) -> dict[str, Any]:
        """Load a .storage/<filename> JSON file and return its ``data`` key."""
        storage_path = self.config_dir / ".storage" / filename

        def _read() -> dict[str, Any]:
            if not storage_path.exists():
                return {}
            with open(storage_path, encoding="utf-8") as fh:
                return json.load(fh)

        try:
            raw = await self.hass.async_add_executor_job(_read)
            return raw.get("data", {})
        except (json.JSONDecodeError, OSError) as exc:
            _LOGGER.debug("Could not load storage file %s: %s", filename, exc)
            return {}

    @staticmethod
    def _make_ha_yaml_loader() -> type:
        """Return a YAML SafeLoader subclass that handles HA custom tags."""
        loader = type("HALoader", (yaml.SafeLoader,), {})
        # Handle !include, !include_dir_merge_named, !secret, etc.
        for tag in ("!include", "!include_dir_merge_named",
                    "!include_dir_list", "!include_dir_named",
                    "!secret", "!env_var"):
            loader.add_constructor(
                tag, lambda l, n: l.construct_scalar(n)
            )
        return loader

    def _list_yaml_dashboard_files(self) -> list[Path]:
        """Return file paths for YAML-mode Lovelace dashboards.

        Reads ``configuration.yaml`` to find dashboards registered with
        ``mode: yaml`` and returns their resolved file paths.
        """
        config_yaml = self.config_dir / "configuration.yaml"
        if not config_yaml.exists():
            return []
        try:
            loader = self._make_ha_yaml_loader()
            with open(config_yaml, encoding="utf-8") as fh:
                raw = yaml.load(fh, Loader=loader) or {}
        except (yaml.YAMLError, OSError):
            return []
        lovelace = raw.get("lovelace", {}) or {}
        # `lovelace:` / `dashboards:` may be a string (e.g. `dashboards: !include
        # dashboards.yaml`, which our loader represents as a scalar) rather than a
        # mapping. Guard so the whole scan doesn't abort (#54).
        if not isinstance(lovelace, dict):
            return []
        dashboards = lovelace.get("dashboards", {}) or {}
        if not isinstance(dashboards, dict):
            dashboards = {}
        paths: list[Path] = []
        for dash_cfg in dashboards.values():
            if not isinstance(dash_cfg, dict):
                continue
            if dash_cfg.get("mode") != "yaml":
                continue
            filename = dash_cfg.get("filename")
            if not filename:
                continue
            resolved = self.config_dir / filename
            if resolved.exists():
                paths.append(resolved)
        # Also check if the default dashboard is YAML mode
        if lovelace.get("mode") == "yaml":
            default = self.config_dir / lovelace.get("filename", "ui-lovelace.yaml")
            if default.exists():
                paths.append(default)
        return paths

    async def _load_hacs_repositories(self) -> dict[str, Any]:
        """Return HACS repository dict (cached for the scan cycle)."""
        if self._hacs_repositories is None:
            self.provider, self._hacs_repositories = await async_repository_snapshot(self.hass)
        return self._hacs_repositories

    def _invalidate_cache(self) -> None:
        """Clear cached data so the next scan re-reads from disk."""
        self._hacs_repositories = None

    # -- date helpers --------------------------------------------------------

    def _get_install_timestamp(self, path: Path) -> float | None:
        """Return the best-effort install timestamp for *path*.

        On Linux ``st_birthtime`` is unavailable, so we use
        ``min(st_ctime, st_mtime)`` as an approximation.

        This is only ever an *estimate*: a HACS update rewrites the directory
        and resets it. It seeds :class:`InstallDateRegistry`, which is what
        actually answers "how long has this been installed" (#93).
        """
        try:
            if not path.exists():
                return None
            stat = path.stat()
            if hasattr(stat, "st_birthtime"):
                return stat.st_birthtime
            return min(stat.st_ctime, stat.st_mtime)
        except OSError:
            return None

    def _resolve_install_path(self, repo: dict[str, Any]) -> Path | None:
        """Determine the local filesystem path for an installed HACS repo."""
        local_path = repo.get("local_path")
        if local_path:
            candidate = Path(local_path)
            if candidate.is_relative_to(self.config_dir) and candidate.exists():
                return candidate
        category = repo.get("category", "")
        full_name = repo.get("full_name", "")
        repo_short = full_name.split("/")[-1] if "/" in full_name else full_name
        domain = repo.get("domain", "")

        if category == "integration":
            if domain:
                candidate = self.config_dir / "custom_components" / domain
                if candidate.exists():
                    return candidate
            # fallback: try repo manifest domain
            rm = repo.get("repository_manifest", {})
            if isinstance(rm, dict) and rm.get("domain"):
                candidate = self.config_dir / "custom_components" / rm["domain"]
                if candidate.exists():
                    return candidate
        elif category == "theme":
            rm = repo.get("repository_manifest", {}) or {}
            # Build candidate names from various sources
            candidates = [repo_short]
            if repo.get("name"):
                candidates.append(repo["name"])
            rm_name = rm.get("name", "")
            if rm_name:
                candidates.append(rm_name)
            # Strip common repo name prefixes
            for prefix in ("ha-", "homeassistant-", "home-assistant-", "lovelace-"):
                if repo_short.lower().startswith(prefix):
                    candidates.append(repo_short[len(prefix):])
            # Also strip trailing "-theme" / "-themes" suffix
            for cand in list(candidates):
                low = cand.lower()
                for suffix in ("-theme", "-themes"):
                    if low.endswith(suffix):
                        candidates.append(cand[:len(cand) - len(suffix)])
            # Try the YAML filename (without extension) from manifest
            rm_filename = rm.get("filename", "")
            if rm_filename:
                stem = rm_filename.rsplit(".", 1)[0]
                candidates.append(stem)
            themes_dir = self.config_dir / "themes"
            for name in candidates:
                if not name:
                    continue
                candidate = themes_dir / name
                if candidate.exists():
                    return candidate
            # Broader match: check if dir name appears in a candidate or vice versa
            if themes_dir.exists():
                for child in themes_dir.iterdir():
                    if child.is_dir():
                        dir_low = child.name.lower()
                        for name in candidates:
                            if not name:
                                continue
                            name_low = name.lower()
                            if name_low in dir_low or dir_low in name_low:
                                return child
        elif category == "plugin":
            candidates = [repo_short]
            if repo.get("name"):
                candidates.append(repo["name"])
            if repo_short.startswith("ha-"):
                candidates.append(repo_short[3:])
            community_dir = self.config_dir / "www" / "community"
            for name in candidates:
                if not name:
                    continue
                candidate = community_dir / name
                if candidate.exists():
                    return candidate

        return None

    def _resolve_install_path_and_timestamp(
        self, repo: dict[str, Any]
    ) -> tuple[Path | None, float | None]:
        """Install path plus its filesystem timestamp, in one executor hop."""
        path = self._resolve_install_path(repo)
        if path is None:
            return None, None
        return path, self._get_install_timestamp(path)

    async def _install_info(
        self, repo: dict[str, Any], now: datetime
    ) -> InstallInfo:
        """Return where *repo* lives on disk and how long it has been installed.

        The filesystem timestamp is only a seed; the install-date registry is
        the authority, because a HACS update resets the files (#93).
        """
        path, timestamp = await self.hass.async_add_executor_job(
            self._resolve_install_path_and_timestamp, repo
        )
        if self._install_dates is not None:
            days, estimated = self._install_dates.observe(repo, timestamp, now)
            return InstallInfo(path, days, estimated)

        days = None
        if timestamp is not None:
            # Compare elapsed time in UTC, just like the install-date registry.
            # Local wall-clock subtraction adds/removes an hour across DST.
            installed_dt = datetime.fromtimestamp(timestamp, tz=dt_util.UTC)
            days = max(0, (dt_util.as_utc(now) - installed_dt).days)
        return InstallInfo(path, days, True)

    # -- theme helpers -------------------------------------------------------

    @staticmethod
    def _normalise_theme_name(name: str) -> str:
        """Normalise a theme name for comparison.

        Converts to lowercase and replaces spaces/underscores with dashes so
        that ``Transformers Dirty Metal`` matches ``transformers-dirty-metal``.
        """
        return re.sub(r"[\s_]+", "-", name.strip()).lower()

    async def _get_theme_variants(self, theme_path: Path) -> list[str]:
        """Extract theme variant names from a theme YAML directory or file.

        Theme YAML files use top-level keys as variant names, e.g.::

            weylandyutani:
              primary-color: "#00ff00"
            Transformers Dark:
              primary-color: "#111"
        """

        def _read_variants() -> list[str]:
            files: list[Path] = []
            if theme_path.is_file():
                files = [theme_path]
            elif theme_path.is_dir():
                files = list(theme_path.glob("*.yaml")) + list(
                    theme_path.glob("*.yml")
                )
            variants: list[str] = []
            for fp in files:
                try:
                    for line in fp.read_text(encoding="utf-8").splitlines():
                        # Top-level key: no leading whitespace, has a colon
                        if (
                            line
                            and not line[0].isspace()
                            and ":" in line
                            and not line.startswith("#")
                            and not line.startswith("---")
                        ):
                            key = line.split(":", 1)[0].strip().strip("\"'")
                            if key and len(key) < 80:
                                variants.append(key)
                except OSError:
                    continue
            return variants

        return await self.hass.async_add_executor_job(_read_variants)

    async def _collect_used_theme_names(self) -> set[str]:
        """Return the normalised set of theme names actively in use.

        Checks:
        * system-wide default theme (core.config → frontend)
        * per-user theme (auth → users, frontend.user_data_*)
        * all lovelace dashboards (view-level and card-level ``theme`` keys)
        """
        used: set[str] = set()

        # 1) System default theme in core.config
        core_cfg = await self._load_storage_file("core.config")
        frontend = core_cfg.get("frontend", {})
        if isinstance(frontend, dict):
            default_theme = frontend.get("selected_theme") or frontend.get("theme")
            if default_theme and isinstance(default_theme, str):
                used.add(self._normalise_theme_name(default_theme))

        # 2) Per-user theme preferences
        auth_data = await self._load_storage_file("auth")
        for user in auth_data.get("users", []):
            for key in ("user_data", "local_data", "data"):
                ud = user.get(key, {})
                if isinstance(ud, dict):
                    t = ud.get("theme")
                    if t and isinstance(t, str):
                        used.add(self._normalise_theme_name(t))

        # Also check frontend.user_data_* files
        def _list_frontend_files() -> list[str]:
            storage = self.config_dir / ".storage"
            if not storage.exists():
                return []
            return [
                f.name
                for f in storage.iterdir()
                if f.is_file() and f.name.startswith("frontend")
            ]

        frontend_files = await self.hass.async_add_executor_job(
            _list_frontend_files
        )
        for fname in frontend_files:
            fdata = await self._load_storage_file(fname)
            if isinstance(fdata, dict):
                t = fdata.get("selectedTheme") or fdata.get("selected_theme")
                if t and isinstance(t, str):
                    used.add(self._normalise_theme_name(t))

        # 3) All lovelace dashboard files — recursive search for "theme" keys
        def _list_lovelace_files() -> list[str]:
            storage = self.config_dir / ".storage"
            if not storage.exists():
                return []
            skip = {"lovelace_resources", "lovelace_dashboards"}
            return [
                f.name
                for f in storage.iterdir()
                if f.is_file()
                and f.name.startswith("lovelace")
                and f.name not in skip
            ]

        lovelace_files = await self.hass.async_add_executor_job(
            _list_lovelace_files
        )

        def _read_json(path: Path) -> dict:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)

        for lf in lovelace_files:
            lf_path = self.config_dir / ".storage" / lf
            try:
                raw = await self.hass.async_add_executor_job(_read_json, lf_path)
            except (json.JSONDecodeError, OSError):
                continue
            self._extract_theme_refs(raw, used)

        # Also scan YAML-mode dashboards for theme references
        def _read_yaml(path: Path) -> Any:
            loader = self._make_ha_yaml_loader()
            with open(path, encoding="utf-8") as fh:
                return yaml.load(fh, Loader=loader)

        yaml_dashboards = await self.hass.async_add_executor_job(
            self._list_yaml_dashboard_files
        )
        for yf in yaml_dashboards:
            try:
                raw = await self.hass.async_add_executor_job(_read_yaml, yf)
            except (yaml.YAMLError, OSError):
                continue
            if raw:
                self._extract_theme_refs(raw, used)

        _LOGGER.debug("Used theme names (normalised): %s", used)
        return used

    def _extract_theme_refs(self, obj: Any, out: set[str]) -> None:
        """Recursively collect all ``theme`` string values from *obj*."""
        if isinstance(obj, dict):
            t = obj.get("theme")
            if isinstance(t, str) and t:
                out.add(self._normalise_theme_name(t))
            for v in obj.values():
                self._extract_theme_refs(v, out)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_theme_refs(item, out)

    async def scan_themes(self) -> dict[str, Any]:
        """Scan HACS-installed themes and determine usage.

        Returns ``{"total": int, "used": [...], "unused": [...]}``.
        """
        hacs_repos = await self._load_hacs_repositories()
        used_names = await self._collect_used_theme_names()
        now = dt_util.now()

        used_list: list[dict[str, Any]] = []
        unused_list: list[dict[str, Any]] = []

        for _repo_id, repo in hacs_repos.items():
            if not isinstance(repo, dict):
                continue
            if repo.get("category") != "theme" or not repo.get("installed", False):
                continue

            full_name = repo.get("full_name", "")
            repo_manifest = repo.get("repository_manifest", {}) or {}
            name = (
                repo.get("manifest_name")
                or repo.get("name")
                or repo_manifest.get("name")
                or (full_name.split("/")[-1] if "/" in full_name else full_name)
            )

            install = await self._install_info(repo, now)

            # Get variant names from the actual YAML file(s)
            variants: list[str] = []
            if install.path is not None:
                variants = await self._get_theme_variants(install.path)

            # The repo is "used" if ANY of its variants appear in used_names
            is_used = False
            if variants:
                for v in variants:
                    if self._normalise_theme_name(v) in used_names:
                        is_used = True
                        break
            else:
                # No variants extracted — fall back to repo name matching
                if self._normalise_theme_name(name) in used_names:
                    is_used = True

            entry = {
                "name": name,
                "repository": (
                    f"https://github.com/{full_name}" if full_name else ""
                ),
                "version": repo.get("version_installed", "unknown"),
                "days_installed": install.days,
                "install_date_estimated": install.estimated,
                "variants": len(variants),
            }

            if is_used:
                used_list.append(entry)
            else:
                unused_list.append(entry)

        return {
            "total": len(used_list) + len(unused_list),
            "used": used_list,
            "unused": unused_list,
        }

    # -- frontend / card helpers ---------------------------------------------

    def _derive_card_types(self, repo: dict[str, Any]) -> list[str]:
        """Derive the custom card type name(s) a HACS plugin provides.

        Returns lowercased card type names (without the ``custom:`` prefix).
        E.g. ``["weather-forecast-card"]``.

        Uses multiple strategies to handle cases where the JS filename
        doesn't match the registered custom element name (e.g. Firemote,
        Mushroom).
        """
        rm = repo.get("repository_manifest", {}) or {}
        full_name = repo.get("full_name", "")
        repo_short = full_name.split("/")[-1] if "/" in full_name else full_name

        names: list[str] = []

        # 1) From manifest filename
        manifest_fn = rm.get("filename", "")
        if manifest_fn and manifest_fn.endswith(".js"):
            names.append(manifest_fn[:-3].lower())

        # 2) Scan the community directory for .js files and extract
        #    customElements.define calls from them
        candidates = [repo_short]
        if repo_short.startswith("ha-"):
            candidates.append(repo_short[3:])
        if repo_short.startswith("HA-"):
            candidates.append(repo_short[3:])
        community_dir = self.config_dir / "www" / "community"
        for cname in candidates:
            d = community_dir / cname
            if d.is_dir():
                for js_file in d.glob("*.js"):
                    stem = js_file.stem.lower()
                    if stem not in names and not stem.endswith(".gz"):
                        names.append(stem)
                    # Strip common packaging suffixes so e.g.
                    # "mini-media-player-bundle" → "mini-media-player" (#60).
                    for suf in ("-bundle", "-min", ".min", "-umd", "-esm"):
                        if stem.endswith(suf):
                            base = stem[: -len(suf)]
                            if base and base not in names:
                                names.append(base)
                    # Extract actual registered custom element names from JS
                    ce_names = self._extract_custom_elements(js_file)
                    for ce in ce_names:
                        if ce not in names:
                            names.append(ce)

        # 2b) The repo name itself is very commonly the card type, even when the
        #     main element is registered via a non-literal the JS scan can't see
        #     (e.g. mini-media-player). Add it (and prefix-stripped form) (#60).
        repo_name = repo_short.lower()
        for cand in (repo_name, repo_name[3:] if repo_name.startswith("ha-") else ""):
            if cand and cand not in names:
                names.append(cand)

        # 3) Add prefix-stripped variants for every name collected so far
        #    (e.g. "lovelace-horizon-card" → "horizon-card")
        stripped_extras: list[str] = []
        for n in names:
            for prefix in ("ll-strategy-", "lovelace-", "ha-"):
                if n.startswith(prefix):
                    stripped = n[len(prefix):]
                    if stripped and stripped not in names and stripped not in stripped_extras:
                        stripped_extras.append(stripped)
        names.extend(stripped_extras)

        # 4) Fall back to repo short name if nothing found
        if not names:
            stripped = repo_short.lower()
            for prefix in ("ll-strategy-", "lovelace-", "ha-"):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
            names.append(stripped)

        return names

    _CE_DEFINE_RE = re.compile(
        r'customElements\.define\(\s*["\']([a-z0-9](?:[a-z0-9-]*[a-z0-9])?)["\']',
        re.IGNORECASE,
    )

    def _extract_custom_elements(self, js_path: Path) -> list[str]:
        """Extract custom element names from customElements.define() calls.

        Reads up to 1MB of the JS file and returns all unique element names
        that look like card types (contain a hyphen, as required by the
        custom elements spec). Filters out editor elements.
        """
        try:
            # Read a bounded amount to avoid huge memory use on large bundles
            raw = js_path.read_bytes()[:1_048_576].decode("utf-8", errors="ignore")
        except OSError:
            return []

        found: list[str] = []
        for match in self._CE_DEFINE_RE.finditer(raw):
            name = match.group(1).lower()
            # Must contain a hyphen (custom elements spec requirement)
            if "-" not in name:
                continue
            # Skip editor elements — they aren't card types
            if name.endswith("-editor"):
                continue
            if name not in found:
                found.append(name)
        return found

    # -- icon library helpers ------------------------------------------------

    _ICON_PREFIX_RE = re.compile(
        r'window\.custom(?:Iconsets|Icons)\s*\[\s*["\']([a-zA-Z][a-zA-Z0-9_-]*)["\']\s*\]',
    )

    def _extract_icon_prefixes(self, js_path: Path) -> list[str]:
        """Extract icon set prefixes registered by a JS file.

        Looks for patterns like ``window.customIconsets["phu"]`` and
        ``window.customIcons["cil"]``.
        """
        try:
            raw = js_path.read_bytes()[:2_097_152].decode("utf-8", errors="ignore")
        except OSError:
            return []
        found: list[str] = []
        for match in self._ICON_PREFIX_RE.finditer(raw):
            prefix = match.group(1).lower()
            if prefix not in found:
                found.append(prefix)
        return found

    def _has_custom_element_calls(self, js_path: Path) -> bool:
        """Return True if the JS file contains any customElements.define() call.

        Unlike ``_extract_custom_elements`` which only matches string-literal
        element names, this catches minified forms like
        ``customElements.define(e,n)`` as well.
        """
        try:
            raw = js_path.read_bytes()[:2_097_152].decode("utf-8", errors="ignore")
        except OSError:
            return False
        return "customElements.define(" in raw

    def _scan_dir_for_icon_prefixes(
        self, community_dir: Path, cnames: list[str]
    ) -> list[str]:
        """Return icon-set prefixes from .js files in community subdirectories.

        Consolidates the directory scan and file reads into a single executor
        call so no blocking I/O runs on the async event loop.
        """
        prefixes: list[str] = []
        seen: set[str] = set()
        for cname in cnames:
            if not cname:
                continue
            d = community_dir / cname
            if not d.is_dir():
                continue
            for js_file in d.glob("*.js"):
                for p in self._extract_icon_prefixes(js_file):
                    if p not in seen:
                        seen.add(p)
                        prefixes.append(p)
        return prefixes

    def _scan_dir_has_custom_elements(
        self, community_dir: Path, cnames: list[str]
    ) -> bool:
        """Return True if any .js file in community subdirs defines custom elements.

        Consolidates the directory scan and file reads into a single executor
        call so no blocking I/O runs on the async event loop.
        """
        for cname in cnames:
            if not cname:
                continue
            d = community_dir / cname
            if not d.is_dir():
                continue
            for js_file in d.glob("*.js"):
                if self._has_custom_element_calls(js_file):
                    return True
        return False

    async def _collect_used_icon_prefixes(self) -> set[str]:
        """Return the set of non-standard icon prefixes used in dashboards and entity registry."""
        used: set[str] = set()
        icon_re = re.compile(r'"icon"\s*:\s*"([a-zA-Z][a-zA-Z0-9_-]*):')
        builtin = {"mdi", "hass", "homeassistant"}

        def _scan_storage() -> set[str]:
            prefixes: set[str] = set()
            storage = self.config_dir / ".storage"
            if not storage.exists():
                return prefixes
            # Scan lovelace dashboards and entity registry
            for fp in storage.iterdir():
                if not fp.is_file():
                    continue
                if not (fp.name.startswith("lovelace") or fp.name == "core.entity_registry"):
                    continue
                try:
                    text = fp.read_text(encoding="utf-8", errors="ignore")
                    for m in icon_re.finditer(text):
                        p = m.group(1).lower()
                        if p not in builtin:
                            prefixes.add(p)
                except OSError:
                    continue
            return prefixes

        used = await self.hass.async_add_executor_job(_scan_storage)

        # Also scan YAML-mode dashboards for icon prefixes
        yaml_icon_re = re.compile(r'icon:\s*["\']?([a-zA-Z][a-zA-Z0-9_-]*):')
        def _scan_yaml_dashboards() -> set[str]:
            prefixes: set[str] = set()
            for yf in self._list_yaml_dashboard_files():
                try:
                    text = yf.read_text(encoding="utf-8", errors="ignore")
                    for m in yaml_icon_re.finditer(text):
                        p = m.group(1).lower()
                        if p not in builtin:
                            prefixes.add(p)
                except OSError:
                    continue
            return prefixes

        yaml_prefixes = await self.hass.async_add_executor_job(
            _scan_yaml_dashboards
        )
        used |= yaml_prefixes

        # Also scan live entity states: integrations can set a custom icon at
        # runtime (e.g. thermal_comfort applies `icon: tc:…`), which never lands
        # in the entity registry on disk but is visible on the entity state (#56).
        prefix_re = re.compile(r"[a-z][a-z0-9_-]*$")
        for state in self.hass.states.async_all():
            icon = state.attributes.get("icon")
            if isinstance(icon, str) and ":" in icon:
                prefix = icon.split(":", 1)[0].strip().lower()
                if prefix and prefix not in builtin and prefix_re.match(prefix):
                    used.add(prefix)

        _LOGGER.debug("Used icon prefixes: %s", used)
        return used

    async def _collect_used_card_types(self) -> set[str]:
        """Return the set of custom card type names found in lovelace dashboards.

        Extracts all ``"type": "custom:xxx"`` values (returning ``xxx`` lowercased)
        and also notes whether ``card_mod`` styling is used anywhere.
        """
        used: set[str] = set()

        def _list_lovelace_files() -> list[str]:
            storage = self.config_dir / ".storage"
            if not storage.exists():
                return []
            skip = {"lovelace_resources", "lovelace_dashboards",
                    "lovelace_resources.backup"}
            return [
                f.name
                for f in storage.iterdir()
                if f.is_file()
                and f.name.startswith("lovelace")
                and f.name not in skip
            ]

        lovelace_files = await self.hass.async_add_executor_job(
            _list_lovelace_files
        )

        def _read_json(path: Path) -> dict:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)

        for lf in lovelace_files:
            lf_path = self.config_dir / ".storage" / lf
            try:
                raw = await self.hass.async_add_executor_job(_read_json, lf_path)
            except (json.JSONDecodeError, OSError):
                continue
            self._extract_card_types(raw, used)

        # Scan YAML-mode dashboards using Home Assistant's native modular loader
        def _read_yaml(path: Path) -> Any:
            from homeassistant.util.yaml import load_yaml
            from homeassistant.exceptions import HomeAssistantError
            try:
                # load_yaml resuelve automáticamente los !include y !secret de forma recursiva
                return load_yaml(str(path))
            except (HomeAssistantError, OSError) as err:
                _LOGGER.error("Error processing YAML file %s: %s", path, err)
                return {}

        yaml_dashboards = await self.hass.async_add_executor_job(
            self._list_yaml_dashboard_files
        )
        
        for yf in yaml_dashboards:
            raw = await self.hass.async_add_executor_job(_read_yaml, yf)
            if raw:
                self._extract_card_types(raw, used)

        _LOGGER.debug("Used card types (custom): %s", used)
        return used
        
    def _extract_card_types(self, obj: Any, out: set[str]) -> None:
        """Recursively collect custom card types and card_mod usage."""
        if isinstance(obj, dict):
            t = obj.get("type")
            if isinstance(t, str) and t.startswith("custom:"):
                out.add(t[7:].lower())  # strip "custom:" prefix
            # card-mod utility detection
            if "card_mod" in obj:
                out.add("__card_mod__")
            for v in obj.values():
                self._extract_card_types(v, out)
        elif isinstance(obj, list):
            for item in obj:
                self._extract_card_types(item, out)

    async def scan_frontend(self) -> dict[str, Any]:
        """Scan HACS-installed frontend cards and determine usage.

        Returns ``{"total": int, "used": [...], "unused": [...]}``.
        """
        hacs_repos = await self._load_hacs_repositories()
        used_types = await self._collect_used_card_types()
        used_icon_prefixes = await self._collect_used_icon_prefixes()
        now = dt_util.now()

        used_list: list[dict[str, Any]] = []
        unused_list: list[dict[str, Any]] = []

        for _repo_id, repo in hacs_repos.items():
            if not isinstance(repo, dict):
                continue
            if repo.get("category") != "plugin" or not repo.get("installed", False):
                continue

            full_name = repo.get("full_name", "")
            repo_manifest = repo.get("repository_manifest", {}) or {}
            name = (
                repo.get("manifest_name")
                or repo.get("name")
                or repo_manifest.get("name")
                or (full_name.split("/")[-1] if "/" in full_name else full_name)
            )

            install = await self._install_info(repo, now)

            card_types = await self.hass.async_add_executor_job(
                self._derive_card_types, repo
            )

            # card-mod is a utility — it's used if any card has card_mod styling
            repo_short = (
                full_name.split("/")[-1].lower() if "/" in full_name else ""
            )
            is_card_mod = "card-mod" in repo_short

            # Check if this plugin is an icon library.
            # cnames is also reused below for the custom-element check.
            community_dir = self.config_dir / "www" / "community"
            cnames = list(
                dict.fromkeys(
                    c
                    for c in [
                        repo_short,
                        full_name.split("/")[-1] if "/" in full_name else "",
                    ]
                    if c
                )
            )
            icon_prefixes: list[str] = await self.hass.async_add_executor_job(
                self._scan_dir_for_icon_prefixes, community_dir, cnames
            )

            is_used = False
            if is_card_mod:
                is_used = "__card_mod__" in used_types
            elif icon_prefixes:
                # Icon library — used if any of its prefixes appear in dashboards
                for pfx in icon_prefixes:
                    if pfx in used_icon_prefixes:
                        is_used = True
                        break
            else:
                for ct in card_types:
                    if ct in used_types:
                        is_used = True
                        break
                # Prefix-based matching for multi-card libraries (e.g. Mushroom)
                # If a derived name like "mushroom" matches any used card type
                # starting with "mushroom-", count it as used.
                if not is_used:
                    for ct in card_types:
                        for ut in used_types:
                            if ut.startswith(ct + "-"):
                                is_used = True
                                break
                        if is_used:
                            break
                # Utility plugins (like kiosk-mode) that don't register
                # custom elements or icon sets are "used" if they are
                # registered as a lovelace resource.
                if not is_used and not card_types:
                    is_used = False  # no card types derived at all
                elif not is_used:
                    # Check if this is a utility (no custom element
                    # definitions found in JS — only derived from filename)
                    has_ce = await self.hass.async_add_executor_job(
                        self._scan_dir_has_custom_elements, community_dir, cnames
                    )
                    if not has_ce:
                        # No custom elements found — it's a utility plugin.
                        # Mark as used (it's loaded as a resource for a reason).
                        is_used = True

            entry = {
                "name": name,
                "repository": (
                    f"https://github.com/{full_name}" if full_name else ""
                ),
                "version": repo.get("version_installed", "unknown"),
                "days_installed": install.days,
                "install_date_estimated": install.estimated,
                "card_type": (
                    f"custom:{card_types[0]}" if card_types else "unknown"
                ),
            }

            if is_used:
                used_list.append(entry)
            else:
                unused_list.append(entry)

        return {
            "total": len(used_list) + len(unused_list),
            "used": used_list,
            "unused": unused_list,
        }

    # -- integration helpers --------------------------------------------------

    async def _get_configured_domains(self) -> set[str]:
        """Return the set of integration domains that are in use.

        Combines:
        * ``core.config_entries`` — UI-configured integrations.
        * ``hass.config.components`` — all loaded components at runtime,
          which includes YAML-configured integrations.
        """
        data = await self._load_storage_file("core.config_entries")
        domains: set[str] = set()
        for entry in data.get("entries", []):
            if isinstance(entry, dict):
                d = entry.get("domain")
                if d:
                    domains.add(d)
        # Also include runtime-loaded components (covers YAML-configured
        # integrations that don't use config entries).
        for component in self.hass.config.components:
            # Strip sub-platforms like "bureau_of_meteorology.sensor"
            domain = component.split(".", 1)[0]
            domains.add(domain)
        return domains

    async def scan_integrations(self) -> dict[str, Any]:
        """Scan HACS-installed integrations and determine usage.

        An integration is considered *used* if its domain appears in
        ``core.config_entries`` or ``hass.config.components``.

        Returns ``{"total": int, "used": [...], "unused": [...]}``.
        """
        hacs_repos = await self._load_hacs_repositories()
        configured = await self._get_configured_domains()
        now = dt_util.now()

        used_list: list[dict[str, Any]] = []
        unused_list: list[dict[str, Any]] = []

        for _repo_id, repo in hacs_repos.items():
            if not isinstance(repo, dict):
                continue
            if (
                repo.get("category") != "integration"
                or not repo.get("installed", False)
            ):
                continue

            full_name = repo.get("full_name", "")
            repo_manifest = repo.get("repository_manifest", {}) or {}
            domain = (
                repo.get("domain")
                or repo_manifest.get("domain")
                or ""
            )
            name = (
                repo.get("manifest_name")
                or repo.get("name")
                or repo_manifest.get("name")
                or (full_name.split("/")[-1] if "/" in full_name else full_name)
            )

            install = await self._install_info(repo, now)

            is_used = domain in configured

            entry = {
                "name": name,
                "repository": (
                    f"https://github.com/{full_name}" if full_name else ""
                ),
                "version": repo.get("version_installed", "unknown"),
                "days_installed": install.days,
                "install_date_estimated": install.estimated,
                "domain": domain,
            }

            if is_used:
                used_list.append(entry)
            else:
                unused_list.append(entry)

        return {
            "total": len(used_list) + len(unused_list),
            "used": used_list,
            "unused": unused_list,
        }

    # -- main scan -----------------------------------------------------------

    async def scan_all_hacs_components(self) -> list[dict[str, Any]]:
        """Return a list of all HACS-installed components with metadata."""
        hacs_repos = await self._load_hacs_repositories()
        now = dt_util.now()
        components: list[dict[str, Any]] = []

        for _repo_id, repo in hacs_repos.items():
            if not isinstance(repo, dict):
                continue
            if not repo.get("installed", False):
                continue

            category = repo.get("category", "")
            if category not in CATEGORY_MAP:
                continue

            full_name = repo.get("full_name", "")
            repo_manifest = repo.get("repository_manifest", {}) or {}
            name = (
                repo.get("manifest_name")
                or repo.get("name")
                or repo_manifest.get("name")
                or (full_name.split("/")[-1] if "/" in full_name else full_name)
            )

            install = await self._install_info(repo, now)

            components.append(
                {
                    "name": name,
                    "type": CATEGORY_MAP[category],
                    "repository": (
                        f"https://github.com/{full_name}" if full_name else ""
                    ),
                    "version": repo.get("version_installed", "unknown"),
                    "days_installed": install.days,
                    "install_date_estimated": install.estimated,
                }
            )

        # Sort alphabetically by type then name
        components.sort(key=lambda c: (c["type"], c["name"].lower()))
        return components

    # -- updates -------------------------------------------------------------

    @staticmethod
    def _pending_update_versions(
        repo: dict[str, Any]
    ) -> tuple[str, str] | None:
        """Return ``(current, available)`` if *repo* has a pending update.

        Mirrors HACS's own logic: repositories tracking releases compare the
        installed version against the latest available version, while
        repositories tracking the default branch compare commit hashes. The
        returned versions always come from the same pair that detected the
        update, so a branch-tracked repo reports its commits (not a stale
        ``version_installed`` string) and vice versa.
        """
        if "pending_update" in repo:
            if repo["pending_update"] and repo.get("current_version") and repo.get("available_version"):
                return repo["current_version"], repo["available_version"]
            return None
        version_installed = repo.get("version_installed")
        last_version = repo.get("last_version")
        if repo.get("releases") and version_installed and last_version:
            if version_installed != last_version:
                return version_installed, last_version
            return None

        installed_commit = repo.get("installed_commit")
        last_commit = repo.get("last_commit")
        if installed_commit and last_commit:
            if installed_commit != last_commit:
                return installed_commit, last_commit
            return None

        return None

    def _live_hacs_updates(self) -> list[dict[str, Any]]:
        """Return the live HACS ``update.*`` entities as plain dicts (#91).

        ``.storage/hacs.repositories`` is a snapshot HACS writes periodically
        and it demonstrably lags reality — it can miss an update that HACS has
        already surfaced as an entity, and keep listing one the user installed
        hours ago. The entities are the authority, so they are read here and
        reconciled with the snapshot in :meth:`_reconcile_updates`.

        Entities in ``unavailable``/``unknown`` are skipped rather than treated
        as "no update": a flapping entity must not wipe a genuine pending
        update out of the list.
        """
        registry = er.async_get(self.hass)
        live: list[dict[str, Any]] = []
        for state in self.hass.states.async_all("update"):
            if state.state not in ("on", "off"):
                continue
            entry = registry.async_get(state.entity_id)
            if entry is None or entry.platform not in UPDATE_PROVIDERS:
                continue
            attrs = state.attributes
            live.append(
                {
                    "entity_id": state.entity_id,
                    "on": state.state == "on",
                    "name": re.sub(
                        r"\s+update$",
                        "",
                        str(attrs.get("friendly_name") or "").strip(),
                        flags=re.IGNORECASE,
                    ),
                    "installed_version": attrs.get("installed_version") or "",
                    "latest_version": attrs.get("latest_version") or "",
                    "release_url": attrs.get("release_url") or "",
                }
            )
        return live

    @staticmethod
    def _installed_repo_index(hacs_repos: dict[str, Any]) -> dict[str, str]:
        """Map ``owner/repo`` to its HACS category, for every installed repo.

        Used to give a live-only update (one the snapshot hasn't caught up
        with) the right display type instead of a bare "Other" (#91).
        """
        index: dict[str, str] = {}
        for repo in hacs_repos.values():
            if not isinstance(repo, dict) or not repo.get("installed", False):
                continue
            full_name = str(repo.get("full_name") or "").lower()
            if full_name:
                index[full_name] = repo.get("category", "")
        return index

    @staticmethod
    def _installed_manifest_index(
        hacs_repos: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """Map ``owner/repo`` to its ``hacs.json``, for every installed repo.

        The companion to :meth:`_installed_repo_index`, for the same reason
        (#111): a live-only update has no snapshot row, so without this every
        one of them would report "requirement unknown" — which is a lie when
        the repository declares a minimum Home Assistant version and the
        snapshot simply hasn't caught up yet.
        """
        index: dict[str, dict[str, Any]] = {}
        for repo in hacs_repos.values():
            if not isinstance(repo, dict) or not repo.get("installed", False):
                continue
            full_name = str(repo.get("full_name") or "").lower()
            manifest = repo.get("repository_manifest")
            if full_name and isinstance(manifest, dict):
                index[full_name] = manifest
        return index

    def _reconcile_updates(
        self,
        updates: list[dict[str, Any]],
        hacs_repos: dict[str, Any],
        live: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Reconcile the snapshot-derived list against the live entities (#91).

        The card renders rows from the live ``update.*`` entities, so anything
        the two sources disagree about is either an update that can never be
        summarised (live-only) or a phantom row for a component the user already
        updated (snapshot-only). Both are fixed here so the sensor and the card
        always describe the same set.
        """
        if not live:
            # HACS entities aren't loaded yet (very early in startup). Trust the
            # snapshot rather than flapping the sensor to zero.
            return updates

        # Three ways in, in descending order of confidence. Deliberately NOT
        # by version alone: version strings collide across components ("1.0.0"),
        # and a wrong match here either deletes a genuine pending update or
        # pins another component's release notes to it. An unmatched pair costs
        # a visible duplicate row, which is the far cheaper failure.
        live_by_repo: dict[str, dict[str, Any]] = {}
        live_by_name: dict[str, dict[str, Any]] = {}
        live_by_slug: dict[str, dict[str, Any]] = {}
        for entry in live:
            key = repo_key(entry["release_url"])
            if key:
                live_by_repo.setdefault(key, entry)
            name = norm_name(entry["name"])
            if name:
                live_by_name.setdefault(name, entry)
            # The entity id keeps the repo name even when the friendly name has
            # drifted — "update.dad_jokes_update" vs a HACS manifest_name of
            # "Dad Jokes" but a friendly name of just "Jokes". The id is
            # snake_case, so strip the "_update" suffix before normalising.
            object_id = re.sub(
                r"_update$", "", entry["entity_id"].split(".", 1)[-1]
            )
            slug = norm_name(object_id)
            if slug:
                live_by_slug.setdefault(slug, entry)

        reconciled: list[dict[str, Any]] = []
        matched: set[str] = set()
        for item in updates:
            item_name = norm_name(item.get("name"))
            match = (
                live_by_repo.get(repo_key(item.get("repository")))
                or live_by_name.get(item_name)
                or live_by_slug.get(item_name)
            )
            if match is None:
                # No entity to contradict it — keep the snapshot's word.
                reconciled.append(item)
                continue
            matched.add(match["entity_id"])
            if not match["on"]:
                continue  # already updated (or skipped); drop the phantom row
            item[ATTR_ENTITY_ID] = match["entity_id"]
            if match["installed_version"]:
                item["current_version"] = match["installed_version"]
            if match["latest_version"]:
                item["available_version"] = match["latest_version"]
            reconciled.append(item)

        # Live updates the snapshot hasn't caught up with yet.
        repo_categories = self._installed_repo_index(hacs_repos)
        repo_manifests = self._installed_manifest_index(hacs_repos)
        for entry in live:
            if not entry["on"] or entry["entity_id"] in matched:
                continue
            key = repo_key(entry["release_url"])
            category = repo_categories.get(key, "")
            reconciled.append(
                {
                    "name": entry["name"] or entry["entity_id"],
                    "type": CATEGORY_MAP.get(
                        category, category.replace("_", " ").title() or "Other"
                    ),
                    "repository": f"https://github.com/{key}" if key else "",
                    "current_version": entry["installed_version"],
                    "available_version": entry["latest_version"],
                    ATTR_ENTITY_ID: entry["entity_id"],
                    ATTR_HA_REQUIREMENT: ha_requirement(
                        repo_manifests.get(key), HA_VERSION
                    ),
                }
            )

        return reconciled

    async def scan_updates(self) -> dict[str, Any]:
        """Scan HACS-installed components for pending updates.

        Returns ``{"count": int, "updates": [...]}`` where each update entry
        lists the component name, type, repository and current/available
        versions, plus the matching ``update.*`` entity id when one is known.
        """
        hacs_repos = await self._load_hacs_repositories()
        updates: list[dict[str, Any]] = []

        for _repo_id, repo in hacs_repos.items():
            if not isinstance(repo, dict):
                continue
            if not repo.get("installed", False):
                continue
            versions = self._pending_update_versions(repo)
            if versions is None:
                continue
            current_version, available_version = versions

            full_name = repo.get("full_name", "")
            repo_manifest = repo.get("repository_manifest", {}) or {}
            name = (
                repo.get("manifest_name")
                or repo.get("name")
                or repo_manifest.get("name")
                or (full_name.split("/")[-1] if "/" in full_name else full_name)
            )
            category = repo.get("category", "")

            updates.append(
                {
                    "name": name,
                    "type": CATEGORY_MAP.get(
                        category, category.replace("_", " ").title()
                    ),
                    "repository": (
                        f"https://github.com/{full_name}" if full_name else ""
                    ),
                    "current_version": current_version,
                    "available_version": available_version,
                    # What HACS's stored hacs.json says (#111). That snapshot
                    # describes whichever release was last downloaded, so this
                    # is a starting point, not the answer:
                    # ``async_verify_update_requirements`` re-reads it from the
                    # release actually on offer (#116).
                    ATTR_HA_REQUIREMENT: ha_requirement(repo_manifest, HA_VERSION),
                }
            )

        updates = self._reconcile_updates(
            updates, hacs_repos, self._live_hacs_updates()
        )

        # Sort alphabetically by type then name
        updates.sort(key=lambda c: (c["type"], c["name"].lower()))
        return {
            "count": len(updates),
            "updates": updates,
            **requirement_counts(
                item.get(ATTR_HA_REQUIREMENT) for item in updates
            ),
        }

    # -- the OFFERED release's requirement (#116) -----------------------------

    async def async_verify_update_requirements(self, updates: dict[str, Any]) -> None:
        """Re-read each pending update's requirement from the tag on offer.

        :meth:`scan_updates` can only use what HACS stored, and HACS stores one
        ``hacs.json`` per repository with nothing saying which release it came
        from — so after a new release appears it still answers for the version
        already installed. That is #116: Microsoft 365 Calendar v1.14.0 needs
        Home Assistant 2026.11.0 and was shown as "needs 2025.6.0 — you have
        it", which is the installed v1.13.0's requirement, on a 2026.10.4
        house. Six of the other seven pending updates agreed by coincidence,
        which is exactly why it went unnoticed.

        Kept out of ``scan_updates`` on purpose. That scan reads local storage
        and must stay offline: it is called from tests and from paths where a
        network stall would be felt, and a scan that failed because GitHub was
        slow would lose every count on the dashboard. So the correction is a
        separate pass over the rows the scan produced, it never raises, and the
        counts are recomputed from the rows it leaves behind.

        *updates* is mutated in place.
        """
        rows = updates.get("updates") or []
        if not rows:
            # Still write the counts: they describe the rows, and the rows are
            # this function's output — a caller must never have to know which
            # of the two passes last touched them.
            updates.update(requirement_counts(()))
            return

        # A row is only verifiable if we can name both the repository and the
        # tag being offered. ``available_version`` is used verbatim: HACS
        # reports the tag, and tags keep whatever "v" prefix they shipped with.
        keyed: list[tuple[dict[str, Any], tuple[str, str] | None]] = []
        for row in rows:
            full_name = repo_key(row.get("repository"))
            ref = str(row.get("available_version") or "").strip()
            keyed.append((row, (full_name, ref) if full_name and ref else None))

        # Cache successes for the life of the process — a tag's contents never
        # change — but only for tags still on offer, so an update the user
        # installs stops being paid for. Failures are never cached, so the next
        # scan retries them.
        wanted = {key for _row, key in keyed if key is not None}
        self._target_manifest_cache = {
            key: manifest
            for key, manifest in self._target_manifest_cache.items()
            if key in wanted
        }
        missing = sorted(wanted - self._target_manifest_cache.keys())
        if missing:
            try:
                self._target_manifest_cache.update(
                    await self._fetch_target_manifests(missing)
                )
            except Exception:  # noqa: BLE001 - a scan is worth more than a fetch
                _LOGGER.exception(
                    "Could not read the offered releases' hacs.json files; "
                    "their requirements are reported as unchecked"
                )

        for row, key in keyed:
            manifest = self._target_manifest_cache.get(key) if key else None
            if manifest is not None:
                row[ATTR_HA_REQUIREMENT] = ha_requirement(manifest, HA_VERSION)
                continue
            # Nothing read this scan. The stale answer is NOT kept: ``met``
            # from the installed version's manifest is precisely the wrong
            # answer this exists to stop. ``strictest`` with the fresh dict
            # first means a previously established ``blocked`` still wins
            # (more restrictive), a stale ``met`` is discarded, and a tie
            # against another ``unknown`` keeps the honest reason.
            row[ATTR_HA_REQUIREMENT] = strictest(
                [
                    target_unreadable_requirement(HA_VERSION),
                    row.get(ATTR_HA_REQUIREMENT),
                ]
            )

        updates.update(
            requirement_counts(row.get(ATTR_HA_REQUIREMENT) for row in rows)
        )

    async def _fetch_target_manifests(
        self, keys: Sequence[tuple[str, str]]
    ) -> dict[tuple[str, str], dict[str, Any]]:
        """Read ``hacs.json`` at each ``(owner/repo, tag)``, best effort.

        Only an HTTP 200 carrying a JSON object counts as an answer. A 404 is
        emphatically *not* "this release declares nothing" — the tag may not
        exist under the name HACS reported, or the repository may keep its
        manifest elsewhere — and reading it as an empty manifest would turn a
        blocked update green, which is the fault being fixed. Anything that
        isn't a clean 200 is simply absent from the result.
        """
        session = async_get_clientsession(self.hass)
        semaphore = asyncio.Semaphore(TARGET_MANIFEST_CONCURRENCY)
        found: dict[tuple[str, str], dict[str, Any]] = {}

        async def _read_one(key: tuple[str, str]) -> None:
            full_name, ref = key
            url = TARGET_MANIFEST_URL.format(full_name=full_name, ref=ref)
            async with semaphore:
                try:
                    async with session.get(
                        url, timeout=ClientTimeout(total=TARGET_MANIFEST_TIMEOUT)
                    ) as response:
                        if response.status != 200:
                            _LOGGER.debug(
                                "hacs.json for %s@%s: HTTP %s",
                                full_name,
                                ref,
                                response.status,
                            )
                            return
                        # Bounded, and decoded leniently, for the same reason
                        # the local bundle scans in this file are: the body is
                        # a few hundred bytes of JSON if it is what it claims
                        # to be, and if it is not, neither its size nor its
                        # encoding should be able to hurt the scan.
                        raw = b""
                        async for chunk in response.content.iter_chunked(8192):
                            raw += chunk
                            if len(raw) > TARGET_MANIFEST_MAX_BYTES:
                                _LOGGER.debug(
                                    "hacs.json for %s@%s is bigger than %d "
                                    "bytes, so it is not a manifest; ignored",
                                    full_name,
                                    ref,
                                    TARGET_MANIFEST_MAX_BYTES,
                                )
                                return
                except (ClientError, asyncio.TimeoutError, TimeoutError) as err:
                    _LOGGER.debug(
                        "hacs.json for %s@%s could not be read: %s",
                        full_name,
                        ref,
                        err,
                    )
                    return
            try:
                manifest = json.loads(raw.decode("utf-8", errors="ignore"))
            except ValueError as err:
                _LOGGER.debug(
                    "hacs.json for %s@%s is not JSON: %s", full_name, ref, err
                )
                return
            if isinstance(manifest, dict):
                found[key] = manifest

        tasks = [asyncio.create_task(_read_one(key)) for key in keys]
        done, pending = await asyncio.wait(tasks, timeout=TARGET_MANIFEST_BUDGET)
        if pending:
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            _LOGGER.debug(
                "Ran out of time reading %d of %d offered hacs.json files",
                len(pending),
                len(tasks),
            )
        for task in done:
            if (err := task.exception()) is not None:
                _LOGGER.debug("Unexpected error reading an offered hacs.json: %s", err)
        return found

    # -- new in HACS (#95) ----------------------------------------------------

    async def scan_new_items(
        self,
        registry: NewItemRegistry,
        *,
        home_country: str = DEFAULT_HOME_COUNTRY,
        extra_home_terms: Sequence[str] = (),
        auto_hide_foreign: bool = True,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Scan for HACS repositories that are new and not installed (#95).

        Every other scanner above selects ``repo.get("installed", False)``;
        this one deliberately selects the opposite. A repository counts as
        new when it is **not installed** and either HACS still flags it
        ``new: true`` in this snapshot, or the registry's own ``first_seen``
        for it falls inside :data:`NEW_ITEMS_WINDOW_DAYS` — HACS clears its
        own ``new`` flag (a panel action, some upgrades reset it wholesale),
        so the second clause is load-bearing, not a fallback.

        ``registry.note_seen`` is only ever called for a repository that is
        ``new: true`` in *this* snapshot, never for one kept only by the
        window clause — otherwise every repository HACS has ever indexed
        would eventually become "new" forever.

        Integrations, plugins (-> "card"), themes and templates are
        considered. Unsupported categories are left alone. The country
        pre-filter runs before
        anything is handed to the AI — see
        ``docs/specs/new-in-hacs.md``. AI categorisation itself is a
        separate, batched pass
        (:meth:`CustomComponentMonitorCoordinator.async_generate_new_item_categories`)
        so a slow local model can never block this scan.

        Returns ``{"items": [...], "hidden": [...], "dismissed_count": int,
        "total_new": int, "known_full_names": set[str]}``. ``total_new``
        counts every new repository, including the hidden and dismissed
        ones; ``known_full_names`` is every repository present in the
        current HACS snapshot (installed or not), for the caller to prune
        the registry against.
        """
        hacs_repos = await self._load_hacs_repositories()
        now_local = now or dt_util.now()
        now_utc = dt_util.as_utc(now_local)

        known_full_names: set[str] = set()
        candidates: list[dict[str, Any]] = []

        for _repo_id, repo in hacs_repos.items():
            if not isinstance(repo, dict):
                continue
            full_name = str(repo.get("full_name") or "")
            if full_name:
                known_full_names.add(full_name)

            if not full_name or repo.get("installed", False):
                continue

            kind = NEW_ITEM_HACS_KINDS.get(repo.get("category", ""))
            if kind is None:
                continue

            is_new_flag = bool(repo.get("new"))
            if is_new_flag:
                first_seen_iso = registry.note_seen(full_name, now=now_utc)
            else:
                first_seen_iso = registry.first_seen(full_name)
                if first_seen_iso is None:
                    if registry.is_dismissed(full_name) or registry.hidden_record(full_name):
                        # Old registry size limits could trim first-seen while
                        # keeping a review decision. Seed only these known,
                        # uninstalled repos—not every old HACS repository.
                        first_seen_iso = registry.note_seen(full_name, now=now_utc)
                    else:
                        continue

            age_days = registry.age_days(full_name, now=now_utc)
            if (
                not is_new_flag and age_days > NEW_ITEMS_WINDOW_DAYS
                and not registry.is_dismissed(full_name)
                and registry.hidden_record(full_name) is None
                and not registry.is_restored(full_name)
            ):
                continue

            name = display_name_for(full_name, repo)

            description = repo.get("description")
            description = description[:200] if isinstance(description, str) else None

            topics = repo.get("topics")
            topics = list(topics) if isinstance(topics, list) else []

            ai_record = registry.get_ai(full_name)

            candidates.append(
                {
                    "name": name,
                    "full_name": full_name,
                    "repository": f"https://github.com/{full_name}",
                    "kind": kind,
                    "description": description,
                    "topics": topics,
                    "stars": repo.get("stargazers_count"),
                    "downloads": repo.get("downloads"),
                    "last_updated": repo.get("last_updated"),
                    "first_seen": first_seen_iso,
                    "age_days": age_days,
                    "category": (
                        "Dashboard & theming" if kind == "theme"
                        else ai_record["category"] if ai_record else NEW_ITEM_CATEGORY_FALLBACK
                    ),
                    "ai_pending": ai_record is None,
                }
            )

        visible: list[dict[str, Any]] = []
        hidden: list[dict[str, Any]] = []
        dismissed_count = 0

        for item in candidates:
            full_name = item["full_name"]
            if registry.is_dismissed(full_name):
                dismissed_count += 1
                continue

            if (
                not auto_hide_foreign or registry.is_never_hide(full_name)
                or registry.is_restored(full_name)
            ):
                registry.clear_hidden(full_name)
                visible.append(item)
                continue

            verdict = classify(
                {
                    "name": item["name"],
                    "full_name": full_name,
                    "description": item["description"],
                    "topics": item["topics"],
                },
                home_country=home_country,
                extra_home_terms=extra_home_terms,
            )
            if verdict.hide:
                match = verdict.foreign_matches[0]
                registry.record_hidden(
                    full_name,
                    country=match.country,
                    term=match.term,
                    field=match.field,
                    source="rules",
                )
                hidden.append(
                    {
                        **item,
                        "reason": verdict.reason,
                        "country": match.country,
                        "term": match.term,
                        "field": match.field,
                        "source": "rules",
                    }
                )
            else:
                # The AI's country_scope is the second hide chance, and it has
                # to be re-derived here rather than trusted from the
                # ``auto_hidden`` record: this loop is the *only* place
                # visibility is decided, so an AI-sourced hide would otherwise
                # be cleared on the very next scan and — its verdict being
                # cached, leaving ``ai_pending`` false — never reconsidered,
                # un-hiding itself silently and for good.
                ai_record = registry.get_ai(full_name)
                scope = (ai_record or {}).get("country_scope")
                if ai_scope_hides(
                    item,
                    scope,
                    home_country=home_country,
                    extra_home_terms=extra_home_terms,
                    verdict=verdict,
                ):
                    registry.record_hidden(
                        full_name,
                        country=scope,
                        term=scope,
                        field="ai_country_scope",
                        source="ai",
                    )
                    hidden.append(
                        {
                            **item,
                            "reason": f'AI judged this built for "{scope}"',
                            "country": scope,
                            "term": scope,
                            "field": "ai_country_scope",
                            "source": "ai",
                        }
                    )
                else:
                    registry.clear_hidden(full_name)
                    visible.append(item)

        return {
            "items": visible,
            "hidden": hidden,
            "dismissed_count": dismissed_count,
            "total_new": len(candidates),
            "known_full_names": known_full_names,
        }


def ai_scope_hides(
    item: Mapping[str, Any],
    country_scope: str | None,
    *,
    home_country: str,
    extra_home_terms: Sequence[str] = (),
    verdict: CountryVerdict | None = None,
) -> bool:
    """Does the AI's ``country_scope`` justify hiding *item*? (#95)

    The second hide chance lives in one function because two call sites have
    to agree about it, and they must: ``ComponentScanner.scan_new_items``
    re-decides visibility on every cycle, so anything the AI batch
    (:meth:`CustomComponentMonitorCoordinator.async_generate_new_item_categories`)
    decided on its own would be undone an hour later.

    A home signal in the repository's own text beats the model's judgement,
    exactly as it beats a rules match — "AU/NZ energy prices" is for this
    house whatever the AI makes of it. Pass *verdict* when the caller has
    already classified the item, to save a second scan of the same text.
    """
    if not scope_is_foreign(country_scope, home_country):
        return False
    if verdict is None:
        verdict = classify(
            {
                "name": item.get("name"),
                "full_name": item.get("full_name"),
                "description": item.get("description"),
                "topics": item.get("topics"),
            },
            home_country=home_country,
            extra_home_terms=extra_home_terms,
        )
    return not verdict.home_matches


def card_category_override(kind: Any, category: str) -> str:
    """A Lovelace card that came back 'Other' is dashboard furniture (v1.17.0).

    HACS lists a great many cards whose whole subject is the dashboard —
    a date picker, a confirm-tap wrapper, an autocomplete row, a timeline, a
    wall-panel mode. There is no *home* domain to put them in, so the model
    quite reasonably says 'Other'; but ``kind`` already proves the thing is
    frontend, which is a better answer than 'Other' and the one a reader
    expects. Measured on the production store: ~15 of 58 'Other' items.

    For cards, only promotes the fallback. A card the AI placed somewhere
    specific ("Energy & solar" for a power-flow card) keeps that category,
    because the subject is more useful than the medium. Themes always map
    to Dashboard & theming; templates keep their subject category.
    """
    # A theme's job is appearance, even if its name mentions weather/energy.
    # Templates have no medium-based override: group by their subject.
    if str(kind or "").strip().lower() == "theme":
        return "Dashboard & theming"
    if category != NEW_ITEM_CATEGORY_FALLBACK:
        return category
    if str(kind or "").strip().lower() != "card":
        return category
    return "Dashboard & theming"


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class CustomComponentMonitorCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator that drives periodic scans."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry | None = None) -> None:
        """Initialize."""
        self.install_dates = InstallDateRegistry(hass)
        self.scanner = ComponentScanner(hass, self.install_dates)
        self.entry = entry
        self._ai_store: Store | None = None
        self._ai_cache: dict[str, dict[str, Any]] = {}
        self._ai_legacy: dict[str, Any] = {}
        self._ai_loaded = False
        self._ai_last_error: str | None = None
        self._ai_lock = asyncio.Lock()
        self._ai_task: asyncio.Task | None = None
        # None = untested; False = this backend can't do structured output, so
        # stop paying for the structured call before every text call (#91).
        self._ai_structured_ok: bool | None = None
        self.ai_in_progress = False
        self.ai_last_run: str | None = None
        # New in HACS (#95). ``new_items_registry`` is a fixed attribute name —
        # the dismiss/restore/categorise services read and write it directly.
        self.new_items_registry = NewItemRegistry(hass)
        self.new_items_ai_in_progress = False
        self.new_items_ai_last_run: str | None = None
        self._new_items_ai_last_error: str | None = None
        self._new_items_ai_lock = asyncio.Lock()
        # Scan decisions, manual actions and AI result application share this
        # lock. Never hold it while waiting for an AI response or refreshing.
        self.new_items_review_lock = asyncio.Lock()
        self.hacs_sync_last_error: str | None = None
        self._new_items_ai_task: asyncio.Task | None = None
        # Adaptive pacing (v1.15.1). The wave size is how many items go to the
        # AI at once; it tunes itself against observed throughput, so the same
        # code clears a 200-item backlog in minutes on a GPU and settles back
        # to one call at a time on a slow backend. ``_rate`` is an EWMA in
        # items/second and stays None until a wave has actually completed —
        # it is a measurement, never an estimate.
        self._new_items_ai_wave = NEW_ITEMS_AI_CONCURRENCY_START
        # Throughput per wave size, not a single best-ever figure — see
        # _adapt_new_items_ai_wave for why that distinction is the whole
        # difference between a search that recovers and one that pins itself
        # at one item at a time after a single failure.
        self._new_items_ai_wave_rates: dict[int, float] = {}
        self._new_items_ai_wave_holds = 0
        self._new_items_ai_rate: float | None = None
        # Work-to-wall-clock ratio of the last completed pass, used for the
        # ETA. None until a pass has finished.
        self._new_items_ai_duty: float | None = None
        # Scan health (#114). A failing scan keeps the last good data and says
        # so, rather than taking all six sensors unavailable and blanking
        # every card. ``_scan_failures`` drives the retry backoff;
        # ``_scan_error_logged`` dedupes the log line the same way
        # ``_note_ai_error`` does, because the 1.19.0 fault logged the same
        # sentence every 30 seconds for as long as Home Assistant was up.
        self._scan_failures = 0
        self._scan_error: str | None = None
        self._scan_error_logged: str | None = None
        self._last_successful_scan: str | None = None
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    def _excluded_keys(self) -> set[str]:
        """Lower-cased set of user-excluded component identifiers (#70)."""
        raw = (self.entry.options.get(CONF_EXCLUDE, []) if self.entry else []) or []
        if isinstance(raw, str):
            raw = [raw]
        return {str(x).strip().lower() for x in raw if str(x).strip()}

    def _apply_exclusions(self, result: dict[str, Any]) -> dict[str, Any]:
        """Move user-excluded components out of ``unused`` into ``used`` and
        record them under ``excluded`` (#70)."""
        excl = self._excluded_keys()
        moved: list[dict[str, Any]] = []
        if excl:
            kept: list[dict[str, Any]] = []
            for item in result.get("unused", []):
                keys = {
                    str(item.get(k, "")).strip().lower()
                    for k in ("name", "domain", "card_type")
                    if item.get(k)
                }
                if keys & excl:
                    moved.append(item)
                else:
                    kept.append(item)
            result["unused"] = kept
            result["used"] = result.get("used", []) + moved
        result["excluded"] = moved
        return result

    def _new_items_settings(self) -> dict[str, Any]:
        """Per-cycle settings for the New in HACS scan (#95).

        Recomputed on every call (scan and AI batch alike) rather than cached
        on the coordinator, since options can change between scans without a
        reload.
        """
        options = self.entry.options if self.entry else {}
        scan_enabled = options.get(CONF_NEW_ITEMS_ENABLED) is not False
        home_country = (
            options.get(CONF_HOME_COUNTRY)
            or getattr(self.hass.config, "country", None)
            or DEFAULT_HOME_COUNTRY
        )
        extra_home_terms = options.get(CONF_EXTRA_HOME_TERMS) or []
        if isinstance(extra_home_terms, str):
            extra_home_terms = [extra_home_terms]
        auto_hide_foreign = options.get(CONF_AUTO_HIDE_FOREIGN) is not False
        # A time budget rather than an item count (v1.15.1): a count is blind
        # to how fast the AI entity actually answers. Clamped rather than
        # rejected so a hand-edited config entry can't stall the loop.
        try:
            budget = float(
                options.get(
                    CONF_NEW_ITEMS_AI_BUDGET_SECONDS,
                    NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS,
                )
                or NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS
            )
        except (TypeError, ValueError):
            budget = float(NEW_ITEMS_AI_DEFAULT_BUDGET_SECONDS)
        budget = max(
            float(NEW_ITEMS_AI_MIN_BUDGET_SECONDS),
            min(float(NEW_ITEMS_AI_MAX_BUDGET_SECONDS), budget),
        )
        return {
            "scan_enabled": scan_enabled,
            "home_country": home_country,
            "extra_home_terms": extra_home_terms,
            "auto_hide_foreign": auto_hide_foreign,
            "ai_budget_seconds": budget,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from HACS storage."""
        try:
            # Idempotent, but load it here as well as at setup: seeding from an
            # empty in-memory registry would overwrite every recorded install
            # date on disk (#93).
            await self.install_dates.async_load()
            self.scanner._invalidate_cache()
            self.install_dates.begin_scan()

            all_components = await self.scanner.scan_all_hacs_components()
            _LOGGER.debug(
                "Scanned %d HACS-installed components", len(all_components)
            )

            themes = self._apply_exclusions(await self.scanner.scan_themes())
            _LOGGER.debug(
                "Theme scan: %d total, %d used, %d unused",
                themes["total"],
                len(themes["used"]),
                len(themes["unused"]),
            )

            frontend = self._apply_exclusions(await self.scanner.scan_frontend())
            _LOGGER.debug(
                "Frontend scan: %d total, %d used, %d unused",
                frontend["total"],
                len(frontend["used"]),
                len(frontend["unused"]),
            )

            integrations = self._apply_exclusions(await self.scanner.scan_integrations())
            _LOGGER.debug(
                "Integration scan: %d total, %d used, %d unused",
                integrations["total"],
                len(integrations["used"]),
                len(integrations["unused"]),
            )

            # Close the install-date cycle before the update scan, so a failure
            # fetching updates can't throw away this cycle's observations.
            self.install_dates.end_scan()

            updates = await self.scanner.scan_updates()
            _LOGGER.debug("Update scan: %d pending updates", updates["count"])

            # The scan can only report what HACS stored, which is the
            # *installed* version's declared Home Assistant minimum (#116).
            # Re-read it from the release actually on offer. Never raises, and
            # a release it cannot read is reported as unchecked rather than
            # keeping the stale answer.
            await self.scanner.async_verify_update_requirements(updates)
            _LOGGER.debug(
                "Update scan: %d blocked, %d requirement unknown",
                updates["blocked"],
                updates["unknown_requirement"],
            )

            # Stamp cached AI summaries + categories on (#67). The AI calls
            # themselves run in a background worker so a cold cache can never
            # stall the refresh — and, at startup, blow the platform setup
            # timeout and lose every summary it had generated (#91).
            self._apply_cached_ai(updates)

            # New in HACS (#95): not-installed, recently-new repositories.
            new_settings = self._new_items_settings()
            new_items: dict[str, Any] | None = None
            if new_settings["scan_enabled"]:
                async with self.new_items_review_lock:
                    await self.new_items_registry.async_load()
                    new_items = await self.scanner.scan_new_items(
                        self.new_items_registry,
                        home_country=new_settings["home_country"],
                        extra_home_terms=new_settings["extra_home_terms"],
                        auto_hide_foreign=new_settings["auto_hide_foreign"],
                    )
                    self.new_items_registry.prune(new_items.pop("known_full_names", set()))
                    await self.async_sync_new_items_hacs(new_items)

            scanned_at = dt_util.now().isoformat()
            data = {
                "provider": self.scanner.provider,
                "all_components": all_components,
                "themes": themes,
                "frontend": frontend,
                "integrations": integrations,
                "updates": updates,
                "new_items": new_items,
                "new_items_home_country": new_settings["home_country"],
                "last_scan": scanned_at,
                "install_dates_since": self.install_dates.seeded_at,
                # Scan health (#114). A successful scan is the only thing that
                # clears these, and ``last_successful_scan`` is what the stale
                # path below stands on — so it is written here, with the data
                # it describes, and never anywhere else.
                ATTR_DATA_STALE: False,
                ATTR_LAST_SUCCESSFUL_SCAN: scanned_at,
                ATTR_SCAN_ERROR: None,
                ATTR_STORE_STATUS: store_status(self.hass),
            }
        except Exception as exc:
            return self._handle_scan_failure(exc)

        self._scan_failures = 0
        self._scan_error = None
        self._scan_error_logged = None
        self._last_successful_scan = scanned_at
        self.update_interval = SCAN_INTERVAL
        # Only wake the worker for updates it would actually attempt. Counting
        # `ai_pending` here instead would re-spawn a no-op task every hour once
        # an update had exhausted its retries (#91).
        if updates.get("ai_retryable"):
            self._schedule_ai_run()
        if new_items and any(item.get("ai_pending") for item in new_items.get("items", [])):
            self._schedule_new_items_ai_run()
        return data

    def _handle_scan_failure(self, exc: Exception) -> dict[str, Any]:
        """Serve the last good scan, marked stale, instead of going blank (#114).

        1.19.0 raised ``UpdateFailed`` on every failure. That is correct for a
        cold start and wrong for everything else: ``UpdateFailed`` makes all
        six sensors unavailable, so every card loses the counts it already had
        and shows its "no data" branch. A HACS that never finished starting
        made that permanent — the on-disk component data was complete and
        correct the whole time, and the user was looking at six blank cards.

        So a failure with data behind it keeps that data, flags it
        ``data_stale`` and publishes the reason, and the coordinator stays
        successful so the entities stay alive. Three things still fail
        honestly rather than lying:

        * a cold start (nothing to be stale *about*);
        * last-good data older than :data:`SCAN_STALE_GRACE`, where "from
          yesterday" has become "from last week";
        * an unparseable last-good timestamp, which is a store we no longer
          understand.

        The last good ``install_dates`` and ``new_items_registry`` are not
        touched here at all — they are registries on disk, not scan output,
        and a failed scan is not evidence that anything was uninstalled.
        """
        self._scan_failures += 1
        message = str(exc) or exc.__class__.__name__
        # What the cards quote. A StoreUnavailable message is written for a
        # person to read and act on. Anything else reaching here is a bug in
        # this component, and a raw Python exception string on someone's
        # dashboard helps nobody — the log keeps the real text.
        shown = message if isinstance(exc, StoreUnavailable) else (
            "The component scan failed unexpectedly; check the Home Assistant log."
        )
        self._scan_error = shown
        # 30 s, 60 s, 2 min … capped at the ordinary interval. A transient
        # startup race recovers in seconds; a permanent fault stops costing a
        # scan and an error line every 30 seconds for the rest of the uptime.
        self.update_interval = timedelta(
            seconds=min(
                SCAN_RETRY_INTERVAL * 2 ** (self._scan_failures - 1),
                SCAN_RETRY_MAX_INTERVAL,
            )
        )
        if message != self._scan_error_logged:
            self._scan_error_logged = message
            _LOGGER.error("Error scanning custom components: %s", message)
        else:
            _LOGGER.debug("Error scanning custom components: %s", message)

        last_good = self.data
        last_success = (last_good or {}).get(ATTR_LAST_SUCCESSFUL_SCAN)
        parsed = dt_util.parse_datetime(str(last_success)) if last_success else None
        if parsed is None:
            raise UpdateFailed(exc) from exc
        if (dt_util.now() - parsed).total_seconds() > SCAN_STALE_GRACE:
            raise UpdateFailed(exc) from exc

        # Shallow on purpose: the sub-dicts (``updates``, ``new_items``, …)
        # must stay the *same objects*, because the AI workers hold references
        # to them and check identity to decide whether a newer scan has
        # replaced the batch they were working on.
        stale = dict(last_good)
        stale[ATTR_DATA_STALE] = True
        stale[ATTR_SCAN_ERROR] = shown
        # Diagnostics must never escalate a handled failure into an unhandled
        # one: this runs inside the ``except`` that already caught something,
        # so a second raise here would leave the coordinator with neither
        # stale data nor a clean UpdateFailed. Keep the last known status
        # rather than inventing one.
        try:
            stale[ATTR_STORE_STATUS] = store_status(self.hass)
        except Exception:  # noqa: BLE001 - diagnostics only
            _LOGGER.debug("Could not read store status while handling a failed scan", exc_info=True)
            stale[ATTR_STORE_STATUS] = last_good.get(ATTR_STORE_STATUS)
        return stale

    async def async_sync_new_items_hacs(self, new_items: dict[str, Any]) -> None:
        """Reconcile the full rejected set, including pre-upgrade dismissals.

        Caller holds new_items_review_lock. The lists here are the uncapped
        coordinator lists, not the sensor's published subset. Explicit
        restores queue True; ordinary visible items never do.
        """
        registry = self.new_items_registry
        settings = self._new_items_settings()
        if not settings["scan_enabled"]:
            await registry.async_save()
            return
        rejected = set(registry.dismissed_keys)
        if settings["auto_hide_foreign"]:
            rejected.update(
                item["full_name"].strip().lower()
                for item in new_items.get("hidden", [])
                if not registry.is_restored(item["full_name"])
                and not registry.is_never_hide(item["full_name"])
            )
        for key in rejected:
            registry.queue_hacs_new(key, False)
        # Cancel outdated automatic hides, not pending explicit restores.
        pending = registry.hacs_pending
        registry.acknowledge_hacs(
            [key for key, new in pending.items() if not new and key not in rejected], pending
        )
        await registry.async_save()  # durable intent before touching HACS
        desired = registry.hacs_pending
        result = await async_sync_hacs_new(self.hass, desired)
        if result.error and result.error != self.hacs_sync_last_error:
            _LOGGER.warning("%s", result.error)
        self.hacs_sync_last_error = result.error
        if result.completed:
            registry.acknowledge_hacs(result.completed, desired)
            await registry.async_save()
        if result.changed:
            _LOGGER.info("Synced New status for %d HACS repositories", result.changed)

    # -- AI summarise & categorise updates (#67) ----------------------------

    def _ai_settings(self) -> tuple[bool, str]:
        """Return ``(enabled, ai_entity)`` from the config entry options."""
        if self.entry is None:
            return False, ""
        enabled = bool(self.entry.options.get(CONF_AI_CATEGORIZATION_ENABLED, False))
        ai_entity = str(self.entry.options.get(CONF_AI_TASK_ENTITY) or "")
        return enabled and bool(ai_entity), ai_entity

    @property
    def ai_enabled(self) -> bool:
        """Whether AI enrichment is switched on and pointed at an entity."""
        return self._ai_settings()[0]

    @property
    def ai_last_error(self) -> str | None:
        """Last AI failure message, or None when the last run was clean."""
        return self._ai_last_error

    @staticmethod
    def _cache_key(item: dict[str, Any]) -> str:
        """Stable cache key for an update: repo identity + target version (#91).

        Keying on the display name (as v1.11.0 did) meant a rename, a HACS
        manifest change or a ``v``-prefix difference silently invalidated a
        perfectly good summary and paid for it again.
        """
        version = norm_version(item.get("available_version"))
        key = repo_key(item.get("repository"))
        if key:
            return f"{key}|{version}"
        return f"name:{norm_name(item.get('name'))}|{version}"

    @staticmethod
    def _legacy_key(item: dict[str, Any]) -> str:
        """The v1.11.0 cache key, so existing summaries survive the upgrade."""
        return f"{item.get('name', '')}|{item.get('available_version', '')}"

    @staticmethod
    def _record_is_complete(record: dict[str, Any] | None) -> bool:
        """True when a cached record already carries a usable summary.

        This is the guard that keeps already-summarised updates out of the AI
        queue. v1.11.0 tested the record for truthiness, so a record holding
        categories but an empty summary counted as done and that update never
        got a summary again (#91).
        """
        return bool(record) and bool(str(record.get("summary") or "").strip())

    def _cache_get(self, item: dict[str, Any]) -> dict[str, Any] | None:
        """Look a record up, promoting a v1 record to the v2 key on the way."""
        key = self._cache_key(item)
        record = self._ai_cache.get(key)
        if record is not None:
            return record
        legacy = self._ai_legacy.pop(self._legacy_key(item), None)
        if not isinstance(legacy, dict):
            return None
        record = {
            "categories": legacy.get("categories", []),
            "summary": legacy.get("summary", ""),
            "status": "ok" if str(legacy.get("summary") or "").strip() else "partial",
            "attempts": 0,
            "first_seen": dt_util.utcnow().isoformat(),
            "updated": dt_util.utcnow().isoformat(),
            "last_attempt": None,
            "last_error": None,
            "name": item.get("name", ""),
        }
        self._ai_cache[key] = record
        return record

    def _should_retry(self, record: dict[str, Any] | None, force: bool) -> bool:
        """Whether an incomplete update is due another AI call (#91).

        Failures used to be discarded entirely, so a component the model could
        never summarise was re-sent on every single hourly scan forever. Now a
        failure is recorded and backs off; the manual action always forces.
        """
        if force:
            return True
        if record is None:
            return True
        if record.get("attempts", 0) >= AI_MAX_ATTEMPTS:
            return False
        last_attempt = record.get("last_attempt")
        if not last_attempt:
            return True
        parsed = dt_util.parse_datetime(str(last_attempt))
        if parsed is None:
            return True
        index = min(
            max(record.get("attempts", 1) - 1, 0), len(AI_FAILURE_BACKOFF_MINUTES) - 1
        )
        wait = timedelta(minutes=AI_FAILURE_BACKOFF_MINUTES[index])
        return dt_util.utcnow() - parsed >= wait

    def _apply_cached_ai(self, updates: dict[str, Any]) -> None:
        """Stamp cached categories/summaries onto a fresh scan result (#91).

        Synchronous and cheap — this is all ``_async_update_data`` does with
        the AI cache. ``ai_pending`` is what the user sees: how many updates
        still lack a summary. ``ai_retryable`` is the subset the automatic
        worker would actually attempt right now (the rest are in backoff or
        out of attempts) and is what drives scheduling.
        """
        items: list[dict[str, Any]] = updates.get("updates", [])
        enabled, _ = self._ai_settings()
        if not enabled:
            updates["ai_pending"] = 0
            updates["ai_retryable"] = 0
            return

        pending = 0
        retryable = 0
        for item in items:
            record = self._cache_get(item)
            if record is not None:
                item[ATTR_CATEGORIES] = record.get("categories", [])
                item[ATTR_SUMMARY] = record.get("summary", "")
            if not self._record_is_complete(record):
                pending += 1
                if self._should_retry(record, force=False):
                    retryable += 1
        updates["ai_pending"] = pending
        updates["ai_retryable"] = retryable

    async def _async_load_ai_cache(self) -> None:
        """Load the AI cache once, migrating a v1 store on the way (#91)."""
        if self._ai_loaded:
            return
        if self._ai_store is None:
            self._ai_store = AiCacheStore(
                self.hass, AI_CACHE_STORAGE_VERSION, AI_CACHE_STORAGE_KEY
            )
        try:
            raw = await self._ai_store.async_load() or {}
        except Exception:  # pragma: no cover - a bad store must not break setup
            _LOGGER.warning(
                "Could not read the AI summary cache; summaries will be "
                "regenerated",
                exc_info=True,
            )
            raw = {}
        self._ai_cache = raw.get("entries") or {}
        self._ai_legacy = raw.get("legacy") or {}
        self._ai_loaded = True

    def _save_ai_cache(self) -> None:
        """Persist the cache, coalescing bursts of per-item writes (#91)."""
        if self._ai_store is None:
            return
        self._ai_store.async_delay_save(
            lambda: {"entries": self._ai_cache, "legacy": self._ai_legacy}, 5
        )

    def _evict_ai_cache(self, current_keys: set[str]) -> None:
        """Age- and size-bound the cache (#91).

        v1.11.0 hard-pruned to the currently-pending set on every scan. Because
        the HACS snapshot flaps, an update that briefly dropped out lost its
        summary permanently and was regenerated — a large part of why the same
        components kept hitting the AI.
        """
        cutoff = dt_util.utcnow() - timedelta(days=AI_CACHE_MAX_AGE_DAYS)
        for key in list(self._ai_cache):
            if key in current_keys:
                continue
            updated = dt_util.parse_datetime(str(self._ai_cache[key].get("updated")))
            if updated is None or updated < cutoff:
                del self._ai_cache[key]

        if len(self._ai_cache) > AI_CACHE_MAX_ENTRIES:
            ordered = sorted(
                self._ai_cache.items(),
                key=lambda kv: str(kv[1].get("updated") or ""),
            )
            for key, _record in ordered[: len(self._ai_cache) - AI_CACHE_MAX_ENTRIES]:
                if key not in current_keys:
                    del self._ai_cache[key]

    def _schedule_ai_run(self) -> None:
        """Kick off a bounded background enrichment pass, if none is running."""
        if not self.ai_enabled:
            return
        if self._ai_task is not None and not self._ai_task.done():
            return
        self._ai_task = self.hass.async_create_background_task(
            self._async_deferred_ai_run(), name=f"{DOMAIN}_ai_summaries"
        )

    async def _async_deferred_ai_run(self) -> None:
        """Run an enrichment pass once the coordinator has published its data.

        Background tasks start eagerly, so without this yield the pass would
        run synchronously inside ``_async_update_data`` — before
        ``DataUpdateCoordinator`` assigns ``self.data``, which means it would
        either see no data at all (first refresh) or the *previous* scan's
        items and stamp its results onto dicts about to be thrown away (#91).
        """
        await asyncio.sleep(0)
        await self.async_generate_summaries(limit=AI_MAX_PER_SCAN)

    def async_cancel_ai_run(self) -> None:
        """Cancel any in-flight enrichment pass (called on unload)."""
        if self._ai_task is not None and not self._ai_task.done():
            self._ai_task.cancel()
        self._ai_task = None
        if self._new_items_ai_task is not None and not self._new_items_ai_task.done():
            self._new_items_ai_task.cancel()
        self._new_items_ai_task = None

    async def async_generate_summaries(
        self,
        *,
        entity_ids: set[str] | None = None,
        force: bool = False,
        limit: int | None = None,
    ) -> dict[str, int]:
        """Generate the missing AI summaries for pending updates (#91).

        Only updates without a usable summary are sent to the AI unless *force*
        is set. Each result is written to the cache and pushed to listeners as
        it lands, so a slow local model fills the card in progressively instead
        of losing everything if the run is cancelled part-way.
        """
        counts = {"generated": 0, "failed": 0, "skipped": 0, "remaining": 0}
        enabled, ai_entity = self._ai_settings()
        if not enabled or not self.data:
            return counts

        async with self._ai_lock:
            await self._async_load_ai_cache()
            items: list[dict[str, Any]] = self.data.get("updates", {}).get("updates", [])
            entity_by_key = self._build_update_entity_map()

            todo: list[dict[str, Any]] = []
            requested = set(entity_ids or ())
            for item in items:
                if entity_ids and item.get(ATTR_ENTITY_ID) not in entity_ids:
                    continue
                requested.discard(item.get(ATTR_ENTITY_ID))
                record = self._cache_get(item)
                if not force and self._record_is_complete(record):
                    counts["skipped"] += 1
                    continue
                if not self._should_retry(record, force):
                    counts["skipped"] += 1
                    continue
                todo.append(item)

            if requested:
                # Asked about updates this scan doesn't know about. Say so
                # rather than returning all zeros and looking like a dead
                # button (#91).
                counts["skipped"] += len(requested)
                _LOGGER.warning(
                    "Cannot generate summaries for %s — the last scan didn't "
                    "list them. Run custom_component_monitor.scan_now and try "
                    "again.",
                    ", ".join(sorted(requested)),
                )

            if limit is not None and len(todo) > limit:
                counts["remaining"] = len(todo) - limit
                todo = todo[:limit]

            if not todo:
                return counts

            self.ai_in_progress = True
            self.async_update_listeners()
            try:
                for item in todo:
                    result = await self._async_categorise_one(
                        item, ai_entity, entity_by_key
                    )
                    self._record_result(item, result)
                    if result is not None and str(result.get("summary") or "").strip():
                        counts["generated"] += 1
                    else:
                        counts["failed"] += 1
                    self._save_ai_cache()
                    self.async_update_listeners()
            finally:
                self.ai_in_progress = False
                self.ai_last_run = dt_util.utcnow().isoformat()
                self._evict_ai_cache({self._cache_key(i) for i in items})
                self._recount_pending()
                self._save_ai_cache()
                self.async_update_listeners()

        _LOGGER.debug(
            "AI summaries: %d generated, %d failed, %d skipped, %d remaining",
            counts["generated"],
            counts["failed"],
            counts["skipped"],
            counts["remaining"],
        )
        return counts

    def _record_result(
        self, item: dict[str, Any], result: dict[str, Any] | None
    ) -> None:
        """Write an attempt's outcome to the cache and stamp it on *item*."""
        now = dt_util.utcnow().isoformat()
        key = self._cache_key(item)
        record = self._ai_cache.get(key) or {
            "categories": [],
            "summary": "",
            "attempts": 0,
            "first_seen": now,
        }
        categories = list(result.get("categories") or []) if result else []
        summary = str(result.get("summary") or "").strip() if result else ""
        # Never trade a good value for an empty one — a retry that only returns
        # categories must not wipe an earlier partial summary, and vice versa.
        record["categories"] = categories or record.get("categories", [])
        record["summary"] = summary or record.get("summary", "")
        record["attempts"] = record.get("attempts", 0) + 1
        record["last_attempt"] = now
        record["updated"] = now
        record["name"] = item.get("name", "")
        if item.get(ATTR_ENTITY_ID):
            record[ATTR_ENTITY_ID] = item[ATTR_ENTITY_ID]
        if record["summary"]:
            record["status"] = "ok"
            record["last_error"] = None
        elif record["categories"]:
            record["status"] = "partial"
            record["last_error"] = self._ai_last_error
        else:
            record["status"] = "failed"
            record["last_error"] = self._ai_last_error
        self._ai_cache[key] = record

        item[ATTR_CATEGORIES] = record["categories"]
        item[ATTR_SUMMARY] = record["summary"]

    def known_update_entity_ids(self) -> set[str]:
        """The ``update.*`` entities the last scan actually knows about (#91)."""
        if not self.data:
            return set()
        return {
            item[ATTR_ENTITY_ID]
            for item in self.data.get("updates", {}).get("updates", [])
            if item.get(ATTR_ENTITY_ID)
        }

    def update_requirement(self, entity_id: str) -> dict[str, Any] | None:
        """The Home Assistant version requirement of one update row (#111).

        ``None`` when the last scan doesn't know this entity at all — which is
        not the same as "no requirement", and callers must not read it as a
        clearance.

        More than one row can claim the same entity when the reconciler's
        name/slug fallback matches two components to it; the strictest claim
        wins rather than whichever row was sorted first. See
        :func:`compat.strictest`.
        """
        if not self.data:
            return None
        claims = [
            item.get(ATTR_HA_REQUIREMENT)
            for item in self.data.get("updates", {}).get("updates", [])
            if item.get(ATTR_ENTITY_ID) == entity_id
        ]
        if not claims:
            return None
        return strictest(claims)

    def update_block_reason(self, entity_id: str) -> str | None:
        """One sentence if this update is blocked, else ``None`` (#111).

        Deliberately only refuses a requirement positively established as
        unmet. An unknown requirement is not a refusal here: the card's
        per-row acknowledgement is where the user takes that risk knowingly,
        and the service cannot see an acknowledgement.
        """
        requirement = self.update_requirement(entity_id)
        if not is_blocked(requirement):
            return None
        state = self.hass.states.get(entity_id)
        name = entity_id
        if state is not None:
            name = re.sub(
                r"\s+update$",
                "",
                str(state.attributes.get("friendly_name") or entity_id).strip(),
                flags=re.IGNORECASE,
            ) or entity_id
        return block_sentence(name, requirement or {})

    def _recount_pending(self) -> None:
        """Refresh ``ai_pending``/``ai_retryable`` on the data after a run."""
        if not self.data:
            return
        updates = self.data.get("updates", {})
        incomplete = [
            self._cache_get(item)
            for item in updates.get("updates", [])
            if not self._record_is_complete(self._cache_get(item))
        ]
        updates["ai_pending"] = len(incomplete)
        updates["ai_retryable"] = sum(
            1 for record in incomplete if self._should_retry(record, force=False)
        )

    def _build_update_entity_map(self) -> dict[str, dict[str, str]]:
        """Map HACS ``update.*`` entities by normalised name and by version (#67).

        Uses the same ``platform == "hacs"`` filter as ``update_all``. Entity
        titles are unreliable (often ``None``) and ``friendly_name`` carries an
        " Update" suffix the scanned name lacks, so names are normalised (suffix
        stripped, lower-cased, punctuation removed) — mirroring the card. A
        version map is kept as a fallback when names don't line up.
        """
        registry = er.async_get(self.hass)
        by_name: dict[str, str] = {}
        by_version: dict[str, str] = {}
        ambiguous_versions: set[str] = set()
        for state in self.hass.states.async_all("update"):
            entry = registry.async_get(state.entity_id)
            if entry is None or entry.platform not in UPDATE_PROVIDERS:
                continue
            title = (
                state.attributes.get("title")
                or state.attributes.get("friendly_name")
                or ""
            )
            norm = norm_name(title)
            if norm:
                by_name.setdefault(norm, state.entity_id)
            latest = norm_version(state.attributes.get("latest_version"))
            if latest and latest not in ambiguous_versions:
                if latest in by_version:
                    by_version.pop(latest)
                    ambiguous_versions.add(latest)
                else:
                    by_version[latest] = state.entity_id
        return {"by_name": by_name, "by_version": by_version}

    @staticmethod
    def _finalise(parsed: dict[str, Any], note_cats: list[str]) -> dict[str, Any]:
        """Union the AI's categories with those detected from the notes (#67)."""
        cats = list(parsed.get("categories") or [])
        for cat in note_cats:
            if cat not in cats:
                cats.append(cat)
        # "Other" is a catch-all — drop it when a specific category applies.
        if len(cats) > 1 and "Other" in cats:
            cats = [c for c in cats if c != "Other"]
        return {"categories": cats, "summary": parsed.get("summary", "")}

    @staticmethod
    def _categories_from_notes(notes: str) -> list[str]:
        """Derive categories from changelog section headers / commit prefixes (#67).

        Conventional-commit / release-please changelogs use stable section
        headers, so categories can be detected deterministically rather than
        relying on the model alone (which may miss a section). These are unioned
        with the model's categories.
        """
        if not notes:
            return []
        text = notes.lower()
        checks = [
            ("Bug fixes", r"#{1,6}\s*bug\s*fixes|(^|\n)\s*[*-]?\s*fix(\(|:)|\bbug ?fix(es)?\b"),
            ("New features", r"#{1,6}\s*features?|(^|\n)\s*[*-]?\s*feat(\(|:)"),
            ("Documentation", r"#{1,6}\s*(documentation|docs)\b|(^|\n)\s*[*-]?\s*docs(\(|:)"),
            ("Breaking changes", r"breaking[\s_-]?change|#{1,6}\s*breaking"),
            ("Dependencies", r"#{1,6}\s*dependenc|build\(deps|chore\(deps|(^|\n)\s*[*-]?\s*deps(\(|:)|\bbump\b.*\bfrom\b"),
            ("Translations", r"#{1,6}\s*(translation|i18n)|\bi18n\b|locali[sz]ation|\btranslations?\b"),
        ]
        found: list[str] = []
        for cat, pattern in checks:
            if cat not in found and re.search(pattern, text, re.MULTILINE):
                found.append(cat)
        return found

    async def _async_categorise_one(
        self,
        item: dict[str, Any],
        ai_entity: str,
        entity_by_key: dict[str, dict[str, str]],
    ) -> dict[str, Any] | None:
        """Run a single update through the AI Task; return categories+summary."""
        name = item.get("name", "")
        version = item.get("available_version", "")
        current = item.get("current_version", "")
        utype = item.get("type", "")

        # Best-effort release notes from the matching update entity. The scan
        # now resolves the entity itself (#91); the name/version maps remain as
        # a fallback for entries the reconcile couldn't match.
        notes = ""
        entity_id = (
            item.get(ATTR_ENTITY_ID)
            or entity_by_key["by_name"].get(norm_name(name))
            or entity_by_key["by_version"].get(norm_version(version))
        )
        if entity_id:
            notes = await self._async_release_notes(entity_id) or ""
        note_cats = self._categories_from_notes(notes)

        base = (
            "A Home Assistant custom component has a pending update.\n"
            f"Name: {name}\n"
            f"Type: {utype}\n"
            f"Current version: {current}\n"
            f"New version: {version}\n\n"
            "Release notes / changelog (may be empty):\n"
            f"{notes or '(none provided)'}\n\n"
            "The notes may cover several releases and contain sections such as "
            "Features, Bug Fixes, Documentation, Dependencies or Breaking Changes.\n"
            "Choose the categories clearly supported by the notes, using these "
            "labels exactly (do not invent new ones): "
            f"{', '.join(AI_CATEGORY_OPTIONS)}. "
            "Use 'Other' only when no specific category fits.\n"
            "Then write one short, plain-language sentence summarising the headline "
            "changes. If the notes are sparse, infer conservatively from the version "
            "change and component name."
        )
        json_suffix = (
            "\n\nRespond with ONLY a JSON object, no prose, exactly:\n"
            '{"categories": ["..."], "summary": "..."}'
        )
        last_err: Exception | None = None

        # Conversation-agent source (#67): a single text call, then parse JSON
        # from the reply. This avoids the ai_task structured/streaming path,
        # which some provider bridges mishandle for certain models.
        if ai_entity.startswith("conversation."):
            try:
                parsed = self._parse_ai_text(
                    await self._async_conversation_call(ai_entity, base + json_suffix)
                )
                if parsed is not None:
                    self._ai_last_error = None
                    return self._finalise(parsed, note_cats)
            except Exception as err:
                last_err = err
            self._note_ai_error(name, last_err)
            return None

        # AI Task source. 1) Preferred: structured output (works where the
        # provider supports strict JSON schema, e.g. official OpenAI/Ollama).
        #    Skipped entirely once this backend has proved it can't do it, so a
        #    self-hosted setup pays for one generation per update, not two (#91).
        structure = {
            "categories": {
                "description": "All change categories that apply to this update",
                "required": True,
                "selector": {
                    "select": {"multiple": True, "options": AI_CATEGORY_OPTIONS}
                },
            },
            "summary": {
                "description": "One short, plain-language sentence on what changed",
                "required": True,
                "selector": {"text": {"multiline": True}},
            },
        }
        best: dict[str, Any] | None = None
        if self._ai_structured_ok is not False:
            try:
                parsed = self._parse_ai_data(
                    await self._async_ai_call(ai_entity, base, structure)
                )
                if parsed is not None and str(parsed.get("summary") or "").strip():
                    self._ai_structured_ok = True
                    self._ai_last_error = None
                    return self._finalise(parsed, note_cats)
                # Reached the model but got no summary out of it — keep any
                # categories and stop paying for the structured call.
                best = parsed
                self._ai_structured_ok = False
            except asyncio.CancelledError:
                raise
            except TimeoutError as err:
                # A slow response says nothing about whether the backend
                # supports structured output — don't latch it off for the rest
                # of the session over one hiccup (#91).
                last_err = err
            except Exception as err:  # provider/model/transport failure
                last_err = err
                self._ai_structured_ok = False

        # 2) Fallback: plain text + an explicit JSON request, then parse it.
        #    Rescues backends that can't do strict structured output but can
        #    still generate text (a large class of self-hosted setups).
        try:
            parsed = self._parse_ai_text(
                await self._async_ai_call(ai_entity, base + json_suffix, None)
            )
            if parsed is not None:
                self._ai_last_error = None
                return self._finalise(self._merge_parsed(best, parsed), note_cats)
        except Exception as err:
            last_err = err

        if best is not None:
            self._note_ai_error(name, last_err)
            return self._finalise(best, note_cats)

        self._note_ai_error(name, last_err)
        return None

    @staticmethod
    def _merge_parsed(
        first: dict[str, Any] | None, second: dict[str, Any]
    ) -> dict[str, Any]:
        """Keep the best of two attempts — never trade a value for a blank."""
        if not first:
            return second
        return {
            "categories": second.get("categories") or first.get("categories") or [],
            "summary": second.get("summary") or first.get("summary") or "",
        }

    async def _async_conversation_call(self, agent_id: str, text: str) -> str | None:
        """Call conversation.process and return the agent's reply text (#67)."""
        async with asyncio.timeout(AI_CALL_TIMEOUT):
            resp = await self.hass.services.async_call(
                "conversation",
                "process",
                {"agent_id": agent_id, "text": text},
                blocking=True,
                return_response=True,
            )
        response = (resp or {}).get("response") or {}
        speech = (
            (response.get("speech") or {}).get("plain") or {}
        ).get("speech")
        # conversation.process returns HTTP 200 even on failure, with the error
        # in the reply — surface it so categorisation degrades + warns cleanly.
        if response.get("response_type") == "error":
            raise RuntimeError(speech or "conversation agent error")
        return speech

    async def _async_ai_call(
        self, ai_entity: str, instructions: str, structure: dict | None
    ) -> Any:
        """Call ai_task.generate_data and return its response 'data' (#67)."""
        service_data: dict[str, Any] = {
            "entity_id": ai_entity,
            "task_name": "Categorise HACS update",
            "instructions": instructions,
        }
        if structure is not None:
            service_data["structure"] = structure
        async with asyncio.timeout(AI_CALL_TIMEOUT):
            resp = await self.hass.services.async_call(
                "ai_task",
                "generate_data",
                service_data,
                blocking=True,
                return_response=True,
            )
        return (resp or {}).get("data")

    def _parse_ai_data(self, data: Any) -> dict[str, Any] | None:
        """Parse a structured ai_task result (dict, or a JSON string)."""
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                return None
        if not isinstance(data, dict):
            return None
        cats = self._normalise_categories(data.get("categories"))
        summary = str(data.get("summary") or "").strip()
        if not cats and not summary:
            return None
        return {"categories": cats, "summary": summary}

    def _parse_ai_text(self, text: Any) -> dict[str, Any] | None:
        """Parse a plain-text ai_task result, extracting an embedded JSON object."""
        if not isinstance(text, str) or not text.strip():
            return None
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                obj = json.loads(match.group(0))
                if isinstance(obj, dict):
                    cats = self._normalise_categories(obj.get("categories"))
                    summary = str(obj.get("summary") or "").strip()
                    if cats or summary:
                        return {"categories": cats, "summary": summary}
            except (ValueError, TypeError):
                pass
        # No usable JSON — keep the first line as a summary, no categories.
        summary = text.strip().splitlines()[0][:300]
        return {"categories": [], "summary": summary} if summary else None

    def _note_ai_error(self, name: str, err: Exception | None) -> None:
        """Log a deduped, actionable warning when categorisation fails (#67)."""
        msg = str(err) if err else "no usable response"
        _LOGGER.debug("AI categorisation failed for %s: %s", name, msg)
        if msg != self._ai_last_error:
            self._ai_last_error = msg
            ent = self.entry.options.get(CONF_AI_TASK_ENTITY) if self.entry else "?"
            _LOGGER.warning(
                "AI categorisation via '%s' failed (%s). Pending updates will show "
                "no categories. If this persists, your AI Task provider/model may "
                "not support structured or JSON generation — the official OpenAI, "
                "Ollama, Anthropic or Google Generative AI integrations are "
                "recommended as the AI Task backend.",
                ent,
                msg,
            )

    @staticmethod
    def _normalise_categories(raw: Any) -> list[str]:
        """Map a model's free-form category output onto the canonical set (#67).

        Models don't reliably honour the select-selector options — they return a
        list, a single string, or a comma/semicolon/slash-joined string with
        loose wording. Split it, lower-case it, and map via aliases.
        """
        if isinstance(raw, str):
            parts = re.split(r"[,;/]", raw)
        elif isinstance(raw, (list, tuple)):
            parts = []
            for x in raw:
                parts.extend(re.split(r"[,;/]", str(x)))
        else:
            parts = []
        canon_by_lower = {c.lower(): c for c in AI_CATEGORY_OPTIONS}
        out: list[str] = []
        for part in parts:
            key = part.strip().lower()
            if not key:
                continue
            canon = canon_by_lower.get(key) or AI_CATEGORY_ALIASES.get(key)
            if canon and canon not in out:
                out.append(canon)
        return out

    async def _async_release_notes(self, entity_id: str) -> str | None:
        """Fetch release notes from a Home Assistant update entity (#67)."""
        entity_comp = self.hass.data.get("entity_components", {}).get("update")
        if entity_comp is None:
            return None
        entity = entity_comp.get_entity(entity_id)
        if entity is None or not hasattr(entity, "async_release_notes"):
            return None
        try:
            return await entity.async_release_notes()
        except Exception:
            _LOGGER.debug(
                "Failed to fetch release notes for %s", entity_id, exc_info=True
            )
            return None

    # -- New in HACS: AI categorisation (#95) -------------------------------

    @property
    def new_items_ai_last_error(self) -> str | None:
        """Last new-items AI failure message, or None when the last run was clean."""
        return self._new_items_ai_last_error

    @property
    def new_items_ai_rate_per_minute(self) -> float | None:
        """Measured categorisation throughput, items per minute.

        ``None`` until a wave has completed — this is what the loop actually
        achieved, never a guess, so there is nothing to report before the
        first measurement (v1.15.1).
        """
        if not self._new_items_ai_rate:
            return None
        return round(self._new_items_ai_rate * 60.0, 1)

    def new_items_ai_eta_seconds(self, pending: int) -> int | None:
        """Wall-clock seconds to clear *pending* items at the measured rate.

        The measured rate counts only time spent inside AI calls, but the
        catch-up chain sleeps between passes, so the raw rate would promise a
        finish the clock never delivers. Scaling by the duty cycle makes this
        an answer about wall clock, which is the only kind worth showing
        anyone.

        The duty cycle is measured from the last completed pass rather than
        assumed from the budget, because the two disagree exactly where it
        matters: a backend slower than the whole budget runs one long wave and
        overruns it, so its work phase is far longer than the budget and its
        real duty cycle is much closer to 1. Assuming the budget there would
        quote a backlog as taking longer than it does.
        """
        if pending <= 0 or not self._new_items_ai_rate:
            return None
        duty = self._new_items_ai_duty
        if duty is None:
            # No pass has finished yet, so estimate from the configured budget.
            budget = self._new_items_settings()["ai_budget_seconds"]
            duty = budget / (budget + NEW_ITEMS_AI_CATCHUP_SECONDS)
        effective = self._new_items_ai_rate * duty
        if effective <= 0:
            return None
        return int(round(pending / effective))

    def _record_new_items_ai_rate(self, sent: int, ok: int, elapsed: float) -> None:
        """Fold one wave's throughput into the reported EWMA (v1.15.1).

        Only whole waves count. A wave where some calls failed spent most of
        its time waiting on the ones that never answered, so its items/second
        describes the failure and not the hardware — folding that in would
        drop the rate the card shows (and the ETA built on it) on a single
        flaky wave, which is misreporting rather than measuring.
        """
        if ok <= 0 or ok < sent or elapsed <= 0:
            return
        rate = ok / elapsed
        if self._new_items_ai_rate is None:
            self._new_items_ai_rate = rate
        else:
            self._new_items_ai_rate = 0.7 * self._new_items_ai_rate + 0.3 * rate

    def _adapt_new_items_ai_wave(self, sent: int, ok: int, elapsed: float) -> None:
        """Retune the wave size from the wave just completed (v1.15.1).

        Tuned on **throughput** (items/second) rather than latency: latency
        always worsens as concurrency grows, so tuning on it would shrink the
        wave forever and never find the hardware's actual ceiling. Throughput
        rises until the backend saturates and then flattens, which gives the
        loop something to converge on.

        Throughput is remembered **per wave size** rather than as a single
        best-ever figure, which is what makes the search recoverable. A
        best-ever bar only ever ratchets up, so one transient failure at the
        top of the ramp would halve the wave and then leave every later,
        necessarily-slower wave looking like a regression — walking the size
        down to 1 and pinning it there for the life of the coordinator, which
        is precisely the trickle this release exists to remove. Comparing a
        size against its neighbours instead means each decision is made
        against a like-for-like measurement, and a size can always be tried
        again.
        """
        size = max(1, sent)
        if ok < sent:
            # A failure is the one signal that isn't about speed. The usual
            # cause is a backend refusing concurrent calls, so back off hard
            # instead of nibbling.
            #
            # The failed size is recorded as worth *nothing* rather than having
            # its record deleted: an absent record reads as "never tried" and
            # the growth rule explores those eagerly, so deleting it would send
            # the very next successful wave straight back into the size that
            # just failed, and the loop would spend half its waves failing. A
            # zero keeps it out of the running until the periodic re-probe
            # below clears it, which is what makes recovery deliberate rather
            # than immediate.
            self._new_items_ai_wave_rates[size] = 0.0
            self._new_items_ai_wave = max(1, size // 2)
            self._new_items_ai_wave_holds = 0
            return
        if elapsed <= 0 or sent <= 0:
            return

        rate = sent / elapsed
        known = self._new_items_ai_wave_rates.get(size)
        # A zero is the failure sentinel above, not a throughput measurement.
        # Blending into it would halve the record for a size that has just
        # proved it works, and that wrong number would then be compared
        # against its neighbours for as long as it survives the EWMA.
        self._new_items_ai_wave_rates[size] = (
            rate if known is None or known <= 0 else 0.5 * known + 0.5 * rate
        )

        if size != self._new_items_ai_wave:
            # The tail of a backlog: fewer items were left than the configured
            # wave asked for. It is an honest measurement *of the smaller
            # size*, which is why it is filed under ``size`` and not under the
            # configured size — doing the latter fills the table with figures
            # taken at a concurrency the key does not describe, and since most
            # backlogs do not divide exactly by the wave size that would be
            # the common case rather than an edge case. What it is not is a
            # verdict on the configured size, so it must not move it.
            return

        bigger = min(NEW_ITEMS_AI_CONCURRENCY_MAX, size * 2)
        smaller = max(1, size // 2)
        bigger_rate = self._new_items_ai_wave_rates.get(bigger)
        smaller_rate = self._new_items_ai_wave_rates.get(smaller)

        if smaller != size and smaller_rate is None:
            # Probe the floor before climbing. Without this the walk from the
            # start size only ever jumps upwards, so a single call at a time
            # is never measured at all and a backend that is genuinely fastest
            # that way — one that serialises internally but never errors, so
            # nothing ever halves the wave — would sit at the start size
            # forever. One wave is what finding that out costs.
            self._new_items_ai_wave = smaller
            self._new_items_ai_wave_holds = 0
            return
        if bigger != size and bigger_rate is None:
            # Never measured. Explore upwards: one possibly-slow wave is a
            # bounded cost, and never exploring is how a fast GPU ends up
            # trickling items one at a time.
            self._new_items_ai_wave = bigger
            self._new_items_ai_wave_holds = 0
            return
        if (
            bigger != size
            and bigger_rate is not None
            and bigger_rate >= rate * NEW_ITEMS_AI_GROW_GAIN
        ):
            self._new_items_ai_wave = bigger
            self._new_items_ai_wave_holds = 0
            return
        if (
            smaller != size
            and smaller_rate is not None
            and smaller_rate >= rate * NEW_ITEMS_AI_GROW_GAIN
        ):
            # A smaller wave genuinely did better — a backend that serialises
            # requests internally, or one throttling concurrent callers.
            self._new_items_ai_wave = smaller
            self._new_items_ai_wave_holds = 0
            return

        # This size beats both neighbours — hold. Requiring a clear margin to
        # move at all is what stops the size oscillating once it has
        # converged.
        self._new_items_ai_wave_holds += 1
        if self._new_items_ai_wave_holds >= NEW_ITEMS_AI_REPROBE_WAVES:
            # Measurements go stale: the GPU was busy, a cloud quota window
            # reset, another integration stopped competing for the model.
            # Forgetting the size above makes the search climb again rather
            # than trusting a number from an hour ago forever — and it is the
            # only way back up after a failure, which records a zero there.
            #
            # Only the size above, and only when there is one. The current
            # size is re-measured on every wave, so a backend that slows down
            # is noticed without any help; and the size below keeps its record
            # because a stale figure there can cost at most one wave (the move
            # down re-measures it immediately). Popping the degenerate
            # neighbour at the floor or the ceiling — where double or half is
            # the current size itself — would throw away the one measurement
            # the decision is actually standing on.
            self._new_items_ai_wave_holds = 0
            if bigger != size:
                self._new_items_ai_wave_rates.pop(bigger, None)

    async def async_reset_new_items_ai_cache(self) -> int:
        """Drop every cached new-items AI verdict so the next pass re-asks.

        Two things have to give way for a forced re-run: the in-memory
        ``ai_pending`` flag (what the pass queues on) and the registry's
        cached verdict (what it would hand back instead of calling the model).
        Dropping both means a forced call that fails retries on the next cycle
        rather than quietly reverting to the category being replaced.

        It lives here, under the categorisation lock, rather than in the
        service handler — and that is the whole point of the method. The
        catch-up chain holds this lock for the length of a pass, so a reset
        running alongside it would flip items the pass had *already* finished
        back to pending and clear their fresh verdicts while the pass's cursor
        had moved beyond them. The work that had just succeeded would be lost,
        the pass would over-report what was left, and the card would show
        items as unsorted that it had correctly sorted seconds earlier.
        """
        if not self.data:
            return 0
        async with self._new_items_ai_lock:
            async with self.new_items_review_lock:
                items = (self.data.get("new_items") or {}).get("items") or []
                for item in items:
                    item["ai_pending"] = True
                    self.new_items_registry.clear_ai(item["full_name"])
                await self.new_items_registry.async_save()
                return len(items)

    def async_schedule_new_items_ai_run(self) -> None:
        """Public entry point for the catch-up chain (used by the service)."""
        self._schedule_new_items_ai_run()

    def _schedule_new_items_ai_run(self) -> None:
        """Kick off a background new-items categorisation chain, if none running."""
        if not self.ai_enabled:
            return
        if self._new_items_ai_task is not None and not self._new_items_ai_task.done():
            return
        self._new_items_ai_task = self.hass.async_create_background_task(
            self._async_deferred_new_items_ai_run(), name=f"{DOMAIN}_new_items_ai"
        )

    async def _async_deferred_new_items_ai_run(self) -> None:
        """Keep running passes until the backlog is clear (v1.15.1).

        The first ``sleep(0)`` is the same reasoning as
        ``_async_deferred_ai_run``: without it the pass would run inside
        ``_async_update_data``, before ``DataUpdateCoordinator`` assigns
        ``self.data`` (#91/#95).

        The loop after it is the batch-and-retry: v1.15.0 ran exactly one
        bounded pass per hourly scan, so a HACS drop of a few hundred items
        took the best part of a day no matter how fast the AI answered. Here a
        pass that leaves work behind is followed by another, and the only
        things that stop the chain are an empty backlog, a pass that made no
        progress at all, or the hard pass cap.

        ``new_items_ai_in_progress`` is held True across the whole chain, so
        the card reads "sorting" once rather than flickering between passes.
        """
        await asyncio.sleep(0)
        was_in_progress = self.new_items_ai_in_progress
        self.new_items_ai_in_progress = True
        try:
            for _ in range(NEW_ITEMS_AI_MAX_PASSES):
                counts = await self.async_generate_new_item_categories()
                if counts.get("remaining", 0) <= 0:
                    break
                if counts.get("generated", 0) == 0:
                    # No forward progress. Leave the rest to the next hourly
                    # scan rather than spinning on a broken AI entity — that
                    # scan is the back-off, so no extra timer is needed.
                    break
                await asyncio.sleep(NEW_ITEMS_AI_CATCHUP_SECONDS)
        finally:
            self.new_items_ai_in_progress = was_in_progress
            self.async_update_listeners()

    async def _async_run_new_items_ai_wave(
        self, wave: list[dict[str, Any]], ai_entity: str | None
    ) -> list[dict[str, Any] | None]:
        """Send one wave of items to the AI concurrently.

        ``asyncio.gather`` is used without ``return_exceptions`` on purpose:
        :meth:`_async_categorise_new_item` already turns every ordinary
        failure into ``None``, so the only thing that can escape is
        ``CancelledError`` — and swallowing that would make an unload wait for
        the whole wave. ``gather`` does not cancel siblings when one member
        raises, hence the explicit cancel on the way out.

        Concurrency makes two coordinator attributes racy —
        ``_ai_structured_ok`` and ``_new_items_ai_last_error``. Both are
        last-writer-wins caches of "what did the backend just do", so losing a
        race costs at most one redundant structured attempt or one duplicate
        log line; neither is worth a lock on the hot path.
        """
        if len(wave) == 1:
            return [await self._async_categorise_new_item(wave[0], ai_entity)]
        tasks = [
            asyncio.create_task(self._async_categorise_new_item(item, ai_entity))
            for item in wave
        ]
        try:
            return await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            raise

    def _apply_new_item_ai_result(
        self,
        item: dict[str, Any],
        result: dict[str, Any],
        *,
        items: list[dict[str, Any]],
        new_items: dict[str, Any],
        settings: dict[str, Any],
        ai_entity: str | None,
    ) -> None:
        """Record one categorisation and apply the AI's second hide chance.

        When the AI's ``country_scope`` turns out to be foreign
        (:func:`ai_scope_hides`) and the item has no ``never_hide`` exemption,
        it moves from ``items`` to ``hidden`` with ``source == "ai"`` — the
        rules pre-filter in ``ComponentScanner.scan_new_items`` only catches
        what the repository's own text gives away; this catches what the AI
        inferred instead.

        That move is only the immediate, in-memory half of it, so the card
        updates while the batch runs. The hide *persists* because the cached
        ``country_scope`` is re-read by ``scan_new_items`` on every later
        cycle, which is the only place visibility is ever decided — hence both
        sides going through the one :func:`ai_scope_hides` predicate.

        :func:`card_category_override` is applied here, before the cache
        write, because this method is the only place in production that calls
        ``set_ai``. Overriding once at the choke point means the cached
        verdict, the in-memory item the sensor publishes, and the category
        ``restore_new_items`` later reads back for a dismissed item are all
        the same string, with no second copy of the rule to fall out of step.
        """
        full_name = item["full_name"]
        category = card_category_override(item.get("kind"), result["category"])
        self.new_items_registry.set_ai(
            full_name,
            category=category,
            country_scope=result["country_scope"],
            model=ai_entity,
        )
        item["category"] = category
        item["ai_pending"] = False

        if (
            settings["auto_hide_foreign"]
            and not self.new_items_registry.is_never_hide(full_name)
            and not self.new_items_registry.is_restored(full_name)
            and ai_scope_hides(
                item,
                result["country_scope"],
                home_country=settings["home_country"],
                extra_home_terms=settings["extra_home_terms"],
            )
        ):
            self.new_items_registry.record_hidden(
                full_name,
                country=result["country_scope"],
                term=result["country_scope"],
                field="ai_country_scope",
                source="ai",
            )
            if item in items:
                items.remove(item)
            new_items.setdefault("hidden", []).append(
                {
                    **item,
                    "reason": (
                        f'AI judged this built for "{result["country_scope"]}"'
                    ),
                    "country": result["country_scope"],
                    "term": result["country_scope"],
                    "field": "ai_country_scope",
                    "source": "ai",
                }
            )

    async def async_generate_new_item_categories(
        self, *, limit: int | None = None, max_seconds: float | None = None
    ) -> dict[str, int]:
        """Categorise pending new HACS items via the AI Task (#95, #96).

        One *pass*: waves of concurrent AI calls until the time budget runs
        out, the work runs out, or the backend stops answering. Progress is
        pushed to listeners after every wave, so a slow local model fills the
        card in progressively rather than all-or-nothing.

        The budget (``max_seconds``, defaulting to the
        ``CONF_NEW_ITEMS_AI_BUDGET_SECONDS`` option) is checked **between**
        waves and never mid-call. That is deliberate: a model slower than the
        entire budget still completes one wave per pass and so still makes
        progress, where a mid-call cut-off would make none and loop forever.

        *limit* caps the number of items considered in this pass at all; it is
        the service's ``max_items`` and is unset for automatic runs, because a
        count is exactly the hardware-blind knob v1.15.1 exists to retire.

        ``remaining`` is the pending count when the pass ends, so a non-zero
        value is the signal for the catch-up chain to run another pass.
        """
        counts = {"generated": 0, "failed": 0, "skipped": 0, "remaining": 0}
        enabled, ai_entity = self._ai_settings()
        if not enabled or not self.data:
            return counts

        new_items = self.data.get("new_items")
        if not new_items:
            return counts

        settings = self._new_items_settings()
        if not settings["scan_enabled"]:
            return counts
        budget = (
            settings["ai_budget_seconds"] if max_seconds is None else float(max_seconds)
        )

        async with self._new_items_ai_lock:
            items: list[dict[str, Any]] = new_items.get("items", [])
            todo = [item for item in items if item.get("ai_pending")]

            if limit is not None and len(todo) > limit:
                todo = todo[:limit]

            if not todo:
                # Still report the true backlog: a caller that passed
                # ``limit=0`` has selected nothing to do, which is not the
                # same as there being nothing left to sort.
                counts["remaining"] = sum(
                    1 for item in items if item.get("ai_pending")
                )
                return counts

            # Saved and restored rather than forced False, so a pass nested
            # inside the catch-up chain doesn't flip the card back to "done"
            # between passes.
            was_in_progress = self.new_items_ai_in_progress
            self.new_items_ai_in_progress = True
            self.async_update_listeners()
            started = time.monotonic()
            failed_waves = 0
            index = 0
            try:
                while index < len(todo):
                    if index and time.monotonic() - started >= budget:
                        break

                    wave = todo[index : index + max(1, self._new_items_ai_wave)]
                    index += len(wave)

                    wave_started = time.monotonic()
                    results = await self._async_run_new_items_ai_wave(wave, ai_entity)
                    elapsed = time.monotonic() - wave_started

                    ok = 0
                    async with self.new_items_review_lock:
                        for item, result in zip(wave, results):
                            if result is None:
                                counts["failed"] += 1
                                continue
                            ok += 1
                            # A scan or dismissal can replace/remove this row
                            # while its AI response is in flight. Do not apply
                            # stale work to the new review decision.
                            current = (self.data or {}).get("new_items")
                            if (
                                current is not new_items
                                or self.new_items_registry.is_dismissed(item["full_name"])
                            ):
                                counts["skipped"] += 1
                                continue
                            counts["generated"] += 1
                            self._apply_new_item_ai_result(
                                item,
                                result,
                                items=items,
                                new_items=new_items,
                                settings=self._new_items_settings(),
                                ai_entity=ai_entity,
                            )
                        # A newer scan may have replaced this batch's snapshot.
                        # Reconcile current decisions, never its obsolete hides.
                        await self.async_sync_new_items_hacs(
                            (self.data or {}).get("new_items") or {}
                        )

                    self._record_new_items_ai_rate(len(wave), ok, elapsed)
                    self._adapt_new_items_ai_wave(len(wave), ok, elapsed)
                    self.async_update_listeners()

                    if ok:
                        failed_waves = 0
                    else:
                        failed_waves += 1
                        if failed_waves >= NEW_ITEMS_AI_MAX_FAILED_WAVES:
                            break
            finally:
                # What fraction of this pass's wall clock was actually spent
                # working, measured rather than assumed, for the ETA. A
                # backend slower than the budget overruns it on its single
                # wave, so its duty cycle is far nearer 1 than the budget
                # would suggest.
                work = time.monotonic() - started
                if work > 0:
                    self._new_items_ai_duty = work / (
                        work + NEW_ITEMS_AI_CATCHUP_SECONDS
                    )
                counts["remaining"] = sum(
                    1 for item in items if item.get("ai_pending")
                )
                self.new_items_ai_in_progress = was_in_progress
                self.new_items_ai_last_run = dt_util.utcnow().isoformat()
                async with self.new_items_review_lock:
                    await self.new_items_registry.async_save()
                self.async_update_listeners()

        return counts

    async def _async_categorise_new_item(
        self, item: dict[str, Any], ai_entity: str
    ) -> dict[str, Any] | None:
        """Run a single new HACS item through the AI Task; return a verdict.

        Patterned on ``_async_categorise_one``'s structured-then-text fallback
        and its reuse of the ``_ai_structured_ok`` backend-capability probe —
        but does not open a new HTTP client or fall back to
        ``conversation.process``; it reuses ``_async_ai_call`` exactly. Unlike
        ``_async_categorise_one``, a response that merely parses to 'Other' or
        a blank country scope still counts as success (so junk is cached as
        'Other', never retried forever) — only an exception from the service
        call itself (timeout, missing entity, provider error) returns
        ``None``, leaving the item ``ai_pending`` for the next cycle.
        """
        name = item.get("name", "")
        description = item.get("description") or ""
        topics = ", ".join(item.get("topics") or [])

        # Static catalogue first, item second. See
        # NEW_ITEM_CATEGORY_CATALOGUE for why the order matters.
        base = (
            "You are triaging listings from the Home Assistant HACS store "
            "for a 'New in HACS' review card.\n\n"
            "Pick exactly one of these categories, using the label verbatim "
            "and never inventing a new one:\n"
            f"{NEW_ITEM_CATEGORY_CATALOGUE}\n\n"
            "Rules for choosing:\n"
            "- Always choose the closest category on the list, even when the "
            "fit is imperfect. A rough fit is more useful to the reader than "
            "'Other'.\n"
            "- 'Other' means the repository is not about a home or about "
            "Home Assistant at all. It is not a way to say 'none of these is "
            "exactly right'.\n"
            "- If it manages or inspects Home Assistant itself — entities, "
            "devices, integrations, logs, the database, statistics, backups, "
            "updates — that is 'System & diagnostics', not "
            "'Network & infrastructure'.\n"
            "- If the Kind below is 'card' and no other category clearly "
            "applies, it is 'Dashboard & theming'.\n"
            "- With a thin description, judge from the name and the topics "
            "rather than falling back to 'Other'.\n\n"
            "The listing:\n"
            f"Name: {name}\n"
            f"Repository: {item.get('full_name', '')}\n"
            f"Kind: {item.get('kind', '')}\n"
            f"Description: {description or '(none provided)'}\n"
            f"Topics: {topics or '(none)'}\n\n"
            "Then say whether this is built for use in one particular "
            "country (e.g. a national postal service, a local utility or "
            "transit authority) — if so, name that country; otherwise say "
            "'global'."
        )
        json_suffix = (
            "\n\nRespond with ONLY a JSON object, no prose, exactly:\n"
            '{"category": "...", "country_scope": "..."}'
        )
        structure = {
            "category": {
                "description": "The single best-fitting category",
                "required": True,
                "selector": {
                    "select": {
                        "multiple": False,
                        "options": NEW_ITEM_CATEGORY_OPTIONS,
                    }
                },
            },
            "country_scope": {
                "description": (
                    "The single country this item is built for, or 'global' "
                    "if it isn't country-specific"
                ),
                "required": True,
                "selector": {"text": {}},
            },
        }
        last_err: Exception | None = None

        if self._ai_structured_ok is not False:
            try:
                data = await self._async_ai_call(ai_entity, base, structure)
                self._ai_structured_ok = True
                self._new_items_ai_last_error = None
                return self._parse_new_item_ai_data(data)
            except asyncio.CancelledError:
                raise
            except TimeoutError as err:
                # A slow response says nothing about structured-output
                # support — don't latch it off over one hiccup (#91/#95).
                last_err = err
            except Exception as err:
                last_err = err
                self._ai_structured_ok = False

        try:
            text = await self._async_ai_call(ai_entity, base + json_suffix, None)
            self._new_items_ai_last_error = None
            return self._parse_new_item_ai_text(text)
        except Exception as err:
            last_err = err

        self._note_new_items_ai_error(name, last_err)
        return None

    def _parse_new_item_ai_data(self, data: Any) -> dict[str, Any]:
        """Parse a structured ai_task result for a new item (#95).

        Deliberately never returns ``None`` — unlike ``_parse_ai_data``. An
        unparseable or unrecognised answer maps to
        :data:`NEW_ITEM_CATEGORY_FALLBACK` so a reachable-but-junk model
        response still yields a cached, displayable verdict instead of
        leaving the item pending forever.
        """
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (ValueError, TypeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        return {
            "category": self._normalise_new_item_category(data.get("category")),
            "country_scope": self._clean_country_scope(data.get("country_scope")),
        }

    def _parse_new_item_ai_text(self, text: Any) -> dict[str, Any]:
        """Parse a plain-text ai_task result, extracting an embedded JSON object.

        Like :meth:`_parse_new_item_ai_data`, always returns a dict.
        """
        obj: dict[str, Any] = {}
        if isinstance(text, str) and text.strip():
            match = re.search(r"\{.*\}", text, re.S)
            if match:
                try:
                    parsed = json.loads(match.group(0))
                    if isinstance(parsed, dict):
                        obj = parsed
                except (ValueError, TypeError):
                    pass
        return {
            "category": self._normalise_new_item_category(obj.get("category")),
            "country_scope": self._clean_country_scope(obj.get("country_scope")),
        }

    @staticmethod
    def _clean_country_scope(raw: Any) -> str | None:
        """Normalise an AI-reported country scope; blank/global-ish -> None."""
        text = str(raw or "").strip()
        if not text or text.lower() in (
            "global",
            "none",
            "n/a",
            "na",
            "worldwide",
            "international",
            "all",
            "any",
        ):
            return None
        return text

    @staticmethod
    def _normalise_new_item_category(raw: Any) -> str:
        """Map a model's free-form category onto the New-in-HACS category set (#95)."""
        key = str(raw or "").strip().lower()
        if not key:
            return NEW_ITEM_CATEGORY_FALLBACK
        canon_by_lower = {c.lower(): c for c in NEW_ITEM_CATEGORY_OPTIONS}
        return (
            canon_by_lower.get(key)
            or NEW_ITEM_CATEGORY_ALIASES.get(key)
            or NEW_ITEM_CATEGORY_FALLBACK
        )

    def _note_new_items_ai_error(self, name: str, err: Exception | None) -> None:
        """Log a deduped, actionable warning when new-items categorisation fails."""
        msg = str(err) if err else "no usable response"
        _LOGGER.debug("New-items AI categorisation failed for %s: %s", name, msg)
        if msg != self._new_items_ai_last_error:
            self._new_items_ai_last_error = msg
            ent = self.entry.options.get(CONF_AI_TASK_ENTITY) if self.entry else "?"
            _LOGGER.warning(
                "New-items AI categorisation via '%s' failed (%s). Affected "
                "items will stay pending and retry next cycle.",
                ent,
                msg,
            )


# ---------------------------------------------------------------------------
# Sensor descriptions
# ---------------------------------------------------------------------------


SENSOR_DESCRIPTIONS: list[SensorEntityDescription] = [
    SensorEntityDescription(
        key=SENSOR_ALL_COMPONENTS,
        name="HACS Installed Components",
        icon="mdi:package-variant",
        native_unit_of_measurement="components",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_UNUSED_INTEGRATIONS,
        name="Unused Custom Integrations",
        icon="mdi:puzzle-outline",
        native_unit_of_measurement="integrations",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_UNUSED_THEMES,
        name="Unused Custom Themes",
        icon="mdi:palette-outline",
        native_unit_of_measurement="themes",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_UNUSED_FRONTEND,
        name="Unused Frontend Resources",
        icon="mdi:web",
        native_unit_of_measurement="resources",
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key=SENSOR_HACS_UPDATES,
        name="HACS Updates",
        icon="mdi:package-up",
        native_unit_of_measurement="updates",
        state_class=SensorStateClass.MEASUREMENT,
    ),
]


# ---------------------------------------------------------------------------
# Platform setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = hass.data[DOMAIN]["coordinator"]

    entities: list[CoordinatorEntity] = [
        CustomComponentMonitorSensor(coordinator, desc)
        for desc in SENSOR_DESCRIPTIONS
    ]
    if config_entry.options.get(CONF_NEW_ITEMS_ENABLED) is not False:
        entities.append(HacsNewItemsSensor(coordinator))
    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Sensor entity
# ---------------------------------------------------------------------------


class CustomComponentMonitorSensor(CoordinatorEntity, SensorEntity):
    """A Custom Component Monitor sensor."""

    # Exclude large lists from the Recorder (they can exceed the 16 384-byte
    # attribute limit) while keeping them available on the live entity state so
    # cards and templates can still access them. ATTR_UPDATES is unrecorded too
    # because AI summaries (#67) can push the updates list past the limit.
    _unrecorded_attributes = frozenset({ATTR_COMPONENTS, ATTR_UPDATES})

    def __init__(
        self,
        coordinator: CustomComponentMonitorCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"

    # -- state ---------------------------------------------------------------

    @property
    def native_value(self) -> int:
        """Return the sensor value (count)."""
        key = self.entity_description.key
        # ``or {}`` throughout: a stale-data scan keeps these entities alive
        # (#114), and an entity that is alive is asked for its state.
        coord_data = self.coordinator.data or {}

        if key == SENSOR_ALL_COMPONENTS:
            return len(coord_data.get("all_components", []))

        # Phases 2-4 will populate these; for now return 0
        if key == SENSOR_UNUSED_INTEGRATIONS:
            data = coord_data.get("integrations", {})
            return len(data.get("unused", []))
        if key == SENSOR_UNUSED_THEMES:
            data = coord_data.get("themes", {})
            return len(data.get("unused", []))
        if key == SENSOR_UNUSED_FRONTEND:
            data = coord_data.get("frontend", {})
            return len(data.get("unused", []))
        if key == SENSOR_HACS_UPDATES:
            data = coord_data.get("updates", {})
            return data.get("count", 0)

        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes."""
        key = self.entity_description.key
        coord_data = self.coordinator.data or {}
        attrs: dict[str, Any] = {
            "component_provider": coord_data.get("provider"),
            # Scan health on every sensor, so the cards can say "these counts
            # are from earlier" instead of either hiding the staleness or
            # showing nothing at all (#114).
            ATTR_DATA_STALE: bool(coord_data.get(ATTR_DATA_STALE, False)),
            ATTR_LAST_SUCCESSFUL_SCAN: coord_data.get(ATTR_LAST_SUCCESSFUL_SCAN),
            ATTR_SCAN_ERROR: coord_data.get(ATTR_SCAN_ERROR),
            ATTR_STORE_STATUS: coord_data.get(ATTR_STORE_STATUS),
        }

        if key == SENSOR_ALL_COMPONENTS:
            components = coord_data.get("all_components", [])
            attrs[ATTR_TOTAL_COMPONENTS] = len(components)
            attrs[ATTR_COMPONENTS] = components
            # When exact install-date tracking began; the cards use it to
            # explain why older components carry an estimated date (#93).
            attrs[ATTR_INSTALL_DATES_SINCE] = coord_data.get("install_dates_since")

        elif key in (
            SENSOR_UNUSED_INTEGRATIONS,
            SENSOR_UNUSED_THEMES,
            SENSOR_UNUSED_FRONTEND,
        ):
            # Phases 2-4 will add real data here
            bucket = {
                SENSOR_UNUSED_INTEGRATIONS: "integrations",
                SENSOR_UNUSED_THEMES: "themes",
                SENSOR_UNUSED_FRONTEND: "frontend",
            }[key]
            data = coord_data.get(bucket, {})
            attrs[ATTR_TOTAL_COMPONENTS] = data.get("total", 0)
            attrs[ATTR_USED_COMPONENTS] = len(data.get("used", []))
            attrs[ATTR_UNUSED_COMPONENTS] = data.get("unused", [])
            attrs[ATTR_EXCLUDED_COMPONENTS] = data.get("excluded", [])

        elif key == SENSOR_HACS_UPDATES:
            data = coord_data.get("updates", {})
            attrs[ATTR_UPDATES] = data.get("updates", [])
            # Small scalars, kept recorded so automations can act on the AI
            # backlog and the card can show progress (#91).
            attrs[ATTR_AI_ENABLED] = self.coordinator.ai_enabled
            attrs[ATTR_AI_PENDING] = data.get("ai_pending", 0)
            attrs[ATTR_AI_IN_PROGRESS] = self.coordinator.ai_in_progress
            attrs[ATTR_AI_LAST_ERROR] = self.coordinator.ai_last_error
            attrs[ATTR_AI_LAST_RUN] = self.coordinator.ai_last_run
            # How many of those updates this Home Assistant is too old for,
            # and how many declared nothing usable (#111).
            attrs[ATTR_UPDATES_BLOCKED] = data.get("blocked", 0)
            attrs[ATTR_UPDATES_UNKNOWN_REQUIREMENT] = data.get(
                "unknown_requirement", 0
            )

        if "last_scan" in coord_data:
            attrs["last_scan"] = coord_data["last_scan"]

        return attrs


# ---------------------------------------------------------------------------
# New in HACS sensor (#95)
# ---------------------------------------------------------------------------


class HacsNewItemsSensor(CoordinatorEntity, SensorEntity):
    """New, not-yet-installed HACS repositories, triaged for review (#95).

    A dedicated class rather than another branch in
    ``CustomComponentMonitorSensor``'s dict-dispatch: that sensor's
    ``if key == ...`` ladder is built around the used/unused shape shared by
    the first four sensors, and grafting the by-category/hidden/dismissed
    shape onto it would make both harder to follow. ``_attr_*`` class
    attributes stand in for a ``SensorEntityDescription`` for the same
    reason — this entity's state and attributes don't come from the shared
    description list at all.
    """

    _attr_name = "HACS New Items"
    _attr_icon = "mdi:new-box"
    _attr_native_unit_of_measurement = "items"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _unrecorded_attributes = frozenset({ATTR_ITEMS, ATTR_HIDDEN})

    def __init__(self, coordinator: CustomComponentMonitorCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}_{SENSOR_NEW_ITEMS}"

    @property
    def _new_items(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get("new_items") or {}

    @property
    def native_value(self) -> int:
        """Return the number of visible (not hidden, not dismissed) new items."""
        return len(self._new_items.get("items", []))

    @staticmethod
    def _newest_first(item: dict[str, Any]) -> tuple[int, int]:
        """Sort key for choosing which rows survive the cap: newest, then popular.

        The card re-sorts whatever it is given, so this only decides *which*
        rows are published when there are more than the cap allows. Newest
        first is the right bias for a review queue, and stars break the tie on
        a day when fifty things arrive at once.
        """
        return (item.get("age_days") or 0, -(item.get("stars") or 0))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return state attributes.

        ``items`` and ``hidden`` are capped here (see
        :data:`NEW_ITEMS_MAX_EXPOSED`). The state itself, ``total_new``,
        ``hidden_count`` and ``dismissed_count`` all keep counting the full
        set, and the two ``*_truncated`` attributes say how much was held
        back — so the numbers on screen stay true even when the list is
        shortened. ``by_category`` is deliberately computed over the
        *published* rows, so a group header never promises a row the card was
        never given.
        """
        data = self._new_items
        all_items: list[dict[str, Any]] = data.get("items", [])
        all_hidden: list[dict[str, Any]] = data.get("hidden", [])

        items = all_items
        if len(all_items) > NEW_ITEMS_MAX_EXPOSED:
            items = sorted(all_items, key=self._newest_first)[:NEW_ITEMS_MAX_EXPOSED]
        hidden = all_hidden
        if len(all_hidden) > NEW_ITEMS_MAX_EXPOSED_HIDDEN:
            hidden = sorted(all_hidden, key=self._newest_first)[
                :NEW_ITEMS_MAX_EXPOSED_HIDDEN
            ]

        by_category: dict[str, int] = {}
        for item in items:
            category = item.get("category", NEW_ITEM_CATEGORY_FALLBACK)
            by_category[category] = by_category.get(category, 0) + 1

        coord_data = self.coordinator.data or {}
        attrs: dict[str, Any] = {
            "component_provider": coord_data.get("provider"),
            # Same scan-health block as the other five sensors (#114).
            ATTR_DATA_STALE: bool(coord_data.get(ATTR_DATA_STALE, False)),
            ATTR_LAST_SUCCESSFUL_SCAN: coord_data.get(ATTR_LAST_SUCCESSFUL_SCAN),
            ATTR_SCAN_ERROR: coord_data.get(ATTR_SCAN_ERROR),
            ATTR_STORE_STATUS: coord_data.get(ATTR_STORE_STATUS),
            ATTR_ITEMS: items,
            ATTR_BY_CATEGORY: by_category,
            ATTR_HIDDEN: hidden,
            ATTR_HIDDEN_COUNT: len(all_hidden),
            ATTR_ITEMS_TRUNCATED: len(all_items) - len(items),
            ATTR_HIDDEN_TRUNCATED: len(all_hidden) - len(hidden),
            ATTR_DISMISSED_COUNT: data.get("dismissed_count", 0),
            ATTR_TOTAL_NEW: data.get("total_new", 0),
            "hacs_sync_pending": len(self.coordinator.new_items_registry.hacs_pending),
            "hacs_sync_last_error": self.coordinator.hacs_sync_last_error,
            ATTR_HOME_COUNTRY: coord_data.get("new_items_home_country"),
            ATTR_AI_ENABLED: self.coordinator.ai_enabled,
            # Counted over the full set, not the published slice: this drives
            # the card's "27 still being sorted" line, which is about the
            # backlog, not about what fitted in the attributes.
            ATTR_AI_PENDING: sum(1 for item in all_items if item.get("ai_pending")),
            ATTR_AI_IN_PROGRESS: self.coordinator.new_items_ai_in_progress,
            ATTR_AI_LAST_ERROR: self.coordinator.new_items_ai_last_error,
            ATTR_AI_LAST_RUN: self.coordinator.new_items_ai_last_run,
        }
        # Measured throughput and what it implies for the backlog (v1.15.1).
        # Both stay null until a wave has run, because they are measurements
        # and an invented number here would be worse than no number.
        pending = attrs[ATTR_AI_PENDING]
        attrs[ATTR_AI_RATE_PER_MINUTE] = self.coordinator.new_items_ai_rate_per_minute
        attrs[ATTR_AI_ETA_SECONDS] = self.coordinator.new_items_ai_eta_seconds(pending)
        if "last_scan" in coord_data:
            attrs["last_scan"] = coord_data["last_scan"]
        return attrs
