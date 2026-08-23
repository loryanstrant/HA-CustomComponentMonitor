# Decisions

Dated, non-obvious choices and the quirks that forced them. One entry per
decision, each ending in a lesson worth carrying elsewhere.

## 2026-08-23 — The card editors are built on `ha-form`

All three visual editors were hand-built `innerHTML`, producing browser-default
form controls inside Home Assistant's own card dialog. They matched none of
their neighbours and followed the browser's `color-scheme` rather than the
active theme, so on a dark theme they were light-mode boxes in a dark dialog.

The stronger reason was structural. `_render()` replaced the whole subtree, and
it ran on every `setConfig()` — which the card dialog calls back after every
`config-changed`. So the editors destroyed the control the user was operating.
The Update Action Tracker's title box fired on `input`, meaning every keystroke
replaced the `<input>` being typed into. `HA-Laundry-Weather` had the visible
form of this (a dropdown flashing open and shut) and fixed it in v0.3.1 with an
equality guard before re-rendering; v0.4.0 replaced the guard with `ha-form`,
where the form element is created once and thereafter only `.hass`, `.schema`
and `.data` are assigned. There is no `innerHTML` left to blow away.

**Lesson:** when a bug is "we destroyed the thing the user was holding", an
equality guard is a promise and the wrong shape is the cause. Move to a
structure where the destructive step does not exist, and the next edit to
`_render()` cannot reintroduce it.

## 2026-08-23 — One editor per card, not one shared editor keyed by card type

`HA-Transformers-Allspark-UI` keys a single editor off `config.type` and picks
its schema from a map. That is right there, and wrong here, for a reason that is
invisible from the source: Allspark is a **bundle** — one built file — whereas
`__init__.py` registers each of this repo's three cards as its own
`res_type: module` Lovelace resource, each with its own `?hacstag=<mtime>`
cache-bust.

A shared editor needs a fourth resource, and two faults follow:

- The modules load in parallel. If `getConfigElement()` runs before the shared
  module has defined the element, Home Assistant assigns `.setConfig`/`.hass` to
  an un-upgraded `HTMLElement`; they become plain expando properties and the
  class's setters never fire on upgrade. The editor opens blank, intermittently.
- A static `import "./ccm-card-editor.js"` resolves against the importing
  module's URL and **drops the query string**, so the shared file would be
  served with no `hacstag` and cached forever — the stale-bundle trap this
  fleet already knows.

The price of the choice is about 35 lines of identical plumbing repeated three
times. That is cheaper than an intermittent blank editor.

**Lesson:** a pattern borrowed from another repo carries that repo's build
model with it. "One file at runtime" and "one file in the editor" are different
claims, and only the first one makes a shared module safe.

## 2026-08-23 — The editor writes nothing the user did not choose

The title boxes used to be pre-filled with the card's default title, and
`getStubConfig()` baked every default into every newly-added card. That is not
cosmetic: `_riu_migrateConfig` exists in this repo solely to strip
`RIU_LEGACY_TITLE`, the old "Recently Installed but Unused" string, back out of
dashboards where an earlier stub had frozen it. Renaming the card did not reach
anyone who had ever added it.

So the rebuilt editors start the title blank, name the default in helper text
underneath, and prune the emitted config: an empty title is dropped rather than
stored as `""`, and any value equal to the card's own default is not persisted.
`getStubConfig()` is trimmed to match.

**Lesson:** a default that has been copied into user data is no longer a
default — it is a value you now have to migrate. Store the absence, and let the
code supply the default at read time.
