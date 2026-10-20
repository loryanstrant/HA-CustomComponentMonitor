/**
 * HACS New Items Card
 * Reviews newly-flagged HACS integrations and cards: a type filter, AI
 * category groups (alphabetical, "Other" pinned last), tick-and-dismiss,
 * and an auditable "hidden" list for country-filtered items.
 */

const CARD_VERSION = "1.15.1";
const HNI_DOMAIN = "custom_component_monitor";

/* -- Helpers -------------------------------------------------- */

function hniEscapeHtml(text) {
  const el = document.createElement("span");
  el.textContent = String(text);
  return el.innerHTML;
}

// innerHTML escapes & < > in text nodes but leaves quotes alone, so an
// already-escaped string still needs this before it goes in an attribute.
function hniAttr(escapedText) {
  return String(escapedText).replace(/"/g, "&quot;");
}

function hniFormatNumber(n) {
  const num = Number(n);
  if (!Number.isFinite(num)) return String(n);
  return num.toLocaleString("en-US");
}

function hniFormatAge(days) {
  const n = Number(days);
  if (!Number.isFinite(n)) return "";
  return n + " day" + (n === 1 ? "" : "s") + " old";
}

// The key every action identifies an item by: HACS's "owner/repo", with the
// display name as a fallback for a record that somehow lacks one.
function hniItemKey(item) {
  return (item && (item.full_name || item.name)) || "";
}

function hniGroupByCategory(items) {
  const map = {};
  for (const it of items) {
    const cat = (it && it.category && String(it.category).trim()) || "Other";
    (map[cat] = map[cat] || []).push(it);
  }
  return map;
}

// Alphabetical by category name, case-insensitive - except "Other", which is
// always pinned last regardless of where it would otherwise sort (it can
// start with any letter, so a plain sort cannot be relied on to put it last).
function hniOrderedCategories(categories) {
  const isOther = (c) => String(c).trim().toLowerCase() === "other";
  const rest = categories
    .filter((c) => !isOther(c))
    .sort((a, b) => String(a).toLowerCase().localeCompare(String(b).toLowerCase()));
  const others = categories.filter(isOther);
  return rest.concat(others);
}

function hniSortItems(items, mode) {
  const arr = items.slice();
  arr.sort((a, b) => {
    if (mode === "stars") {
      const sa = a.stars == null ? -1 : Number(a.stars);
      const sb = b.stars == null ? -1 : Number(b.stars);
      if (sb !== sa) return sb - sa;
    } else if (mode === "downloads") {
      const da = a.downloads == null ? -1 : Number(a.downloads);
      const db = b.downloads == null ? -1 : Number(b.downloads);
      if (db !== da) return db - da;
    } else {
      // "newest" (default): most recently first-seen first; fall back to the
      // smallest age_days when first_seen is missing on one or both sides.
      const ta = a.first_seen ? Date.parse(a.first_seen) : NaN;
      const tb = b.first_seen ? Date.parse(b.first_seen) : NaN;
      if (!Number.isNaN(ta) && !Number.isNaN(tb) && tb !== ta) return tb - ta;
      const ada = a.age_days == null ? Infinity : Number(a.age_days);
      const adb = b.age_days == null ? Infinity : Number(b.age_days);
      if (ada !== adb) return ada - adb;
    }
    const na = String(a.name || "").toLowerCase();
    const nb = String(b.name || "").toLowerCase();
    return na.localeCompare(nb);
  });
  return arr;
}

/* -- Shared defaults (card + editor) --------------------------- */

const HNI_DEFAULTS = {
  entity: "sensor.hacs_new_items",
  title: "New in HACS",
  kind_filter: "all",
  sort: "newest",
  show_hidden: false,
  max_items_per_group: 0,
};

/* -- Editor Element --------------------------------------------
 *
 * Built on ha-form, per docs/specs/card-editor-ha-form.md and matching the
 * three editors rebuilt in v1.14.0: the form element is created once and
 * thereafter only .hass, .schema and .data are assigned, so Lit patches in
 * place rather than the dialog's repeated setConfig() destroying a control
 * the user is mid-edit on.
 */

const HNI_EDITOR_LABELS = {
  entity: "Entity",
  title: "Card title (optional)",
  kind_filter: "Open filtered to",
  sort: "Sort items by",
  show_hidden: "Start with the hidden list expanded",
  max_items_per_group: "Max items per category (0 = all)",
};

const HNI_EDITOR_HELPERS = {
  entity: 'Defaults to "' + HNI_DEFAULTS.entity + '".',
  title: 'Leave blank to use "' + HNI_DEFAULTS.title + '".',
  kind_filter: "Which kinds of item the card shows first. You can still change it on the card.",
  sort: "How items are ordered within each category group.",
  max_items_per_group: "0 shows every item in a category group.",
};

const HNI_EDITOR_SCHEMA = [
  { name: "entity", selector: { entity: { domain: "sensor" } } },
  { name: "title", selector: { text: {} } },
  {
    name: "kind_filter",
    selector: { select: { mode: "dropdown", options: [
      { value: "all", label: "All types" },
      { value: "integration", label: "Integrations" },
      { value: "card", label: "Cards" },
    ] } },
  },
  {
    name: "sort",
    selector: { select: { mode: "dropdown", options: [
      { value: "newest", label: "Newest first" },
      { value: "stars", label: "Most stars" },
      { value: "downloads", label: "Most downloads" },
    ] } },
  },
  { name: "show_hidden", selector: { boolean: {} } },
  { name: "max_items_per_group", selector: { number: { mode: "box", min: 0, step: 1 } } },
];

/**
 * Force the frontend chunk that defines ha-form. `window.customElements` is
 * re-read on every call rather than captured: Home Assistant swaps it for a
 * scoped-registry polyfill while its core bundle boots, which is also why this
 * must never be `customElements.whenDefined()` at module top level.
 */
function _hniLoadHaComponents() {
  const registry = window.customElements;
  if (registry && !registry.get("ha-form")) {
    const tile = registry.get("hui-tile-card");
    if (tile && tile.getConfigElement) { tile.getConfigElement(); }
  }
}

/** Drop anything the user did not actually choose. See _ccmPrune. */
function _hniPrune(config) {
  const out = Object.assign({}, config);
  if (out.title === "" || out.title == null) { delete out.title; }
  Object.keys(HNI_DEFAULTS).forEach((key) => {
    if (key in out && out[key] === HNI_DEFAULTS[key]) { delete out[key]; }
  });
  return out;
}

class HacsNewItemsCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = Object.assign({}, config);
    this._render();
  }

  // Safe to render on every tick, unlike a hand-built editor would be:
  // nothing is destroyed, the form just receives new values.
  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  connectedCallback() {
    _hniLoadHaComponents();
  }

  /**
   * What the form displays: the effective values, so no dropdown opens blank -
   * except the title, which stays empty when unset so its helper line can name
   * the default instead of the box claiming a value that isn't stored.
   */
  _formData() {
    const data = Object.assign({}, HNI_DEFAULTS, this._config);
    if (this._config.title == null) { data.title = ""; }
    return data;
  }

  _render() {
    // ha-form needs hass to resolve its selectors, so wait for it.
    if (!this._hass || !this._config) { return; }

    if (!this._form) {
      const form = document.createElement("ha-form");
      form.computeLabel = (schema) => HNI_EDITOR_LABELS[schema.name] || schema.name;
      form.computeHelper = (schema) => HNI_EDITOR_HELPERS[schema.name] || "";
      form.addEventListener("value-changed", (ev) => this._onValueChanged(ev));
      // Light DOM, matching the sibling cards: the dialog styles the editor's
      // own children, and the selectors read `hass` from a Lit context
      // provider further up the tree.
      this.appendChild(form);
      this._form = form;
    }

    this._form.hass = this._hass;
    this._form.schema = HNI_EDITOR_SCHEMA;
    this._form.data = this._formData();
  }

  _onValueChanged(event) {
    // Stop the inner event so only our config-changed reaches the editor host.
    event.stopPropagation();
    const merged = Object.assign({}, this._config, event.detail.value);
    const config = _hniPrune(merged);
    this._config = config;
    this.dispatchEvent(new CustomEvent("config-changed", {
      detail: { config },
      bubbles: true,
      composed: true,
    }));
  }
}

