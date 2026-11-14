import assert from 'node:assert/strict';
import test from 'node:test';
import {ChartController, validBuy, mintFromUrl, navigateInPage} from '../chrome-gmgn/core.js';

const mintA = 'A'.repeat(32), mintB = 'B'.repeat(32);
function fakeChrome(initial = [], internal = 'unavailable') {
  const state = {}, tabs = new Map(initial.map(tab => [tab.id, {...tab}]));
  const created = [], updated = [], injected = [];
  const chrome = {
    storage: {local: {
      async get() { return {...state}; }, async set(values) { Object.assign(state, values); }
    }},
    tabs: {
      async get(id) { if (!tabs.has(id)) throw Error('Closed tab'); return {...tabs.get(id)}; },
      async query() { return [...tabs.values()].filter(tab => tab.url.startsWith('https://gmgn.ai/')); },
      async create(options) {
        const tab = {id: Math.max(0, ...tabs.keys()) + 1, ...options};
        tabs.set(tab.id, tab); created.push(options); return {...tab};
      },
      async update(id, options) { Object.assign(tabs.get(id), options); updated.push({id, ...options}); }
    },
    scripting: {async executeScript(options) {
      injected.push(options);
      if (internal === 'router') tabs.get(options.target.tabId).url = 'https://gmgn.ai/sol/token/' + options.args[0];
      return [{result: internal}];
    }}
  };
  return {chrome, state, tabs, created, updated, injected};
}

test('Repeated buys of the current token never reload or inject', async () => {
  const mock = fakeChrome([{id: 5, url: 'https://gmgn.ai/sol/token/' + mintA, active: true}]);
  const controller = new ChartController(mock.chrome);
  await controller.show(mintA); await controller.show(mintA);
  assert.equal(mock.created.length, 0); assert.equal(mock.injected.length, 0);
  assert.equal(mock.updated.filter(options => 'url' in options).length, 0);
  assert.equal(mock.state.gmgnTabId, 5);
});

test('Different tokens use a single tab and a safe fallback if no router exists', async () => {
  const mock = fakeChrome(); const controller = new ChartController(mock.chrome);
  await controller.show(mintA); await controller.show(mintB); await controller.show(mintB);
  assert.equal(mock.created.length, 1);
  assert.equal(mock.updated.filter(options => 'url' in options).length, 2);
  assert.equal(new Set(mock.updated.map(options => options.id)).size, 1);
});

test('A supported internal router avoids tabs.update URL navigation', async () => {
  const mock = fakeChrome([{id: 1, url: 'https://gmgn.ai/sol', active: true}], 'router');
  const result = await new ChartController(mock.chrome).show(mintA);
  assert.equal(result.mode, 'Navigation interne demandee');
  assert.equal(mock.updated.filter(options => 'url' in options).length, 0);
});

test('A router which does not change the URL falls back in the same tab', async () => {
  const mock = fakeChrome([{id: 1, url: 'https://gmgn.ai/sol', active: true}], 'link');
  const waits = [];
  await new ChartController(mock.chrome, async ms => waits.push(ms)).show(mintA);
  assert.equal(waits.length, 10);
  assert.equal(mock.updated.filter(options => 'url' in options).length, 1);
});

test('A pending navigation to this mint is not restarted', async () => {
  const mock = fakeChrome([{id: 1, url: 'https://gmgn.ai/sol', pendingUrl: 'https://gmgn.ai/sol/token/' + mintA}]);
  await new ChartController(mock.chrome).show(mintA);
  assert.equal(mock.injected.length, 0); assert.equal(mock.updated.filter(options => 'url' in options).length, 0);
});

test('A reserved tab navigated outside GMGN is not taken over', async () => {
  const mock = fakeChrome([{id: 4, url: 'https://example.org/'}]); mock.state.gmgnTabId = 4;
  await new ChartController(mock.chrome).show(mintA);
  assert.equal(mock.created.length, 1); assert.equal(mock.tabs.get(4).url, 'https://example.org/');
});

test('Only valid and recent verified buys enter the controller', async () => {
  const event = {v: 1, type: 'verified_buy', mint: mintA, wallet: mintB, signature: 'C'.repeat(88), detected_at: 1000};
  assert.ok(validBuy(event, 1001));
  for (const bad of [{...event, type: 'sell'}, {...event, mint: '../trade'}, {...event, detected_at: 1}]) {
    assert.equal(validBuy(bad, 1001), false);
  }
  assert.equal(mintFromUrl('https://other.example/sol/token/' + mintA), null);
  const mock = fakeChrome();
  await assert.rejects(new ChartController(mock.chrome).show('javascript:alert(1)'));
  assert.equal(mock.created.length, 0);
});

test('Injected navigation calls only the router and retains an already displayed mint', () => {
  globalThis.location = {origin: 'https://gmgn.ai', pathname: '/sol', href: 'https://gmgn.ai/sol'};
  const pushed = []; globalThis.window = {next: {router: {push: path => pushed.push(path)}}};
  assert.equal(navigateInPage(mintA), 'router'); assert.deepEqual(pushed, ['/sol/token/' + mintA]);
  location.pathname = '/sol/token/' + mintA;
  assert.equal(navigateInPage(mintA), 'same'); assert.equal(pushed.length, 1);
  delete globalThis.location; delete globalThis.window;
});
