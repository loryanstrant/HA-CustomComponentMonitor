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

    def _is_frontend_resource_used(self, resource: dict[str, Any], local_path: str) -> bool:
        """Enhanced check to determine if a frontend resource is used."""
        try:
            # Check if resource is referenced in Home Assistant configuration files
            config_files = [
                self.hass.config.config_dir / "configuration.yaml",
                self.hass.config.config_dir / "ui-lovelace.yaml",
            ]
            
            for config_file in config_files:
                if config_file.exists():
                    try:
                        with open(config_file, encoding="utf-8") as f:
                            content = f.read()
                            if local_path in content or resource["name"] in content:
                                return True
                    except (OSError, UnicodeDecodeError):
                        continue
            
            # Check if it's in a dashboard configuration directory
            dashboards_dir = self.hass.config.config_dir / "dashboards"
            if dashboards_dir.exists():
                for dashboard_file in dashboards_dir.glob("*.yaml"):
                    try:
                        with open(dashboard_file, encoding="utf-8") as f:
                            content = f.read()
                            if local_path in content or resource["name"] in content:
                                return True
                    except (OSError, UnicodeDecodeError):
                        continue
            
            return False
        except Exception as ex:
            _LOGGER.debug("Error checking frontend resource usage for %s: %s", local_path, ex)
            return False

    def _is_theme_used(self, theme: dict[str, Any], current_theme: str, configured_themes: set) -> bool:
        """Enhanced check to determine if a theme is used."""
        theme_name = theme["name"]
        theme_file = theme.get("file", "")
        
        # Basic checks
        if (theme_name == current_theme or 
            theme_name in configured_themes or
            any(theme_name in str(configured) for configured in configured_themes)):
            return True
        
        # Check for partial name matches (themes might use different naming conventions)
        theme_base_name = theme_name.split("/")[-1] if "/" in theme_name else theme_name
        theme_base_name = theme_base_name.replace("-", "_").replace("_", "-")
        
        for configured in configured_themes:
            configured_base = configured.replace("-", "_").replace("_", "-")
            if (theme_base_name.lower() in configured_base.lower() or
                configured_base.lower() in theme_base_name.lower()):
                return True
        
        # Check if theme is referenced in configuration files
        try:
            config_files = [
                self.hass.config.config_dir / "configuration.yaml",
                self.hass.config.config_dir / "themes.yaml",
            ]
            
            for config_file in config_files:
                if config_file.exists():
                    try:
                        with open(config_file, encoding="utf-8") as f:
                            content = f.read()
                            if (theme_name in content or 
                                theme_base_name in content or
                                (theme_file and theme_file in content)):
                                return True
                    except (OSError, UnicodeDecodeError):
                        continue
        except Exception as ex:
            _LOGGER.debug("Error checking theme configuration files: %s", ex)
        
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
        used_integrations = []
        unused_integrations = []
        
        for integration in installed_integrations:
            domain = integration["domain"]
            # Check if integration is loaded and has entities or is configured
            is_used = (
                domain in self.hass.config.components or
                any(entity_id.startswith(f"{domain}.") for entity_id in self.hass.states.async_entity_ids())
            )
            
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

        # Get current theme from frontend and check all configured themes
        used_themes = []
        unused_themes = []
        
        # Get theme configuration from Home Assistant
        current_theme = None
        configured_themes = set()
        
        # Try to get current theme from frontend
        if "frontend" in self.hass.data:
            current_theme = self.hass.data["frontend"].get("default_theme")
        
        # Try to get theme configuration from core
        if hasattr(self.hass.data, "themes") and self.hass.data.get("themes"):
            theme_data = self.hass.data["themes"]
            if hasattr(theme_data, "themes"):
                configured_themes.update(theme_data.themes.keys())
        
        for theme in installed_themes:
            is_used = self._is_theme_used(theme, current_theme, configured_themes)
            
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

        # Check for usage in Lovelace configuration
        used_frontend = []
        unused_frontend = []
        
        # Get Lovelace configuration to check for referenced resources
        lovelace_resources = set()
        
        # Try to get resources from Lovelace configuration
        try:
            if "lovelace" in self.hass.data:
                lovelace_config = self.hass.data.get("lovelace", {})
                if hasattr(lovelace_config, "config") and lovelace_config.config:
                    resources = lovelace_config.config.get("resources", [])
                    for resource in resources:
                        if isinstance(resource, dict) and "url" in resource:
                            url = resource["url"]
                            if url.startswith("/local/"):
                                lovelace_resources.add(url[7:])  # Remove "/local/" prefix
        except Exception as ex:
            _LOGGER.debug("Could not check Lovelace resources: %s", ex)

        # Check each frontend resource
        for resource in installed_frontend:
            resource_path = resource["path"]
            if resource_path.startswith("www/"):
                local_path = resource_path[4:]  # Remove "www/" prefix
            else:
                local_path = resource_path
                
            # For HACS plugins, we consider them used if they are installed via HACS
            # since they are typically actively managed components
            is_used = (
                local_path in lovelace_resources or
                any(local_path in res for res in lovelace_resources) or
                self._is_frontend_resource_used(resource, local_path)  # Enhanced usage detection
            )
            
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