if (!customElements.get("hacs-new-items-card-editor")) {
  customElements.define("hacs-new-items-card-editor", HacsNewItemsCardEditor);
}

/* -- Main Card ------------------------------------------------- */

class HacsNewItemsCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = {};
    this._hass = null;
    this._shellBuilt = false;
    this._els = null;
    this._lastHash = "";
    this._lastSensorSnapshot = null;
    this._typeFilter = "all"; // runtime type filter: all | integration | card
    this._expanded = {}; // category -> bool, defaulted the first time it's seen
    this._groupKeys = {}; // category -> the keys "Dismiss all (n)" counted
    this._selected = new Set(); // ticked full_name keys
    this._hiddenOpen = false;
    this._cardSizeEstimate = 5;
  }

  /* -- Lovelace lifecycle --------------------------------------- */

  static getConfigElement() {
    return document.createElement("hacs-new-items-card-editor");
  }

  static getStubConfig() {
    // Deliberately empty: setConfig applies every default at read time. Baking
    // them into a newly-added card is how the sibling cards' old editors froze
    // a stale title/option into every dashboard - see DECISIONS.md.
    return {};
  }

  setConfig(config) {
    this._config = Object.assign({}, config);
    if (config && config.kind_filter) { this._typeFilter = config.kind_filter; }
    this._hiddenOpen = !!(config && config.show_hidden);
    this._lastHash = "";
    if (this._hass) {
      this._ensureShell();
      this._renderDynamic();
    }
  }

  set hass(hass) {
    this._hass = hass;
    const sensor = hass.states[this._entityId()];
    if (!sensor || sensor.state === "unavailable") {
      this._lastSensorSnapshot = null;
    } else {
      this._lastSensorSnapshot = { state: sensor.state, attributes: sensor.attributes || {} };
    }
    const hashKey = this._lastSensorSnapshot
      ? this._lastSensorSnapshot.state + "|" + JSON.stringify(this._lastSensorSnapshot.attributes)
      : "unavailable";
    const changed = hashKey !== this._lastHash;
    this._lastHash = hashKey;
    if (changed || !this._shellBuilt) {
      this._ensureShell();
      this._renderDynamic();
    }
  }

  getCardSize() {
    return this._cardSizeEstimate || 5;
  }

  /* -- Config helpers --------------------------------------------- */

  _entityId() {
    return (this._config && this._config.entity) || HNI_DEFAULTS.entity;
  }

  /* -- Shell: built ONCE. Every later render only touches the child
     containers' innerHTML below, never shadowRoot.innerHTML again, so
     anything card-mod attaches to the shadow root survives a data update. -- */

  _ensureShell() {
    if (this._shellBuilt) return;
    this.shadowRoot.innerHTML =
      "<style>" + this._getStyles() + "</style>" +
      "<ha-card>" +
      '<div class="header">' +
      '<div class="title"></div>' +
      '<div class="subtitle"></div>' +
      "</div>" +
      '<div class="filters"></div>' +
      '<div class="selection-bar"></div>' +
      '<div class="groups"></div>' +
      '<button type="button" class="show-hidden-btn"></button>' +
      '<div class="hidden-audit"></div>' +
      "</ha-card>";
    this._els = {
      card: this.shadowRoot.querySelector("ha-card"),
      title: this.shadowRoot.querySelector(".title"),
      subtitle: this.shadowRoot.querySelector(".subtitle"),
      filters: this.shadowRoot.querySelector(".filters"),
      selectionBar: this.shadowRoot.querySelector(".selection-bar"),
      groups: this.shadowRoot.querySelector(".groups"),
      showHiddenBtn: this.shadowRoot.querySelector(".show-hidden-btn"),
      hiddenAudit: this.shadowRoot.querySelector(".hidden-audit"),
    };
    this._els.card.addEventListener("click", (e) => this._onCardClick(e));
    this._shellBuilt = true;
  }

  /* -- Event delegation: bound once on the persistent <ha-card>, so partial
     re-renders of the containers above never need to re-bind listeners. -- */

  _onCardClick(e) {
    const dismissAllBtn = e.target.closest("[data-dismiss-all]");
    if (dismissAllBtn) {
      e.stopPropagation();
      this._handleDismissCategory(dismissAllBtn.getAttribute("data-dismiss-all"));
      return;
    }
    const groupHeader = e.target.closest("[data-toggle-group]");
    if (groupHeader) {
      const cat = groupHeader.getAttribute("data-toggle-group");
      this._expanded[cat] = !this._expanded[cat];
      this._renderDynamic();
      return;
    }
    const filterChip = e.target.closest("[data-filter]");
    if (filterChip) {
      this._typeFilter = filterChip.getAttribute("data-filter");
      this._renderDynamic();
      return;
    }
    const checkBox = e.target.closest("[data-check]");
    if (checkBox) {
      this._toggleSelect(checkBox.getAttribute("data-check"));
      return;
    }
    const dismissItemBtn = e.target.closest("[data-dismiss-item]");
    if (dismissItemBtn) {
      this._handleDismissItems([dismissItemBtn.getAttribute("data-dismiss-item")]);
      return;
    }
    const dismissSelBtn = e.target.closest('[data-action="dismiss-selected"]');
    if (dismissSelBtn) {
      this._handleDismissItems(Array.from(this._selected));
      return;
    }
    const clearBtn = e.target.closest('[data-action="clear-selection"]');
    if (clearBtn) {
      this._selected.clear();
      this._renderDynamic();
      return;
    }
    const toggleHiddenBtn = e.target.closest(".show-hidden-btn");
    if (toggleHiddenBtn) {
      this._hiddenOpen = !this._hiddenOpen;
      this._renderDynamic();
      return;
    }
    const restoreBtn = e.target.closest("[data-restore]");
    if (restoreBtn) {
      this._handleRestore(restoreBtn.getAttribute("data-restore"));
    }
  }

  /* -- Actions (service calls) -------------------------------------- */

  async _handleDismissItems(repositories) {
    const targets = (repositories || []).filter(Boolean);
    if (!targets.length) return;
    try {
      await this._hass.callService(HNI_DOMAIN, "dismiss_new_items", { repositories: targets });
      targets.forEach((k) => this._selected.delete(k));
      this._renderDynamic();
    } catch (_err) {
      /* HA surfaces the failure in its own toast. */
    }
  }

  /**
   * "Dismiss all (n)" dismisses exactly the n items the button counted.
   *
   * It sends the explicit list of keys recorded for the group at render time,
   * NOT { category }: the count on the button comes from the type-filtered
   * set, while the backend's `category` selector matches on category alone -
   * so filtering to Integrations and pressing "Dismiss all (3)" would also
   * dismiss the Cards in that category, which the user never saw. The list
   * keeps the label and the blast radius identical under every filter.
   */
  async _handleDismissCategory(category) {
    if (!category) return;
    const keys = (this._groupKeys[category] || []).filter(Boolean);
    if (!keys.length) return;
    await this._handleDismissItems(keys);
  }

  async _handleRestore(fullName) {
    if (!fullName) return;
    try {
      await this._hass.callService(HNI_DOMAIN, "restore_new_items", { repositories: [fullName] });
      this._renderDynamic();
    } catch (_err) {
      /* HA surfaces the failure in its own toast. */
    }
  }

  _toggleSelect(key) {
    if (!key) return;
    if (this._selected.has(key)) { this._selected.delete(key); }
    else { this._selected.add(key); }
    this._renderDynamic();
  }

  _pruneSelection(items) {
    const valid = new Set(items.map(hniItemKey));
    for (const k of Array.from(this._selected)) {
      if (!valid.has(k)) { this._selected.delete(k); }
    }
  }

  /* -- Rendering: only ever touches the containers captured in this._els,
     never shadowRoot.innerHTML (that happens exactly once, in _ensureShell). -- */

  _renderDynamic() {
    if (!this._hass || !this._shellBuilt) return;

    const title = hniEscapeHtml(this._config.title || HNI_DEFAULTS.title);
    this._els.title.textContent = title;

    const snap = this._lastSensorSnapshot;
    if (!snap) {
      this._renderUnavailable();
      return;
    }

    const attrs = snap.attributes || {};
    const items = Array.isArray(attrs.items) ? attrs.items : [];
    this._pruneSelection(items);

    /* Subtitle: "N to review · N hidden · N still being sorted", each clause
       omitted when its count is zero (ai_pending can additionally be absent).
       When the sensor held rows back (NEW_ITEMS_MAX_EXPOSED), the clause reads
       "showing 300 of 2,049 to review" instead. The counts come from the
       sensor's own full tally - the list is shortened, the number is not - so
       without this the header would quietly report the slice as the whole
       queue. */
    const parsedState = parseInt(snap.state, 10);
    const reviewCount = Number.isFinite(parsedState) ? parsedState : items.length;
    const hiddenArr = Array.isArray(attrs.hidden) ? attrs.hidden : [];
    const hiddenCount = Number.isFinite(Number(attrs.hidden_count)) && attrs.hidden_count != null
      ? Number(attrs.hidden_count)
      : hiddenArr.length;
    const aiPending = Number(attrs.ai_pending) || 0;
    const itemsTruncated = Math.max(0, Number(attrs.items_truncated) || 0);
    const hiddenTruncated = Math.max(0, Number(attrs.hidden_truncated) || 0);
    const countClause = (total, shown, truncated, noun) =>
      truncated > 0
        ? "showing " + hniFormatNumber(shown) + " of " + hniFormatNumber(total) + " " + noun
        : hniFormatNumber(total) + " " + noun;
    const subtitleParts = [];
    if (reviewCount > 0) {
      subtitleParts.push(countClause(reviewCount, items.length, itemsTruncated, "to review"));
    }
    if (hiddenCount > 0) {
      subtitleParts.push(countClause(hiddenCount, hiddenArr.length, hiddenTruncated, "hidden"));
    }
    if (aiPending > 0) subtitleParts.push(hniFormatNumber(aiPending) + " still being sorted");
    this._els.subtitle.textContent = subtitleParts.join(" · ");
    this._els.subtitle.style.display = subtitleParts.length ? "" : "none";

    /* Type filter chips: same idiom as update-action-tracker-card.js's
       `.filters` / `.chip` / data-filter / chip-count (see report). */
    this._els.filters.innerHTML = items.length ? this._filtersHtml(items) : "";

    /* Selection bar */
    if (this._selected.size) {
      this._els.selectionBar.classList.add("visible");
      this._els.selectionBar.innerHTML = this._selectionBarHtml();
    } else {
      this._els.selectionBar.classList.remove("visible");
      this._els.selectionBar.innerHTML = "";
    }

    /* Groups: alphabetical by category, "Other" pinned last, default collapsed
       except the first two - computed from the FULL (unfiltered-by-type) set
       of categories so the type filter never reshuffles which ones default
       open, then the type filter only ever hides a group whose count drops
       to zero under it. */
    const allCatMap = hniGroupByCategory(items);
    const orderedCats = hniOrderedCategories(Object.keys(allCatMap));
    orderedCats.forEach((cat, idx) => {
      if (!(cat in this._expanded)) { this._expanded[cat] = idx < 2; }
    });

    const filteredItems = this._typeFilter === "all"
      ? items
      : items.filter((it) => it.kind === this._typeFilter);

    // Rebuilt every render so "Dismiss all" can only ever act on what the
    // groups currently show under the current type filter.
    this._groupKeys = {};

    if (items.length === 0) {
      this._els.groups.innerHTML = '<div class="empty-state">Nothing new to review.</div>';
      this._cardSizeEstimate = 3;
    } else if (filteredItems.length === 0) {
      this._els.groups.innerHTML = '<div class="empty-state">No items match this filter.</div>';
      this._cardSizeEstimate = 3;
    } else {
      const filteredCatMap = hniGroupByCategory(filteredItems);
      const maxPer = Number(this._config.max_items_per_group) || 0;
      const sortMode = this._config.sort || HNI_DEFAULTS.sort;
      let groupsHtml = "";
      let visibleGroupCount = 0;
      for (const cat of orderedCats) {
        const catItems = filteredCatMap[cat];
        if (!catItems || !catItems.length) continue; // emptied out under the filter - hidden entirely
        visibleGroupCount++;
        this._groupKeys[cat] = catItems.map(hniItemKey);
        const sorted = hniSortItems(catItems, sortMode);
        const shown = maxPer > 0 ? sorted.slice(0, maxPer) : sorted;
        groupsHtml += this._groupHtml(cat, shown, catItems.length);
      }
      this._els.groups.innerHTML = groupsHtml;
      this._cardSizeEstimate = 3 + visibleGroupCount;
    }

    /* Footer: "Show hidden (n)" + the audit list it expands to. The label
       carries the same "n of total" honesty as the subtitle, because the audit
       view is the only way to check the country filter - a button promising 191
       that opens onto 100 would make the filter look like it had lost rows. */
    if (hiddenCount > 0 || hiddenArr.length > 0) {
      const n = hiddenCount || hiddenArr.length;
      const hiddenLabel = hiddenTruncated > 0
        ? hniFormatNumber(hiddenArr.length) + " of " + hniFormatNumber(n)
        : hniFormatNumber(n);
      this._els.showHiddenBtn.style.display = "";
      this._els.showHiddenBtn.innerHTML =
        "Show hidden (" + hiddenLabel + ") " +
        '<ha-icon class="chevron' + (this._hiddenOpen ? " open" : "") + '" icon="mdi:chevron-down"></ha-icon>';
      this._els.hiddenAudit.innerHTML = this._hiddenOpen
        ? this._hiddenAuditHtml(hiddenArr, hiddenTruncated)
        : "";
      if (this._hiddenOpen) this._cardSizeEstimate += 2;
    } else {
      this._els.showHiddenBtn.style.display = "none";
      this._els.hiddenAudit.innerHTML = "";
    }
  }

  _renderUnavailable() {
    const entityId = hniEscapeHtml(this._entityId());
    this._groupKeys = {};
    this._els.subtitle.textContent = "";
    this._els.subtitle.style.display = "none";
    this._els.filters.innerHTML = "";
    this._els.selectionBar.classList.remove("visible");
    this._els.selectionBar.innerHTML = "";
    this._els.groups.innerHTML = '<div class="empty-state">"' + entityId + '" is not available.</div>';
    this._els.showHiddenBtn.style.display = "none";
    this._els.hiddenAudit.innerHTML = "";
    this._cardSizeEstimate = 3;
  }

  _filtersHtml(items) {
    const counts = { all: items.length, integration: 0, card: 0 };
    for (const it of items) {
      if (it.kind === "integration") counts.integration++;
      else if (it.kind === "card") counts.card++;
    }
    const chip = (key, label, n) =>
      '<button type="button" class="chip' + (this._typeFilter === key ? " active" : "") + '" data-filter="' + key + '">' +
      hniEscapeHtml(label) + ' <span class="chip-count">' + n + "</span></button>";
    return (
      chip("all", "All", counts.all) +
      chip("integration", "Integrations", counts.integration) +
      chip("card", "Cards", counts.card)
    );
  }

  _selectionBarHtml() {
    const n = this._selected.size;
    return (
      '<span class="sel-text">' + n + " selected</span>" +
      '<div class="sel-actions">' +
      '<button type="button" class="btn btn-primary" data-action="dismiss-selected">Dismiss ' + n + " selected</button>" +
      '<button type="button" class="btn btn-text" data-action="clear-selection">Clear</button>' +
      "</div>"
    );
  }

  _groupHtml(category, shownItems, totalCount) {
    const expanded = !!this._expanded[category];
    const catAttr = hniAttr(hniEscapeHtml(category));
    const dismissAllBtn = expanded
      ? '<button type="button" class="dismiss-all-btn" data-dismiss-all="' + catAttr + '">Dismiss all (' + totalCount + ")</button>"
      : "";
    const itemsHtml = shownItems.map((it) => this._itemRowHtml(it)).join("");
    return (
      '<div class="group' + (expanded ? " expanded" : "") + '">' +
      '<div class="group-header" data-toggle-group="' + catAttr + '">' +
      '<span class="group-name">' + hniEscapeHtml(category) + " (" + totalCount + ")</span>" +
      dismissAllBtn +
      '<ha-icon class="chevron' + (expanded ? " open" : "") + '" icon="mdi:chevron-down"></ha-icon>' +
      "</div>" +
      '<div class="group-items">' + itemsHtml + "</div>" +
      "</div>"
    );
  }

  _itemRowHtml(item) {
    const key = hniItemKey(item);
    const keyAttr = hniAttr(hniEscapeHtml(key));
    const selected = this._selected.has(key);
    const name = hniEscapeHtml(item.name || item.full_name || "Unknown");
    const kindLabel = item.kind === "integration" ? "Integration"
      : item.kind === "card" ? "Card"
      : hniEscapeHtml(item.kind || "");

    const metaParts = [item.stars == null ? "★ —" : "★ " + hniFormatNumber(item.stars)];
    if (item.downloads != null) metaParts.push(hniFormatNumber(item.downloads) + " downloads");
    if (item.age_days != null) metaParts.push(hniFormatAge(item.age_days));
    const metaText = hniEscapeHtml(metaParts.join(" · "));

    const descHtml = item.description
      ? '<div class="item-desc">' + hniEscapeHtml(item.description) + "</div>"
      : '<div class="item-desc muted">No description provided</div>';

    const repoUrl = item.repository || "";
    const repoLinkHtml = repoUrl
      ? '<a class="icon-btn" href="' + hniAttr(hniEscapeHtml(repoUrl)) + '" target="_blank" rel="noopener noreferrer" title="Open repository"><ha-icon icon="mdi:open-in-new"></ha-icon></a>'
      : "";

    return (
      '<div class="item">' +
      '<div class="item-check' + (selected ? " checked" : "") + '" data-check="' + keyAttr + '" role="checkbox" aria-checked="' + (selected ? "true" : "false") + '">' +
      (selected ? '<ha-icon icon="mdi:check"></ha-icon>' : "") +
      "</div>" +
      '<div class="item-body">' +
      '<div class="item-top">' +
      // The tooltip carries "owner/repo": the visible line is now HACS's
      // display name, so this is the only place left to see which repository
      // it actually is without opening the link.
      '<span class="item-name" title="' + hniAttr(hniEscapeHtml(item.full_name || item.name || "")) + '">' + name + "</span>" +
      '<span class="kind-chip">' + kindLabel + "</span>" +
      "</div>" +
      '<div class="item-meta">' + metaText + "</div>" +
      descHtml +
      "</div>" +
      '<div class="item-actions">' +
      repoLinkHtml +
      '<button type="button" class="icon-btn dismiss" data-dismiss-item="' + keyAttr + '" title="Dismiss"><ha-icon icon="mdi:close"></ha-icon></button>' +
      "</div>" +
      "</div>"
    );
  }

  _hiddenAuditHtml(hidden, truncated) {
    if (!hidden.length) return '<div class="empty-state">Nothing hidden.</div>';
    // A closing note rather than silence: the restore button only exists for
    // rows that are actually listed, so the user has to be told when some of
    // the hidden set is not on screen to restore.
    const note = truncated > 0
      ? '<div class="audit-note">' + hniFormatNumber(truncated) +
        " more hidden, not listed here.</div>"
      : "";
    return hidden.map((row) => {
      const key = row.full_name || row.name || "";
      return (
        '<div class="audit-row">' +
        "<div>" +
        '<div class="audit-name" title="' + hniAttr(hniEscapeHtml(row.full_name || "")) + '">' +
        hniEscapeHtml(row.name || row.full_name || "Unknown") + "</div>" +
        '<div class="audit-reason">' + hniEscapeHtml(row.reason || "") + "</div>" +
        "</div>" +
        '<button type="button" class="restore-btn" data-restore="' + hniAttr(hniEscapeHtml(key)) + '">Restore</button>' +
        "</div>"
      );
    }).join("") + note;
  }

  /* -- Styles ------------------------------------------------- */

  _getStyles() {
    return [
      ":host {",
      "  --hni-primary: var(--primary-text-color, #212121);",
      "  --hni-secondary: var(--secondary-text-color, #727272);",
      "  --hni-accent: var(--primary-color, #03a9f4);",
      "  --hni-divider: var(--divider-color, rgba(0,0,0,0.12));",
      "  --hni-error: var(--error-color, #db4437);",
      "}",
      "ha-card { padding: 16px; }",
      ".header { margin-bottom: 12px; }",
      ".title { font-size:1.1em; font-weight:500; color:var(--hni-primary); }",
      ".subtitle { font-size:0.82em; color:var(--hni-secondary); margin-top:2px; }",
      /* Type filter chips - same idiom as .filters/.chip in
         update-action-tracker-card.js (see the written report for exact lines). */
      ".filters { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:12px; }",
      ".chip { display:inline-flex; align-items:center; gap:6px; padding:3px 10px; border-radius:14px; cursor:pointer; font-size:0.82em; font-family:inherit; color:var(--hni-secondary); background:var(--hni-row-bg, rgba(127,127,127,0.08)); border:1px solid var(--divider-color, rgba(127,127,127,0.2)); }",
      ".chip:hover { background:var(--hni-row-hover, rgba(127,127,127,0.16)); }",
      ".chip.active { background:var(--primary-color, #03a9f4); color:#fff; border-color:var(--primary-color, #03a9f4); }",
      ".chip-count { font-size:0.85em; opacity:0.85; }",
      /* Selection bar */
      ".selection-bar { display:none; align-items:center; justify-content:space-between; gap:10px; background:rgba(var(--rgb-primary-color, 3,169,244), 0.08); border:1px solid rgba(var(--rgb-primary-color, 3,169,244), 0.2); border-radius:8px; padding:8px 12px; margin-bottom:12px; font-size:0.85em; font-weight:500; color:var(--hni-primary); }",
      ".selection-bar.visible { display:flex; }",
      ".sel-actions { display:flex; align-items:center; gap:4px; flex-shrink:0; }",
      ".btn { border:none; border-radius:8px; padding:8px 16px; font-size:0.85em; font-weight:500; cursor:pointer; font-family:inherit; transition:opacity 0.15s; }",
      ".btn:hover { opacity:0.85; }",
      ".btn-primary { background:var(--hni-accent); color:#fff; }",
      ".btn-text { background:none; color:var(--hni-secondary); padding:8px 10px; }",
      /* Category groups - same idiom as .section/.section-header in
         custom-component-monitor-card.js */
      ".group { border-bottom:1px solid var(--hni-divider); }",
      ".group:last-of-type { border-bottom:none; }",
      ".group-header { display:flex; align-items:center; gap:8px; padding:10px 0; font-weight:500; font-size:0.95em; color:var(--hni-primary); cursor:pointer; user-select:none; }",
      ".group-header:hover { opacity:0.85; }",
      ".group-name { flex:1 1 auto; }",
      ".dismiss-all-btn { background:none; border:none; color:var(--hni-accent); font-size:0.82em; font-weight:500; cursor:pointer; padding:4px 8px; white-space:nowrap; font-family:inherit; flex-shrink:0; }",
      ".chevron { --mdc-icon-size:18px; color:var(--hni-secondary); transition:transform 0.2s ease; flex-shrink:0; }",
      ".chevron.open { transform:rotate(180deg); }",
      ".group-items { display:none; padding-bottom:4px; }",
      ".group.expanded .group-items { display:block; }",
      /* Item row - same idiom as .item-header in update-action-tracker-card.js */
      ".item { display:flex; align-items:flex-start; gap:12px; padding:10px 0 10px 8px; border-bottom:1px solid var(--hni-divider); }",
      ".item:last-child { border-bottom:none; }",
      ".item-check { width:18px; height:18px; margin-top:2px; border:2px solid var(--hni-secondary); border-radius:4px; display:flex; align-items:center; justify-content:center; cursor:pointer; flex-shrink:0; }",
      ".item-check.checked { background:var(--hni-accent); border-color:var(--hni-accent); }",
      ".item-check ha-icon { --mdc-icon-size:14px; color:#fff; }",
      ".item-body { flex:1; min-width:0; }",
      ".item-top { display:flex; align-items:center; gap:8px; flex-wrap:wrap; min-width:0; }",
      ".item-name { font-size:0.95em; font-weight:500; color:var(--hni-primary); flex:1 1 auto; min-width:0; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }",
      /* Kind chip - same idiom as .cat-badge (AI category pill) in
         update-action-tracker-card.js */
      ".kind-chip { font-size:0.66em; font-weight:600; padding:2px 7px; border-radius:10px; white-space:nowrap; color:var(--hni-secondary); background:var(--hni-row-bg, rgba(127,127,127,0.12)); border:1px solid var(--divider-color, rgba(127,127,127,0.28)); flex-shrink:0; }",
      ".item-meta { font-size:0.78em; color:var(--hni-secondary); margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }",
      ".item-desc { font-size:0.85em; color:var(--hni-secondary); line-height:1.4; margin-top:4px; display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }",
      ".item-desc.muted { font-style:italic; opacity:0.75; }",
      ".item-actions { display:flex; align-items:center; gap:2px; flex-shrink:0; margin-top:1px; }",
      ".icon-btn { background:none; border:none; cursor:pointer; color:var(--hni-secondary); padding:4px 6px; line-height:1; border-radius:4px; --mdc-icon-size:18px; display:inline-flex; }",
      ".icon-btn:hover { background:rgba(127,127,127,0.12); color:var(--hni-primary); }",
      ".icon-btn.dismiss:hover { color:var(--hni-error); }",
      ".empty-state { padding:16px 8px; font-size:0.85em; color:var(--hni-secondary); font-style:italic; }",
      /* Footer / hidden audit */
      ".show-hidden-btn { width:100%; display:flex; align-items:center; justify-content:center; gap:4px; background:none; border:none; border-top:1px solid var(--hni-divider); margin-top:4px; padding:12px 0 4px; color:var(--hni-accent); font-size:0.88em; font-weight:500; cursor:pointer; font-family:inherit; }",
      ".hidden-audit { padding-top:4px; }",
      ".audit-row { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:8px 0; border-bottom:1px solid var(--hni-divider); }",
      ".audit-row:last-child { border-bottom:none; }",
      ".audit-name { font-size:0.88em; color:var(--hni-primary); font-weight:500; }",
      ".audit-reason { font-size:0.76em; color:var(--hni-secondary); margin-top:2px; }",
      ".restore-btn { background:none; border:none; color:var(--hni-accent); font-size:0.8em; font-weight:500; cursor:pointer; white-space:nowrap; flex-shrink:0; font-family:inherit; padding:4px 8px; }",
      ".audit-note { padding:8px 0 0; font-size:0.76em; color:var(--hni-secondary); font-style:italic; }",
      /* ~380px: kind chip + meta wrap under the name, group's Dismiss all goes
         full-width below the group title, action buttons go full-width. */
      "@media (max-width: 380px) {",
      "  .item-top { flex-direction:column; align-items:flex-start; gap:3px; }",
      "  .item-name { white-space:normal; }",
      "  .group-header { flex-wrap:wrap; }",
      "  .group-header .group-name { order:1; flex:1 1 auto; }",
      "  .group-header .chevron { order:2; }",
      "  .group-header .dismiss-all-btn { order:3; flex:1 1 100%; text-align:left; background:rgba(var(--rgb-primary-color, 3,169,244), 0.08); border-radius:8px; margin-top:4px; padding:8px 10px; }",
      "  .selection-bar { flex-direction:column; align-items:stretch; }",
      "  .sel-actions { flex-direction:column; align-items:stretch; }",
      "  .btn { width:100%; text-align:center; }",
      "}",
    ].join("\n");
  }
}

if (!customElements.get("hacs-new-items-card")) {
  customElements.define("hacs-new-items-card", HacsNewItemsCard);
}

console.info(
  "%c HACS-NEW-ITEMS %c v" + CARD_VERSION + " ",
  "color: white; background: #3f51b5; font-weight: bold; padding: 2px 6px; border-radius: 4px 0 0 4px;",
  "color: #3f51b5; background: #e8eaf6; font-weight: bold; padding: 2px 6px; border-radius: 0 4px 4px 0;"
);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "hacs-new-items-card",
  name: "HACS New Items",
  description: "Review newly-flagged HACS integrations and cards, grouped and dismissable.",
  preview: true,
});
