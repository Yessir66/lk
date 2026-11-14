import {ChartController, validBuy, keyFor, isGmgn} from './core.js';

const charts = new ChartController(chrome);
let port, reconnect, poll, chain = Promise.resolve();
let awaitingReply = false;
let lastBridge = '';

async function status(patch) {
  const {status: previous = {}} = await chrome.storage.local.get('status');
  await chrome.storage.local.set({status: {...previous, ...patch}});
}

async function handleBuy(event) {
  if (!validBuy(event)) return;
  const {seen = [], enabled = true} = await chrome.storage.local.get(['seen', 'enabled']);
  if (seen.includes(keyFor(event))) return;
  if (!enabled) {
    await status({mint: event.mint, mode: 'En pause : graphique conserve'});
  } else {
    const started = performance.now();
    const result = await charts.show(event.mint);
    await status({mint: event.mint, mode: result.mode, requestedAt: Date.now(),
      localMs: Math.round(performance.now() - started)});
  }
  await chrome.storage.local.set({seen: [...seen, keyFor(event)].slice(-2048)});
}

function connect() {
  clearTimeout(reconnect);
  if (port) return;
  try {
    port = chrome.runtime.connectNative('fr.walletalerts.gmgn');
    port.onMessage.addListener(message => {
      awaitingReply = false;
      const bridge = message.ok ? 'Connexion locale OK' : 'Lecture locale indisponible';
      if (bridge !== lastBridge) { lastBridge = bridge; void status({bridge}); }
      if (message.ok && Array.isArray(message.events)) {
        for (const event of message.events) {
          chain = chain.then(() => handleBuy(event)).catch(() => status({mode: 'Affichage impossible : verifier GMGN'}));
        }
      }
    });
    port.onDisconnect.addListener(() => {
      // Read lastError here so Chrome does not log an unchecked native-host error.
      const error = chrome.runtime.lastError;
      port = null; awaitingReply = false; lastBridge = ''; clearInterval(poll);
      void status({bridge: 'Pont local absent : lancer INSTALLER_EXTENSION_GMGN.cmd'});
      chrome.action.setBadgeText({text: 'OFF'});
      reconnect = setTimeout(connect, 5000);
      void error;
    });
    poll = setInterval(() => {
      if (!port || awaitingReply) return;
      awaitingReply = true;
      try { port.postMessage({action: 'poll'}); } catch { awaitingReply = false; }
    }, 100);
    chrome.action.setBadgeText({text: '1'});
    chrome.action.setBadgeBackgroundColor({color: '#477a34'});
  } catch {
    void status({bridge: 'Connexion locale impossible'});
    reconnect = setTimeout(connect, 5000);
  }
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message.action === 'reserveActive') {
    void (async () => {
      const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
      if (!isGmgn(tab?.url)) return reply({ok: false});
      await chrome.storage.local.set({gmgnTabId: tab.id});
      reply({ok: true});
    })().catch(() => reply({ok: false}));
    return true;
  }
  if (message.action === 'prepare') {
    void charts.ensureTab().then(() => reply({ok: true})).catch(() => reply({ok: false}));
    connect();
    return true;
  }
});

chrome.runtime.onInstalled.addListener(() => { void charts.ensureTab().catch(() => {}); connect(); });
chrome.runtime.onStartup.addListener(() => { void charts.ensureTab().catch(() => {}); connect(); });
connect();
