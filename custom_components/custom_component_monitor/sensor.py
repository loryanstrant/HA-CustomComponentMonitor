"""Sensor platform for Custom Component Monitor."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_TOTAL_COMPONENTS,
    ATTR_UNUSED_COMPONENTS,
    ATTR_USED_COMPONENTS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SENSOR_UNUSED_FRONTEND,
    SENSOR_UNUSED_INTEGRATIONS,
    SENSOR_UNUSED_THEMES,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)


class ComponentScanner:
    """Scanner for custom components."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the scanner."""
        self.hass = hass
        self.config_dir = Path(hass.config.config_dir)
        self._hacs_repositories = None

    def _load_storage_file(self, filename: str) -> dict[str, Any]:
        """Load a storage file and return its data."""
        try:
            storage_path = self.config_dir / ".storage" / filename
            if not storage_path.exists():
                _LOGGER.debug("Storage file not found: %s", storage_path)
                return {}
                
            with open(storage_path, encoding="utf-8") as f:
                storage_data = json.load(f)
                
            return storage_data.get("data", {})
            
        except (json.JSONDecodeError, FileNotFoundError, OSError) as ex:
            _LOGGER.debug("Could not load storage file %s: %s", filename, ex)
            return {}

    def _get_configured_integrations(self) -> set[str]:
        """Get configured integration domains from core.config_entries."""
        config_entries_data = self._load_storage_file("core.config_entries")
        configured_domains = set()
        
        for entry in config_entries_data.get("entries", []):
            domain = entry.get("domain")
            if domain and domain != "hacs":  # Exclude HACS itself
                configured_domains.add(domain)
        
        _LOGGER.debug("Found configured domains: %s", configured_domains)
        return configured_domains

    def _get_used_themes_and_resources_from_storage(self) -> tuple[set[str], set[str]]:
        """Get used themes and frontend resources from lovelace storage files."""
        storage_dir = self.config_dir / ".storage"
        themes_used = set()
        resources_used = set()
        
        if not storage_dir.exists():
            _LOGGER.debug("Storage directory not found: %s", storage_dir)
            return themes_used, resources_used
        
        # Find all files that start with "lovelace" - this includes:
        # - lovelace (main dashboard)
        # - lovelace.map, lovelace.family_area (named dashboards)
        # - lovelace_backup, lovelace.area.kitchen (other variations)
        lovelace_files = []
        for file_path in storage_dir.iterdir():
            if file_path.is_file() and file_path.name.startswith("lovelace"):
                lovelace_files.append(file_path)
        
        _LOGGER.debug("Found %d lovelace files: %s", len(lovelace_files), 
                     [f.name for f in lovelace_files])
        
        for file_path in lovelace_files:
            _LOGGER.debug("Processing lovelace file: %s", file_path.name)
            try:
                with open(file_path, encoding="utf-8") as f:
                    data = json.load(f)
                
                config = data.get("data", {}).get("config", {})
                if not config:
                    _LOGGER.debug("Skipping %s: no config section found", file_path.name)
                    continue
                
                _LOGGER.debug("Processing config from %s", file_path.name)
                
                # Check for theme at root level
                if "theme" in config:
                    themes_used.add(config["theme"])
                    _LOGGER.debug("Found root theme '%s' in %s", config["theme"], file_path.name)
                
                # Check for themes in views
                for i, view in enumerate(config.get("views", [])):
                    if "theme" in view:
                        themes_used.add(view["theme"])
                        _LOGGER.debug("Found view theme '%s' in %s view %d", view["theme"], file_path.name, i)
                
                # Check for frontend resources
                for i, resource in enumerate(config.get("resources", [])):
                    url = resource.get("url", "")
                    if url.startswith("/hacsfiles/"):
                        # Extract component name from path
                        parts = url.split("/")
                        if len(parts) >= 3:
                            component_name = parts[2]  # /hacsfiles/component-name/file.js
                            resources_used.add(component_name)
                            _LOGGER.debug("Found resource '%s' from URL '%s' in %s", 
                                         component_name, url, file_path.name)
                
                # Check for custom card types in cards
                def extract_custom_cards(cards, file_name, view_index=None, depth=0):
                    custom_cards = set()
                    if not isinstance(cards, list):
                        return custom_cards
                    
                    for j, card in enumerate(cards):
                        if isinstance(card, dict):
                            card_type = card.get("type", "")
                            if card_type.startswith("custom:"):
                                card_name = card_type[7:]  # Remove "custom:" prefix
                                custom_cards.add(card_name)
                                location = f"{file_name}"
                                if view_index is not None:
                                    location += f" view {view_index}"
                                if depth > 0:
                                    location += f" depth {depth}"
                                _LOGGER.debug("Found custom card '%s' in %s", card_name, location)
                            
                            # Recursively check nested cards
                            if "cards" in card:
                                nested_cards = extract_custom_cards(
                                    card["cards"], file_name, view_index, depth + 1
                                )
                                custom_cards.update(nested_cards)
                    
                    return custom_cards
                
                for i, view in enumerate(config.get("views", [])):
                    if "cards" in view:
                        view_custom_cards = extract_custom_cards(view["cards"], file_path.name, i)
                        resources_used.update(view_custom_cards)
                        
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as ex:
                _LOGGER.warning("Error processing lovelace file %s: %s", file_path.name, ex)
                continue
        
        _LOGGER.debug("Found used themes: %s", themes_used)
        _LOGGER.debug("Found used resources: %s", resources_used)
        return themes_used, resources_used

    def _is_frontend_resource_used(self, resource: dict[str, Any], local_path: str) -> bool:
        """Check if a frontend resource is used by scanning lovelace storage files."""
        try:
            _, resources_used = self._get_used_themes_and_resources_from_storage()
            
            # Get resource name from the HACS repository name or path
            resource_name = resource.get("name", "")
            hacs_repo = resource.get("hacs_repository", "")
            
            # Extract component name from repository name
            if hacs_repo and "/" in hacs_repo:
                repo_name = hacs_repo.split("/")[-1]
            else:
                repo_name = resource_name
            
            # Check various name patterns
            name_variants = {resource_name, repo_name}
            if local_path:
                # Extract name from local path
                if local_path.startswith("www/"):
                    path_name = local_path[4:].split("/")[0]
                    name_variants.add(path_name)
            
            # Check if any variant matches
            for variant in name_variants:
                if variant in resources_used:
                    return True
            
            return False
        except Exception as ex:
            _LOGGER.debug("Error checking frontend resource usage for %s: %s", local_path, ex)
            return False

    def _is_theme_used(self, theme: dict[str, Any], current_theme: str, configured_themes: set) -> bool:
        """Check if a theme is used by scanning lovelace storage files."""
        theme_name = theme["name"]
        
        # Get used themes from storage files
        themes_used, _ = self._get_used_themes_and_resources_from_storage()
        
        # Check direct name match
        if theme_name in themes_used:
            return True
        
        # Check for partial name matches (themes might use different naming conventions)
        theme_base_name = theme_name.split("/")[-1] if "/" in theme_name else theme_name
        theme_base_name = theme_base_name.replace("-", "_").replace("_", "-")
        
        for used_theme in themes_used:
            used_base = used_theme.replace("-", "_").replace("_", "-")
            if (theme_base_name.lower() in used_base.lower() or
                used_base.lower() in theme_base_name.lower()):
                return True
        
        # Also check HACS repository name
        hacs_repo = theme.get("hacs_repository", "")
        if hacs_repo and "/" in hacs_repo:
            repo_name = hacs_repo.split("/")[-1]
            if repo_name in themes_used:
                return True
        
        return False

    def _load_hacs_repositories(self) -> dict[str, Any]:
        """Load HACS repositories from .storage/hacs.repositories file."""
        if self._hacs_repositories is not None:
            return self._hacs_repositories
            
        try:
            hacs_storage_path = self.config_dir / ".storage" / "hacs.repositories"
            if not hacs_storage_path.exists():
                _LOGGER.debug("HACS repositories file not found at %s", hacs_storage_path)
                self._hacs_repositories = {}
                return self._hacs_repositories
                
            with open(hacs_storage_path, encoding="utf-8") as f:
                hacs_data = json.load(f)
                
            # Extract repositories data
            repositories = hacs_data.get("data", {})
            _LOGGER.debug("Loaded %d repositories from HACS storage", len(repositories))
            self._hacs_repositories = repositories
            return self._hacs_repositories
            
        except (json.JSONDecodeError, FileNotFoundError, OSError) as ex:
            _LOGGER.warning("Could not load HACS repositories file: %s", ex)
            self._hacs_repositories = {}
            return self._hacs_repositories

    def _get_installation_date(self, path: Path | str) -> str | None:
        """Get the installation date of a component."""
        try:
            # Convert string to Path if needed
            if isinstance(path, str):
                path = Path(path)
            
            # Check if path exists
            if not path.exists():
                return None
            
            # Get the creation time (birth time on some systems) or modification time
            stat = path.stat()
            
            # Use st_ctime (creation time on Windows, metadata change time on Unix)
            # This is the best approximation for installation time
            install_time = datetime.fromtimestamp(stat.st_ctime)
            return install_time.isoformat()
        except (OSError, ValueError) as ex:
            _LOGGER.debug("Could not get installation date for %s: %s", path, ex)
            return None

    async def scan_custom_integrations(self) -> dict[str, Any]:
        """Scan for HACS-installed custom integrations."""
        # Load HACS repositories and filter for installed integrations
        hacs_repositories = self._load_hacs_repositories()
        
        installed_integrations = []
        
        for repo_key, repo_data in hacs_repositories.items():
            if not isinstance(repo_data, dict):
                continue
            
            # Only process integrations that are installed via HACS
            if (repo_data.get("category") == "integration" and 
                repo_data.get("installed", False)):
                
                domain = repo_data.get("domain", "")
                if not domain:
                    # Try to extract domain from repository_manifest
                    repo_manifest = repo_data.get("repository_manifest", {})
                    if isinstance(repo_manifest, dict):
                        domain = repo_manifest.get("domain", "")
                
                if not domain:
                    # Skip if we can't determine the domain
                    continue
                
                # Extract repository information
                full_name = repo_data.get("full_name", "")
                repo_url = f"https://github.com/{full_name}" if full_name else ""
                
                # Get documentation from repository manifest or fallback
                repo_manifest = repo_data.get("repository_manifest", {})
                doc_url = ""
                if isinstance(repo_manifest, dict):
                    doc_url = repo_manifest.get("documentation", repo_url)
                elif repo_url:
                    doc_url = repo_url
                
                # Get name from HACS data or repository manifest
                name = repo_data.get("name", "")
                if not name and isinstance(repo_manifest, dict):
                    name = repo_manifest.get("name", domain)
                if not name:
                    name = domain
                
                # Get installation date from local path if available
                local_path = repo_data.get("local_path", "")
                install_date = None
                if local_path:
                    # Try to construct a valid path
                    try:
                        if not Path(local_path).is_absolute():
                            # If it's relative, make it relative to config directory
                            local_path = str(self.config_dir / local_path)
                        install_date = self._get_installation_date(local_path)
                    except Exception:
                        # Fallback: try to construct path from domain
                        install_date = self._get_installation_date(
                            self.config_dir / "custom_components" / domain
                        )
                
                installed_integrations.append({
                    "domain": domain,
                    "name": name,
                    "documentation": doc_url,
                    "repository": repo_url,
                    "version": repo_data.get("version_installed", "unknown"),
                    "codeowners": [],  # Not available in HACS data
                    "installed_date": install_date,
                    "hacs_repository": full_name,
                    "hacs_status": "installed",
                })

        # Check which integrations are actually configured
        configured_domains = self._get_configured_integrations()
        used_integrations = []
        unused_integrations = []
        
        for integration in installed_integrations:
            domain = integration["domain"]
            # Check if integration is configured in storage
            is_used = domain in configured_domains
            
            if is_used:
                used_integrations.append(integration)
            else:
                unused_integrations.append(integration)

        return {
            "total": len(installed_integrations),
            "used": used_integrations,
            "unused": unused_integrations,
        }

    async def scan_custom_themes(self) -> dict[str, Any]:
        """Scan for HACS-installed custom themes."""
        # Load HACS repositories and filter for installed themes
        hacs_repositories = self._load_hacs_repositories()
        
        installed_themes = []
        
        for repo_key, repo_data in hacs_repositories.items():
            if not isinstance(repo_data, dict):
                continue
            
            # Only process themes that are installed via HACS
            if (repo_data.get("category") == "theme" and 
                repo_data.get("installed", False)):
                
                # Extract repository information
                full_name = repo_data.get("full_name", "")
                repo_url = f"https://github.com/{full_name}" if full_name else ""
                
                # Get documentation from repository manifest or fallback
                repo_manifest = repo_data.get("repository_manifest", {})
                doc_url = ""
                display_name = repo_data.get("name", "")
                
                if isinstance(repo_manifest, dict):
                    doc_url = repo_manifest.get("documentation", repo_url)
                    # Use repository manifest name if available
                    if not display_name:
                        display_name = repo_manifest.get("name", "")
                elif repo_url:
                    doc_url = repo_url
                
                # Use repository name as fallback for display name
                if not display_name and full_name:
                    display_name = full_name.split("/")[-1] if "/" in full_name else full_name
                
                # Get installation date from local path if available
                local_path = repo_data.get("local_path", "")
                install_date = None
                theme_file_path = ""
                
                if local_path:
                    theme_file_path = local_path
                    # Try to construct a valid path
                    try:
                        if not Path(local_path).is_absolute():
                            # If it's relative, make it relative to config directory
                            local_path = str(self.config_dir / local_path)
                        install_date = self._get_installation_date(local_path)
                    except Exception:
                        # Fallback: try themes directory
                        install_date = self._get_installation_date(
                            self.config_dir / "themes" / display_name
                        )
                
                installed_themes.append({
                    "name": display_name,
                    "file": theme_file_path,
                    "full_path": local_path,
                    "repository": repo_url,
                    "documentation": doc_url,
                    "installed_date": install_date,
                    "hacs_repository": full_name,
                    "hacs_status": "installed",
                    "version": repo_data.get("version_installed", "unknown"),
                })

        # Check theme usage from storage files
        used_themes = []
        unused_themes = []
        
        for theme in installed_themes:
            is_used = self._is_theme_used(theme, None, set())
            
            if is_used:
                used_themes.append(theme)
            else:
                unused_themes.append(theme)

        return {
            "total": len(installed_themes),
            "used": used_themes,
            "unused": unused_themes,
        }

    async def scan_custom_frontend(self) -> dict[str, Any]:
        """Scan for HACS-installed frontend resources."""
        # Load HACS repositories and filter for installed plugins (frontend resources)
        hacs_repositories = self._load_hacs_repositories()
        
        installed_frontend = []
        
        for repo_key, repo_data in hacs_repositories.items():
            if not isinstance(repo_data, dict):
                continue
            
            # Only process plugins (frontend resources) that are installed via HACS
            if (repo_data.get("category") == "plugin" and 
                repo_data.get("installed", False)):
                
                # Extract repository information
                full_name = repo_data.get("full_name", "")
                repo_url = f"https://github.com/{full_name}" if full_name else ""
                
                # Get documentation from repository manifest or fallback
                repo_manifest = repo_data.get("repository_manifest", {})
                doc_url = ""
                display_name = repo_data.get("name", "")
                
                if isinstance(repo_manifest, dict):
                    doc_url = repo_manifest.get("documentation", repo_url)
                    # Use repository manifest name if available
                    if not display_name:
                        display_name = repo_manifest.get("name", "")
                elif repo_url:
                    doc_url = repo_url
                
                # Use repository name as fallback for display name
                if not display_name and full_name:
                    display_name = full_name.split("/")[-1] if "/" in full_name else full_name
                
                # Get installation date from local path if available
                local_path = repo_data.get("local_path", "")
                install_date = None
                resource_path = ""
                
                if local_path:
                    try:
                        # Convert to relative path from config directory if possible
                        if Path(local_path).is_absolute():
                            try:
                                resource_path = str(Path(local_path).relative_to(self.config_dir))
                            except ValueError:
                                # If path is not relative to config dir, use as-is
                                resource_path = local_path
                        else:
                            resource_path = local_path
                            
                        install_date = self._get_installation_date(local_path)
                    except Exception:
                        # Fallback: use the path as-is
                        resource_path = local_path
                
                installed_frontend.append({
                    "name": display_name,
                    "path": resource_path,
                    "type": "hacs_plugin",
                    "full_path": local_path,
                    "repository": repo_url,
                    "documentation": doc_url,
                    "installed_date": install_date,
                    "hacs_repository": full_name,
                    "hacs_status": "installed",
                    "version": repo_data.get("version_installed", "unknown"),
                })

        # Check for usage in Lovelace configuration from storage files
        used_frontend = []
        unused_frontend = []
        
        # Check each frontend resource
        for resource in installed_frontend:
            resource_path = resource["path"]
            if resource_path.startswith("www/"):
                local_path = resource_path[4:]  # Remove "www/" prefix
            else:
                local_path = resource_path
                
            # Check if resource is used in lovelace storage files
            is_used = self._is_frontend_resource_used(resource, local_path)
            
            if is_used:
                used_frontend.append(resource)
            else:
                unused_frontend.append(resource)

        return {
            "total": len(installed_frontend),
            "used": used_frontend,
            "unused": unused_frontend,
        }


class CustomComponentMonitorCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the API."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize."""
        self.scanner = ComponentScanner(hass)
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Update data via library."""
        try:
            _LOGGER.debug("Starting custom component scan")
            
            integrations = await self.scanner.scan_custom_integrations()
            _LOGGER.debug("Found %d custom integrations (%d unused)", 
                         integrations["total"], len(integrations["unused"]))
            
            themes = await self.scanner.scan_custom_themes()
            _LOGGER.debug("Found %d custom themes (%d unused)", 
                         themes["total"], len(themes["unused"]))
            
            frontend = await self.scanner.scan_custom_frontend()
            _LOGGER.debug("Found %d frontend resources (%d unused)", 
                         frontend["total"], len(frontend["unused"]))
            
            return {
                "integrations": integrations,
                "themes": themes,
                "frontend": frontend,
                "last_scan": dt_util.now().isoformat(),
            }
        except Exception as exception:
            _LOGGER.error("Error updating custom component data: %s", exception)
            raise UpdateFailed(exception) from exception


SENSOR_DESCRIPTIONS = [
    SensorEntityDescription(
        key=SENSOR_UNUSED_INTEGRATIONS,
        name="Unused Custom Integrations",
        icon="mdi:puzzle-outline",
    ),
    SensorEntityDescription(
        key=SENSOR_UNUSED_THEMES,
        name="Unused Custom Themes",
        icon="mdi:palette-outline",
    ),
    SensorEntityDescription(
        key=SENSOR_UNUSED_FRONTEND,
        name="Unused Frontend Resources",
        icon="mdi:web",
    ),
]


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the sensor platform."""
    coordinator = CustomComponentMonitorCoordinator(hass)
    await coordinator.async_config_entry_first_refresh()

    entities = []
    for description in SENSOR_DESCRIPTIONS:
        entities.append(CustomComponentMonitorSensor(coordinator, description))

    async_add_entities(entities)


