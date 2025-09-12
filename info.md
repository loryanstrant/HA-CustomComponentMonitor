# Custom Component Monitor

This integration monitors your HACS installation for custom components and identifies which ones are not being used.

## Features

- **HACS Integration Monitoring**: Scans your HACS repositories and identifies which integrations are installed but not configured
- **HACS Theme Monitoring**: Checks your HACS themes and identifies unused ones with repository links
- **HACS Frontend Resource Monitoring**: Scans your HACS frontend plugins with repository links
- **Installation Date Tracking**: Shows when each component was first installed

## Sensors

The integration creates three sensors:

1. **Unused Custom Integrations** (`sensor.unused_custom_integrations`)
   - Shows the count of unused HACS integrations
   - Attributes include detailed information about each unused integration with repository links and installation dates

2. **Unused Custom Themes** (`sensor.unused_custom_themes`)
   - Shows the count of unused HACS themes
   - Attributes include list of unused theme files with repository links and installation dates

3. **Unused Frontend Resources** (`sensor.unused_frontend_resources`)
   - Shows the count of unused HACS frontend resources
   - Attributes include list of potentially unused files and directories with repository links and installation dates

## Installation

### HACS (Recommended)

1. Open HACS in Home Assistant
2. Go to "Integrations" 
3. Click the "+" button
4. Search for "Custom Component Monitor"
5. Install the integration
6. Restart Home Assistant
7. Go to Settings > Integrations
8. Click "Add Integration" and search for "Custom Component Monitor"
9. Follow the setup process

### Manual Installation

1. Copy the `custom_components/custom_component_monitor` directory to your Home Assistant `custom_components` directory
2. Restart Home Assistant
3. Go to Settings > Integrations
4. Click "Add Integration" and search for "Custom Component Monitor"
5. Follow the setup process

## Configuration

No configuration is required. The integration will automatically scan your installation every hour for changes.

## Usage

After installation, you can:

- View the sensor states in the Home Assistant dashboard
- Create automations based on the sensor values
- Use the detailed attribute data to identify specific unused components
- Access repository links for unused integrations, themes, and frontend resources to easily remove them
- Track when components were installed to help with maintenance decisions

## Support

If you encounter any issues, please report them on the [GitHub repository](https://github.com/loryanstrant/HA-CustomComponentMonitor/issues).