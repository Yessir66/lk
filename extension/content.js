// Reads the alert channel as Discord shows it and hands each new message holding a Solana address to
// background.js, which posts it to alert_scout.py. Nothing is clicked or sent on Discord.
// Works from DOM mutations only (no timers): Chrome throttles timers in background tabs, not mutation callbacks.
(() => {
  const MESSAGE = 'li[id^="chat-messages-"]';
  const ADDRESS = /[1-9A-HJ-NP-Za-km-z]{32,44}/;
  const MAX_AGE_MS = 3 * 60 * 1000;   // older messages are history (channel opened, scrolled up)
  const done = new Set();

  function channelOf(li) {
    const m = li.id.match(/^chat-messages-(\d+)-(\d+)$/);
    return m ? m[1] : null;
  }

  function read(li) {
    if (done.has(li.id)) return;
    const channel = channelOf(li);
    if (!channel || (SCOUT_CHANNELS.length && !SCOUT_CHANNELS.includes(channel))) return;
    const time = li.querySelector("time[datetime]");
    const ts = time ? Date.parse(time.getAttribute("datetime")) : Date.now();
    if (Date.now() - ts > MAX_AGE_MS) {
      done.add(li.id);
      return;
    }
    const text = li.innerText || "";
    const links = [...li.querySelectorAll("a[href]")].map(a => a.href);
    if (!ADDRESS.test(text + " " + links.join(" "))) return;   // embed not drawn yet: read again on its next mutation
    done.add(li.id);
    chrome.runtime.sendMessage({id: li.id, channel, text, links, ts, port: SCOUT_PORT});
  }

  document.querySelectorAll(MESSAGE).forEach(li => done.add(li.id));   // on screen at load: history
  new MutationObserver(mutations => {
    const touched = new Set();
    for (const m of mutations) {
      const el = m.target.nodeType === 1 ? m.target : m.target.parentElement;
      const li = el && el.closest ? el.closest(MESSAGE) : null;
      if (li) touched.add(li);
      for (const n of m.addedNodes) {
        if (n.nodeType !== 1) continue;
        if (n.matches(MESSAGE)) touched.add(n);
        n.querySelectorAll(MESSAGE).forEach(x => touched.add(x));
      }
    }
    touched.forEach(read);
  }).observe(document.body, {childList: true, subtree: true, characterData: true});
})();
