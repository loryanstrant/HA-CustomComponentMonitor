# Spec — Card editors on `ha-form`

*Status: approved 2026-08-23.*

## Problem

All three of this integration's Lovelace cards ship a hand-built visual editor: an `innerHTML`
string producing browser-default `<select>`, `<input type="text">`, `<input type="number">`
and `<input type="checkbox">`. Those editors render inside Home Assistant's own card dialog,
surrounded by HA Material fields, and match none of them.

Two `standards/ux.md` rules are broken:

- **Consistency** — *"a new control matches the treatment of its neighbours; reuse the app's
  existing primitives before inventing new ones."* Raw form controls have different font
  metrics, focus rings and spacing from every other control in that dialog, and they follow
  the browser's `color-scheme` rather than the active Home Assistant theme. On a dark theme
  the editors are visibly light-mode boxes.
- **Copy** — *"no jargon on user-facing surfaces."* The Update Action Tracker's editor is a
  single **Title** box. Its two documented options, `default_filter` and
  `default_category_filter`, exist only for someone willing to hand-write YAML. The main
  card's `show` option is in the same position.

There is a structural point as well. Each editor rebuilds its whole subtree in `_render()`,
and `_render()` runs on every `setConfig()`. The card dialog calls `setConfig()` back after
each `config-changed`, so the control the user is currently operating is destroyed underneath
them. The Update Action Tracker's title box fires on `input`, so every keystroke replaces the
`<input>` element being typed into.

The sibling `HA-Laundry-Weather` card had the visible form of this fault — a dropdown that
flashed open and shut — and fixed it in v0.3.1 with an equality guard before re-rendering.
That fix is a *guard*: a later edit to `_render()` can undo it. `ha-form` removes the
possibility instead. The form element is created once and thereafter only `.hass`, `.schema`
and `.data` are assigned; Lit patches in place and there is no `innerHTML` to blow away.

## Scope

The three **card editors** only, in
`custom_components/custom_component_monitor/www/`:

- `custom-component-monitor-card.js` → `custom-component-monitor-card-editor`
- `recently-installed-unused-card.js` → `recently-installed-unused-card-editor`
- `update-action-tracker-card.js` → `update-action-tracker-card-editor`

Not the integration's configuration screen. That is a Python config/options flow rendered by
Home Assistant itself, and it already uses `EntitySelector`, `BooleanSelector`,
`NumberSelector`, `SelectSelector` and `TextSelector`.

Not the cards' own rendering.

## Constraints found before designing

**This is not a new pattern for this fleet.** `HA-Laundry-Weather` v0.4.0
(`custom_components/laundry_weather/www/laundry-weather-card.js`) is the reference:
`EDITOR_LABELS` + `EDITOR_HELPERS`, a schema builder, `_lwLoadHaComponents`, one `ha-form`,
and a `value-changed` listener that stops the inner event and re-emits `config-changed`.
`ha-jokes` and `HA-Transformers-Allspark-UI` are the same shape.

**One editor per card, not one shared editor keyed by card type.** Allspark keys a single
editor off `config.type` and that is right *there*, because Allspark is a bundle — one built
file. Here, `__init__.py` registers each card `.js` separately as its own `res_type: module`
Lovelace resource with its own `?hacstag=<mtime>` cache-bust (`_register_lovelace_resource`).
A shared editor needs a fourth resource, which brings two concrete faults:

- *Load order.* The modules load in parallel. If `getConfigElement()` runs before the shared
  module defines the element, Home Assistant assigns `.setConfig`/`.hass` to an un-upgraded
  `HTMLElement`; they land as plain expando properties and the class's setters never fire on
  upgrade. The editor opens blank.
- *Cache-busting.* A static `import "./ccm-card-editor.js"` resolves relative to the importing
  module's URL and **drops its query string**, so the shared file would be served with no
  `hacstag` and cached indefinitely — the stale-bundle trap.

The cost of one-per-card is roughly 35 lines of identical plumbing repeated three times. That
is the cheaper of the two.

