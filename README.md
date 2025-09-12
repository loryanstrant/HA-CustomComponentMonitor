# Home Assistant Custom Component Monitor

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/loryanstrant/HA-CustomComponentMonitor.svg)](https://github.com/loryanstrant/HA-CustomComponentMonitor/releases/)

A Home Assistant custom integration that monitors your installation for unused custom components, themes, and frontend resources.

## Overview

Home Assistant allows the installation of custom components such as dashboards/cards, themes, and integrations, which can be installed directly or via Home Assistant Community Store (HACS). Many of these are left unused over time.

This integration creates sensors that list all installed custom components and identifies if they are used in any way (configured integrations, active themes, referenced frontend resources). It helps you clean up your installation by identifying unused components with links to their respective repositories.

## Features

- 🔍 **Automatic Discovery**: Scans your Home Assistant installation for custom components
- 📊 **Usage Analysis**: Determines which components are actually being used
- 🏷️ **Repository Links**: Provides links to component repositories for easy management
- ⚡ **Real-time Updates**: Updates hourly to reflect configuration changes
- 🎨 **HACS Compatible**: Full integration with Home Assistant Community Store

## Installation

### Via HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Navigate to "Integrations"
3. Click the "+" button to add a new repository
4. Search for "Custom Component Monitor"
5. Install the integration
6. Restart Home Assistant
7. Add the integration via Settings → Integrations

### Manual Installation

1. Download the latest release from the [releases page](https://github.com/loryanstrant/HA-CustomComponentMonitor/releases)
2. Extract the contents
3. Copy the `custom_components/custom_component_monitor` directory to your Home Assistant `custom_components` directory
4. Restart Home Assistant
5. Add the integration via Settings → Integrations

## Usage

After installation, the integration will create three sensors:

### Sensors

- **`sensor.unused_custom_integrations`**: Count and details of unused custom integrations
- **`sensor.unused_custom_themes`**: Count and details of unused custom themes  
- **`sensor.unused_frontend_resources`**: Count and details of unused frontend resources

### Sensor Attributes

Each sensor provides detailed attributes including:
- Total count of installed components
- Count of used components
- List of unused components with metadata (names, versions, repository links)

## Example Automation

```yaml
automation:
  - alias: "Notify about unused components"
    trigger:
      - platform: state
        entity_id: sensor.unused_custom_integrations
    condition:
      - condition: numeric_state
        entity_id: sensor.unused_custom_integrations
        above: 0
    action:
      - service: notify.mobile_app
        data:
          message: "You have {{ states('sensor.unused_custom_integrations') }} unused custom integrations"
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

If you encounter any issues, please report them on the [GitHub Issues page](https://github.com/loryanstrant/HA-CustomComponentMonitor/issues).
