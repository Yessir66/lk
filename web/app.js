"use strict";
const $ = (id) => document.getElementById(id);
const arrowIcon =
  '<svg class="icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M5 15 15 5M5 5h10v10"/></svg>';
const playIcon =
  '<svg class="icon play-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="m6 4 10 6-10 6Z"/></svg>';
const pauseIcon =
  '<svg class="icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M7 4v12M13 4v12"/></svg>';
const token = document.querySelector('meta[name="app-token"]').content;
let state = null,
  hydrated = false,
  feedFilter = "all",
  positionView = "open",
  paused = false;
let frozenFeed = [],
  frozenCallers = [],
  detailMint = null,
  errorsOnly = false,
  logCutoff = 0,
  toastTimer;
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const money = (value) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : "$" +
      Number(value).toLocaleString("en-US", {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2,
      });
const compact = (value) =>
  value == null || !Number.isFinite(Number(value))
    ? "MC indisponible"
    : "$" +
      (Number(value) >= 1e6
        ? (Number(value) / 1e6).toFixed(2) + "M"
        : Number(value) >= 1000
          ? (Number(value) / 1000).toFixed(1) + "k"
          : Math.round(Number(value)));
function epoch(value) {
  if (value == null || value === "") return NaN;
  const n = Number(value);
  return Number.isFinite(n)
    ? n > 1e11
      ? n / 1000
      : n
    : Date.parse(value) / 1000;
}
function age(value) {
  const elapsed = Math.max(0, Date.now() / 1000 - epoch(value));
  return !Number.isFinite(elapsed)
    ? "—"
    : elapsed < 60
      ? Math.floor(elapsed) + " s"
      : elapsed < 3600
        ? Math.floor(elapsed / 60) + " min"
        : elapsed < 86400
          ? Math.floor(elapsed / 3600) + " h"
          : Math.floor(elapsed / 86400) + " j";
}
function toast(text, error = false) {
  clearTimeout(toastTimer);
  $("toast").textContent = text;
  $("toast").className = "toast" + (error ? " error" : "");
  $("toast").hidden = false;
  toastTimer = setTimeout(() => ($("toast").hidden = true), 6000);
}
async function request(path, data) {
  const response = await fetch(path, {
    method: data === undefined ? "GET" : "POST",
    headers: { "Content-Type": "application/json", "X-Desk-Token": token },
    ...(data === undefined ? {} : { body: JSON.stringify(data) }),
  });
  const body = await response.json();
  if (!response.ok) throw Error(body.error || "Erreur de connexion locale.");
  return body;
}
function hydrate(config, callers) {
  for (const name of ["profile", "usd", "delay", "take_profit", "max_age"])
    $(name).value = config[name];
  $("slippage").value = config.slippage_bps / 100;
  setMode(config.mode);
  $("callers").innerHTML = callers
    .map(
      (name) =>
        `<label class="caller-item" title="${esc(name)}"><input type="checkbox" name="caller" value="${esc(name)}" ${config.callers.includes(name) ? "checked" : ""}><span>${esc(name)}</span></label>`,
    )
    .join("");
  updateSelection();
}
function setMode(mode) {
  $("mode").value = mode;
  document
    .querySelectorAll("[data-mode]")
    .forEach((b) => b.classList.toggle("selected", b.dataset.mode === mode));
  $("mode-help").textContent =
    mode === "live"
      ? "Achats, publications et Telegram réels."
      : "Lecture réelle du flux. Aucun envoi ni achat.";
  updateStart();
}
function updateSelection() {
  const selected = document.querySelectorAll(
    'input[name="caller"]:checked',
  ).length;
  const all = document.querySelectorAll('input[name="caller"]').length;
  $("selected-count").textContent = `${selected} / ${all}`;
  updateStart();
}
function updateStart() {
  const live = $("mode").value === "live";
  $("start").classList.toggle("real", live);
  $("start").innerHTML =
    playIcon + " " + (live ? "Démarrer en réel" : "Démarrer");
  $("strategy-note").textContent =
    `À +${$("take_profit").value} %, vente de 100 % de la position.`;
  $("empty-tp").textContent = "+" + $("take_profit").value + " %";
}
function configuration() {
  const values = {
    mode: $("mode").value,
    profile: $("profile").value.trim(),
    callers: [...document.querySelectorAll('input[name="caller"]:checked')].map(
      (n) => n.value,
    ),
    slippage_bps: Math.round(Number($("slippage").value) * 100),
  };
  for (const name of ["usd", "delay", "take_profit", "max_age"])
    values[name] = Number($(name).value);
  return values;
}
function phaseUI(s) {
  const text = {
    stopped: "À L’ARRÊT",
    starting: "CONNEXION",
    running: "ACTIF",
    stopping: "ARRÊT EN COURS",
    error: "ERREUR",
  };
  $("status-badge").className =
    "badge " +
    (s.phase === "running"
      ? "active"
      : s.phase === "error"
        ? "error"
        : "muted");
  $("status-badge").innerHTML = "<i></i> " + (text[s.phase] || "À L’ARRÊT");
  const live = (s.running ? s.config.mode : $("mode").value) === "live";
  $("mode-badge").textContent = live ? "RÉEL" : "SIMULATION";
  $("mode-badge").className = "badge " + (live ? "live" : "blue");
  $("start").disabled = s.running;
  $("stop").disabled = !s.running || s.phase === "stopping";
  document
    .querySelectorAll("#settings input,#settings button")
    .forEach((e) => (e.disabled = s.running));
  $("profile-label").textContent = s.config.profile;
  $("journal-status").textContent =
    s.phase === "running"
      ? "Hub en cours d’exécution"
      : s.phase === "starting"
        ? "Connexion aux services…"
        : s.phase === "stopping"
          ? "Arrêt en cours…"
          : "Hub à l’arrêt";
  $("journal-dot").className = "dot" + (s.phase === "running" ? " lime" : "");
  const health = s.health || {};
  const recent = Date.now() / 1000 - (health.at || 0) < 30;
  const connected = s.running && health.feed_ok && recent;
  $("feed-live").className =
    "live-label" + (connected ? " active" : s.running ? " wait" : "");
  $("feed-live").innerHTML =
    "<i></i> " +
    (connected
      ? "EN DIRECT"
      : s.running
        ? health.at
          ? "FLUX EN PAUSE"
          : "CONNEXION"
        : "EN ATTENTE");
  $("feed-foot").textContent = paused
    ? "Affichage en pause · détection active"
    : connected
      ? "Source : pump.fun · flux partagé"
      : "Source : pump.fun";
}
function renderPositions(s) {
  $("metric-positions").innerHTML =
    s.summary.positions + "<span>positions</span>";
  $("metric-cost").textContent = money(s.summary.cost);
  $("metric-pnl").textContent =
    s.summary.pnl == null
      ? "—"
      : (s.summary.pnl >= 0 ? "+" : "−") + money(Math.abs(s.summary.pnl));
  $("metric-pnl").classList.toggle("negative", s.summary.pnl < 0);
  $("position-note").textContent = s.summary.positions
    ? `TP configuré à +${s.config.take_profit} % · vente totale`
    : "Aucune position ouverte";
  $("journal-mode").textContent =
    "Journal " + (s.config.mode === "live" ? "réel" : "de simulation");
  const confirmed = (j) =>
    j.buy_confirmed ||
    ["confirmed", "publishing", "uncertain", "posted"].includes(j.state);
  const jobs = s.jobs.filter(
    (j) =>
      positionView === "all" ||
      (confirmed(j) && !["sold", "empty"].includes(j.exit_state)),
  );
  $("position-count").textContent = jobs.length;
  $("positions-empty").hidden = jobs.length > 0;
  const names = {
    tp_paused: "TP différé",
    watching: "TP en suivi",
    signed: "Vente signée",
    sold: "TP confirmé",
    empty: "Solde nul",
    review: "À vérifier",
    new: "Préparation",
    confirmed: "Achat confirmé",
    publishing: "Publication",
    posted: "Publié",
    failed: "Échec",
    simulated: "Simulé",
    uncertain: "À vérifier",
  };
  $("positions").innerHTML = jobs
    .map((j) => {
      const status =
        j.tp_error && !["sold", "empty", "review"].includes(j.exit_state)
          ? "tp_paused"
          : j.exit_state || j.state;
      const kind = ["review", "failed", "uncertain", "tp_paused"].includes(
        status,
      )
        ? "error"
        : ["sold", "empty", "simulated"].includes(status)
          ? "closed"
          : ["signed", "new", "publishing"].includes(status)
            ? "wait"
            : "";
      const mint = String(j.mint);
      return `<tr><td><div class="token-main"><span class="token-avatar">${esc((j.symbol || j.name || mint)[0])}</span><div><div class="token-name" title="${esc(j.name || mint)}">${esc(j.symbol ? "$" + j.symbol : j.name || mint.slice(0, 7) + "…")}</div><div class="token-caller">${esc(j.caller)}</div><div class="money-sub">MC call : ${esc(compact(j.entry_mc))}</div></div></div></td><td><span class="money">${money(j.cost_usd)}</span><div class="money-sub">≈ ${esc(compact(j.buy_details?.mc_estimate))}</div><div class="money-sub">${age(j.created)}</div></td><td><span class="money">${money(j.mark_usd)}</span><div class="money-sub">${j.mark_checked_at ? "cotée il y a " + age(j.mark_checked_at) : "cotation indisponible"}</div></td><td><span class="state-tag ${kind}" title="${esc(status === "tp_paused" ? j.tp_error : j.failure_reason === "entry_expired" ? "Callout trop ancien pour préparer l’achat" : status === "empty" ? "Solde nul ; ne confirme pas une vente au TP" : status === "simulated" ? "Décision simulée, aucun achat" : names[status] || status)}">${esc(names[status] || status)}</span></td><td><button class="details-button" data-mint="${esc(mint)}" aria-label="Détails de ${esc(j.symbol || j.name || mint)}">Détails ${arrowIcon}</button></td></tr>`;
    })
    .join("");
}
function exactTime(value) {
  const seconds = epoch(value);
  return Number.isFinite(seconds)
    ? new Date(seconds * 1000).toLocaleString("fr-FR", {
        dateStyle: "short",
        timeStyle: "medium",
      })
    : "Heure indisponible";
}
function renderDetails() {
  if (!detailMint || !state) return;
  const j = state.jobs.find((job) => job.mint === detailMint);
  if (!j) {
    $("position-details").textContent =
      "Cette position n’est plus dans la fenêtre d’historique affichée.";
    return;
  }
  $("detail-title").textContent =
    (j.symbol ? "$" + j.symbol : j.name || j.mint.slice(0, 8) + "…") +
    " · " +
    j.caller;
  const buy = j.buy_details,
    sell = j.sell_details;
  const entry = Number(buy?.mc_estimate),
    called = Number(j.entry_mc);
  const gap =
    buy?.mc_estimate != null && j.entry_mc != null && entry > 0 && called > 0
      ? (entry / called - 1) * 100
      : null;
  const txLink = (url, label) => {
    // Only canonical explorer URLs may become clickable, even in an old journal.
    return typeof url === "string" &&
      /^https:\/\/solscan\.io\/tx\/[1-9A-HJ-NP-Za-km-z]{64,88}$/.test(url)
      ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${label} ${arrowIcon}</a>`
      : "";
  };
  const supplyInfo = buy || sell;
  $("position-details").innerHTML = `
    <p class="detail-address">${esc(j.mint)}</p>
    <div class="detail-mc-grid">
      <section><span>MC DU CALLOUT</span><strong>${esc(compact(j.entry_mc))}</strong><small>Rapporté par pump.fun</small><time>${esc(exactTime(j.created))}</time></section>
      <section><span>TON MC D’ENTRÉE · ESTIMÉ</span><strong>${esc(compact(buy?.mc_estimate))}</strong><small>${buy ? "Montant investi / tokens réellement reçus × offre" : "Données non enregistrées ou récupération en attente"}</small><time>${esc(exactTime(buy?.block_time || j.confirmed_at))}</time></section>
      <section><span>TON MC DE SORTIE · ESTIMÉ</span><strong>${esc(compact(sell?.mc_estimate))}</strong><small>${j.exit_state === "sold" ? "Calcul après la vente confirmée" : j.exit_state === "empty" ? "Solde nul : aucune vente attestée par le bot" : "Aucune vente confirmée par le bot"}</small><time>${esc(j.exit_state === "sold" ? exactTime(sell?.block_time || j.sold_at) : "—")}</time></section>
    </div>
    <dl class="detail-facts">
      <div><dt>Écart estimé entrée / callout</dt><dd>${gap == null ? "Indisponible" : (gap >= 0 ? "+" : "") + gap.toFixed(2) + " %"}</dd></div>
      <div><dt>Montant investi</dt><dd>${money(j.cost_usd)}</dd></div>
      <div><dt>SOL reçus à la vente, hors frais réseau</dt><dd>${sell?.sol_amount == null ? "Indisponible" : esc(sell.sol_amount) + " SOL"}</dd></div>
      <div><dt>Produit de vente estimé en USD</dt><dd>${money(sell?.usd_estimate)}</dd></div>
      <div><dt>Détection → première tentative d’envoi</dt><dd>${j.detect_to_send_ms == null ? "Indisponible" : esc(j.detect_to_send_ms) + " ms"}</dd></div>
      <div><dt>Publication demandée à</dt><dd>${esc(j.post_started ? exactTime(j.post_started) : "Non enregistrée")}</dd></div>
    </dl>
    <div class="detail-links">${txLink(j.buy_tx_url, "Transaction d’achat")}${txLink(j.sell_tx_url, "Transaction de vente")}<a href="https://pump.fun/coin/${encodeURIComponent(j.mint)}" target="_blank" rel="noopener noreferrer">Token sur pump.fun ${arrowIcon}</a></div>
    <p class="dialog-note">Les MC d’exécution sont des estimations : quantité exécutée sur Solana, offre relevée après confirmation et référence SOL/USD enregistrée lors de la préparation de l’achat ou du déclenchement du TP. Ils peuvent différer du MC affiché par pump.fun. Offre relevée : ${esc(supplyInfo?.supply_observed_at ? exactTime(supplyInfo.supply_observed_at) : "indisponible")}. La dernière cotation du tableau n’est pas le montant de vente exécuté. Une donnée historique manquante reste indisponible.</p>
    ${j.buy_details_error || j.sell_details_error ? `<p class="detail-warning">Récupération différée : ${esc(j.buy_details_error || j.sell_details_error)}</p>` : ""}`;
}
function renderFeed(s) {
  const globalFeed = paused ? frozenFeed : s.feed;
  const callerFeed = paused ? frozenCallers : s.caller_feed || s.feed;
  const available = feedFilter === "all" ? globalFeed : callerFeed;
  $("metric-calls").textContent = s.feed_received ?? s.feed.length;
  $("feed-total").textContent = s.feed.length;
  const query = $("search").value.toLowerCase().trim();
  const selected = new Set(configuration().callers);
  const callerName = (c) =>
    s.caller_wallets?.[c.author] || c.author_name || "Inconnu";
  const chosen = (c) => selected.has(callerName(c));
  $("callers-total").textContent = callerFeed.filter(chosen).length;
  const rows = available
    .filter(
      (c) =>
        (feedFilter === "all" || chosen(c)) &&
        [
          callerName(c),
          c.author_name,
          c.name,
          c.symbol,
          c.coinMint,
          c.thesis,
        ].some((v) =>
          String(v || "")
            .toLowerCase()
            .includes(query),
        ),
    )
    .sort((a, b) => epoch(b.createdAt) - epoch(a.createdAt));
  if (!available.length && feedFilter === "all") {
    $("feed").dataset.signature = "";
    if (!$("feed").querySelector(".feed-empty"))
      $("feed").innerHTML =
        '<div class="feed-empty"><div class="radar"><span></span><i></i></div><h3>À l’écoute du marché</h3><p>Les callouts reçus apparaîtront ici pendant l’exécution du hub.</p><div class="feed-example"><span class="dot lime"></span> Un flux commun. Deux usages.</div></div>';
    return;
  }
  if (!rows.length) {
    $("feed").dataset.signature = "";
    $("feed").innerHTML =
      feedFilter !== "all" && !query
        ? '<div class="no-results">Aucun callout reçu pour les callers cochés dans cette session. Leurs callouts seront conservés ici séparément du flux général.</div>'
        : '<div class="no-results">Aucun callout ne correspond à cette recherche.</div>';
    return;
  }
  const signature = JSON.stringify([
    [...selected],
    rows.map((c) => [
      c.calloutId,
      Math.floor((Date.now() / 1000 - epoch(c.createdAt)) / 10),
    ]),
  ]);
  if ($("feed").dataset.signature === signature) return;
  $("feed").dataset.signature = signature;
  $("feed").innerHTML = rows
    .map((c) => {
      const name = callerName(c);
      return `<article class="feed-card"><div class="feed-person"><span class="feed-avatar">${esc(name.slice(0, 2).toUpperCase())}</span><strong>${esc(name)}</strong>${chosen(c) ? '<span class="feed-selected">SUIVI</span>' : ""}<span class="feed-age">il y a ${age(c.createdAt)}</span></div><div class="feed-symbol-row"><a class="feed-symbol" href="https://pump.fun/coin/${encodeURIComponent(c.coinMint)}" target="_blank" rel="noopener noreferrer">${esc(c.symbol ? "$" + c.symbol : c.name || c.coinMint.slice(0, 7) + "…")}</a><span class="feed-mc">${compact(c.marketCap)} MC</span><button class="feed-copy" data-mint="${esc(c.coinMint)}" title="Copier l’adresse du token">+ Copier CA</button></div><p class="feed-thesis">${esc(c.thesis || "Aucun commentaire.")}</p></article>`;
    })
    .join("");
}
function renderLogs(s) {
  const wasBottom =
    $("logs").scrollHeight - $("logs").scrollTop - $("logs").clientHeight < 45;
  const logs = s.logs.filter(
    (l) => l.at > logCutoff && (!errorsOnly || l.level === "error"),
  );
  $("log-count").textContent =
    logs.length + " événement" + (logs.length > 1 ? "s" : "");
  const sig = JSON.stringify(logs.map((l) => [l.at, l.message]));
  if ($("logs").dataset.signature === sig) return;
  $("logs").dataset.signature = sig;
  $("logs").innerHTML = logs.length
    ? logs
        .map(
          (l) =>
            `<div class="log-line ${esc(l.level)}"><span class="log-time">${new Date(l.at * 1000).toLocaleTimeString("fr-FR", { hour12: false })}</span><span class="dot"></span><p>${esc(l.message)}</p></div>`,
        )
        .join("")
    : '<div class="log-placeholder">Aucun événement dans cet affichage.</div>';
  if (wasBottom) $("logs").scrollTop = $("logs").scrollHeight;
}
function renderCredentials(s) {
  const labels = [
    ["wallet", "Wallet pump.fun"],
    ["pump_session", "Session pump.fun"],
    ["telegram", "Telegram"],
    ["jupiter", "API Jupiter"],
  ];
  $("credentials").innerHTML = labels
    .map(
      ([key, label]) =>
        `<div class="credential"><span>${label}</span><strong class="${s.credentials[key] ? "" : "missing"}"><span class="dot ${s.credentials[key] ? "lime" : ""}"></span>${s.credentials[key] ? "Disponible" : "À configurer"}</strong></div>`,
    )
    .join("");
}
async function refresh() {
  try {
    const next = await request("/api/state");
    state = next;
    if (!hydrated) {
      hydrate(next.config, next.callers);
      hydrated = true;
    }
    phaseUI(next);
    renderPositions(next);
    if ($("position-dialog").open) renderDetails();
    renderFeed(next);
    renderLogs(next);
    renderCredentials(next);
    $("connection").innerHTML = "<i></i> Connexion locale";
    if (next.journal_error) toast(next.journal_error, true);
  } catch (error) {
    $("connection").innerHTML = "<i></i> Serveur déconnecté";
    $("feed-live").innerHTML = "<i></i> DÉCONNECTÉ";
    $("feed-live").className = "live-label";
    $("start").disabled = true;
  }
}
document.querySelectorAll("[data-mode]").forEach((b) =>
  b.addEventListener("click", () => {
    setMode(b.dataset.mode);
    if (state) phaseUI(state);
  }),
);
$("settings").addEventListener("submit", (e) => e.preventDefault());
$("settings").addEventListener("input", () => {
  updateStart();
  updateSelection();
  if (state) {
    $("feed").dataset.signature = "";
    renderFeed(state);
  }
});
$("start").addEventListener("click", async () => {
  if (!$("settings").reportValidity()) return;
  const config = configuration();
  if (!config.callers.length)
    return toast("Sélectionne au moins un caller.", true);
  $("start").disabled = true;
  try {
    await request("/api/start", config);
    toast(
      config.mode === "live"
        ? "Démarrage réel demandé."
        : "Simulation démarrée.",
    );
    await refresh();
  } catch (error) {
    toast(error.message, true);
    $("start").disabled = false;
  }
});
$("stop").addEventListener("click", async () => {
  $("stop").disabled = true;
  try {
    await request("/api/stop", {});
    toast("Arrêt demandé. Le journal est conservé.");
    await refresh();
  } catch (error) {
    toast(error.message, true);
  }
});
$("search").addEventListener("input", () => {
  if (state) {
    $("feed").dataset.signature = "";
    renderFeed(state);
  }
});
document.querySelectorAll("[data-filter]").forEach((b) =>
  b.addEventListener("click", () => {
    feedFilter = b.dataset.filter;
    document
      .querySelectorAll("[data-filter]")
      .forEach((t) => t.classList.toggle("active", t === b));
    $("feed").dataset.signature = "";
    if (state) renderFeed(state);
  }),
);
$("pause-feed").addEventListener("click", () => {
  paused = !paused;
  frozenFeed = state ? [...state.feed] : [];
  frozenCallers = state ? [...(state.caller_feed || state.feed)] : [];
  $("pause-feed").innerHTML = paused ? playIcon : pauseIcon;
  $("pause-feed").title = paused
    ? "Reprendre l’affichage"
    : "Mettre l’affichage en pause";
  $("feed").dataset.signature = "";
  if (state) {
    renderFeed(state);
    phaseUI(state);
  }
});
$("feed").addEventListener("click", async (e) => {
  const button = e.target.closest("[data-mint]");
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.mint);
    button.textContent = "Copié ✓";
    toast("Adresse du token copiée.");
  } catch (error) {
    toast("Copie indisponible : ouvre le lien du token.", true);
  }
});
$("position-tabs").addEventListener("click", (e) => {
  const b = e.target.closest("[data-view]");
  if (!b) return;
  positionView = b.dataset.view;
  document
    .querySelectorAll("[data-view]")
    .forEach((t) => t.classList.toggle("active", t === b));
  if (state) renderPositions(state);
});
$("errors-toggle").addEventListener("click", () => {
  errorsOnly = !errorsOnly;
  $("errors-toggle").textContent = errorsOnly
    ? "Erreurs uniquement"
    : "Toutes les lignes";
  if (state) renderLogs(state);
});
$("clear-logs").addEventListener("click", () => {
  logCutoff = Date.now() / 1000;
  if (state) renderLogs(state);
  toast("Affichage vidé. Les journaux du bot sont conservés.");
});
$("help-button").addEventListener("click", () => $("help-dialog").showModal());
$("close-help").addEventListener("click", () => $("help-dialog").close());
$("positions").addEventListener("click", (event) => {
  const button = event.target.closest("button[data-mint]");
  if (!button) return;
  detailMint = button.dataset.mint;
  renderDetails();
  $("position-dialog").showModal();
});
$("close-position").addEventListener("click", () =>
  $("position-dialog").close(),
);
setInterval(() => {
  $("clock").textContent = new Date().toLocaleTimeString("fr-FR", {
    hour12: false,
  });
}, 1000);
$("pause-feed").innerHTML = pauseIcon;
async function poll() {
  await refresh();
  setTimeout(poll, 1500);
}
poll();
