// Posts each alert (and each report on what the page shows) from content.js to alert_scout.py on this PC
// (the page itself may not reach 127.0.0.1).
chrome.runtime.onMessage.addListener(msg => {
  fetch(`http://127.0.0.1:${msg.port}/alert`, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(msg),
  })
    .then(r => r.text())
    .then(t => console.log(`${msg.kind || "alerte"} ${msg.id || msg.channel || ""} -> alert_scout.py : ${t}`))
    .catch(e => console.warn(`alert_scout.py injoignable (lancé ?) : ${e.message}`));
});
