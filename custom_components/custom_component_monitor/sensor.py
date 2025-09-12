"""Sensor platform for Custom Component Monitor."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

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

    def _get_installation_date(self, path: Path) -> str | None:
        """Get the installation date of a component."""
        try:
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

    def _extract_hacs_repository_info(self, component_path: Path, component_type: str) -> dict[str, str]:
        """Extract repository information from HACS metadata or other sources."""
        repo_info = {"repository": "", "documentation": ""}
        
        try:
            # Check for HACS info in the component directory
            hacs_info_file = component_path / ".hacs_info"
            if hacs_info_file.exists():
                try:
                    with open(hacs_info_file, encoding="utf-8") as f:
                        hacs_data = json.load(f)
                    repo_info["repository"] = hacs_data.get("repository", "")
                    repo_info["documentation"] = hacs_data.get("documentation", "")
                except (json.JSONDecodeError, FileNotFoundError):
                    pass
            
            # Check for repository info in theme YAML files
            if component_type == "theme" and component_path.suffix in [".yaml", ".yml"] and HAS_YAML:
                try:
                    with open(component_path, encoding="utf-8") as f:
                        theme_data = yaml.safe_load(f)
                    if isinstance(theme_data, dict):
                        # Look for repository info in theme metadata
                        for theme_name, theme_config in theme_data.items():
                            if isinstance(theme_config, dict):
                                repo_url = theme_config.get("repository", "")
                                doc_url = theme_config.get("documentation", "")
                                if repo_url:
                                    repo_info["repository"] = repo_url
                                if doc_url:
                                    repo_info["documentation"] = doc_url
                                break
                except Exception:
                    # Handle both YAML errors (if available) and other exceptions
                    pass
            
            # Check for package.json in frontend resources
            if component_type == "frontend":
                package_json = component_path / "package.json"
                if package_json.exists():
                    try:
                        with open(package_json, encoding="utf-8") as f:
                            package_data = json.load(f)
                        
                        # Extract repository from package.json
                        repo_data = package_data.get("repository", {})
                        if isinstance(repo_data, dict):
                            repo_info["repository"] = repo_data.get("url", "")
                        elif isinstance(repo_data, str):
                            repo_info["repository"] = repo_data
                        
                        # Extract homepage as documentation
                        homepage = package_data.get("homepage", "")
                        if homepage:
                            repo_info["documentation"] = homepage
                            
                    except (json.JSONDecodeError, FileNotFoundError):
                        pass
            
            # Check parent directories for HACS metadata (common pattern)
            parent_path = component_path.parent
            for metadata_file in [".hacs_repository", "hacs.json", ".github/HACS_MANIFEST.json"]:
                metadata_path = parent_path / metadata_file
                if metadata_path.exists():
                    try:
                        with open(metadata_path, encoding="utf-8") as f:
                            if metadata_file.endswith(".json"):
                                metadata = json.load(f)
                            else:
                                # Plain text repository URL
                                repo_info["repository"] = f.read().strip()
                                break
                        
                        if metadata_file.endswith(".json"):
                            repo_info["repository"] = metadata.get("repository", "")
                            repo_info["documentation"] = metadata.get("documentation", "")
                            
                    except (json.JSONDecodeError, FileNotFoundError):
                        continue
            
        except Exception as ex:
            _LOGGER.debug("Error extracting repository info for %s: %s", component_path, ex)
        
        return repo_info

    async def scan_custom_integrations(self) -> dict[str, Any]:
        """Scan for custom integrations."""
        custom_components_path = self.config_dir / "custom_components"
        if not custom_components_path.exists():
            return {"total": 0, "used": [], "unused": []}

        installed_integrations = []
        for item in custom_components_path.iterdir():
            if item.is_dir() and not item.name.startswith(".") and item.name != "__pycache__":
                manifest_path = item / "manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path, encoding="utf-8") as f:
                            manifest = json.load(f)
                        
                        # Extract repository URL from documentation or issue tracker
                        repo_url = ""
                        doc_url = manifest.get("documentation", "")
                        issue_url = manifest.get("issue_tracker", "")
                        
                        if doc_url and "github.com" in doc_url:
                            repo_url = doc_url
                        elif issue_url and "github.com" in issue_url:
                            # Convert issues URL to main repo URL
                            repo_url = issue_url.replace("/issues", "")
                        
                        installed_integrations.append({
                            "domain": item.name,
                            "name": manifest.get("name", item.name),
                            "documentation": doc_url,
                            "repository": repo_url,
                            "version": manifest.get("version", "unknown"),
                            "codeowners": manifest.get("codeowners", []),
                            "installed_date": self._get_installation_date(item),
                        })
                    except (json.JSONDecodeError, FileNotFoundError) as ex:
                        _LOGGER.warning("Could not read manifest for %s: %s", item.name, ex)

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
        """Scan for custom themes."""
        themes_path = self.config_dir / "themes"
        if not themes_path.exists():
            return {"total": 0, "used": [], "unused": []}

        installed_themes = []
        
        # Scan for theme files
        for item in themes_path.iterdir():
            if item.is_file() and item.suffix in [".yaml", ".yml"]:
                repo_info = self._extract_hacs_repository_info(item, "theme")
                installed_themes.append({
                    "name": item.stem,
                    "file": str(item.relative_to(self.config_dir)),
                    "full_path": str(item),
                    "repository": repo_info["repository"],
                    "documentation": repo_info["documentation"],
                    "installed_date": self._get_installation_date(item),
                })

        # Also check themes directory for subdirectories containing themes
        for item in themes_path.iterdir():
            if item.is_dir() and not item.name.startswith("."):
                for theme_file in item.iterdir():
                    if theme_file.is_file() and theme_file.suffix in [".yaml", ".yml"]:
                        repo_info = self._extract_hacs_repository_info(theme_file, "theme")
                        installed_themes.append({
                            "name": f"{item.name}/{theme_file.stem}",
                            "file": str(theme_file.relative_to(self.config_dir)),
                            "full_path": str(theme_file),
                            "repository": repo_info["repository"],
                            "documentation": repo_info["documentation"],
                            "installed_date": self._get_installation_date(theme_file),
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
            theme_name = theme["name"]
            is_used = (
                theme_name == current_theme or
                theme_name in configured_themes or
                any(theme_name in str(configured) for configured in configured_themes)
            )
            
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
        """Scan for custom frontend resources."""
        www_path = self.config_dir / "www"
        if not www_path.exists():
            return {"total": 0, "used": [], "unused": []}

        installed_frontend = []
        
        def scan_directory(path: Path, relative_path: str = "") -> None:
            """Recursively scan directory for frontend resources."""
            for item in path.iterdir():
                if item.name.startswith("."):
                    continue
                    
                rel_path = f"{relative_path}/{item.name}" if relative_path else item.name
                
                if item.is_dir():
                    repo_info = self._extract_hacs_repository_info(item, "frontend")
                    installed_frontend.append({
                        "name": rel_path,
                        "path": str(item.relative_to(self.config_dir)),
                        "type": "directory",
                        "full_path": str(item),
                        "repository": repo_info["repository"],
                        "documentation": repo_info["documentation"],
                        "installed_date": self._get_installation_date(item),
                    })
                    scan_directory(item, rel_path)
                elif item.suffix in [".js", ".css", ".html", ".json", ".map"]:
                    repo_info = self._extract_hacs_repository_info(item, "frontend")
                    installed_frontend.append({
                        "name": rel_path,
                        "path": str(item.relative_to(self.config_dir)),
                        "type": "file",
                        "extension": item.suffix,
                        "full_path": str(item),
                        "repository": repo_info["repository"],
                        "documentation": repo_info["documentation"],
                        "installed_date": self._get_installation_date(item),
                    })

        scan_directory(www_path)

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
                
            # Check if resource is referenced in Lovelace or commonly used patterns
            is_used = (
                local_path in lovelace_resources or
                any(local_path in res for res in lovelace_resources) or
                resource["name"] in ["community", "hacsfiles", "hacs-frontend"] or  # Common HACS directories
                resource.get("extension") in [".map"] or  # Source maps are usually auto-generated
                "node_modules" in resource["name"]  # Node modules
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