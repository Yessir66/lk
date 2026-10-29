import { UNITS, splitInterval, formatInterval, DEFAULT_INTERVAL } from './common.js';

const $ = (id) => document.getElementById(id);
const FALLBACK_ICON = 'icons/16.png';

let tabs = {}; // the refreshed tabs, as in session storage
let current; // the tab the popup was opened on
const rows = new Map(); // tab id -> its row in the list

function send(msg) {
  return chrome.runtime.sendMessage(msg);
}

function fillUnits(select) {
  for (const u of UNITS) select.add(new Option(u.label, u.seconds));
}

function showInterval(input, select, seconds) {
  const { value, unit } = splitInterval(seconds);
  input.value = value;
  select.value = unit.seconds;
}

// The interval typed in, in seconds, or null when it is not at least 1 s.
function readInterval(input, select) {
  const seconds = Math.round(parseFloat(input.value) * Number(select.value));
  const ok = seconds >= 1;
  input.classList.toggle('invalid', !ok);
  return ok ? seconds : null;
}

// Puts the tab's interval in the fields, unless the user is editing them.
function syncInterval(input, select, seconds) {
  const editing = [input, select].includes(document.activeElement);
  if (!editing && readInterval(input, select) !== seconds) showInterval(input, select, seconds);
}

function setIcon(img, url) {
  img.onerror = () => {
    img.onerror = null;
    img.src = FALLBACK_ICON;
  };
  img.src = url || FALLBACK_ICON;
}

function countdown(next) {
  const s = Math.max(0, Math.ceil((next - Date.now()) / 1000));
  if (s < 60) return `${s} s`;
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const pad = (n) => String(n).padStart(2, '0');
  return h ? `${h} h ${pad(m)}` : `${m} min ${pad(s % 60)}`;
}

async function goTo(id) {
  const tab = await chrome.tabs.update(id, { active: true });
  await chrome.windows.update(tab.windowId, { focused: true });
  window.close();
}

function makeRow(id) {
  const li = $('row').content.firstElementChild.cloneNode(true);
  const row = {
    li,
    icon: li.querySelector('.favicon'),
    title: li.querySelector('.title'),
    value: li.querySelector('input'),
    unit: li.querySelector('select'),
    countdown: li.querySelector('.countdown'),
  };
  fillUnits(row.unit);
  row.title.onclick = (e) => {
    e.preventDefault();
    goTo(id);
  };
  const apply = () => {
    const interval = readInterval(row.value, row.unit);
    if (interval) send({ type: 'add', tabId: id, interval });
  };
  row.value.onchange = apply;
  row.unit.onchange = apply;
  li.querySelector('.remove').onclick = () => send({ type: 'remove', tabId: id });
  return row;
}

async function render() {
  const shown = tabs;
  const ids = Object.keys(shown).map(Number);
  const infos = await Promise.all(ids.map((id) => chrome.tabs.get(id).catch(() => null)));
  const cur = await chrome.tabs.get(current.id).catch(() => current);
  if (shown !== tabs) return; // the list changed meanwhile: a newer render() draws it

  // Current tab
  const tracked = shown[current.id];
  setIcon($('cur-icon'), cur.favIconUrl);
  $('cur-title').textContent = cur.title || cur.url;
  $('cur-title').title = cur.url || '';
  $('cur-toggle').textContent = tracked ? 'Arrêter' : 'Démarrer';
  $('cur-toggle').classList.toggle('stop', !!tracked);
  if (tracked) syncInterval($('cur-value'), $('cur-unit'), tracked.interval);

  // List
  for (const [id, row] of rows) {
    if (shown[id]) continue;
    row.li.remove();
    rows.delete(id);
  }
  ids.forEach((id, i) => {
    let row = rows.get(id);
    if (!row) {
      row = makeRow(id);
      rows.set(id, row);
      $('list').append(row.li);
    }
    const info = infos[i];
    if (info) {
      setIcon(row.icon, info.favIconUrl);
      row.title.textContent = info.title || info.url;
    }
    row.li.classList.toggle('current', id === current.id);
    syncInterval(row.value, row.unit, shown[id].interval);
  });
  $('count').textContent = ids.length ? `(${ids.length})` : '';
  $('empty').hidden = ids.length > 0;
  tick();
}

function tick() {
  for (const [id, row] of rows) {
    if (tabs[id]) row.countdown.textContent = `· dans ${countdown(tabs[id].next)}`;
  }
  const tracked = tabs[current.id];
  $('cur-status').textContent = tracked
    ? `Actualisé toutes les ${formatInterval(tracked.interval)} · prochaine fois dans ${countdown(tracked.next)}`
    : "Cet onglet n'est pas actualisé.";
}

async function toggleCurrent() {
  if (tabs[current.id]) return send({ type: 'remove', tabId: current.id });
  const interval = readInterval($('cur-value'), $('cur-unit'));
  if (!interval) return $('cur-value').focus();
  await chrome.storage.local.set({ lastInterval: interval });
  await send({ type: 'add', tabId: current.id, interval });
}

// A new interval for the current tab: applied at once if it is refreshed, and kept for the next tab (and the
// keyboard shortcut).
async function changeCurrentInterval() {
  const interval = readInterval($('cur-value'), $('cur-unit'));
  if (!interval) return;
  await chrome.storage.local.set({ lastInterval: interval });
  if (tabs[current.id]) await send({ type: 'add', tabId: current.id, interval });
}

async function showShortcut() {
  const command = (await chrome.commands.getAll()).find((c) => c.name === 'toggle-tab');
  const footer = $('shortcut');
  if (command?.shortcut) {
    footer.append('Raccourci ');
    footer.append(Object.assign(document.createElement('kbd'), { textContent: command.shortcut }));
    footer.append(" : ajoute / retire l'onglet · ");
  } else {
    footer.append('Pas de raccourci clavier · ');
  }
  const link = Object.assign(document.createElement('a'), { href: '#', textContent: 'modifier' });
  link.onclick = (e) => {
    e.preventDefault();
    chrome.tabs.create({ url: 'chrome://extensions/shortcuts' });
  };
  footer.append(link);
}

async function main() {
  [current] = await chrome.tabs.query({ active: true, currentWindow: true });
  fillUnits($('cur-unit'));
  const { lastInterval = DEFAULT_INTERVAL } = await chrome.storage.local.get('lastInterval');
  showInterval($('cur-value'), $('cur-unit'), lastInterval);

  $('cur-toggle').onclick = toggleCurrent;
  $('cur-value').onchange = changeCurrentInterval;
  $('cur-unit').onchange = changeCurrentInterval;
  $('cur-value').onkeydown = (e) => {
    if (e.key === 'Enter' && !tabs[current.id]) toggleCurrent();
  };

  tabs = (await chrome.storage.session.get('tabs')).tabs ?? {};
  chrome.storage.session.onChanged.addListener((changes) => {
    if (!changes.tabs) return;
    tabs = changes.tabs.newValue ?? {};
    render();
  });
  await render();
  setInterval(tick, 1000);
  showShortcut();
}

main();
