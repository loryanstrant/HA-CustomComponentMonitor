// #111: the card's Home Assistant compatibility behaviour, offline.
//
// Browser rendering is verified separately against DEV; this checks the rules
// the design preview settled — hidden by default, revealed on request, never
// installable while blocked, and bulk acting only on the eligible set.
//
// Run: node tests/test_tracker_card_compat.cjs
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const elements = new Map();
const sandbox = {
  HTMLElement: class { attachShadow() { this.shadowRoot = { innerHTML: '', querySelectorAll: () => [], querySelector: () => null }; } },
  customElements: { get: n => elements.get(n), define: (n, c) => elements.set(n, c) },
  window: { customCards: [], confirm: () => true }, console: { info() {} },
  setInterval: () => 1, clearInterval: () => {},
  document: { createElement() { return { set textContent(v) { this.innerHTML = v; } }; } },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('custom_components/custom_component_monitor/www/update-action-tracker-card.js', 'utf8'), sandbox);

const req = (state, required, installed, reason) => ({ state, required, installed, reason: reason || null });

// The markup for one row, cut at the start of the next one.
function rowOf(html, entityId) {
  const start = html.indexOf('data-entity="' + entityId + '"');
  assert.notEqual(start, -1, entityId + ' is not rendered');
  // The next row opens with class="item" or class="item <modifier>"; the inner
  // item-header/item-info divs are class="item-…" and must not end the slice.
  const rest = html.slice(start);
  const next = rest.slice(1).search(/<div class="item[" ]/);
  return next === -1 ? rest : rest.slice(0, next + 1);
}

function makeCard() {
  const card = new (elements.get('update-action-tracker-card'))();
  const states = {
    'sensor.hacs_updates': {
      state: '4',
      attributes: {
        component_provider: 'hacs',
        updates: [
          { entity_id: 'update.ok', name: 'Fine Thing', ha_requirement: req('met', '2025.6.0', '2026.10.4') },
          { entity_id: 'update.ok2', name: 'Other Thing', ha_requirement: req('met', '2024.1.0', '2026.10.4') },
          { entity_id: 'update.blocked', name: 'Microsoft 365 Calendar', ha_requirement: req('blocked', '2026.11.0', '2026.10.4') },
          { entity_id: 'update.mystery', name: 'Dad Jokes', ha_requirement: req('unknown', null, '2026.10.4', 'not_declared') },
        ],
      },
    },
    'update.ok': { state: 'on', attributes: { friendly_name: 'Fine Thing update', installed_version: '1.0.0', latest_version: '1.1.0' } },
    'update.ok2': { state: 'on', attributes: { friendly_name: 'Other Thing update', installed_version: '2.0.0', latest_version: '2.1.0' } },
    'update.blocked': { state: 'on', attributes: { friendly_name: 'Microsoft 365 Calendar update', installed_version: '1.13.1', latest_version: '1.14.0' } },
    'update.mystery': { state: 'on', attributes: { friendly_name: 'Dad Jokes update', installed_version: '1.4.0', latest_version: '1.5.0' } },
  };
  const serviceCalls = [];
  card._hass = {
    states,
    config: { version: '2026.10.4' },
    callWS: async () => ({
      'update.ok': { domain: 'hacs' }, 'update.ok2': { domain: 'hacs' },
      'update.blocked': { domain: 'hacs' }, 'update.mystery': { domain: 'hacs' },
    }),
    callService: async (domain, service, data) => { serviceCalls.push([domain, service, data]); },
  };
  // Keep expanding a row offline: _fetchReleaseNotes returns early when set.
  for (const eid of ['update.ok', 'update.ok2', 'update.blocked', 'update.mystery']) {
    card._releaseNotes[eid] = 'notes';
  }
  return { card, states, serviceCalls };
}

