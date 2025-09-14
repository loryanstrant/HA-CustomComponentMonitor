# Home Assistant Custom Component Monitor

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/loryanstrant/HA-CustomComponentMonitor.svg)](https://github.com/loryanstrant/HA-CustomComponentMonitor/releases/)

A Home Assistant custom integration that monitors your HACS installation for unused custom components, themes, and frontend resources.
<p align="center"><img width="256" height="256" alt="icon" src="https://github.com/user-attachments/assets/3ae6431f-b649-47df-bc9c-181e3c4fb63f" /></p>


## Overview

Home Assistant allows the installation of custom components such as dashboards/cards, themes, and integrations via Home Assistant Community Store (HACS). Many of these are left unused over time.

This integration creates sensors that list all HACS-installed custom components and identifies if they are used in any way (configured integrations, active themes, referenced frontend resources). It helps you clean up your installation by identifying unused HACS components with links to their respective repositories.

## Features

- 🔍 **HACS Discovery**: Scans your HACS repositories for installed custom components
- 📊 **Usage Analysis**: Determines which HACS components are actually being used
- 🏷️ **Repository Links**: Provides links to component repositories for easy management (integrations, themes, and frontend resources)
- 📅 **Installation Tracking**: Shows when custom components were first installed
- ⚡ **Real-time Updates**: Updates hourly to reflect configuration changes
- 🎨 **HACS Focused**: Specifically designed to work with HACS-managed components

## Installation

### HACS (Recommended)
(Waiting to add this to HACS default repository as of 12th Sept 2025, for now use the below method...)

1. Open HACS in your Home Assistant instance
2. Go to "Integrations"
3. Click the three dots menu and select "Custom repositories"
4. Add `https://github.com/loryanstrant/HA-CustomComponentMonitor` as repository
5. Set category to "Integration"
6. Click "Add"
7. Find "Custom Component Monitor" in the integration list and install it
8. Restart Home Assistant
9. Go to Configuration > Integrations
10. Click "+ Add Integration" and search for "Custom Component Monitor"
11. Press Submit to complete the installation.

![Custom Compoment Monitor setup](https://github.com/user-attachments/assets/053722b1-7292-46d4-975e-b85308b7f5dd)

Or replace steps 1-6 with this:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=loryanstrant&repository=HA-CustomComponentMonitor&category=integration)

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
- List of unused components with metadata (names, versions, repository links, installation dates)

## Example
You can find the code for the below example in the [example-dashboard.yaml](https://github.com/loryanstrant/HA-CustomComponentMonitor/blob/main/example_dashboard.yaml) file.

<img width="1374" height="944" alt="image" src="https://github.com/user-attachments/assets/944232c0-9fac-4c28-a2a9-6aa101617786" />

## Current Known Issue(s) / Tasks

- [ ] Some integrations show us unused when they are used (e.g. helpers)
- [ ] Some themes are showing as unused when they are used
- [ ] Themes that were not installed via HACS will not show a repo link
- [ ] Some themes may twice: once as a reference and once using their HACS details (due to inability to match)
- [ ] Some frontend components show as unused when they are used (either they provide icons, or there hasn't been a proper match found)

Look, fundamentally it does a decent job ok? It's better than just *not* knowing what isn't used at all, isn't it???


## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Development Approach
<img width="256" height="256" alt="Vibe Coding with GitHub Copilot 256x256" src="https://github.com/user-attachments/assets/bb41d075-6b3e-4f2b-a88e-94b2022b5d4f" />


## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

If you encounter any issues, please report them on the [GitHub Issues page](https://github.com/loryanstrant/HA-CustomComponentMonitor/issues).
