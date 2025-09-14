"""Sensor platform for Custom Component Monitor."""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed, CoordinatorEntity
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN, 
    DEFAULT_SCAN_INTERVAL,
    SENSOR_UNUSED_INTEGRATIONS,
    SENSOR_UNUSED_THEMES, 
    SENSOR_UNUSED_FRONTEND,
    ATTR_TOTAL_COMPONENTS,
    ATTR_USED_COMPONENTS,
    ATTR_UNUSED_COMPONENTS,
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

    async def _load_storage_file(self, filename: str) -> dict[str, Any]:
        """Load a storage file and return its data."""
        try:
            storage_path = self.config_dir / ".storage" / filename
            if not storage_path.exists():
                _LOGGER.debug("Storage file not found: %s", storage_path)
                return {}
                
            # Use run_in_executor to avoid blocking the event loop
            def _read_storage_file():
                with open(storage_path, encoding="utf-8") as f:
                    return json.load(f)
            
            storage_data = await self.hass.async_add_executor_job(_read_storage_file)
                
            return storage_data.get("data", {})
            
        except (json.JSONDecodeError, FileNotFoundError, OSError) as ex:
            _LOGGER.debug("Could not load storage file %s: %s", filename, ex)
            return {}

    async def _load_hacs_repositories(self) -> dict[str, Any]:
        """Load HACS repository data."""
        if self._hacs_repositories is None:
            self._hacs_repositories = await self._load_storage_file("hacs.repositories")
        return self._hacs_repositories

    async def _get_configured_integrations(self) -> set[str]:
        """Get configured integration domains from core.config_entries."""
        config_entries_data = await self._load_storage_file("core.config_entries")
        configured_domains = set()
        
        for entry in config_entries_data.get("entries", []):
            domain = entry.get("domain")
            if domain and domain != "hacs":  # Exclude HACS itself
                configured_domains.add(domain)
        
        _LOGGER.debug("Found configured domains: %s", configured_domains)
        return configured_domains

    def _get_installation_date(self, path: str | Path) -> str | None:
        """Get installation date from file or directory."""
        try:
            path_obj = Path(path) if isinstance(path, str) else path
            if not path_obj.exists():
                return None
            
            # For directories, get the creation time of the directory itself
            # For files, get the creation time of the file
            stat_info = path_obj.stat()
            
            # Use birth time if available (macOS/Windows), otherwise fall back to ctime
            if hasattr(stat_info, 'st_birthtime'):
                timestamp = stat_info.st_birthtime
            else:
                # On Linux, use the earliest of ctime and mtime
                timestamp = min(stat_info.st_ctime, stat_info.st_mtime)
            
            # Convert to ISO format string
            return datetime.fromtimestamp(timestamp).isoformat()
            
        except Exception as ex:
            _LOGGER.debug("Could not get installation date for %s: %s", path, ex)
            return None

    async def _get_theme_names_from_file(self, theme_path: str | Path) -> list[str]:
        """Extract theme names from a theme file."""
        try:
            path_obj = Path(theme_path) if isinstance(theme_path, str) else theme_path
            
            # Handle both files and directories
            theme_files = []
            if path_obj.is_file():
                theme_files = [path_obj]
            elif path_obj.is_dir():
                # Look for .yaml files in the directory
                theme_files = list(path_obj.glob("*.yaml")) + list(path_obj.glob("*.yml"))
            
            if not theme_files:
                _LOGGER.debug("No theme files found in %s", theme_path)
                return []
            
            all_theme_names = []
            
            for theme_file in theme_files:
                if not theme_file.exists():
                    continue
                    
                def _read_theme_file():
                    with open(theme_file, encoding="utf-8") as f:
                        content = f.read()
                    return content
                
                content = await self.hass.async_add_executor_job(_read_theme_file)
                
                # Extract theme names from YAML content
                # Theme names are top-level keys in the YAML file
                theme_names = []
                lines = content.split('\n')
                
                for line in lines:
                    stripped_line = line.strip()
                    # Look for top-level YAML keys (theme names)
                    # Skip empty lines, comments, and indented lines (including YAML doc separators)
                    if (stripped_line and 
                        not stripped_line.startswith('#') and 
                        not stripped_line.startswith('---') and  # YAML document separator
                        not line.startswith(' ') and 
                        not line.startswith('\t') and
                        ':' in stripped_line):
                        
                        theme_name = stripped_line.split(':')[0].strip()
                        # Remove any quotes from theme name
                        theme_name = theme_name.strip('"\'')
                        
                        # Additional validation: theme names shouldn't contain certain characters
                        # and should be reasonable names (not CSS variables)
                        if (theme_name and 
                            not theme_name.startswith('#') and
                            not theme_name.startswith('-') and  # CSS variables start with --
                            not theme_name.startswith('paper-') and  # Skip paper variables
                            not theme_name.startswith('ha-') and  # Skip HA variables
                            not theme_name.endswith('-color') and  # Skip color variables
                            len(theme_name) < 50):  # Reasonable length check
                            theme_names.append(theme_name)
                            _LOGGER.debug("Found valid theme name: %s", theme_name)
                
                _LOGGER.debug("Found theme names in %s: %s", theme_file, theme_names)
                all_theme_names.extend(theme_names)
            
            # Remove duplicates while preserving order
            unique_theme_names = []
            seen = set()
            for name in all_theme_names:
                if name not in seen:
                    seen.add(name)
                    unique_theme_names.append(name)
            
            _LOGGER.debug("Final theme names from %s: %s", theme_path, unique_theme_names)
            return unique_theme_names
            
        except Exception as ex:
            _LOGGER.debug("Could not read theme file/directory %s: %s", theme_path, ex)
            return []

    async def _get_frontend_resource_types(self, resource_path: str | Path, resource_name: str) -> list[str]:
        """Extract custom card types from a frontend resource."""
        try:
            path_obj = Path(resource_path) if isinstance(resource_path, str) else resource_path
            if not path_obj.exists():
                return []
            
            custom_types = []
            
            # If it's a directory, look for JS files
            if path_obj.is_dir():
                js_files = list(path_obj.glob("*.js"))
                for js_file in js_files:
                    types = await self._extract_types_from_js_file(js_file, resource_name)
                    custom_types.extend(types)
            elif path_obj.suffix == '.js':
                types = await self._extract_types_from_js_file(path_obj, resource_name)
                custom_types.extend(types)
            
            _LOGGER.debug("Found custom types in %s: %s", resource_path, custom_types)
            return list(set(custom_types))  # Remove duplicates
            
        except Exception as ex:
            _LOGGER.debug("Could not analyze frontend resource %s: %s", resource_path, ex)
            return []

    async def _extract_types_from_js_file(self, js_file: Path, resource_name: str) -> list[str]:
        """Extract custom element types from a JavaScript file."""
        try:
            def _read_js_file():
                with open(js_file, encoding="utf-8", errors="ignore") as f:
                    return f.read()
            
            content = await self.hass.async_add_executor_job(_read_js_file)
            
            custom_types = []
            
            # PRIMARY: Look for custom cards registry first (most reliable for Lovelace cards)
            registry_patterns = [
                r'window\.customCards\s*=\s*window\.customCards\s*\|\|\s*\[\];\s*window\.customCards\.push\s*\(\s*\{\s*type:\s*[\'"]([^\'\"]+)[\'"]',
                r'window\.customCards\.push\s*\(\s*\{\s*type:\s*[\'"]([^\'\"]+)[\'"]',
                r'customCards\.push\s*\(\s*\{\s*type:\s*[\'"]([^\'\"]+)[\'"]',
            ]
            
            for pattern in registry_patterns:
                matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL)
                for match in matches:
                    if match and match.strip():
                        card_type = match.strip()
                        custom_types.append(card_type)
                        if not card_type.startswith('custom:'):
                            custom_types.append(f"custom:{card_type}")
                        _LOGGER.debug("Found customCards registration: %s in %s", card_type, js_file.name)
            
            # SECONDARY: customElements.define() patterns - only if no card registry found
            if not custom_types:
                define_patterns = [
                    # Standard patterns for customElements.define
                    r'customElements\.define\s*\(\s*[\'"]([^\'\"]+)[\'"]',
                    r'window\.customElements\.define\s*\(\s*[\'"]([^\'\"]+)[\'"]',
                    # Patterns with variable spacing and quotes
                    r'customElements\s*\.\s*define\s*\(\s*[\'"]([^\'\"]+)[\'"]',
                    r'window\s*\.\s*customElements\s*\.\s*define\s*\(\s*[\'"]([^\'\"]+)[\'"]',
                ]
                
                # Extract from customElements.define() calls
                for pattern in define_patterns:
                    matches = re.findall(pattern, content, re.MULTILINE | re.DOTALL | re.IGNORECASE)
                    for match in matches:
                        if match and match.strip():
                            element_name = match.strip()
                            # Add the element name as found
                            custom_types.append(element_name)
                            # Also add with custom: prefix if not already present
                            if not element_name.startswith('custom:'):
                                custom_types.append(f"custom:{element_name}")
                            _LOGGER.debug("Found customElements.define: %s in %s", element_name, js_file.name)
            
            # TERTIARY: Look for class definitions that might indicate custom elements - only if nothing found yet
            if not custom_types:
                class_patterns = [
                    r'class\s+(\w*[Cc]ard)\s+extends\s+',
                    r'class\s+(\w*[Ee]lement)\s+extends\s+',
                    r'class\s+(\w+)\s+extends\s+LitElement',
                    r'class\s+(\w+)\s+extends\s+HTMLElement',
                ]
                
                for pattern in class_patterns:
                    matches = re.findall(pattern, content)
                    for match in matches:
                        if match and len(match) > 2:  # Skip very short matches
                            # Convert CamelCase to kebab-case
                            kebab_name = re.sub(r'(?<!^)(?=[A-Z])', '-', match).lower()
                            custom_types.append(kebab_name)
                            custom_types.append(f"custom:{kebab_name}")
                            _LOGGER.debug("Found class definition: %s -> %s in %s", match, kebab_name, js_file.name)
            
            # FALLBACK: Smart fallback based on resource name and common patterns - only if nothing found
            if not custom_types and resource_name:
                resource_lower = resource_name.lower().replace('_', '-').replace(' ', '-')
                
                # Create smart patterns based on common naming conventions
                fallback_patterns = []
                
                # Remove common suffixes/prefixes to get base name
                base_name = resource_lower
                for suffix in ['-card', '_card', '-element', '_element']:
                    if base_name.endswith(suffix):
                        base_name = base_name[:-len(suffix)]
                        break
                
                # Common patterns based on resource name
                fallback_patterns = [
                    base_name,
                    f"{base_name}-card",
                    resource_lower,
                    f"{resource_lower}-card",
                ]
                
                # Also try patterns with custom: prefix
                for pattern in fallback_patterns.copy():
                    if not pattern.startswith('custom:'):
                        fallback_patterns.append(f"custom:{pattern}")
                
                # Add to list only if we didn't find any explicit definitions
                if not any(t for t in custom_types if not t.startswith('custom:')):
                    custom_types.extend(fallback_patterns)
                    _LOGGER.debug("Added fallback patterns for %s: %s", resource_name, fallback_patterns)
            
            # Remove duplicates while preserving order
            seen = set()
            unique_types = []
            for custom_type in custom_types:
                if custom_type not in seen and custom_type.strip():
                    seen.add(custom_type)
                    unique_types.append(custom_type)
            
            _LOGGER.debug("Final extracted types from %s: %s", js_file.name, unique_types)
            return unique_types
            
        except Exception as ex:
            _LOGGER.debug("Could not read JS file %s: %s", js_file, ex)
            return []

    async def _check_theme_usage(self, theme_name: str) -> bool:
        """Check if a specific theme name is used anywhere in the configuration."""
        _LOGGER.debug("Checking theme usage for: %s", theme_name)
        
        # Check core.config for theme configuration (case-insensitive)
        core_config = await self._load_storage_file("core.config")
        frontend_config = core_config.get("frontend", {})
        
        # Check if theme is set as default theme
        default_theme = frontend_config.get("theme")
        if default_theme and default_theme.lower() == theme_name.lower():
            _LOGGER.debug("Theme %s found as default theme", theme_name)
            return True
            
        # Check user profiles for theme usage (case-insensitive)
        auth_data = await self._load_storage_file("auth")
        for user in auth_data.get("users", []):
            user_data = user.get("user_data", {})
            user_theme = user_data.get("theme")
            if user_theme and user_theme.lower() == theme_name.lower():
                _LOGGER.debug("Theme %s found in user profile", theme_name)
                return True
        
        # Check all lovelace configurations for theme usage
        storage_dir = self.config_dir / ".storage"
        if storage_dir.exists():
            # Get all files that start with "lovelace" using executor
            def _get_lovelace_files():
                lovelace_files = []
                for storage_file in storage_dir.iterdir():
                    if storage_file.is_file() and storage_file.name.startswith("lovelace"):
                        lovelace_files.append(storage_file.name)
                return lovelace_files
            
            lovelace_files = await self.hass.async_add_executor_job(_get_lovelace_files)
            _LOGGER.debug("Found lovelace files: %s", lovelace_files)
            
            for filename in lovelace_files:
                _LOGGER.debug("Checking lovelace config: %s for theme %s", filename, theme_name)
                
                try:
                    # Read using the _load_storage_file method but get full structure
                    storage_path = storage_dir / filename
                    def _read_full_storage_file():
                        with open(storage_path, encoding="utf-8") as f:
                            return json.load(f)
                    
                    full_data = await self.hass.async_add_executor_job(_read_full_storage_file)
                    
                    # Check in data.config section
                    data_section = full_data.get("data", {})
                    config_data = data_section.get("config", {})
                    
                    # Check if theme is used in lovelace configuration
                    if self._check_theme_in_lovelace(config_data, theme_name):
                        _LOGGER.debug("Theme %s found in %s", theme_name, filename)
                        return True
                    
                    # Also check top-level data for theme references
                    if self._check_theme_in_lovelace(data_section, theme_name):
                        _LOGGER.debug("Theme %s found in data section of %s", theme_name, filename)
                        return True
                        
                except Exception as ex:
                    _LOGGER.debug("Error reading lovelace file %s: %s", filename, ex)
        
        _LOGGER.debug("Theme %s not found in any configuration", theme_name)
        return False

    def _check_theme_in_lovelace(self, config: dict, theme_name: str) -> bool:
        """Recursively check if theme is used in lovelace configuration."""
        if not isinstance(config, dict):
            return False
        
        # Check for theme key at current level (case-insensitive)
        current_theme = config.get("theme")
        if current_theme:
            if isinstance(current_theme, str):
                if current_theme.lower() == theme_name.lower():
                    return True
            elif isinstance(current_theme, dict):
                # Handle theme objects like {'selected_theme': 'themename'}
                for key, value in current_theme.items():
                    if isinstance(value, str) and value.lower() == theme_name.lower():
                        return True
        
        # Check for theme in views
        views = config.get("views", [])
        if isinstance(views, list):
            for view in views:
                if isinstance(view, dict):
                    view_theme = view.get("theme")
                    if view_theme and isinstance(view_theme, str):
                        if view_theme.lower() == theme_name.lower():
                            return True
        
        # Recursively check all nested structures
        for key, value in config.items():
            if key == "theme" and isinstance(value, str):
                if value.lower() == theme_name.lower():
                    return True
            elif isinstance(value, dict):
                if self._check_theme_in_lovelace(value, theme_name):
                    return True
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and self._check_theme_in_lovelace(item, theme_name):
                        return True
                        
        return False

    async def _is_frontend_resource_registered(self, resource_name: str, resource_path: str) -> bool:
        """Check if a frontend resource is registered in lovelace_resources."""
        _LOGGER.debug("Checking if frontend resource %s is registered", resource_name)
        
        # Load lovelace_resources storage
        lovelace_resources = await self._load_storage_file("lovelace_resources")
        registered_resources = lovelace_resources.get("items", [])
        
        if not registered_resources:
            _LOGGER.debug("No registered lovelace resources found")
            return False
        
        # Create possible URL patterns for this resource
        # HACS uses /hacsfiles/ as a virtual path that maps to www/community/
        possible_patterns = []
        
        if resource_name:
            # Normalize the resource name to create multiple possible URL patterns
            normalized_names = [
                resource_name,  # Original name
                resource_name.lower(),  # Lowercase
                resource_name.lower().replace(' ', '-'),  # Spaces to dashes
                resource_name.lower().replace(' ', '_'),  # Spaces to underscores
                resource_name.replace(' ', '-'),  # Spaces to dashes (preserve case)
                resource_name.replace(' ', '_'),  # Spaces to underscores (preserve case)
                resource_name.replace('_', '-'),  # Underscores to dashes
                resource_name.replace('-', '_'),  # Dashes to underscores
            ]
            
            # Remove duplicates while preserving order
            seen = set()
            unique_names = []
            for name in normalized_names:
                if name not in seen:
                    seen.add(name)
                    unique_names.append(name)
            
            # HACS pattern: /hacsfiles/{resource_name}/
            for name in unique_names:
                possible_patterns.append(f"/hacsfiles/{name}/")
        
        # Check against the actual www/community path structure
        if resource_path and resource_path.startswith("www/community/"):
            community_path = resource_path[14:]  # Remove "www/community/" prefix (14 chars)
            resource_dir = community_path.split('/')[0] if '/' in community_path else community_path
            if resource_dir:  # Only add if not empty
                possible_patterns.append(f"/hacsfiles/{resource_dir}/")
            
            # Also add the directory name to the normalized names list for broader matching
            if resource_dir and resource_dir not in possible_patterns:
                possible_patterns.append(f"/hacsfiles/{resource_dir}/")
        
        # Check if any registered resource matches our patterns
        for resource in registered_resources:
            resource_url = resource.get("url", "")
            if resource_url:
                # Strip HACS tag parameters for matching
                clean_url = resource_url.split('?')[0] if '?' in resource_url else resource_url
                
                for pattern in possible_patterns:
                    if clean_url.startswith(pattern):
                        _LOGGER.debug("Found matching registered resource: %s (clean: %s) matches pattern %s", resource_url, clean_url, pattern)
                        return True
        
        _LOGGER.debug("Resource %s not found in registered lovelace resources", resource_name)
        _LOGGER.debug("Checked patterns: %s", possible_patterns)
        _LOGGER.debug("Available resources: %s", [r.get("url", "") for r in registered_resources])
        return False

    async def _get_registered_resource_info(self, resource_name: str) -> dict[str, str]:
        """Get information about a registered frontend resource."""
        lovelace_resources = await self._load_storage_file("lovelace_resources")
        registered_resources = lovelace_resources.get("items", [])
        
        # Find the matching resource
        for resource in registered_resources:
            resource_url = resource.get("url", "")
            if resource_name.lower() in resource_url.lower():
                return {
                    "url": resource_url,
                    "type": resource.get("type", "module"),
                    "id": resource.get("id", "")
                }
        
        return {}
    async def _check_frontend_resource_usage(self, custom_type: str, resource_name: str) -> bool:
        """Check if a custom type is used in any Lovelace configuration."""
        _LOGGER.debug("Checking frontend resource: %s (type: %s)", resource_name, custom_type)
        
        # Check all lovelace configurations
        storage_dir = self.config_dir / ".storage"
        if storage_dir.exists():
            # Get all files that start with "lovelace" using executor
            def _get_lovelace_files():
                lovelace_files = []
                for storage_file in storage_dir.iterdir():
                    if storage_file.is_file() and storage_file.name.startswith("lovelace"):
                        # Skip lovelace_resources and lovelace_dashboards as they don't contain card usage
                        if storage_file.name not in ["lovelace_resources", "lovelace_dashboards"]:
                            lovelace_files.append(storage_file.name)
                return lovelace_files
            
            lovelace_files = await self.hass.async_add_executor_job(_get_lovelace_files)
            
            for filename in lovelace_files:
                _LOGGER.debug("Checking lovelace config for custom type: %s in %s", custom_type, filename)
                
                try:
                    # Read using the full file path
                    storage_path = storage_dir / filename
                    def _read_full_storage_file():
                        with open(storage_path, encoding="utf-8") as f:
                            return json.load(f)
                    
                    full_data = await self.hass.async_add_executor_job(_read_full_storage_file)
                    
                    # Check in data.config section
                    data_section = full_data.get("data", {})
                    config_data = data_section.get("config", {})
                    
                    # Check if custom type is used in lovelace configuration
                    if self._check_custom_type_in_lovelace(config_data, custom_type):
                        _LOGGER.debug("Custom type %s found in %s", custom_type, filename)
                        return True
                    
                    # Also check top-level data for custom type references
                    if self._check_custom_type_in_lovelace(data_section, custom_type):
                        _LOGGER.debug("Custom type %s found in data section of %s", custom_type, filename)
                        return True
                        
                except Exception as ex:
                    _LOGGER.debug("Error reading lovelace file %s: %s", filename, ex)
        
        return False

    def _check_custom_type_in_lovelace(self, config: dict, custom_type: str) -> bool:
        """Recursively check if custom type is used in lovelace configuration."""
        if not isinstance(config, dict):
            return False
            
        # Check for type key at current level
        current_type = config.get("type")
        if current_type:
            # Direct match
            if current_type == custom_type:
                return True
            
            # Check without the custom: prefix
            if custom_type.startswith("custom:"):
                short_type = custom_type[7:]  # Remove "custom:" prefix
                if current_type == short_type:
                    return True
                # Also try with "custom:" prefix if the current type doesn't have it
                if current_type == f"custom:{short_type}":
                    return True
            
            # Check with custom: prefix added
            if not current_type.startswith("custom:"):
                prefixed_type = f"custom:{current_type}"
                if prefixed_type == custom_type:
                    return True
            
            # Flexible matching for common variations
            # Convert both to lowercase and remove separators for comparison
            normalized_custom = custom_type.lower().replace("custom:", "").replace("-", "").replace("_", "")
            normalized_current = current_type.lower().replace("custom:", "").replace("-", "").replace("_", "")
            
            if normalized_custom == normalized_current:
                return True
        
        # Recursively check all nested structures
        for key, value in config.items():
            if isinstance(value, dict):
                if self._check_custom_type_in_lovelace(value, custom_type):
                    return True
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and self._check_custom_type_in_lovelace(item, custom_type):
                        return True
                        
        return False

    async def scan_custom_integrations(self) -> dict[str, Any]:
        """Scan for HACS-installed custom integrations."""
        # Load HACS repositories and filter for installed integrations
        hacs_repositories = await self._load_hacs_repositories()
        
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
                
                # Get installation date by looking at the custom_components directory
                # This ensures we get the actual installation date, not the last update date
                install_date = self._get_installation_date(
                    self.config_dir / "custom_components" / domain
                )
                
                # If that fails, try the HACS local path as a fallback
                if install_date is None:
                    local_path = repo_data.get("local_path", "")
                    if local_path:
                        try:
                            if not Path(local_path).is_absolute():
                                # If it's relative, make it relative to config directory
                                local_path = str(self.config_dir / local_path)
                            install_date = self._get_installation_date(local_path)
                        except Exception as ex:
                            _LOGGER.debug("Could not get installation date from HACS local_path %s: %s", local_path, ex)
                
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
        configured_domains = await self._get_configured_integrations()
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
        hacs_repositories = await self._load_hacs_repositories()
        
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
                
                # Get installation date - try multiple approaches
                install_date = None
                theme_file_path = ""
                
                # First, try the HACS local path which should be most accurate
                local_path = repo_data.get("local_path", "")
                if local_path:
                    try:
                        # Convert relative path to absolute
                        if not Path(local_path).is_absolute():
                            abs_local_path = self.config_dir / local_path
                        else:
                            abs_local_path = Path(local_path)
                            
                        if abs_local_path.exists():
                            install_date = self._get_installation_date(abs_local_path)
                            theme_file_path = str(abs_local_path.relative_to(self.config_dir))
                    except Exception as ex:
                        _LOGGER.debug("Could not get installation date from HACS local_path %s: %s", local_path, ex)
                
                # If local_path is empty or doesn't exist, try to find theme by repository name
                if install_date is None:
                    theme_dir = self.config_dir / "themes"
                    if theme_dir.exists():
                        # Extract theme name from repository name (e.g., "loryanstrant/blackout" -> "blackout")
                        repo_theme_name = full_name.split("/")[-1] if "/" in full_name else ""
                        
                        # Try multiple patterns for theme directory/file names
                        theme_patterns = [
                            repo_theme_name,  # Repository name
                            display_name,  # Display name
                            display_name.lower() if display_name else "",  # Lowercase version
                        ]
                        
                        # Remove empty patterns and duplicates
                        theme_patterns = list(set([p for p in theme_patterns if p]))
                        
                        for pattern in theme_patterns:
                            # Try as directory first
                            theme_directory = theme_dir / pattern
                            if theme_directory.exists() and theme_directory.is_dir():
                                install_date = self._get_installation_date(theme_directory)
                                theme_file_path = str(theme_directory.relative_to(self.config_dir))
                                break
                            
                            # Try with .yaml extension as a file
                            theme_file = theme_dir / f"{pattern}.yaml"
                            if theme_file.exists():
                                install_date = self._get_installation_date(theme_file)
                                theme_file_path = str(theme_file.relative_to(self.config_dir))
                                break
                
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

        # Also check for non-HACS themes in the themes directory
        theme_dir = self.config_dir / "themes"
        if theme_dir.exists():
            # Get comprehensive lists for deduplication
            hacs_theme_names = set()
            hacs_theme_paths = set()
            hacs_directories = set()
            hacs_repo_names = set()
            
            for theme in installed_themes:
                # Add the display name and variations
                theme_name = theme["name"]
                if theme_name:
                    hacs_theme_names.add(theme_name.lower())
                
                # Add path variations
                if theme["file"]:
                    hacs_theme_paths.add(theme["file"])
                    # Extract directory name from path
                    path_parts = Path(theme["file"]).parts
                    if len(path_parts) > 1 and path_parts[0] == "themes":
                        hacs_directories.add(path_parts[1].lower())
                
                # Add repository name variations
                if theme["hacs_repository"]:
                    full_repo_name = theme["hacs_repository"]
                    repo_name = full_repo_name.split("/")[-1].lower()
                    hacs_repo_names.add(repo_name)
                    hacs_directories.add(repo_name)
                    
                    # Also add the full repository name parts
                    if "/" in full_repo_name:
                        repo_parts = full_repo_name.lower().split("/")
                        hacs_repo_names.update(repo_parts)
            
            def _scan_themes_directory():
                found_themes = []
                for item in theme_dir.iterdir():
                    if item.is_dir():
                        # Check if this directory contains theme files
                        yaml_files = list(item.glob("*.yaml")) + list(item.glob("*.yml"))
                        if yaml_files:
                            rel_path = str(item.relative_to(self.config_dir))
                            dir_name_lower = item.name.lower()
                            
                            # Check if this directory is already covered by HACS themes
                            if (rel_path not in hacs_theme_paths and 
                                dir_name_lower not in hacs_directories and
                                dir_name_lower not in hacs_theme_names and
                                dir_name_lower not in hacs_repo_names):
                                found_themes.append({
                                    "name": item.name,
                                    "path": rel_path,
                                    "type": "directory",
                                    "install_date": self._get_installation_date(item)
                                })
                    elif item.suffix in ['.yaml', '.yml']:
                        # Direct theme file
                        theme_name = item.stem
                        rel_path = str(item.relative_to(self.config_dir))
                        theme_name_lower = theme_name.lower()
                        
                        # Check if this file is already covered by HACS themes
                        if (rel_path not in hacs_theme_paths and 
                            theme_name_lower not in hacs_theme_names and
                            theme_name_lower not in hacs_repo_names):
                            found_themes.append({
                                "name": theme_name,
                                "path": rel_path,
                                "type": "file",
                                "install_date": self._get_installation_date(item)
                            })
                return found_themes
            
            non_hacs_themes = await self.hass.async_add_executor_job(_scan_themes_directory)
            
            # Add non-HACS themes to the list
            for theme_info in non_hacs_themes:
                installed_themes.append({
                    "name": theme_info["name"],
                    "file": theme_info["path"],
                    "full_path": theme_info["path"],
                    "repository": "",
                    "documentation": "",
                    "installed_date": theme_info["install_date"],
                    "hacs_repository": "",
                    "hacs_status": "manual",
                    "version": "unknown",
                })

        # Check theme usage
        used_themes = []
        unused_themes = []
        
        for theme in installed_themes:
            is_used = await self._is_theme_used(theme)
            
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
        hacs_repositories = await self._load_hacs_repositories()
        
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
                
                # Get installation date - try multiple approaches
                install_date = None
                resource_path = ""
                
                # First, try the HACS local path which should be most accurate
                local_path = repo_data.get("local_path", "")
                if local_path:
                    try:
                        # Convert relative path to absolute
                        if not Path(local_path).is_absolute():
                            abs_local_path = self.config_dir / local_path
                        else:
                            abs_local_path = Path(local_path)
                            
                        if abs_local_path.exists():
                            install_date = self._get_installation_date(abs_local_path)
                            try:
                                resource_path = str(abs_local_path.relative_to(self.config_dir))
                            except ValueError:
                                resource_path = local_path
                    except Exception as ex:
                        _LOGGER.debug("Could not get installation date from HACS local_path %s: %s", local_path, ex)
                
                # If local_path is empty or doesn't exist, try to find resource by repository name
                if install_date is None:
                    community_dir = self.config_dir / "www" / "community"
                    if community_dir.exists():
                        # Extract resource name from repository name (e.g., "custom-cards/button-card" -> "button-card")
                        repo_resource_name = full_name.split("/")[-1] if "/" in full_name else ""
                        
                        # Try multiple patterns for resource directory names
                        resource_patterns = [
                            repo_resource_name,  # Repository name
                            display_name,  # Display name
                            display_name.lower() if display_name else "",  # Lowercase version
                        ]
                        
                        # Remove empty patterns and duplicates
                        resource_patterns = list(set([p for p in resource_patterns if p]))
                        
                        for pattern in resource_patterns:
                            # Try as directory
                            resource_directory = community_dir / pattern
                            if resource_directory.exists() and resource_directory.is_dir():
                                install_date = self._get_installation_date(resource_directory)
                                resource_path = str(resource_directory.relative_to(self.config_dir))
                                break
                
                # Final fallback: try www/ directory with display_name
                if install_date is None and display_name:
                    fallback_path = self.config_dir / "www" / display_name
                    if fallback_path.exists():
                        install_date = self._get_installation_date(fallback_path)
                        if not resource_path:
                            resource_path = str(fallback_path.relative_to(self.config_dir))
                
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
        
        # Check each frontend resource
        for resource in installed_frontend:
            # For HACS resources, we need to check in www/community/
            # The resource_path from HACS might not be accurate for the actual file location
            is_used = await self._is_frontend_resource_used(resource, "")
            
            if is_used:
                used_frontend.append(resource)
            else:
                unused_frontend.append(resource)

        return {
            "total": len(installed_frontend),
            "used": used_frontend,
            "unused": unused_frontend,
        }

    async def _is_theme_used(self, theme: dict[str, Any]) -> bool:
        """Check if a theme is used in Home Assistant configuration."""
        theme_name = theme.get("name", "")
        if not theme_name:
            return False
        
        # Get actual theme names from the theme file
        theme_file_path = theme.get("file", "")
        full_path = theme.get("full_path", "")
        
        actual_theme_names = []
        
        # Try to get theme names from the actual theme file
        if theme_file_path:
            file_path = self.config_dir / theme_file_path
            actual_theme_names = await self._get_theme_names_from_file(file_path)
        elif full_path:
            if not Path(full_path).is_absolute():
                file_path = self.config_dir / full_path
            else:
                file_path = Path(full_path)
            actual_theme_names = await self._get_theme_names_from_file(file_path)
        
        # If we couldn't extract theme names, fall back to the theme name
        if not actual_theme_names:
            actual_theme_names = [theme_name]
        
        # Check each actual theme name
        for actual_theme_name in actual_theme_names:
            if await self._check_theme_usage(actual_theme_name):
                return True
                
        return False

    async def _is_frontend_resource_used(self, resource: dict[str, Any], local_path: str) -> bool:
        """Check if frontend resource is used in Lovelace configuration."""
        resource_name = resource.get("name", "")
        resource_path = resource.get("path", "")
        full_path = resource.get("full_path", "")
        
        if not resource_name:
            return False
        
        # First check if the resource is even registered in lovelace_resources
        # If it's not registered, it can't be used
        is_registered = await self._is_frontend_resource_registered(resource_name, resource_path)
        if not is_registered:
            _LOGGER.debug("Resource %s is not registered in lovelace_resources", resource_name)
            return False
        
        # Get the actual custom element types from the resource files
        # HACS stores files in www/community/ but serves them via /hacsfiles/
        actual_resource_path = None
        
        # Try to find the actual files in www/community/
        if resource_path and resource_path.startswith("www/community/"):
            actual_resource_path = self.config_dir / resource_path
        elif resource_name:
            # For HACS resources, try the standard www/community/{resource_name} pattern
            community_path = self.config_dir / "www" / "community" / resource_name
            if community_path.exists():
                actual_resource_path = community_path
            else:
                # Try with some common name variations
                name_variations = [
                    resource_name.lower(),
                    resource_name.replace('_', '-'),
                    resource_name.replace('-', '_'),
                ]
                for variation in name_variations:
                    community_path = self.config_dir / "www" / "community" / variation
                    if community_path.exists():
                        actual_resource_path = community_path
                        break
        
        custom_types = []
        if actual_resource_path and actual_resource_path.exists():
            custom_types = await self._get_frontend_resource_types(actual_resource_path, resource_name)
        
        # If we couldn't extract types, create fallback patterns based on common naming conventions
        if not custom_types:
            _LOGGER.debug("Could not extract custom types from %s, using fallback patterns", resource_name)
            # Common naming patterns for custom cards
            fallback_patterns = [
                resource_name.lower(),
                resource_name.lower().replace('_', '-'),
                resource_name.lower().replace('-', '_'),
            ]
            
            # Add common suffixes if not present
            for pattern in fallback_patterns.copy():
                if not pattern.endswith('-card'):
                    fallback_patterns.append(f"{pattern}-card")
                if not pattern.endswith('_card'):
                    fallback_patterns.append(f"{pattern}_card")
            
            # Add custom: prefix to all patterns
            custom_types = [f"custom:{pattern}" for pattern in fallback_patterns]
            custom_types.extend(fallback_patterns)  # Also check without prefix
        
        _LOGGER.debug("Checking usage for custom types: %s", custom_types)
        
        # Check if any of the custom types are used in Lovelace
        for custom_type in custom_types:
            if await self._check_frontend_resource_usage(custom_type, resource_name):
                _LOGGER.debug("Found usage of custom type %s for resource %s", custom_type, resource_name)
                return True
        
        _LOGGER.debug("No usage found for resource %s", resource_name)
        return False

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
            _LOGGER.debug("Found %d frontend resources (%d unused, %d used)", 
                         frontend["total"], len(frontend["unused"]), len(frontend["used"]))
            
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
    try:
        coordinator = CustomComponentMonitorCoordinator(hass)
        await coordinator.async_config_entry_first_refresh()
    except Exception as ex:
        _LOGGER.error("Failed to set up Custom Component Monitor: %s", ex)
        raise ConfigEntryNotReady(f"Failed to initialize coordinator: {ex}") from ex

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

    def _filter_component_for_attributes(self, component: dict[str, Any]) -> dict[str, Any]:
        """Filter component data to include only essential fields for attributes.
        
        This reduces the size of state attributes to prevent exceeding the 16384 byte limit.
        Removes: file, path, full_path, documentation, hacs_repository, type, codeowners, hacs_status
        Keeps: name, installed_date, version, repository
        """
        filtered = {}
        
        # Keep essential identification and metadata
        if "name" in component:
            filtered["name"] = component["name"]
        if "domain" in component:  # For integrations
            filtered["domain"] = component["domain"]
        if "installed_date" in component:
            filtered["installed_date"] = component["installed_date"]
        if "version" in component:
            filtered["version"] = component["version"]
        if "repository" in component:
            filtered["repository"] = component["repository"]
            
        return filtered

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

        # Filter unused components to include only essential fields to reduce attribute size
        unused_components = data.get("unused", [])
        # Ensure unused_components is always a list, never None
        if unused_components is None:
            unused_components = []
            
        filtered_unused = [self._filter_component_for_attributes(component) 
                          for component in unused_components]

        attributes = {
            ATTR_TOTAL_COMPONENTS: data.get("total", 0),
            ATTR_USED_COMPONENTS: len(data.get("used", [])),
            ATTR_UNUSED_COMPONENTS: filtered_unused,
        }
        
        # Add last scan time
        if "last_scan" in self.coordinator.data:
            attributes["last_scan"] = self.coordinator.data["last_scan"]
            
        return attributes