**No entity picker is involved.** Unlike Laundry Weather, none of these cards has an `entity`
option — they discover `sensor.unused_*`, `sensor.hacs_updates` and `update.*` themselves.
So the "raw entity IDs in a `<select>`" half of the reference's motivation does not apply.

**`customElements.whenDefined()` must not be called at module top level.** Home Assistant
replaces `window.customElements` with a scoped-registry polyfill while its core bundle boots,
so a top-level binding attaches to the native registry's method and may never fire.
`window.customElements` is re-read on every call instead.

**`ha-form` is only defined once Home Assistant loads its editor chunk.** In practice the
editor is only ever built from the card dialog, which has already loaded it, but Mushroom's
`loadHaComponents()` — calling `hui-tile-card.getConfigElement()` to force the chunk — is the
community-canonical belt-and-braces and costs nothing.

**`computeHelper` exists**, so a field can carry a hint line without a separate paragraph.

**This repo has already been bitten by a baked-in default.** `_riu_migrateConfig` exists
solely to strip `RIU_LEGACY_TITLE`, a title string an old `getStubConfig()` wrote into every
dashboard, which froze the card's old name on screen after it was renamed. The rebuilt editors
must not recreate that: nothing is written to the stored config unless the user chose it.

## Flow

Unchanged for the user: *Edit dashboard → the card's pencil → Visual editor* → the same fields
in the same order → changes apply live in the preview beside them. What changes is that the
controls are Home Assistant's own and follow the theme, that three previously YAML-only
options are now on the screen, and that a control can no longer be destroyed while in use.

### Fields

| Card | Key | Label | Control |
|---|---|---|---|
| Monitor | `title` | Card title (optional) | text; helper names the default |
| Monitor | `show` | Which components to list | Only unused / Everything / Only ones in use |
| Monitor | `sort` | Sort by | Name / Days since install |
| Monitor | `sections` | Sections to show | multi-select list: Integrations / Themes / Frontend cards |
| Monitor | `collapsed_by_default` | Start with every section collapsed | toggle |
| Recently installed | `title` | Card title (optional) | text; helper names the default |
| Recently installed | `days_window` | Only show items installed in the last | number, 1–365, unit *days* |
| Recently installed | `sections` | Sections to show | as above |
| Update Tracker | `title` | Card title (optional) | text; helper names the default |
| Update Tracker | `default_filter` | Open filtered to | All types / Integrations / Cards / Themes / Other |
| Update Tracker | `default_category_filter` | Open filtered to category | All categories + the seven AI categories — **only shown when AI categorisation is on** |

`sections` stays a list of the same three strings. Three separate toggles would read more
naturally but would change the stored shape and break existing dashboards.

## Acceptance criteria

1. Every control in all three editors is a Home Assistant component; no raw `<select>`,
   `<input>` or `<textarea>` survives anywhere in an editor's rendered tree.
2. The `ha-form` element is the same node object across 20 consecutive `hass` assignments, and
   each editor still updates when the config genuinely changes.
3. Every stored config key round-trips unchanged, including a config written by the old
   editors and one carrying `RIU_LEGACY_TITLE`. An existing dashboard needs no edit.
4. Nothing is written to the stored config that the user did not choose: an empty title is
   dropped rather than stored as `""`, and a value equal to the card's own default is not
   persisted. `getStubConfig()` no longer bakes defaults into newly-added cards.
5. `days_window` keeps its 1–365 bounds.
6. The Update Tracker's category field is absent from the form when
   `sensor.hacs_updates` reports `ai_enabled: false`, and present when it reports true. A
   value stored while it was visible survives being hidden.
7. Screenshots at desktop and ~380px show all three editors matching the surrounding Home
   Assistant fields, with no clipping or horizontal scroll.

## Non-goals

- The cards' rendering paths. Only the editors change.
- The integration's config/options flow.
- Migrating stored configs. Nothing about the stored shape changes, so there is nothing to
  migrate.
- Mobile parity work. There is no separate mobile app; the Home Assistant companion renders
  this same dialog from these same components, so the 380px check is the mobile check.
