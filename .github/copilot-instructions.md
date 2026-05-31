# Copilot instructions — HA-CustomComponentMonitor

> Canonical standards live in the `dev-standards` repo on SOUNDWAVE/Gitea.
> Read by Copilot chat **and** inline suggestions. For full HA build conventions,
> see the `build-ha-component` skill in dev-standards.

## What this repo is

A **Home Assistant custom component** that monitors installed custom components /
integrations and surfaces update status, with a todo/update-tracker and bundled
Lovelace cards. Domain: `custom_component_monitor`.

## Repo shape

- `custom_components/custom_component_monitor/` — `manifest.json`, `__init__.py`,
  `config_flow.py`, `const.py`, `sensor.py` (large), `todo.py`, `services.yaml`,
  `strings.json`.
  - `brand/` — `icon.png` / `icon@2x.png` (brand assets).
  - `www/` — bundled Lovelace cards (`custom-component-monitor-card.js`,
    `update-action-tracker-card.js`) served by the integration.
- `hacs.json`, `info.md`, `example_dashboard.yaml`, `docs/`,
  `.github/workflows/` (`release.yaml` + `validate.yaml`), `.github/ISSUE_TEMPLATE/`.

## Conventions

- Bump `manifest.json` **version** every release (semver); `domain` matches the
  folder name.
- Test: `hassfest` + HACS validation, then `pytest` with
  `pytest-homeassistant-custom-component`.
- Deploy/test via the published release artifact into TEST1/TEST2, not host
  file-copy. Backup + auto-rollback.
- The `www/*.js` cards are part of the integration's frontend — bump/ship them
  with the component; they're not a separate HACS plugin.

## Never

- Don't commit HA tokens or deploy keys — Gitea Actions secrets only.
