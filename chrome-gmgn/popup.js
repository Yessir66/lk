async function refresh() {
  const {status = {}, enabled = true} = await chrome.storage.local.get(['status', 'enabled']);
  document.querySelector('#bridge').textContent = status.bridge || 'Connexion au pont local…';
  document.querySelector('#mode').textContent = status.mode || 'En attente d’un achat verifie';
  document.querySelector('#mint').textContent = status.mint || '';
  document.querySelector('#timing').textContent = Number.isFinite(status.localMs) ?
    'Commande d’affichage : ' + status.localMs + ' ms, hors chargement du graphique.' : '';
  document.querySelector('#pause').textContent = enabled ? 'Mettre en pause les changements' : 'Reprendre les changements';
}
for (const [id, action] of [['prepare', 'prepare'], ['reserve', 'reserveActive']]) {
  document.querySelector('#' + id).onclick = async () => {
    const result = await chrome.runtime.sendMessage({action});
    document.querySelector('#feedback').textContent = result?.ok ? 'Onglet pret.' : 'Selectionne d’abord un onglet GMGN.';
  };
}
document.querySelector('#pause').onclick = async () => {
  const {enabled = true} = await chrome.storage.local.get('enabled');
  await chrome.storage.local.set({enabled: !enabled});
  await refresh();
};
chrome.storage.onChanged.addListener(refresh);
await refresh();
