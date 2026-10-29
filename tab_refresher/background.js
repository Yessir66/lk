import { badgeText, DEFAULT_INTERVAL } from './common.js';

const ALARM = 'refresh';

chrome.action.setBadgeBackgroundColor({ color: '#1a73e8' });
chrome.action.setBadgeTextColor?.({ color: '#ffffff' });

// The refreshed tabs: { [tabId]: { interval: seconds, next: time of the next reload in ms } }. Kept in session
// storage, which Chrome empties when it closes, like the tab ids.
async function loadTabs() {
  return (await chrome.storage.session.get('tabs')).tabs ?? {};
}

// Every change to the list goes through here, one at a time, so that a reload and a click in the popup never
// overwrite each other. One alarm wakes the extension for the next reload due.
let queue = Promise.resolve();
function update(change) {
  const run = queue.then(async () => {
    const tabs = await loadTabs();
    await change(tabs);
    await chrome.storage.session.set({ tabs });
    const next = Math.min(...Object.values(tabs).map((t) => t.next));
    if (next < Infinity) await chrome.alarms.create(ALARM, { when: next });
    else await chrome.alarms.clear(ALARM);
  });
  queue = run.catch(console.error);
  return run;
}

function showBadge(tabId, interval) {
  chrome.action.setBadgeText({ tabId, text: interval ? badgeText(interval) : '' }).catch(() => {});
}

function add(tabId, interval) {
  showBadge(tabId, interval);
  return update((tabs) => {
    if (tabs[tabId]?.interval === interval) return; // same interval: the countdown goes on
    tabs[tabId] = { interval, next: Date.now() + interval * 1000 };
  });
}

function remove(tabId) {
  showBadge(tabId, 0);
  return update((tabs) => {
    delete tabs[tabId];
  });
}

async function reloadDue(tabs) {
  const now = Date.now();
  for (const [id, tab] of Object.entries(tabs)) {
    if (tab.next > now + 100) continue;
    try {
      await chrome.tabs.reload(Number(id));
      // From now rather than from tab.next: after a sleep, one reload and not one per missed interval.
      tab.next = now + tab.interval * 1000;
    } catch {
      delete tabs[id]; // closed
    }
  }
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) update(reloadDue);
});

// From the popup: { type: 'add', tabId, interval } or { type: 'remove', tabId }.
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  const done = msg.type === 'add' ? add(msg.tabId, msg.interval) : remove(msg.tabId);
  done.then(() => reply(true), () => reply(false));
  return true;
});

chrome.commands.onCommand.addListener(async (command, tab) => {
  if (command !== 'toggle-tab' || !tab) return;
  if ((await loadTabs())[tab.id]) return remove(tab.id);
  const { lastInterval = DEFAULT_INTERVAL } = await chrome.storage.local.get('lastInterval');
  add(tab.id, lastInterval);
});

chrome.tabs.onRemoved.addListener((tabId) => {
  update((tabs) => {
    delete tabs[tabId];
  });
});

// Chrome swaps a tab for another one when it shows a page it had preloaded.
chrome.tabs.onReplaced.addListener((addedId, removedId) => {
  update((tabs) => {
    if (!tabs[removedId]) return;
    tabs[addedId] = tabs[removedId];
    delete tabs[removedId];
    showBadge(addedId, tabs[addedId].interval);
  });
});

// A tab's badge is cleared each time it loads a page.
chrome.tabs.onUpdated.addListener(async (tabId, info) => {
  if (info.status !== 'complete') return;
  const tab = (await loadTabs())[tabId];
  if (tab) showBadge(tabId, tab.interval);
});
