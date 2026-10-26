// Posts each alert (and each report on what the page shows) from content.js to alert_scout.py on this PC
// (the page itself may not reach 127.0.0.1). config.js is loaded here too: an error in it shows on the
// extension's card in chrome://extensions instead of failing silently in the Discord tab.
importScripts("config.js");

function post(msg) {
  return fetch(`http://127.0.0.1:${SCOUT_PORT}/alert`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(msg),
  })
    .then(r => r.text())
    .then(t => console.log(`${msg.kind || "alerte"} ${msg.id || msg.channel || ""} -> alert_scout.py : ${t}`))
    .catch(e => console.warn(`alert_scout.py injoignable (lancé ?) : ${e.message}`));
}

post({kind: "sw", channels: SCOUT_CHANNELS});         // each time Chrome starts the extension
chrome.runtime.onMessage.addListener(msg => { post(msg); });
