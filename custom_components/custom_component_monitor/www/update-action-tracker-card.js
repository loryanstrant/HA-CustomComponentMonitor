/**
 * Update Action Tracker Card
 * Lists HACS integrations with pending updates and provides
 * Skip, Update, and Update & Action buttons.
 */

const CARD_VERSION = "1.21.0";
const UAT_DOMAIN = "custom_component_monitor";
// #91: AI summaries and the ai_enabled flag live here, not on the update.*
// entities. Both the card and its editor read it.
const UAT_SENSOR = "sensor.hacs_updates";

/* -- Helpers -------------------------------------------------- */

function uatEscapeHtml(text) {
  const el = document.createElement("span");
  el.textContent = String(text);
  return el.innerHTML;
}

// innerHTML escapes & < > in text nodes but leaves quotes alone, so an
// already-escaped string still needs this before it goes in an attribute.
function uatAttr(escapedText) {
  return String(escapedText).replace(/"/g, "&quot;");
}

/**
 * How long ago the last good scan was, in words. The integration stops serving
 * stale data after 24 hours (SCAN_STALE_GRACE), so minutes and hours cover
 * every age this can be asked about.
 */
function uatStaleAgo(iso) {
  const then = Date.parse(iso);
  if (!iso || Number.isNaN(then)) return "";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "a moment ago";
  if (mins < 60) return mins + " minute" + (mins === 1 ? "" : "s") + " ago";
  const hours = Math.round(mins / 60);
  return hours + " hour" + (hours === 1 ? "" : "s") + " ago";
}

/**
 * The "this is the last good scan" notice (#114).
 *
 * The sensor stays available and keeps serving the last successful scan when a
 * scan fails, instead of the card going blank - which is what 1.19.0 did. The
 * trade is that the rows on screen can be old, so they have to say so, with
 * the same drawn half-filled mark the compatibility lines use.
 */
function uatStaleNoticeHtml(attrs) {
  if (!attrs || !attrs.data_stale) return "";
  const ago = uatStaleAgo(attrs.last_successful_scan);
  const err = attrs.scan_error ? String(attrs.scan_error) : "";
  return (
    '<div class="stale-note" role="status">' +
    '<span class="glyph stale"></span>' +
    "<span><strong>Stale</strong> &mdash; showing the last good scan" +
    (ago ? " from " + ago : "") + ". The newest scan failed" +
    (err ? ": " + uatEscapeHtml(err) : ".") +
    "</span></div>"
  );
}

function uatStaleBadgeHtml(attrs) {
  if (!attrs || !attrs.data_stale) return "";
  return '<span class="badge stale"><span class="glyph stale"></span> Stale</span>';
}

/* -- Editor Element -------------------------------------------
 *
 * Built on ha-form. See docs/specs/card-editor-ha-form.md. The old editor was
 * a single hand-built title box that re-rendered - and so replaced the <input>
 * being typed into - on every keystroke, because it fired on `input` and the
 * card dialog calls setConfig() back after each config-changed.
 */

// One source of truth for the card and its editor. The editor shows these as
// the effective values but never stores them - see _uatPrune.
const UAT_DEFAULTS = {
  title: "HACS Update Tracker",
  default_filter: "all",
  default_category_filter: "all",
};

// Mirrors AI_CATEGORY_OPTIONS in const.py. Keep the two in step.
const UAT_AI_CATEGORIES = [
  "Bug fixes",
  "New features",
  "Documentation",
  "Translations",
  "Breaking changes",
  "Dependencies",
  "Other",
];

const UAT_EDITOR_LABELS = {
  title: "Card title (optional)",
  default_filter: "Open filtered to",
  default_category_filter: "Open filtered to category",
};

const UAT_EDITOR_HELPERS = {
  title: `Leave blank to use "${UAT_DEFAULTS.title}".`,
  default_filter: "Which kinds of update the card shows first. You can still change it on the card.",
  default_category_filter: "What each update is about, as summarised by AI.",
};

/**
 * The category field only exists once AI categorisation is switched on in the
 * integration - offering a control that silently does nothing is worse than
 * not offering it. A value stored while it was visible is left alone.
 */
function _uatEditorSchema(aiEnabled) {
  const schema = [
    { name: "title", selector: { text: {} } },
    {
      name: "default_filter",
      selector: { select: { mode: "dropdown", options: [
        { value: "all", label: "All types" },
        { value: "integration", label: "Integrations" },
        { value: "card", label: "Cards" },
        { value: "theme", label: "Themes" },
        { value: "other", label: "Other" },
      ] } },
    },
  ];
  if (aiEnabled) {
    schema.push({
      name: "default_category_filter",
      selector: { select: { mode: "dropdown", options: [
        { value: "all", label: "All categories" },
        ...UAT_AI_CATEGORIES.map((c) => ({ value: c, label: c })),
      ] } },
    });
  }
  return schema;
}

/**
 * Force the frontend chunk that defines ha-form. `window.customElements` is
 * re-read on every call rather than captured: Home Assistant swaps it for a
 * scoped-registry polyfill while its core bundle boots, which is also why this
 * must never be `customElements.whenDefined()` at module top level.
 */
function _uatLoadHaComponents() {
  const registry = window.customElements;
  if (registry && !registry.get("ha-form")) {
    const tile = registry.get("hui-tile-card");
    if (tile && tile.getConfigElement) { tile.getConfigElement(); }
  }
}

/** Drop anything the user did not actually choose. See _ccmPrune. */
function _uatPrune(config) {
  const out = Object.assign({}, config);
  if (out.title === "" || out.title == null) { delete out.title; }
  Object.keys(UAT_DEFAULTS).forEach((key) => {
    if (key in out && out[key] === UAT_DEFAULTS[key]) { delete out[key]; }
  });
  return out;
}

class UpdateActionTrackerCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = Object.assign({}, config);
    this._render();
  }

  // Safe to render on every tick, unlike the hand-built version this replaced:
  // nothing is destroyed, the form just receives new values.
  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    _uatLoadHaComponents();
  }

  _aiEnabled() {
    // The same flag the card itself reads to decide whether to offer AI at all.
    const s = this._hass && this._hass.states[UAT_SENSOR];
    return !!(s && s.attributes && s.attributes.ai_enabled);
  }

  _schema() {
    // Rebuilt only when the flag actually flips, so ha-form is not handed a
    // fresh array object on every state update.
    const aiEnabled = this._aiEnabled();
    if (!this._schemaCache || this._schemaAi !== aiEnabled) {
      this._schemaAi = aiEnabled;
      this._schemaCache = _uatEditorSchema(aiEnabled);
    }
    return this._schemaCache;
  }

  /**
   * What the form displays: the effective values, so no dropdown opens blank -
   * except the title, which stays empty when unset so its helper line can name
   * the default instead of the box claiming a value that isn't stored.
   */
  _formData() {
    const data = Object.assign({}, UAT_DEFAULTS, this._config);
    if (this._config.title == null) { data.title = ""; }
    return data;
  }

  _render() {
    // ha-form needs hass to resolve its selectors, so wait for it.
    if (!this._hass || !this._config) { return; }

    if (!this._form) {
      const form = document.createElement("ha-form");
      form.computeLabel = (schema) => UAT_EDITOR_LABELS[schema.name] || schema.name;
      form.computeHelper = (schema) => UAT_EDITOR_HELPERS[schema.name] || "";
      form.addEventListener("value-changed", (ev) => this._onValueChanged(ev));
      // Light DOM, matching the sibling cards: the dialog styles the editor's
      // own children, and the selectors read `hass` from a Lit context
      // provider further up the tree.
      this.appendChild(form);
      this._form = form;
    }

    this._form.hass = this._hass;
    this._form.schema = this._schema();
    this._form.data = this._formData();
  }

  _onValueChanged(event) {
    // Stop the inner event so only our config-changed reaches the editor host.
    event.stopPropagation();
    // ha-form only returns the keys it was given, so a category stored while
    // the field was visible survives the field being hidden again.
    const merged = Object.assign({}, this._config, event.detail.value);
    const config = _uatPrune(merged);
    this._config = config;
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config },
      bubbles: true,
      composed: true,
    }));
  }
}

if (!customElements.get("update-action-tracker-card-editor")) {
  customElements.define("update-action-tracker-card-editor", UpdateActionTrackerCardEditor);
}

/* -- Main Card ------------------------------------------------ */

class UpdateActionTrackerCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._hacsEntities = null;
    this._expandedEntity = null;
    this._releaseNotes = {};
    this._loadingNotes = {};
    this._actionInProgress = {};
    this._lastStateHash = "";
    this._progressPollTimer = null;
    this._typeFilter = "all"; // #69: filter updates by component type
    this._categoryFilter = "all"; // #67: filter by AI category
    this._categorySort = false; // #67: sort by AI category
    this._shownEntities = []; // currently-visible set, for "Update selected"
    this._aiBusy = false; // #91: a generate_summaries call is in flight
    // #111: updates that need a newer Home Assistant are hidden by default.
    this._showIncompatible = false;
    // #111: per-row "I understand the requirement is unknown" ticks. Deliberately
    // cleared whenever a row closes, so the risk is taken once per install.
    this._ackUnknown = {};
    this._eligibleEntities = []; // the subset "Update all" may actually install
  }

  /* -- Lovelace lifecycle ------------------------------------- */

  static getConfigElement() {
    return document.createElement("update-action-tracker-card-editor");
  }

  static getStubConfig() {
    // Deliberately empty: setConfig applies the default at read time. Baking a
    // title in freezes it in the dashboard, which is how the sibling card ended
    // up needing _riu_migrateConfig. See DECISIONS.md.
    return {};
  }

  setConfig(config) {
    this._config = Object.assign({ title: UAT_DEFAULTS.title }, config);
    this._lastStateHash = "";
    if (config && config.default_filter) this._typeFilter = config.default_filter;
    if (config && config.default_category_filter) this._categoryFilter = config.default_category_filter;
    if (this._hass) this._doRender();
  }

  set hass(hass) {
    this._hass = hass;
    const hash = this._computeStateHash();
    if (hash !== this._lastStateHash) {
      this._lastStateHash = hash;
      this._clearCompletedActions();
      this._doRender();
      this._manageProgressPoll();
    }
  }

  getCardSize() {
    return 4;
  }

  /* -- Data helpers -------------------------------------------- */

  _computeStateHash() {
    if (!this._hass) return "";
    const parts = [];
    for (const eid of Object.keys(this._hass.states)) {
      if (eid.startsWith("update.")) {
        const s = this._hass.states[eid];
        const prog = s.attributes.in_progress;
        // update_percentage drives the determinate progress bar (#66); include
        // it so percentage changes during an install trigger a re-render.
        parts.push(eid + "=" + s.state + "|" + (s.attributes.installed_version || "") + "|" + (s.attributes.latest_version || "") + "|" + String(prog) + "|" + String(s.attributes.update_percentage));
      }
    }
    // #91: AI summaries live on sensor.hacs_updates, not on the update.*
    // entities. Without this the card never re-renders when a summary lands.
    // Hash the summary length (not last_scan) so an hourly no-op scan doesn't
    // force a pointless re-render but a new summary does.
    const sensor = this._hass.states[UAT_SENSOR];
    if (sensor) {
      const a = sensor.attributes || {};
      parts.push("source=" + sensor.state + "|" + a.component_provider);
      // #114: whether these rows are the last good scan, and why. Without this
      // the notice would not appear until some other attribute happened to
      // change, and would not clear when the next scan succeeded.
      parts.push("stale=" + String(a.data_stale) + "|" + String(a.last_successful_scan) + "|" + String(a.scan_error));
      parts.push("ai=" + String(a.ai_enabled) + "|" + String(a.ai_pending) + "|" + String(a.ai_in_progress));
      for (const u of a.updates || []) {
        // #111: the Home Assistant requirement is part of what a row shows, so
        // a scan that newly resolves one must re-render the card.
        const req = u.ha_requirement || {};
        parts.push(
          "u:" + (u.entity_id || u.repository || u.name) +
          "|" + ((u.summary || "").length) +
          "|" + ((u.categories || []).join(",")) +
          "|" + (req.state || "") + "/" + (req.required || "") + "/" + (req.installed || "")
        );
      }
    }
    return parts.sort().join("||");
  }

  _hasAnyInProgress() {
    if (!this._hass) return false;
    for (const eid of Object.keys(this._hass.states)) {
      if (eid.startsWith("update.")) {
        const prog = this._hass.states[eid].attributes.in_progress;
        if (prog !== false && prog !== undefined && prog !== null) return true;
      }
    }
    return Object.keys(this._actionInProgress).length > 0;
  }

  _manageProgressPoll() {
    if (this._hasAnyInProgress()) {
      if (!this._progressPollTimer) {
        this._progressPollTimer = setInterval(() => {
          if (this._hass) {
            const hash = this._computeStateHash();
            if (hash !== this._lastStateHash) {
              this._lastStateHash = hash;
              this._doRender();
            }
            if (!this._hasAnyInProgress()) {
              clearInterval(this._progressPollTimer);
              this._progressPollTimer = null;
            }
          }
        }, 1000);
      }
    } else if (this._progressPollTimer) {
      clearInterval(this._progressPollTimer);
      this._progressPollTimer = null;
    }
  }

  _clearCompletedActions() {
    for (const eid of Object.keys(this._actionInProgress)) {
      const s = this._hass.states[eid];
      if (!s || s.state !== "on") {
        delete this._actionInProgress[eid];
      }
    }
  }

  async _getHacsUpdateEntities() {
    if (!this._hass) return [];

    const updateEntities = [];
    for (const eid of Object.keys(this._hass.states)) {
      if (eid.startsWith("update.") && this._hass.states[eid].state === "on") {
        updateEntities.push(eid);
      }
    }
    if (updateEntities.length === 0) return [];

    // Re-resolve ownership whenever the set changes or the provider migrates.
    const provider = (this._hass.states[UAT_SENSOR] || {}).attributes?.component_provider;
    const sourceKey = provider + "|" + updateEntities.slice().sort().join(",");
    if (this._hacsEntities === null || this._sourceKey !== sourceKey) {
      const sources = await this._hass.callWS({ type: "entity/source" });
      const owned = new Set();
      for (const [eid, info] of Object.entries(sources)) {
        if (info.domain === "hacs" || info.domain === "marketplace") owned.add(eid);
      }
      this._hacsEntities = owned;
      this._sourceKey = sourceKey;
    }
    const filtered = updateEntities.filter((eid) => this._hacsEntities.has(eid));
    // Sort alphabetically by display name, like native HACS (#69).
    filtered.sort((a, b) => {
      const na = (this._hass.states[a].attributes.friendly_name || a).toLowerCase();
      const nb = (this._hass.states[b].attributes.friendly_name || b).toLowerCase();
      return na.localeCompare(nb);
    });
    return filtered;
  }

  /* -- Type classification (#69) ------------------------------ */

  _repoKey(url) {
    const m = String(url || "").toLowerCase().match(/github\.com\/([^/]+\/[^/#?]+)/);
    return m ? m[1].replace(/\.git$/, "") : "";
  }

  _catOf(typeStr) {
    const t = String(typeStr || "").toLowerCase();
    if (t.includes("integration")) return "integration";
    if (t.includes("theme")) return "theme";
    if (t.includes("card") || t.includes("frontend") || t.includes("plugin") || t.includes("lovelace")) return "card";
    return "other";
  }

  // Build {repoKey|name -> category} from this integration's sensor.hacs_updates,
  // since the HACS update.* entities themselves carry no type/category.
  _buildTypeMap() {
    const byEntity = {}, byRepo = {}, byName = {};
    const s = this._hass.states[UAT_SENSOR];
    const updates = (s && s.attributes && s.attributes.updates) || [];
    for (const u of updates) {
      const cat = this._catOf(u.type);
      if (u.entity_id) byEntity[u.entity_id] = cat;
      const rk = this._repoKey(u.repository);
      if (rk) byRepo[rk] = cat;
      if (u.name) byName[String(u.name).toLowerCase().trim()] = cat;
    }
    return { byEntity, byRepo, byName };
  }

  _typeOf(entityId, map) {
    // #91: the scan now resolves the update entity itself, so prefer that exact
    // match over the repo-URL and friendly-name heuristics.
    if (map.byEntity && map.byEntity[entityId]) return map.byEntity[entityId];
    const a = (this._hass.states[entityId] || {}).attributes || {};
    const rk = this._repoKey(a.release_url);
    if (rk && map.byRepo[rk]) return map.byRepo[rk];
    const name = String(a.friendly_name || "").replace(/ update$/i, "").toLowerCase().trim();
    if (name && map.byName[name]) return map.byName[name];
    return "other";
  }

  /* -- AI categories & summary (#67) -------------------------- */

  // Build {repoKey|name -> {categories, summary}} from sensor.hacs_updates,
  // which the integration enriches when AI categorisation is enabled.
  _buildAiMap() {
    const byEntity = {}, byRepo = {}, byName = {};
    const s = this._hass.states[UAT_SENSOR];
    const updates = (s && s.attributes && s.attributes.updates) || [];
    for (const u of updates) {
      const info = {
        categories: Array.isArray(u.categories) ? u.categories : [],
        summary: u.summary || "",
      };
      if (u.entity_id) byEntity[u.entity_id] = info;
      const rk = this._repoKey(u.repository);
      if (rk) byRepo[rk] = info;
      if (u.name) byName[String(u.name).toLowerCase().trim()] = info;
    }
    return { byEntity, byRepo, byName };
  }

  _aiOf(entityId, map) {
    // #91: exact entity match first — the friendly-name fallback misses cases
    // like "Jokes Update" vs the HACS name "Dad Jokes".
    if (map.byEntity && map.byEntity[entityId]) return map.byEntity[entityId];
    const a = (this._hass.states[entityId] || {}).attributes || {};
    const rk = this._repoKey(a.release_url);
    if (rk && map.byRepo[rk]) return map.byRepo[rk];
    const name = String(a.friendly_name || "").replace(/ update$/i, "").toLowerCase().trim();
    if (name && map.byName[name]) return map.byName[name];
    return { categories: [], summary: "" };
  }

  /* -- Home Assistant compatibility (#111) --------------------- */

  // Build {entity|repoKey|name -> ha_requirement} from sensor.hacs_updates. The
  // integration compares each repository's declared minimum Home Assistant
  // version against the running one; see compat.py.
  _buildCompatMap() {
    const byEntity = {}, byRepo = {}, byName = {};
    const s = this._hass.states[UAT_SENSOR];
    const updates = (s && s.attributes && s.attributes.updates) || [];
    const put = (bucket, key, req) => {
      // Two rows can collide on a key — a fork beside its original normalises
      // to the same name, and the scanner's own entity matching can hand one
      // entity to two rows. Keep the STRICTER claim rather than whichever came
      // first: over-warning shows a message the user can read and override,
      // under-warning installs the thing this card exists to hold back.
      bucket[key] = this._stricterReq(bucket[key], req);
    };
    for (const u of updates) {
      const req = u.ha_requirement;
      if (!req || typeof req !== "object") continue;
      if (u.entity_id) put(byEntity, u.entity_id, req);
      const rk = this._repoKey(u.repository);
      if (rk) put(byRepo, rk, req);
      if (u.name) put(byName, String(u.name).toLowerCase().trim(), req);
    }
    return { byEntity, byRepo, byName };
  }

  // blocked > unknown > met. Mirrors compat.strictest() on the Python side.
  _stricterReq(a, b) {
    const rank = (r) => (r && r.state === "blocked" ? 2 : r && r.state === "met" ? 0 : 1);
    if (!a) return b;
    if (!b) return a;
    return rank(b) > rank(a) ? b : a;
  }

  // Never returns "met" by default: a row the last scan didn't cover is
  // "requirement unknown", not "checked and cleared".
  _compatOf(entityId, map) {
    const unknown = { state: "unknown", required: null, installed: null, reason: "not_declared" };
    if (!map) return unknown;
    if (map.byEntity && map.byEntity[entityId]) return map.byEntity[entityId];
    const a = (this._hass.states[entityId] || {}).attributes || {};
    const rk = this._repoKey(a.release_url);
    if (rk && map.byRepo[rk]) return map.byRepo[rk];
    const name = String(a.friendly_name || "").replace(/ update$/i, "").toLowerCase().trim();
    if (name && map.byName[name]) return map.byName[name];
    return unknown;
  }

  // The Home Assistant version to quote in the hidden-rows hint. Prefer what
  // the integration compared against; fall back to what the frontend knows.
  _runningHaVersion(compatMap) {
    const maps = compatMap ? [compatMap.byEntity, compatMap.byRepo, compatMap.byName] : [];
    for (const m of maps) {
      for (const key of Object.keys(m || {})) {
        if (m[key] && m[key].installed) return String(m[key].installed);
      }
    }
    const cfg = (this._hass && this._hass.config) || {};
    return cfg.version ? String(cfg.version) : "";
  }

  // A blocked row renders no install button and an unknown one renders a
  // disabled pair, but guard the handlers too: a click on a stale DOM must not
  // slip past the rule the card exists to enforce.
  _installAllowed(entityId) {
    const req = this._compatOf(entityId, this._buildCompatMap());
    if (req.state === "blocked") return false;
    if (req.state === "unknown" && !this._ackUnknown[entityId]) return false;
    return true;
  }

  /* -- Progress helper ---------------------------------------- */

  _getProgressInfo(entityId) {
    const state = this._hass.states[entityId];
    if (!state) return null;
    const prog = state.attributes.in_progress;
    // Modern HA reports the real percentage in update_percentage (in_progress
    // is just a boolean); older entities put the number in in_progress (#66).
    const pctAttr = state.attributes.update_percentage;
    const actionState = this._actionInProgress[entityId];

    if (typeof pctAttr === "number") {
      const pct = Math.min(100, Math.max(0, Math.round(pctAttr)));
      return { active: true, percent: pct, label: pct + "%" };
    }
    if (typeof prog === "number" && prog >= 0) {
      const pct = Math.min(100, Math.max(0, Math.round(prog)));
      return { active: true, percent: pct, label: pct + "%" };
    }
    if (prog === true || (actionState && prog !== false)) {
      return { active: true, percent: null, label: "Installing\u2026" };
    }
    if (actionState === "skip") {
      return { active: true, percent: null, label: "Skipping\u2026" };
    }
    if (actionState === "update" || actionState === "update_action") {
      return { active: true, percent: null, label: "Starting\u2026" };
    }
    return null;
  }

  /* -- Actions ------------------------------------------------ */

  async _handleSkip(entityId) {
    this._actionInProgress[entityId] = "skip";
    this._doRender();
    this._manageProgressPoll();
    try {
      await this._hass.callService("update", "skip", { entity_id: entityId });
    } catch (_err) {
      delete this._actionInProgress[entityId];
      this._doRender();
    }
  }

  async _handleUpdate(entityId) {
    if (!this._installAllowed(entityId)) return;
    this._actionInProgress[entityId] = "update";
    this._doRender();
    this._manageProgressPoll();
    try {
      await this._hass.callService("update", "install", { entity_id: entityId });
    } catch (_err) {
      delete this._actionInProgress[entityId];
      this._doRender();
    }
  }

  async _handleUpdateAndAction(entityId) {
    if (!this._installAllowed(entityId)) return;
    this._actionInProgress[entityId] = "update_action";
    this._doRender();
    this._manageProgressPoll();
    try {
      await this._hass.callService(UAT_DOMAIN, "update_and_action", { entity_id: entityId });
    } catch (_err) {
      delete this._actionInProgress[entityId];
      this._doRender();
    }
  }

  async _handleUpdateAll() {
    // #111: act on the ELIGIBLE set captured during render — the visible rows
    // whose Home Assistant requirement is met. Blocked and unknown are both
    // left out, and the dialog quotes that same number so the confirmation
    // cannot promise more than the service will do.
    const targets = (this._eligibleEntities || []).slice();
    if (!targets.length) return;
    const filtered = this._typeFilter !== "all" || this._categoryFilter !== "all";
    const partial = targets.length < (this._shownEntities || []).length;
    const noun = "update" + (targets.length > 1 ? "s" : "");
    let msg;
    if (partial) {
      // Some listed rows are out of scope (blocked, or requirement unknown).
      // Said whether or not a type/category filter is also on, because "the N
      // selected" would otherwise quietly mean fewer than the N on screen.
      msg = "Install " + targets.length + " of the " + this._shownEntities.length +
        " listed HACS updates now? The rest are left out.";
    } else if (filtered) {
      msg = "Install the " + targets.length + " selected HACS " + noun + " now?";
    } else {
      msg = "Install all " + targets.length + " available HACS " + noun + " now?";
    }
    if (!window.confirm(msg)) return;
    // Mark each target in progress for immediate feedback.
    targets.forEach((eid) => { this._actionInProgress[eid] = "update"; });
    this._doRender();
    this._manageProgressPoll();
    try {
      // #111: ALWAYS pass the explicit eligible set. The old empty-payload
      // branch told the service "everything you can find", which would have
      // installed the very updates this card is hiding.
      await this._hass.callService(UAT_DOMAIN, "update_all", { entity_ids: targets });
    } catch (_err) {
      targets.forEach((eid) => { delete this._actionInProgress[eid]; });
      this._doRender();
    }
  }

  /* -- AI summaries (#91) -------------------------------------- */

  _aiRunning() {
    const s = this._hass && this._hass.states[UAT_SENSOR];
    return this._aiBusy || !!(s && s.attributes && s.attributes.ai_in_progress);
  }

  async _handleGenerateSummaries() {
    const aiMap = this._buildAiMap();
    // Only ask for the ones actually missing a summary — the service filters
    // again server-side, but this keeps the request honest and cheap.
    const targets = (this._shownEntities || []).filter(
      (eid) => !this._aiOf(eid, aiMap).summary
    );
    if (!targets.length) return;
    this._aiBusy = true;
    this._doRender();
    try {
      // force: an explicit click should retry updates the automatic worker has
      // backed off or given up on. It can't cost a redundant call — `targets`
      // only ever holds rows that have no summary.
      await this._hass.callService(UAT_DOMAIN, "generate_summaries", {
        entity_ids: targets,
        force: true,
      });
    } catch (_err) {
      /* HA surfaces the failure in its own toast. */
    }
    this._aiBusy = false;
    // Summaries arrive via sensor.hacs_updates; force the next push through.
    this._lastStateHash = "";
    this._doRender();
  }

  async _fetchReleaseNotes(entityId) {
    if (this._releaseNotes[entityId] || this._loadingNotes[entityId]) return;
    this._loadingNotes[entityId] = true;
    this._doRender();
    try {
      const result = await this._hass.callWS({
        type: "update/release_notes",
        entity_id: entityId,
      });
      this._releaseNotes[entityId] = result || "_No release notes available._";
    } catch (_err) {
      this._releaseNotes[entityId] = "_Could not load release notes._";
    }
    this._loadingNotes[entityId] = false;
    this._doRender();
  }

  _toggleExpand(entityId) {
    // #111: an "I understand the requirement is unknown" tick lasts as long as
    // the row is open and no longer, so it can never carry over to the next one.
    this._ackUnknown = {};
    if (this._expandedEntity === entityId) {
      this._expandedEntity = null;
    } else {
      this._expandedEntity = entityId;
      this._fetchReleaseNotes(entityId);
    }
    this._doRender();
  }

  /* -- Rendering ---------------------------------------------- */

  async _doRender() {
    if (!this._hass) return;
    const renderId = this._renderId = (this._renderId || 0) + 1;
    const title = uatEscapeHtml(this._config.title || "HACS Update Tracker");
    const sensor = this._hass.states[UAT_SENSOR];
    let entities = [];
    let unavailable = !sensor || ["unknown", "unavailable"].includes(sensor.state);
    if (!unavailable) {
      try {
        entities = await this._getHacsUpdateEntities();
      } catch (_err) {
        unavailable = true; // Never infer ownership from an icon or title.
        this._hacsEntities = null;
        this._lastStateHash = ""; // Retry on the next HA push.
      }
    }
    if (renderId !== this._renderId) return;
    if (unavailable) {
      this._shownEntities = [];
      this._eligibleEntities = [];
      this.shadowRoot.innerHTML = "<style>" + this._getStyles() + "</style><ha-card>" +
        '<div class="header"><span class="title">' + title + '</span><span class="badge">&#9675; Unavailable</span></div>' +
        '<div class="empty" role="status"><span>Component data unavailable. HACS or Marketplace may still be starting. Check its integration, then run Scan now. This card will retry automatically.</span></div></ha-card>';
      return;
    }

    /* Home Assistant compatibility (#111). Updates whose declared minimum HA
       version is higher than the running one are held out of the list entirely
       until the user asks to see them — they cannot be installed, so offering
       them is the trap this change exists to close. Everything below counts
       the LISTED set, so the chips and the badge never promise rows that
       aren't on screen. */
    const compatMap = this._buildCompatMap();
    const blockedAll = entities.filter(
      (eid) => this._compatOf(eid, compatMap).state === "blocked"
    );
    const listable = this._showIncompatible
      ? entities
      : entities.filter((eid) => this._compatOf(eid, compatMap).state !== "blocked");

    /* Classify each update by component type for the filter chips (#69). */
    const typeMap = this._buildTypeMap();
    const cats = {};
    for (const eid of listable) {
      const c = this._typeOf(eid, typeMap);
      (cats[c] = cats[c] || []).push(eid);
    }
    const present = Object.keys(cats);
    // Drop a stale filter (e.g. the last item of that type just updated).
    if (this._typeFilter !== "all" && !present.includes(this._typeFilter)) {
      this._typeFilter = "all";
    }
    let shown = this._typeFilter === "all" ? listable : (cats[this._typeFilter] || []);

    /* AI categories (#67): build the per-update map, tally present categories
       for the chips, then filter/sort the shown list. All a no-op when the AI
       feature is off (updates carry no categories). */
    const aiMap = this._buildAiMap();
    const catCounts = {};
    let anyCats = false;
    for (const eid of listable) {
      const info = this._aiOf(eid, aiMap);
      if (info.categories && info.categories.length) {
        anyCats = true;
        for (const c of info.categories) catCounts[c] = (catCounts[c] || 0) + 1;
      }
    }
    const presentCats = Object.keys(catCounts);
    if (this._categoryFilter !== "all" && !presentCats.includes(this._categoryFilter)) {
      this._categoryFilter = "all";
    }
    if (this._categoryFilter !== "all") {
      shown = shown.filter((eid) => {
        const info = this._aiOf(eid, aiMap);
        return info.categories && info.categories.includes(this._categoryFilter);
      });
    }
    if (this._categorySort && anyCats) {
      shown = shown.slice().sort((a, b) => {
        const ca = (this._aiOf(a, aiMap).categories[0] || "~").toLowerCase();
        const cb = (this._aiOf(b, aiMap).categories[0] || "~").toLowerCase();
        if (ca !== cb) return ca < cb ? -1 : 1;
        const na = (this._hass.states[a].attributes.friendly_name || a).toLowerCase();
        const nb = (this._hass.states[b].attributes.friendly_name || b).toLowerCase();
        return na.localeCompare(nb);
      });
    }

    // Remember the currently-visible (filtered) set so "Update selected" acts on it.
    this._shownEntities = shown;
    // #111: and the subset the bulk service may actually install. Requirement
    // met only — revealing incompatible rows never grows this set.
    const eligible = shown.filter(
      (eid) => this._compatOf(eid, compatMap).state === "met"
    );
    this._eligibleEntities = eligible;
    const shownBlocked = shown.filter(
      (eid) => this._compatOf(eid, compatMap).state === "blocked"
    ).length;
    const shownUnknown = shown.length - eligible.length - shownBlocked;
    const filtered = this._typeFilter !== "all" || this._categoryFilter !== "all";

    let contentHtml;
    if (entities.length === 0) {
      contentHtml = '<div class="empty"><ha-icon icon="mdi:check-circle-outline"></ha-icon><span>All custom components are up to date!</span></div>';
    } else if (shown.length === 0 && listable.length === 0 && blockedAll.length > 0) {
      // Everything pending needs a newer Home Assistant. Say so, rather than
      // leaving an empty card that looks like nothing is waiting.
      contentHtml = '<div class="empty"><span>Every pending update needs a newer Home Assistant than this system runs. Use &ldquo;Show incompatible&rdquo; to see which.</span></div>';
    } else if (shown.length === 0) {
      contentHtml = '<div class="empty"><span>No updates match this filter.</span></div>';
    } else {
      contentHtml = shown.map((eid) => this._renderItem(eid, aiMap, compatMap)).join("");
    }

    /* Filter chips - only when there's more than one type to pick between. */
    let filtersHtml = "";
    if (listable.length > 0 && present.length > 1) {
      const labels = { integration: "Integrations", card: "Cards", theme: "Themes", other: "Other" };
      const order = ["integration", "card", "theme", "other"];
      const chip = (key, lbl, n) =>
        '<button class="chip' + (this._typeFilter === key ? " active" : "") + '" data-filter="' + key + '">' +
        uatEscapeHtml(lbl) + ' <span class="chip-count">' + n + "</span></button>";
      filtersHtml =
        '<div class="filters">' +
        chip("all", "All", listable.length) +
        order.filter((c) => present.includes(c))
             .map((c) => chip(c, labels[c] || c, cats[c].length))
             .join("") +
        "</div>";
    }

    /* Category filter chips + sort toggle (#67) — only when the AI feature has
       produced categories. Multiple categories per update are supported. */
    let catFiltersHtml = "";
    if (listable.length > 0 && anyCats) {
      const catOrder = ["Bug fixes", "New features", "Documentation", "Translations", "Breaking changes", "Dependencies", "Other"];
      const ordered = catOrder.filter((c) => presentCats.includes(c))
        .concat(presentCats.filter((c) => !catOrder.includes(c)).sort());
      const catChip = (key, lbl, n) =>
        '<button class="chip cat-chip' + (this._categoryFilter === key ? " active" : "") + '" data-catfilter="' + uatEscapeHtml(key) + '">' +
        uatEscapeHtml(lbl) + (n != null ? ' <span class="chip-count">' + n + "</span>" : "") + "</button>";
      const sortChip =
        '<button class="chip sort-chip' + (this._categorySort ? " active" : "") + '" data-catsort="1" title="Sort by category">' +
        '<ha-icon icon="mdi:sort-alphabetical-variant"></ha-icon> Sort</button>';
      catFiltersHtml =
        '<div class="filters cat-filters">' +
        catChip("all", "All categories", listable.length) +
        ordered.map((c) => catChip(c, c, catCounts[c])).join("") +
        sortChip +
        "</div>";
    }

    /* "Generate summaries" (#91): the AI enrichment runs in the background and
       can leave gaps (a slow model, a transient provider error, a backed-off
       retry). Offer an explicit way to fill them in. Only ever targets updates
       that are actually missing a summary. */
    const sensorAttrs = (this._hass.states[UAT_SENSOR] || {}).attributes || {};
    const missingSummaries = shown.filter((eid) => !this._aiOf(eid, aiMap).summary);
    const aiBusy = this._aiBusy || !!sensorAttrs.ai_in_progress;
    const aiBtnHtml =
      sensorAttrs.ai_enabled && (missingSummaries.length > 0 || aiBusy)
        ? '<button class="btn btn-ai" data-action="generate-summaries"' +
          (aiBusy || missingSummaries.length === 0 ? " disabled" : "") + ">" +
          (aiBusy
            ? "Generating…"
            : "Generate summaries (" + missingSummaries.length + ")") +
          "</button>"
        : "";

    const badgeClass = entities.length > 0 ? "pending" : "clean";
    // #111: "Up to date" is reserved for genuinely nothing pending. When every
    // pending update is held back the badge says so rather than claiming calm.
    const badgeText = listable.length > 0
      ? listable.length + " update" + (listable.length > 1 ? "s" : "")
      : (entities.length > 0 ? "None installable" : "Up to date");

    /* #111: the header says how many are being held back, so the count in the
       pending badge is never quietly short of the real number of updates. */
    const badgeParts = ['<span class="badge ' + badgeClass + '">' + badgeText + '</span>'];
    if (blockedAll.length > 0) {
      badgeParts.push(
        '<span class="badge blocked"><span class="glyph blocked"></span> ' +
        blockedAll.length + ' incompatible</span>'
      );
    }
    /* #114: the newest scan failed, so these rows are the last good set. The
       badge marks it in the header; the notice below says from when and why. */
    const staleNoticeHtml = uatStaleNoticeHtml(sensorAttrs);
    const staleBadgeHtml = uatStaleBadgeHtml(sensorAttrs);
    if (staleBadgeHtml) badgeParts.push(staleBadgeHtml);
    let badgesHtml = badgeParts.length > 1
      ? '<span class="header-badges">' + badgeParts.join("") + "</span>"
      : badgeParts[0];

    /* #111: the reveal switch and, beside it, why those rows are not listed. */
    let compatBarHtml = "";
    if (blockedAll.length > 0) {
      const haVer = this._runningHaVersion(compatMap);
      compatBarHtml =
        '<div class="compat-bar">' +
        '<button class="chip compat-chip' + (this._showIncompatible ? " active" : "") +
        '" data-compat-reveal="1">' +
        (this._showIncompatible ? "Showing incompatible" : "Show incompatible") +
        ' <span class="chip-count">' + blockedAll.length + '</span></button>' +
        '<span class="compat-hint">' +
        (this._showIncompatible
          ? "They cannot be installed from this card."
          : "Hidden: they need a newer Home Assistant" +
            (haVer ? " than " + uatEscapeHtml(haVer) : "") + ".") +
        '</span></div>';
    }

    /* #111: the button says how many it will install; the line beside it says
       why the rest are out. Both quote the eligible set, never the visible one. */
    const bulkLabel = eligible.length === shown.length
      ? (filtered
          ? 'Update selected (' + shown.length + ')'
          : 'Update all (' + shown.length + ')')
      : 'Update ' + eligible.length + ' of ' + shown.length;
    const outParts = [];
    if (shownBlocked > 0) {
      outParts.push('<span class="glyph blocked"></span> ' + shownBlocked + ' blocked');
    }
    if (shownUnknown > 0) {
      outParts.push(
        '<span class="glyph unknown"></span> ' + shownUnknown +
        (shownBlocked > 0 ? " requirement unknown" : " skipped — requirement unknown")
      );
    }
    const bulkHintHtml = outParts.length
      ? '<span class="compat-hint">' + outParts.join(" · ") + "</span>"
      : "";

    this.shadowRoot.innerHTML =
      "<style>" + this._getStyles() + "</style>" +
      '<ha-card>' +
      '<div class="header">' +
      '<span class="title">' + title + '</span>' +
      badgesHtml +
      '</div>' +
      staleNoticeHtml +
      filtersHtml +
      catFiltersHtml +
      compatBarHtml +
      (shown.length > 0
        ? '<div class="update-all-bar">' +
          bulkHintHtml +
          aiBtnHtml +
          '<button class="btn btn-update-all" data-action="update-all"' +
          (eligible.length === 0 ? " disabled" : "") + '>' +
          bulkLabel +
          '</button></div>'
        : '') +
      '<div class="items">' + contentHtml + '</div>' +
      '</ha-card>';

    this._bindEvents();
  }

  _renderItem(entityId, aiMap, compatMap) {
    const state = this._hass.states[entityId];
    if (!state) return "";
    const attrs = state.attributes;
    const name = uatEscapeHtml(attrs.friendly_name || entityId).replace(/ update$/i, "");
    const installedVer = uatEscapeHtml(attrs.installed_version || "?");
    const latestVer = uatEscapeHtml(attrs.latest_version || "?");
    const versionText = installedVer + " → " + latestVer;
    const picture = attrs.entity_picture || "";
    const releaseUrl = attrs.release_url || "";
    const isExpanded = this._expandedEntity === entityId;
    const progress = this._getProgressInfo(entityId);
    const ai = this._aiOf(entityId, aiMap || { byRepo: {}, byName: {} });
    const catBadgesHtml = (ai.categories && ai.categories.length)
      ? '<div class="cat-badges">' +
        ai.categories.map((c) => '<span class="cat-badge">' + uatEscapeHtml(c) + "</span>").join("") +
        "</div>"
      : "";

    /* #111: every row states its Home Assistant requirement — met, blocked, or
       unknown. None is silent: a row with no line would make absence mean
       "compatible", and an update whose requirement could not be read must
       never look like one that was checked and cleared. */
    const req = this._compatOf(entityId, compatMap);
    const reqVer = req.required ? uatEscapeHtml(String(req.required)) : "";
    const haVer = req.installed ? uatEscapeHtml(String(req.installed)) : "";
    const isBlocked = req.state === "blocked";
    const isUnknown = req.state !== "blocked" && req.state !== "met";
    let compatLineHtml;
    if (isBlocked) {
      compatLineHtml =
        '<div class="compat-line blocked"><span class="glyph blocked"></span> ' +
        '<span>Needs Home Assistant ' + reqVer +
        (haVer ? " — you have " + haVer : "") + "</span></div>";
    } else if (req.state === "met") {
      compatLineHtml =
        '<div class="compat-line ok"><span class="glyph met"></span> ' +
        '<span>Needs Home Assistant ' + reqVer + ' — you have it</span></div>';
    } else if (req.reason === "target_unreadable") {
      /* #115: "unknown" is true but misleading here. The component may well
         state a requirement — what failed is reading the copy that counts, the
         one in the release being offered. Say that, so the next scan's answer
         doesn't look like a contradiction. */
      compatLineHtml =
        '<div class="compat-line unknown"><span class="glyph unknown"></span> ' +
        "<span>Requirement couldn't be checked</span></div>";
    } else {
      compatLineHtml =
        '<div class="compat-line unknown"><span class="glyph unknown"></span> ' +
        '<span>Requirement unknown</span></div>';
    }

    const pictureHtml = picture
      ? '<img class="entity-pic" src="' + uatEscapeHtml(picture) + '" alt="" />'
      : '<ha-icon class="entity-icon" icon="mdi:package-variant"></ha-icon>';

    let notesHtml = "";
    if (isExpanded) {
      if (this._loadingNotes[entityId]) {
        notesHtml = '<div class="notes"><div class="notes-loading">Loading release notes...</div></div>';
      } else if (this._releaseNotes[entityId]) {
        notesHtml = '<div class="notes"><div class="notes-content">' + this._renderMarkdown(this._releaseNotes[entityId]) + '</div></div>';
      }
    }

    /* Progress bar */
    let progressHtml = "";
    if (progress) {
      const barWidth = progress.percent !== null ? progress.percent + "%" : "100%";
      const barClass = progress.percent !== null ? "determinate" : "indeterminate";
      progressHtml =
        '<div class="progress-wrap">' +
        '<div class="progress-bar ' + barClass + '" style="width:' + barWidth + '"></div>' +
        '<span class="progress-label">' + uatEscapeHtml(progress.label) + '</span>' +
        '</div>';
    }

    const disableButtons = !!progress;
    const btnDisabled = disableButtons ? " disabled" : "";

    let html =
      '<div class="item' + (progress ? " updating" : "") + (isBlocked ? " blocked" : "") +
      '" data-entity="' + entityId + '">' +
      '<div class="item-header" data-toggle="' + entityId + '">' +
      pictureHtml +
      '<div class="item-info">' +
      '<div class="item-name" title="' + uatAttr(name) + '">' + name + '</div>' +
      '<div class="item-version" title="' + uatAttr(versionText) + '">' + versionText + '</div>' +
      compatLineHtml +
      catBadgesHtml +
      '</div>' +
      '<ha-icon class="expand-icon' + (isExpanded ? " open" : "") + '" icon="mdi:chevron-down"></ha-icon>' +
      '</div>';

    if (progressHtml) {
      html += progressHtml;
    }

    if (isExpanded) {
      /* #111: the warning comes first — before the summary and the release
         notes — because it decides whether the rest is worth reading.
         The wording stays honest about where the requirement came from: the
         component's own HACS manifest is the only place this card reads it. */
      if (isBlocked) {
        html +=
          '<div class="compat-warn">' +
          '<span class="glyph blocked"></span>' +
          '<span class="warn-body">' +
          '<strong>Version ' + latestVer + ' needs Home Assistant ' + reqVer + '.</strong> ' +
          (haVer ? "You are on " + haVer + ", so " : "This system is older, so ") +
          'this card will not install it. Update Home Assistant first, or leave ' +
          'this one for now.' +
          '<span class="warn-why">Stated by the component itself, in the HACS ' +
          'manifest it ships.</span>' +
          '</span></div>';
      } else if (isUnknown) {
        /* #115: a failed read is NOT "the component says nothing" — claiming
           that would be asserting something this card does not know. The
           headline says which of the two it is. */
        const unreadTarget = req.reason === "target_unreadable";
        const why = req.reason === "unreadable"
          ? "The component states a requirement this card cannot compare" +
            (reqVer ? " (&ldquo;" + reqVer + "&rdquo;)" : "") + "."
          : req.reason === "ha_unreadable"
            ? "This Home Assistant's own version could not be compared, so the check could not run."
            : unreadTarget
              ? "The requirement is read from the release being offered, not from the copy HACS has cached — and that read failed this time. The next scan tries again."
              : "The component's HACS manifest names no minimum Home Assistant version.";
        html +=
          '<div class="compat-warn unknown">' +
          '<span class="glyph unknown"></span>' +
          '<span class="warn-body">' +
          (unreadTarget
            ? '<strong>Version ' + latestVer + "'s own requirements could not be read.</strong> " +
              'So there is no way to know whether it runs on ' +
              (haVer ? haVer : "this version") + '. Check its release notes below before updating.'
            : '<strong>Version ' + latestVer + ' does not say which Home Assistant it needs.</strong> ' +
              'That is not the same as saying it works on ' +
              (haVer ? haVer : "this version") + '. It may install and then fail to load.') +
          '<span class="warn-why">' + why + '</span>' +
          '</span></div>';
      }
      if (ai.summary) {
        html +=
          '<div class="ai-summary">' +
          '<ha-icon icon="mdi:robot-outline"></ha-icon>' +
          '<span>' + uatEscapeHtml(ai.summary) + '</span>' +
          '</div>';
      } else if (this._aiRunning()) {
        // #91: summaries land one at a time, so show which rows are still
        // waiting rather than an unexplained blank.
        html +=
          '<div class="ai-summary pending">' +
          '<ha-icon icon="mdi:robot-outline"></ha-icon>' +
          '<span>Summarising…</span>' +
          '</div>';
      }
      html += notesHtml;
      if (releaseUrl) {
        html += '<div class="release-link"><a href="' + uatEscapeHtml(releaseUrl) + '" target="_blank" rel="noopener noreferrer">View on GitHub</a></div>';
      }
      /* #111: an unknown requirement is installable, but only deliberately —
         one tick, which _toggleExpand forgets when the row closes. */
      const acked = !!this._ackUnknown[entityId];
      if (isUnknown) {
        html +=
          '<div class="compat-ack" data-ack="' + entityId + '" role="checkbox" tabindex="0" ' +
          'aria-checked="' + (acked ? "true" : "false") + '">' +
          '<span class="box' + (acked ? " checked" : "") + '">' + (acked ? "&#10003;" : "") + '</span>' +
          '<span>I understand the requirement is unknown — install ' + latestVer +
          ' anyway</span></div>';
      }

      if (isBlocked) {
        /* No Update button at all, not a disabled one: there is nothing to
           click at and wonder about. Skip stays — dismissing it is still
           useful — and the sentence says what would unblock it. */
        html +=
          '<div class="actions">' +
          '<button class="btn btn-skip" data-action="skip" data-entity="' + entityId + '"' + btnDisabled + '>Skip</button>' +
          '<span class="blocked-why">Installing is blocked until Home Assistant is ' +
          reqVer + ' or newer.</span>' +
          '</div>';
      } else {
        const installDisabled = (disableButtons || (isUnknown && !acked)) ? " disabled" : "";
        html +=
          '<div class="actions">' +
          '<button class="btn btn-skip" data-action="skip" data-entity="' + entityId + '"' + btnDisabled + '>Skip</button>' +
          '<button class="btn btn-update" data-action="update" data-entity="' + entityId + '"' + installDisabled + '>Update</button>' +
          '<button class="btn btn-action" data-action="update_action" data-entity="' + entityId + '"' + installDisabled + '>Update & Action</button>' +
          '</div>';
      }
    }

    html += '</div>';
    return html;
  }

  _renderMarkdown(text) {
    if (!text) return "";
    let html = uatEscapeHtml(text);

    /* Alerts (#68). Render before the line-based transforms so multi-line
       bodies survive. uatEscapeHtml escapes < > & but NOT quotes, so the
       real escaped form is &lt;ha-alert alert-type="warning"&gt;…&lt;/ha-alert&gt;. */
    const alertClass = (t) => {
      const k = String(t).toLowerCase();
      if (k === "warning") return "warning";
      if (k === "error" || k === "danger" || k === "caution") return "error";
      if (k === "success" || k === "tip") return "success";
      return "info"; // note, important, info, hint, default
    };
    // HA alert element (any/no quotes around the type, body may span lines).
    html = html.replace(
      /&lt;ha-alert\s+alert-type=["']?([a-zA-Z]+)["']?\s*&gt;([\s\S]*?)&lt;\/ha-alert&gt;/g,
      (_m, type, body) =>
        '<div class="ha-alert ha-alert-' + alertClass(type) + '">' +
        body.trim().replace(/\n/g, "<br>") + "</div>"
    );
    // GitHub-style alerts: > [!NOTE] / [!TIP] / [!IMPORTANT] / [!WARNING] / [!CAUTION]
    html = html.replace(
      /(?:^|\n)&gt;\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][^\n]*\n((?:&gt;[^\n]*(?:\n|$))*)/g,
      (_m, type, body) => {
        const inner = body
          .split("\n")
          .map((l) => l.replace(/^&gt;\s?/, ""))
          .join("<br>")
          .replace(/(?:<br>)+$/, "")
          .trim();
        return '\n<div class="ha-alert ha-alert-' + alertClass(type) + '">' + inner + "</div>\n";
      }
    );

    /* Release notes often wrap content in layout-only HTML (e.g.
       <div align="center">). uatEscapeHtml turned those into &lt;div&gt;, which
       would show as literal tag text — strip them (the alert <div>s above use
       unescaped < so they're untouched). Keep <br> as a line break. */
    html = html.replace(/&lt;br\s*\/?&gt;/gi, "<br>");
    html = html.replace(
      /&lt;\/?(?:div|center|p|span|details|summary|figure|picture|sub|sup)\b[^&]*?&gt;/gi,
      ""
    );

    html = html.replace(/^#### (.+)$/gm, "<h4>$1</h4>");
    html = html.replace(/^### (.+)$/gm, "<h3>$1</h3>");
    html = html.replace(/^## (.+)$/gm, "<h2>$1</h2>");
    html = html.replace(/^# (.+)$/gm, "<h1>$1</h1>");
    html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
    html = html.replace(/\*(.+?)\*/g, "<em>$1</em>");
    html = html.replace(/`([^`]+)`/g, "<code>$1</code>");
    html = html.replace(/^---$/gm, "<hr />");
    html = html.replace(/^[-*] (.+)$/gm, "<li>$1</li>");
    html = html.replace(/((?:<li>.*<\/li>\n?)+)/g, "<ul>$1</ul>");
    html = html.replace(
      /\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g,
      '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>'
    );
    html = html.replace(/\n\n/g, "</p><p>");
    html = "<p>" + html + "</p>";
    html = html.replace(/<p>\s*<\/p>/g, "");
    html = html.replace(/<p>(<h[1-4]>)/g, "$1");
    html = html.replace(/(<\/h[1-4]>)<\/p>/g, "$1");
    html = html.replace(/<p>(<hr \/>)<\/p>/g, "$1");
    html = html.replace(/<p>(<ul>)/g, "$1");
    html = html.replace(/(<\/ul>)<\/p>/g, "$1");
    html = html.replace(/<p>(<div class="ha-alert)/g, "$1");
    html = html.replace(/(<\/div>)<\/p>/g, "$1");
    return html;
  }

  _bindEvents() {
    const self = this;
    this.shadowRoot.querySelectorAll("[data-toggle]").forEach(function(el) {
      el.addEventListener("click", function(e) {
        e.stopPropagation();
        self._toggleExpand(el.getAttribute("data-toggle"));
      });
    });
    this.shadowRoot.querySelectorAll(".btn[data-action]").forEach(function(btn) {
      btn.addEventListener("click", function(e) {
        e.stopPropagation();
        const action = btn.getAttribute("data-action");
        const eid = btn.getAttribute("data-entity");
        if (action === "skip") self._handleSkip(eid);
        else if (action === "update") self._handleUpdate(eid);
        else if (action === "update_action") self._handleUpdateAndAction(eid);
        else if (action === "update-all") self._handleUpdateAll();
        else if (action === "generate-summaries") self._handleGenerateSummaries();
      });
    });
    this.shadowRoot.querySelectorAll(".chip[data-filter]").forEach(function(chip) {
      chip.addEventListener("click", function(e) {
        e.stopPropagation();
        self._typeFilter = chip.getAttribute("data-filter");
        self._doRender();
      });
    });
    this.shadowRoot.querySelectorAll(".chip[data-catfilter]").forEach(function(chip) {
      chip.addEventListener("click", function(e) {
        e.stopPropagation();
        self._categoryFilter = chip.getAttribute("data-catfilter");
        self._doRender();
      });
    });
    /* #111: reveal/hide the incompatible updates. */
    const compatChip = this.shadowRoot.querySelector(".chip[data-compat-reveal]");
    if (compatChip) {
      compatChip.addEventListener("click", function(e) {
        e.stopPropagation();
        self._showIncompatible = !self._showIncompatible;
        self._doRender();
      });
    }
    /* #111: the unknown-requirement acknowledgement. */
    this.shadowRoot.querySelectorAll("[data-ack]").forEach(function(row) {
      const toggle = function(e) {
        e.stopPropagation();
        const eid = row.getAttribute("data-ack");
        self._ackUnknown[eid] = !self._ackUnknown[eid];
        self._doRender();
      };
      row.addEventListener("click", toggle);
      row.addEventListener("keydown", function(e) {
        if (e.key === " " || e.key === "Enter") toggle(e);
      });
    });
    const sortChip = this.shadowRoot.querySelector(".chip[data-catsort]");
    if (sortChip) {
      sortChip.addEventListener("click", function(e) {
        e.stopPropagation();
        self._categorySort = !self._categorySort;
        self._doRender();
      });
    }
  }

  /* -- Styles ------------------------------------------------- */

  _getStyles() {
    return [
      ":host {",
      "  --uat-primary: var(--primary-text-color, #212121);",
      "  --uat-secondary: var(--secondary-text-color, #727272);",
      "  --uat-accent: var(--primary-color, #03a9f4);",
      "  --uat-divider: var(--divider-color, rgba(0,0,0,0.12));",
      "  --uat-green: var(--label-badge-green, #4caf50);",
      "  --uat-orange: var(--label-badge-yellow, #ff9800);",
      "}",
      "ha-card { padding: 16px; }",
      ".header { display:flex; align-items:center; justify-content:space-between; margin-bottom:12px; }",
      ".title { font-size:1.1em; font-weight:500; color:var(--uat-primary); }",
      ".badge { font-size:0.8em; padding:2px 10px; border-radius:12px; font-weight:500; color:#fff; }",
      ".badge { background:var(--uat-secondary); }",
      ".badge.clean { background:var(--uat-green); }",
      ".badge.pending { background:var(--uat-orange); }",
      ".filters { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px; }",
      ".chip { display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:14px; cursor:pointer; font-size:0.82em; font-family:inherit; color:var(--uat-secondary); background:var(--uat-row-bg, rgba(127,127,127,0.08)); border:1px solid var(--divider-color, rgba(127,127,127,0.2)); }",
      ".chip:hover { background:var(--uat-row-hover, rgba(127,127,127,0.16)); }",
      ".chip.active { background:var(--primary-color, #03a9f4); color:#fff; border-color:var(--primary-color, #03a9f4); }",
      ".chip-count { font-size:0.85em; opacity:0.85; }",
      /* AI category filter row + sort toggle (#67) */
      ".cat-filters { margin-top:-4px; }",
      ".sort-chip ha-icon { --mdc-icon-size:16px; vertical-align:middle; }",
      /* AI category badges — text pills (not colour-coded, for colour-blind a11y) */
      ".cat-badges { display:flex; flex-wrap:wrap; gap:4px; justify-content:flex-start; align-items:center; margin-top:4px; }",
      ".cat-badge { font-size:0.66em; font-weight:600; padding:2px 7px; border-radius:10px; white-space:nowrap; color:var(--uat-secondary); background:var(--uat-row-bg, rgba(127,127,127,0.12)); border:1px solid var(--divider-color, rgba(127,127,127,0.28)); }",
      /* AI one-line summary in the expanded row */
      ".ai-summary { display:flex; align-items:flex-start; gap:6px; margin:0 0 8px 52px; font-size:0.85em; line-height:1.4; color:var(--uat-primary); font-style:italic; }",
      ".ai-summary ha-icon { --mdc-icon-size:16px; color:var(--uat-accent); flex-shrink:0; margin-top:1px; }",
      ".ai-summary.pending { opacity:0.6; }",
      ".empty { display:flex; align-items:center; gap:8px; padding:16px 0; color:var(--uat-secondary); font-style:italic; }",
      ".empty ha-icon { --mdc-icon-size:24px; color:var(--uat-green); }",
      ".item { border-bottom:1px solid var(--uat-divider); }",
      ".item:last-child { border-bottom:none; }",
      ".item.updating { opacity:0.85; }",
      ".item-header { display:flex; align-items:center; gap:12px; padding:12px 0; cursor:pointer; user-select:none; }",
      ".item-header:hover { opacity:0.85; }",
      ".entity-pic { width:40px; height:40px; border-radius:8px; object-fit:contain; background:var(--uat-divider); flex-shrink:0; }",
      ".entity-icon { --mdc-icon-size:28px; color:var(--uat-secondary); flex-shrink:0; width:40px; display:flex; align-items:center; justify-content:center; }",
      ".item-info { flex:1; min-width:0; }",
      ".item-name { font-size:0.95em; font-weight:500; color:var(--uat-primary); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }",
      ".item-version { font-size:0.8em; color:var(--uat-secondary); margin-top:2px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }",
      ".expand-icon { --mdc-icon-size:20px; color:var(--uat-secondary); transition:transform 0.2s ease; flex-shrink:0; }",
      ".expand-icon.open { transform:rotate(180deg); }",
      /* Progress bar styles */
      ".progress-wrap { position:relative; margin:0 0 8px 52px; height:20px; background:var(--uat-divider); border-radius:10px; overflow:hidden; }",
      ".progress-bar { height:100%; border-radius:10px; transition:width 0.3s ease; }",
      ".progress-bar.determinate { background:var(--uat-accent); }",
      ".progress-bar.indeterminate { background:linear-gradient(90deg, var(--uat-accent), var(--uat-green)); animation:uat-pulse 1.5s ease-in-out infinite; }",
      "@keyframes uat-pulse { 0%{opacity:0.6;width:30%} 50%{opacity:1;width:70%} 100%{opacity:0.6;width:30%} }",
      ".progress-label { position:absolute; top:0; left:0; right:0; bottom:0; display:flex; align-items:center; justify-content:center; font-size:0.75em; font-weight:600; color:#fff; text-shadow:0 1px 2px rgba(0,0,0,0.3); }",
      /* Notes */
      ".notes { padding:8px 12px; margin:0 0 8px 52px; background:var(--uat-divider); border-radius:8px; max-height:300px; overflow-y:auto; }",
      ".notes-loading { color:var(--uat-secondary); font-style:italic; font-size:0.85em; padding:8px 0; }",
      ".notes-content { font-size:0.85em; line-height:1.5; color:var(--uat-primary); }",
      ".notes-content h1 { font-size:1.1em; margin:8px 0 4px; }",
      ".notes-content h2 { font-size:1.05em; margin:8px 0 4px; }",
      ".notes-content h3 { font-size:1em; margin:6px 0 4px; }",
      ".notes-content h4 { font-size:0.95em; margin:6px 0 4px; }",
      ".notes-content ul { padding-left:20px; margin:4px 0; }",
      ".notes-content li { margin:2px 0; }",
      ".notes-content code { background:rgba(0,0,0,0.08); padding:1px 4px; border-radius:3px; font-size:0.9em; }",
      ".notes-content hr { border:none; border-top:1px solid var(--uat-divider); margin:12px 0; }",
      ".notes-content a { color:var(--uat-accent); text-decoration:none; }",
      ".notes-content .ha-alert { padding:8px 12px; border-radius:4px; margin:8px 0; font-size:0.9em; }",
      ".notes-content .ha-alert-warning { background:rgba(255,152,0,0.15); border-left:3px solid var(--uat-orange); }",
      ".notes-content .ha-alert-error { background:rgba(244,67,54,0.15); border-left:3px solid #f44336; }",
      ".notes-content .ha-alert-info { background:rgba(3,155,229,0.15); border-left:3px solid #039be5; }",
      ".notes-content .ha-alert-success { background:rgba(76,175,80,0.15); border-left:3px solid #4caf50; }",
      ".release-link { margin:0 0 8px 52px; font-size:0.8em; }",
      ".release-link a { color:var(--uat-accent); text-decoration:none; }",
      ".release-link a:hover { text-decoration:underline; }",
      // #111: wraps, so a blocked row's explanation sits under its Skip button
      // at 380px instead of being squeezed off the edge.
      ".actions { display:flex; gap:8px; padding:0 0 12px; margin-left:52px; flex-wrap:wrap; align-items:center; }",
      ".btn { border:none; border-radius:8px; padding:8px 16px; font-size:0.85em; font-weight:500; cursor:pointer; transition:opacity 0.15s; color:#fff; }",
      ".btn:hover:not(:disabled) { opacity:0.85; }",
      ".btn:disabled { opacity:0.5; cursor:not-allowed; }",
      ".btn-skip { background:var(--uat-secondary); }",
      ".btn-update { background:var(--uat-accent); }",
      ".btn-action { background:var(--uat-green); }",
      ".update-all-bar { display:flex; justify-content:flex-end; gap:8px; margin-bottom:12px; align-items:center; flex-wrap:wrap; }",
      ".btn-update-all { background:var(--uat-green); font-weight:600; }",
      ".btn-ai { background:var(--uat-accent); margin-right:auto; }",

      /* -- Home Assistant compatibility (#111) ------------------------ *
       * Status is carried by a shape glyph plus words, never by colour
       * alone (house rule 9 — the owner is colour-blind). The glyphs match
       * the rest of the fleet: ● met, ◐ needs attention, ○ not known.
       */

      /* The reveal chip sits on its own row under the type/category filters
         so it reads as a view switch rather than another type of update. */
      ".compat-bar { display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin:-4px 0 12px; }",
      ".chip.compat-chip { font-weight:600; }",
      ".chip.compat-chip.active { background:var(--uat-orange); border-color:var(--uat-orange); color:#fff; }",
      ".compat-hint { font-size:0.78em; color:var(--uat-secondary); }",
      // The glyphs inherit currentColor, so without this the "N blocked" mark
      // in the hint line would be grey while the same mark in the badge and in
      // each row is orange — the same state drawn two ways.
      ".compat-hint .glyph.blocked { color:var(--uat-orange); }",
      ".update-all-bar .compat-hint { margin-right:auto; display:flex; align-items:center; gap:4px; flex-wrap:wrap; }",

      /* Header badge for the blocked count — outlined, so it never reads as
         a second "N updates" pill. */
      ".badge.blocked { background:transparent; color:var(--uat-orange); border:1px solid var(--uat-orange); }",
      ".header-badges { display:flex; align-items:center; gap:6px; flex-wrap:wrap; justify-content:flex-end; }",

      /* #114: these rows are the last good scan. Outlined like the blocked
         badge, so it never reads as a second "N updates" count, and marked
         with the same drawn half-filled glyph. */
      ".badge.stale { background:transparent; color:var(--uat-orange); border:1px solid var(--uat-orange); display:inline-flex; align-items:center; gap:4px; }",
      ".glyph.stale { background:linear-gradient(to right, currentColor 50%, transparent 50%); }",
      ".stale-note { display:flex; align-items:flex-start; gap:6px; margin:-4px 0 12px; padding:8px 10px; border-radius:8px; font-size:0.8em; line-height:1.4; color:var(--uat-primary); background:rgba(255,152,0,0.14); border-left:3px solid var(--uat-orange); }",
      ".stale-note strong { font-weight:600; }",

      /* The per-row requirement line, directly under the version line.
       *
       * EVERY row carries one of the three states — ● met, ◐ blocked,
       * ○ unknown. A row with no line at all would make silence mean
       * "compatible", which is the one thing this change must never do: an
       * update whose requirement could not be read must not be
       * indistinguishable from one that was checked and cleared. So "met" is
       * stated out loud rather than implied by absence.
       *
       * Unlike the name and version lines above it, this one WRAPS rather
       * than ellipsising: "Needs Home Assistant 2026.11.0 — you have
       * 2026.10…" cut off at 380px would hide the number the whole warning
       * exists to show.
       */
      ".compat-line { display:flex; align-items:flex-start; gap:5px; font-size:0.76em; line-height:1.35; margin-top:3px; white-space:normal; }",

      /* The three state marks are DRAWN, not typed. ● ◐ ○ as text characters
       * depend on a font the client happens to have, and the half-filled one
       * is the weak link: measured in this change's own design preview,
       * U+25D0 came out roughly a third the size of the other two and read as
       * a stray wedge rather than a sibling shape. That destroys the single
       * distinction a colour-blind reader is meant to rely on (house rule 9).
       * A circle built from a border plus a background renders identically on
       * every platform, at exactly the same size in all three states.
       */
      ".glyph { display:inline-block; width:0.72em; height:0.72em; box-sizing:border-box; border:1.5px solid currentColor; border-radius:50%; flex-shrink:0; position:relative; top:0.14em; }",
      ".glyph.met { background:currentColor; }",
      ".glyph.blocked { background:linear-gradient(to right, currentColor 50%, transparent 50%); }",
      ".glyph.unknown { background:transparent; }",
      ".compat-line.blocked { color:var(--uat-orange); font-weight:600; }",
      ".compat-line.unknown { color:var(--uat-secondary); }",
      ".compat-line.ok { color:var(--uat-secondary); }",

      /* A revealed, blocked row is dimmed and marked down its leading edge,
         so it is distinguishable from an installable row without hue. */
      ".item.blocked { background:rgba(255,152,0,0.07); box-shadow:inset 3px 0 0 0 var(--uat-orange); padding-left:9px; margin-left:-12px; }",
      ".item.blocked .item-name, .item.blocked .item-version { opacity:0.8; }",

      /* The expanded warning panel. Indented to 52px like notes and actions. */
      ".compat-warn { display:flex; gap:8px; margin:0 0 8px 52px; padding:10px 12px; border-radius:8px; font-size:0.84em; line-height:1.45; background:rgba(255,152,0,0.14); border-left:3px solid var(--uat-orange); color:var(--uat-primary); }",
      ".compat-warn.unknown { background:rgba(127,127,127,0.12); border-left-color:var(--uat-secondary); }",
      ".compat-warn .glyph { width:0.85em; height:0.85em; top:0.22em; }",
      ".compat-warn strong { font-weight:600; }",
      ".compat-warn .warn-body { min-width:0; }",
      ".compat-warn .warn-why { display:block; margin-top:4px; color:var(--uat-secondary); font-size:0.94em; }",

      /* The acknowledgement for an unknown requirement. */
      ".compat-ack { display:flex; align-items:flex-start; gap:8px; margin:0 0 10px 52px; font-size:0.84em; line-height:1.4; color:var(--uat-primary); cursor:pointer; }",
      ".compat-ack .box { display:inline-block; width:16px; height:16px; border:2px solid var(--uat-secondary); border-radius:3px; flex-shrink:0; margin-top:1px; }",
      ".compat-ack .box.checked { border-color:var(--uat-accent); background:var(--uat-accent); color:#fff; text-align:center; line-height:14px; font-size:12px; font-weight:700; }",

      /* A blocked row's Update buttons are replaced by a plain explanation,
         so there is no disabled button to click at and wonder about. */
      ".actions .blocked-why { font-size:0.82em; color:var(--uat-secondary); align-self:center; }",
    ].join("\n");
  }
}

if (!customElements.get("update-action-tracker-card")) {
  customElements.define("update-action-tracker-card", UpdateActionTrackerCard);
}

console.info(
  "%c UPDATE-ACTION-TRACKER %c v" + CARD_VERSION + " ",
  "color: white; background: #4caf50; font-weight: bold; padding: 2px 6px; border-radius: 4px 0 0 4px;",
  "color: #4caf50; background: #e8f5e9; font-weight: bold; padding: 2px 6px; border-radius: 0 4px 4px 0;"
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "update-action-tracker-card",
  name: "HACS Update Action Tracker",
  description: "Track HACS updates with Skip, Update, and Update & Action buttons.",
  preview: true,
});
