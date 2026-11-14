// Offline ownership checks; browser rendering is verified separately against DEV.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const sandbox = {
  // querySelector* are stubbed empty: the render paths under test are asserted
  // on the produced HTML, and event binding has nothing to bind to here.
  HTMLElement: class { attachShadow() { this.shadowRoot = { querySelector: () => null, querySelectorAll: () => [] }; } },
  customElements: { get: n => elements.get(n), define: (n, c) => elements.set(n, c) },
  window: { customCards: [] }, console: { info() {} },
  document: { createElement() { return { set textContent(v) { this.innerHTML = v; } }; } },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync('custom_components/custom_component_monitor/www/update-action-tracker-card.js', 'utf8'), sandbox);
(async () => {
  const card = new (elements.get('update-action-tracker-card'))();
  let requests = 0;
  const states = {
    'sensor.hacs_updates': { state: '2', attributes: { component_provider: 'marketplace' } },
    'update.legacy': { state: 'on', attributes: {} },
    'update.market': { state: 'on', attributes: {} },
    'update.core': { state: 'on', attributes: { entity_picture: 'https://brands.home-assistant.io/core/icon.png' } },
  };
  card._hass = { states, callWS: async () => { requests++; return {
    'update.legacy': { domain: 'hacs' }, 'update.market': { domain: 'marketplace' },
    'update.core': { domain: 'homeassistant' },
  }; } };
  assert.deepEqual(Array.from(await card._getHacsUpdateEntities()), ['update.legacy', 'update.market']);
  await card._getHacsUpdateEntities();
  assert.equal(requests, 1);
  states['update.added'] = { state: 'on', attributes: {} };
  await card._getHacsUpdateEntities();
  assert.equal(requests, 2);
  states['sensor.hacs_updates'].attributes.component_provider = 'hacs';
  await card._getHacsUpdateEntities();
  assert.equal(requests, 3);
  // #114: a failed scan keeps the last good rows and marks them stale, rather
  // than taking the sensor unavailable and blanking the card.
  const attrs = states['sensor.hacs_updates'].attributes;
  attrs.data_stale = true;
  attrs.last_successful_scan = new Date(Date.now() - 7200000).toISOString();
  attrs.scan_error = 'HACS is starting; component data will retry automatically.';
  const staleHash = card._computeStateHash();
  await card._doRender();
  assert.match(card.shadowRoot.innerHTML, /Stale/);
  assert.match(card.shadowRoot.innerHTML, /2 hours ago/);
  assert.match(card.shadowRoot.innerHTML, /retry automatically/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Unavailable/);
  attrs.data_stale = false;
  assert.notEqual(card._computeStateHash(), staleHash);
  await card._doRender();
  assert.doesNotMatch(card.shadowRoot.innerHTML, /Stale/);
  card._hacsEntities = null;
  card._hass.callWS = async () => { throw Error('offline'); };
  await assert.rejects(card._getHacsUpdateEntities(), /offline/);
  const availableHash = card._computeStateHash();
  states['sensor.hacs_updates'].state = 'unavailable';
  assert.notEqual(card._computeStateHash(), availableHash);
  await card._doRender();
  assert.match(card.shadowRoot.innerHTML, /Unavailable/);
  assert.doesNotMatch(card.shadowRoot.innerHTML, /All custom components are up to date/);
  states['sensor.hacs_updates'].state = '2';
  await card._doRender();
  assert.match(card.shadowRoot.innerHTML, /Unavailable/);
  assert.equal(card._lastStateHash, '');
  console.log('Tracker ownership, migration, stale, unavailable and retry checks passed');
})();