(async () => {
  /* 1. Hidden by default -------------------------------------------------- */
  const { card, serviceCalls } = makeCard();
  await card._doRender();
  let html = card.shadowRoot.innerHTML;

  assert.doesNotMatch(html, /update\.blocked/, 'a blocked update must not be listed by default');
  assert.match(html, /3 updates/, 'the pending badge counts the listed rows');
  assert.match(html, /1 incompatible/, 'the header says how many are held back');
  assert.match(html, /Show incompatible/);
  assert.match(html, /Hidden: they need a newer Home Assistant than 2026\.10\.4\./);
  // Two met, one unknown: bulk names its own scope rather than saying "all".
  assert.match(html, /Update 2 of 3/);
  assert.match(html, /1 skipped — requirement unknown/);
  assert.doesNotMatch(html, /Update all/);
  assert.deepEqual(Array.from(card._eligibleEntities), ['update.ok', 'update.ok2']);

  // Every listed row states its requirement; none is silent.
  assert.match(html, /Needs Home Assistant 2025\.6\.0 — you have it/);
  assert.match(html, /Requirement unknown/);

  /* 2. Revealed ----------------------------------------------------------- */
  card._showIncompatible = true;
  await card._doRender();
  html = card.shadowRoot.innerHTML;
  assert.match(html, /update\.blocked/, 'revealing lists the blocked row');
  assert.match(html, /Showing incompatible/);
  assert.match(html, /They cannot be installed from this card\./);
  assert.match(html, /Needs Home Assistant 2026\.11\.0 — you have 2026\.10\.4/);
  assert.match(html, /item blocked/);
  // Revealing never grows the eligible set.
  assert.deepEqual(Array.from(card._eligibleEntities), ['update.ok', 'update.ok2']);
  assert.match(html, /Update 2 of 4/);
  assert.match(html, /1 blocked/);
  assert.match(html, /1 requirement unknown/);

  /* 3. A blocked row offers no install at all ----------------------------- */
  card._expandedEntity = 'update.blocked';
  await card._doRender();
  html = card.shadowRoot.innerHTML;
  const blockedRow = rowOf(html, 'update.blocked');
  assert.match(blockedRow, /Version 1\.14\.0 needs Home Assistant 2026\.11\.0\./);
  assert.match(blockedRow, /Installing is blocked until Home Assistant is 2026\.11\.0 or newer\./);
  assert.match(blockedRow, /btn-skip/);
  // No Update button at all — not a disabled one.
  assert.doesNotMatch(blockedRow, /data-action="update"/);
  assert.doesNotMatch(blockedRow, /data-action="update_action"/);
  assert.equal(card._installAllowed('update.blocked'), false);
  await card._handleUpdate('update.blocked');
  await card._handleUpdateAndAction('update.blocked');
  assert.equal(serviceCalls.length, 0, 'a blocked update is never installed from the card');

  /* 4. An unknown requirement installs only after one tick ---------------- */
  card._expandedEntity = 'update.mystery';
  await card._doRender();
  html = card.shadowRoot.innerHTML;
  assert.match(html, /Version 1\.5\.0 does not say which Home Assistant it needs\./);
  assert.match(html, /names no minimum Home Assistant version/);
  assert.match(html, /I understand the requirement is unknown — install 1\.5\.0 anyway/);
  assert.match(html, /btn btn-update" data-action="update" data-entity="update\.mystery" disabled/);
  assert.equal(card._installAllowed('update.mystery'), false);

  card._ackUnknown['update.mystery'] = true;
  await card._doRender();
  html = card.shadowRoot.innerHTML;
  assert.match(html, /btn btn-update" data-action="update" data-entity="update\.mystery">/);
  assert.match(html, /box checked/);
  assert.equal(card._installAllowed('update.mystery'), true);

  // Closing the row forgets the tick, so it cannot carry over.
  card._toggleExpand('update.mystery');
  assert.deepEqual(Object.keys(card._ackUnknown), []);
  assert.equal(card._installAllowed('update.mystery'), false);

  /* 5. Bulk always passes the explicit eligible set ------------------------ */
  await card._doRender();
  await card._handleUpdateAll();
  assert.equal(serviceCalls.length, 1);
  assert.equal(JSON.stringify(serviceCalls[0]), JSON.stringify([
    'custom_component_monitor', 'update_all', { entity_ids: ['update.ok', 'update.ok2'] },
  ]));

  /* 6. Everything blocked: say so rather than showing an empty card -------- */
  const only = makeCard();
  const attrs = only.states['sensor.hacs_updates'].attributes;
  attrs.updates = attrs.updates.filter((u) => u.entity_id === 'update.blocked');
  for (const eid of ['update.ok', 'update.ok2', 'update.mystery']) delete only.states[eid];
  await only.card._doRender();
  html = only.card.shadowRoot.innerHTML;
  assert.match(html, /Every pending update needs a newer Home Assistant/);
  assert.match(html, /Show incompatible/);
  assert.match(html, /None installable/, 'the badge never claims "Up to date" here');
  // Past the inline stylesheet: .btn-update-all is defined there regardless.
  const markup = html.slice(html.indexOf('</style>'));
  assert.doesNotMatch(markup, /btn-update-all/, 'no bulk button when nothing is listed');
  assert.deepEqual(Array.from(only.card._eligibleEntities), []);

  /* 7. Two rows claiming one entity: the strict one wins ------------------ */
  const clash = makeCard();
  const clashAttrs = clash.states['sensor.hacs_updates'].attributes;
  // A fork beside its original: both rows carry the same entity_id, and the
  // permissive one comes first. Believing it would offer the blocked install.
  clashAttrs.updates = [
    { entity_id: 'update.ok', name: 'Fine Thing', ha_requirement: req('met', '2025.6.0', '2026.10.4') },
    { entity_id: 'update.ok', name: 'Fine Thing Fork', ha_requirement: req('blocked', '2026.11.0', '2026.10.4') },
  ];
  for (const eid of ['update.ok2', 'update.blocked', 'update.mystery']) delete clash.states[eid];
  await clash.card._doRender();
  assert.equal(clash.card._installAllowed('update.ok'), false, 'the blocked claim wins');
  assert.deepEqual(Array.from(clash.card._eligibleEntities), []);
  assert.match(clash.card.shadowRoot.innerHTML, /1 incompatible/);

  /* 8. The dialog says "N of M" even when a filter is also on ------------- */
  const dlg = makeCard();
  let asked = '';
  dlg.card._hass.states = dlg.states;
  await dlg.card._doRender();
  dlg.card._typeFilter = 'all';
  // _shownEntities has 3 rows (the blocked one is hidden); 2 are eligible.
  dlg.card._categoryFilter = 'Bug fixes'; // a filter that changes nothing here
  dlg.card._shownEntities = ['update.ok', 'update.ok2', 'update.mystery'];
  dlg.card._eligibleEntities = ['update.ok', 'update.ok2'];
  sandbox.window.confirm = (m) => { asked = m; return false; };
  await dlg.card._handleUpdateAll();
  assert.match(asked, /Install 2 of the 3 listed HACS updates now\? The rest are left out\./);
  sandbox.window.confirm = () => true;

  /* 9. The requirement is part of the state hash -------------------------- */
  const hashed = makeCard();
  const before = hashed.card._computeStateHash();
  hashed.states['sensor.hacs_updates'].attributes.updates[0].ha_requirement =
    req('blocked', '2027.1.0', '2026.10.4');
  assert.notEqual(hashed.card._computeStateHash(), before);

  console.log('Tracker compatibility checks passed');
})();
