# Custom Component Monitor

This integration monitors your Home Assistant installation for custom components and identifies which ones are not being used.

## Features

- **Custom Integration Monitoring**: Scans your `custom_components` directory and identifies which integrations are installed but not configured
- **Theme Monitoring**: Checks your custom themes and identifies unused ones
- **Frontend Resource Monitoring**: Scans your `www` directory for custom frontend resources

## Sensors

The integration creates three sensors:

1. **Unused Custom Integrations** (`sensor.unused_custom_integrations`)
   - Shows the count of unused custom integrations
   - Attributes include detailed information about each unused integration with repository links

2. **Unused Custom Themes** (`sensor.unused_custom_themes`)
   - Shows the count of unused custom themes
   - Attributes include list of unused theme files

3. **Unused Frontend Resources** (`sensor.unused_frontend_resources`)
   - Shows the count of unused frontend resources in the www directory
   - Attributes include list of potentially unused files and directories

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
- Access repository links for unused integrations to easily remove them

## Support

If you encounter any issues, please report them on the [GitHub repository](https://github.com/loryanstrant/HA-CustomComponentMonitor/issues).