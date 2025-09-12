"""Constants for the Custom Component Monitor integration."""

DOMAIN = "custom_component_monitor"

# Sensor attributes
ATTR_UNUSED_COMPONENTS = "unused_components"
ATTR_TOTAL_COMPONENTS = "total_components"
ATTR_USED_COMPONENTS = "used_components"

# Component types
COMPONENT_TYPE_INTEGRATION = "integration"
COMPONENT_TYPE_THEME = "theme"
COMPONENT_TYPE_FRONTEND = "frontend"

# Default scan paths
DEFAULT_CUSTOM_COMPONENTS_PATH = "custom_components"
DEFAULT_THEMES_PATH = "themes"
DEFAULT_WWW_PATH = "www"

# Sensor names
SENSOR_UNUSED_INTEGRATIONS = "unused_integrations"
SENSOR_UNUSED_THEMES = "unused_themes" 
SENSOR_UNUSED_FRONTEND = "unused_frontend"

# Update interval (in seconds)
DEFAULT_SCAN_INTERVAL = 3600  # 1 hour