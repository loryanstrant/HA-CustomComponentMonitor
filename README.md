# Home Assistant Custom Component Monitor

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub release](https://img.shields.io/github/release/loryanstrant/HA-CustomComponentMonitor.svg)](https://github.com/loryanstrant/HA-CustomComponentMonitor/releases/)

A Home Assistant custom integration that monitors your HACS installation for unused custom components, themes, and frontend resources — with a built-in Lovelace dashboard card.

<p align="center"><img width="256" height="256" alt="icon" src="https://github.com/user-attachments/assets/3ae6431f-b649-47df-bc9c-181e3c4fb63f" /></p>

## Overview

Home Assistant allows the installation of custom components such as dashboards/cards, themes, and integrations via the Home Assistant Community Store (HACS). Many of these are left unused over time.

This integration creates sensors that list all HACS-installed custom components and identifies whether they are actually being used (configured integrations, active themes, referenced frontend resources). It includes a custom Lovelace card to visualise everything at a glance.

## Screenshot

<!-- Replace the image below with a screenshot from your own HA instance -->
> **TODO**: Add screenshot of the Custom Component Monitor card from your HA dashboard.

## Features

- 🔍 **HACS Discovery**: Scans your HACS repositories for all installed custom components
- 📊 **Usage Analysis**: Determines which custom components are actually being used
  - **Integrations**: Checks `core.config_entries` for configured HACS domains
  - **Themes**: Scans Lovelace dashboard configs and HA theme settings for active themes
  - **Frontend Cards**: Matches `custom:` card types in dashboards against installed HACS plugins
- 🃏 **Dashboard Card**: Built-in Lovelace card with collapsible sections, sort options, and section filtering
- 🏷️ **Repository Links**: Direct links to each component's repository
- 📅 **Installation Tracking**: Shows how long each component has been installed
- ⚡ **Real-time Updates**: Hourly scans to reflect configuration changes

## Installation

### HACS (Recommended)

1. Open HACS in your Home Assistant instance
2. Go to "Integrations"
3. Click the three dots menu and select "Custom repositories"
4. Add `https://github.com/loryanstrant/HA-CustomComponentMonitor` as repository
5. Set category to "Integration"
6. Click "Add"
7. Find "Custom Component Monitor" in the integration list and install it
8. Restart Home Assistant
9. Go to Configuration → Integrations
10. Click "+ Add Integration" and search for "Custom Component Monitor"
11. Press Submit to complete the installation

Or replace steps 1–6 with this:

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=loryanstrant&repository=HA-CustomComponentMonitor&category=integration)

### Manual Installation

1. Download the latest release from the [releases page](https://github.com/loryanstrant/HA-CustomComponentMonitor/releases)
2. Extract the contents
3. Copy the `custom_components/custom_component_monitor` directory to your Home Assistant `custom_components` directory
4. Restart Home Assistant
5. Add the integration via Settings → Integrations

## Sensors

The integration creates four sensors:

| Sensor | Description |
|--------|-------------|
| `sensor.hacs_installed_components` | Total count of all HACS-installed components with full metadata |
| `sensor.unused_custom_integrations` | Count and details of unused custom integrations |
| `sensor.unused_custom_themes` | Count and details of unused custom themes |
| `sensor.unused_frontend_resources` | Count and details of unused frontend cards/plugins |

### Sensor Attributes

Each unused sensor provides:
- `total_components` — total installed in that category
- `used_components` — count of components in active use
- `unused_components` — list of unused items with name, version, repository URL, days installed, and category-specific metadata (domain, card type, theme variants)

## Dashboard Card

The integration automatically registers a custom Lovelace card. After installation, add it to any dashboard:

### Via UI
1. Edit a dashboard → **Add Card**
2. Search for "Custom Component Monitor"
3. Configure title, sort order, and visible sections

### Via YAML

```yaml
type: custom:custom-component-monitor-card
```

### Card Configuration

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `title` | string | `Custom Component Monitor` | Card title |
| `sort` | string | `name` | Default sort order: `name` or `days` |
| `sections` | list | `["integrations", "themes", "frontend"]` | Which sections to display |

### Examples

**Show everything (default):**
```yaml
type: custom:custom-component-monitor-card
title: Unused Components
```

**Only unused integrations:**
```yaml
type: custom:custom-component-monitor-card
title: Unused Integrations
sections:
  - integrations
```

**Themes and frontend, sorted by days installed:**
```yaml
type: custom:custom-component-monitor-card
title: Themes & Cards
sort: days
sections:
  - themes
  - frontend
```

### Card Features

- **Summary bar** showing installed / used / unused counts
- **Collapsible sections** that remember their open/closed state
- **Sort toggle** to switch between alphabetical and days-installed ordering
- **Section filtering** to show only the categories you care about
- **Repository links** for each unused component
- **Visual config editor** for all options

## How Detection Works

### Integrations
Compares HACS-installed integration domains against `core.config_entries`. If a domain has no config entry, it's marked unused.

### Themes
Scans all Lovelace dashboard configurations and the system/user theme settings for theme name references. Includes fuzzy matching to handle naming variations (e.g. `ha-transformers-theme` → `transformers-themes`).

### Frontend Cards
Derives expected `custom:` card type names from HACS plugin JS filenames, then searches all Lovelace dashboards for matching `type: custom:xxx` references. Utility plugins like `card-mod` are special-cased.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## Development Approach
<img width="256" height="256" alt="Vibe Coding with GitHub Copilot 256x256" src="https://github.com/user-attachments/assets/bb41d075-6b3e-4f2b-a88e-94b2022b5d4f" />

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Support

If you encounter any issues, please report them on the [GitHub Issues page](https://github.com/loryanstrant/HA-CustomComponentMonitor/issues).
