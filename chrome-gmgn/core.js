export const TOKEN = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
export const SIGNATURE = /^[1-9A-HJ-NP-Za-km-z]{64,100}$/;
export const ROOT = 'https://gmgn.ai';

export function isGmgn(url) {
  try { return new URL(url).origin === ROOT; } catch { return false; }
}

export function mintFromUrl(url) {
  if (!isGmgn(url)) return null;
  const mint = new URL(url).pathname.match(/^\/sol\/token\/([^/]+)\/?$/)?.[1];
  return TOKEN.test(mint || '') ? mint : null;
}

export function validBuy(event, now = Date.now() / 1000) {
  return event?.v === 1 && event.type === 'verified_buy' && TOKEN.test(event.mint || '') &&
    TOKEN.test(event.wallet || '') && SIGNATURE.test(event.signature || '') &&
    Number.isFinite(event.detected_at) && now - event.detected_at >= -5 && now - event.detected_at <= 120;
}

export function keyFor(event) { return event.signature + '|' + event.mint; }

// Runs in GMGN's page context. It never touches a trading form or a buy/sell button.
export function navigateInPage(mint) {
  if (!/^[1-9A-HJ-NP-Za-km-z]{32,44}$/.test(mint) || location.origin !== 'https://gmgn.ai') return 'unavailable';
  const target = '/sol/token/' + mint;
  if (location.pathname.replace(/\/$/, '') === target) return 'same';
  const router = window.next?.router;
  if (typeof router?.push === 'function') {
    try {
      const pending = router.push(target);
      if (pending?.catch) pending.catch(() => {});
      return 'router';
    } catch { /* Fall back to a matching link or a normal navigation. */ }
  }
  for (const anchor of document.querySelectorAll('a[href]')) {
    try {
      const url = new URL(anchor.href, location.href);
      if (url.origin === location.origin && url.pathname.replace(/\/$/, '') === target &&
          !anchor.download && (!anchor.target || anchor.target === '_self')) {
        anchor.click();
        return 'link';
      }
    } catch { /* Ignore malformed links. */ }
  }
  return 'unavailable';
}

export class ChartController {
  constructor(chrome, sleep = ms => new Promise(resolve => setTimeout(resolve, ms))) {
    this.chrome = chrome;
    this.sleep = sleep;
  }

  async ensureTab() {
    const {gmgnTabId} = await this.chrome.storage.local.get('gmgnTabId');
    if (Number.isInteger(gmgnTabId)) {
      try {
        const tab = await this.chrome.tabs.get(gmgnTabId);
        if (isGmgn(tab.pendingUrl || tab.url)) {
          if (tab.autoDiscardable !== false) await this.chrome.tabs.update(tab.id, {autoDiscardable: false});
          return tab;
        }
      } catch { /* Reserved tab was closed. */ }
    }
    const existing = await this.chrome.tabs.query({url: 'https://gmgn.ai/*'});
    const tab = existing.find(item => item.active) || existing[0] ||
      await this.chrome.tabs.create({url: ROOT + '/sol', active: true});
    await this.chrome.storage.local.set({gmgnTabId: tab.id});
    await this.chrome.tabs.update(tab.id, {autoDiscardable: false});
    return tab;
  }

  async show(mint) {
    if (!TOKEN.test(mint)) throw new Error('CA invalide');
    const tab = await this.ensureTab();
    const current = tab.pendingUrl || tab.url;
    if (mintFromUrl(current) === mint) {
      await this.chrome.tabs.update(tab.id, {active: true});
      return {mode: 'Graphique conserve, aucun rechargement', tabId: tab.id};
    }
    let mode = 'unavailable';
    // Do not inject into an old page while the tab is already navigating elsewhere.
    if (!tab.pendingUrl && isGmgn(tab.url)) {
      try {
        const results = await this.chrome.scripting.executeScript({
          target: {tabId: tab.id}, world: 'MAIN', func: navigateInPage, args: [mint]
        });
        mode = results[0]?.result || 'unavailable';
      } catch { /* Injection can fail during an in-flight page load. */ }
    }
    if (mode === 'same') {
      await this.chrome.tabs.update(tab.id, {active: true});
      return {mode: 'Graphique conserve, aucun rechargement', tabId: tab.id};
    }
    if (mode === 'router' || mode === 'link') {
      for (let i = 0; i < 10; i++) {
        const updated = await this.chrome.tabs.get(tab.id);
        if (mintFromUrl(updated.pendingUrl || updated.url) === mint) {
          await this.chrome.tabs.update(tab.id, {active: true});
          return {mode: mode === 'router' ? 'Navigation interne demandee' : 'Lien GMGN utilise', tabId: tab.id};
        }
        await this.sleep(100);
      }
    }
    await this.chrome.tabs.update(tab.id, {url: ROOT + '/sol/token/' + mint, active: true});
    return {mode: 'Meme onglet, chargement classique', tabId: tab.id};
  }
}
