# Custom Component Monitor

Monitor your HACS installation for unused custom components — with a built-in Lovelace dashboard card.

## Features

- **4 Sensors**: Tracks all HACS-installed components, unused integrations, unused themes, and unused frontend resources
- **Dashboard Card**: Built-in Lovelace card with collapsible sections, sort toggle, and section filtering
- **Smart Detection**: Checks config entries, Lovelace dashboards, and theme settings to determine actual usage
- **Repository Links**: Direct links to each component's repository
- **Installation Tracking**: Shows how long each component has been installed

## Sensors

| Sensor | Description |
|--------|-------------|
| `sensor.hacs_installed_components` | Total count of all HACS-installed components |
| `sensor.unused_custom_integrations` | Unused custom integrations |
| `sensor.unused_custom_themes` | Unused custom themes |
| `sensor.unused_frontend_resources` | Unused frontend cards/plugins |

## Dashboard Card

After installation, add the card to any dashboard:

```yaml
type: custom:custom-component-monitor-card
```

The card is automatically registered as a Lovelace resource — no manual setup required.

## Configuration

No configuration is required beyond adding the integration. Scans run hourly.
- Access repository links for unused integrations, themes, and frontend resources to easily remove them
- Track when components were installed to help with maintenance decisions

## Support

If you encounter any issues, please report them on the [GitHub repository](https://github.com/loryanstrant/HA-CustomComponentMonitor/issues).