class CustomComponentMonitorSensor(CoordinatorEntity, SensorEntity):
    """Implementation of a Custom Component Monitor sensor."""

    def __init__(
        self,
        coordinator: CustomComponentMonitorCoordinator,
        description: SensorEntityDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{DOMAIN}_{description.key}"

    @property
    def native_value(self) -> int:
        """Return the native value of the sensor."""
        if self.entity_description.key == SENSOR_UNUSED_INTEGRATIONS:
            return len(self.coordinator.data.get("integrations", {}).get("unused", []))
        elif self.entity_description.key == SENSOR_UNUSED_THEMES:
            return len(self.coordinator.data.get("themes", {}).get("unused", []))
        elif self.entity_description.key == SENSOR_UNUSED_FRONTEND:
            return len(self.coordinator.data.get("frontend", {}).get("unused", []))
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if self.entity_description.key == SENSOR_UNUSED_INTEGRATIONS:
            data = self.coordinator.data.get("integrations", {})
        elif self.entity_description.key == SENSOR_UNUSED_THEMES:
            data = self.coordinator.data.get("themes", {})
        elif self.entity_description.key == SENSOR_UNUSED_FRONTEND:
            data = self.coordinator.data.get("frontend", {})
        else:
            data = {}

        attributes = {
            ATTR_TOTAL_COMPONENTS: data.get("total", 0),
            ATTR_USED_COMPONENTS: len(data.get("used", [])),
            ATTR_UNUSED_COMPONENTS: data.get("unused", []),
        }
        
        # Add last scan time
        if "last_scan" in self.coordinator.data:
            attributes["last_scan"] = self.coordinator.data["last_scan"]
            
        return attributes