// Offline ownership checks; browser rendering is verified separately against DEV.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
const sandbox = {
  HTMLElement: class { attachShadow() { this.shadowRoot = {}; } },
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
  console.log('Tracker ownership, migration, unavailable and retry checks passed');
})();
