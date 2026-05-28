const API = "";

function $(sel) { return document.querySelector(sel); }
function $$(sel) { return [...document.querySelectorAll(sel)]; }

function apiError(data) {
  if (!data) return "Erreur inconnue";
  if (Array.isArray(data.detail)) {
    return data.detail.map((d) => d.msg || JSON.stringify(d)).join(", ");
  }
  return data.detail || data.error || "Erreur";
}

async function api(path, opts = {}) {
  const r = await fetch(API + path, {
    headers: { "Content-Type": "application/json", ...opts.headers },
    ...opts,
  });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(apiError(data) || r.statusText);
  return data;
}

/** Cache Pokédex pour les listes déroulantes Stock / Radar */
let pokedexCache = [];

async function loadPokedexOptions() {
  pokedexCache = await api("/api/pokedex");
  const opts = pokedexCache.map((c) => {
    const label = `${c.nom}${c.extension ? ` — ${c.extension}` : ""}`;
    return `<option value="${c.id}">${label}</option>`;
  }).join("");
  const empty = '<option value="">— Carte (Pokédex) —</option>';
  $("#stock-pokedex-id").innerHTML = empty + opts;
  $("#radar-pokedex-id").innerHTML = empty + opts;
}

async function loadStockForVenteOptions() {
  const rows = await api("/api/stock/for-vente");
  const sel = $("#vente-stock-id");
  if (!rows.length) {
    sel.innerHTML = '<option value="">— Aucun stock (ajoutez d\'abord au Stock) —</option>';
    return;
  }
  sel.innerHTML = '<option value="">— Ligne stock —</option>' + rows.map((r) =>
    `<option value="${r.id}">${r.label} [${r.statut}]</option>`
  ).join("");
}

function escapeAttr(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/"/g, "&quot;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function thumbHtml(imageUrl, tooltip = "") {
  const title = escapeAttr(tooltip || "Carte");
  if (imageUrl) {
    const proxySrc = `/api/image-proxy?url=${encodeURIComponent(imageUrl)}`;
    const safeUrl = escapeAttr(imageUrl);
    return (
      `<img class="thumb" src="${proxySrc}" alt="" title="${title}" ` +
      `data-full="${proxySrc}" data-raw-url="${safeUrl}" loading="lazy" ` +
      `onerror="thumbFallback(this)" />`
    );
  }
  return `<div class="thumb-placeholder" title="${title}">IMG</div>`;
}

function thumbFallback(img) {
  if (!img.dataset.directTried) {
    img.dataset.directTried = "1";
    const raw = img.getAttribute("data-raw-url");
    if (raw) {
      img.src = raw;
      return;
    }
  }
  const title = img.getAttribute("title") || "Image indisponible";
  img.replaceWith(Object.assign(document.createElement("div"), {
    className: "thumb-placeholder",
    title,
    textContent: "IMG",
  }));
}

/**
 * Cellule tableau : miniature + nom.
 * @param {object} row — doit contenir nom, image_url, extension (optionnels)
 */
function cardCell(row) {
  const name = row?.nom || "—";
  const imageUrl = row?.image_url || null;
  const tooltip = [name, row?.extension].filter((x) => x && x !== "—").join(" — ");
  const thumb = thumbHtml(imageUrl, tooltip);
  const extHtml = row?.extension
    ? `<br><small style="color:var(--muted)">${escapeAttr(row.extension)}</small>`
    : "";
  return (
    `<div class="card-cell">` +
    `${thumb}` +
    `<div><strong>${escapeAttr(name)}</strong>${extHtml}</div>` +
    `</div>`
  );
}

function urgenceBadge(u) {
  if (!u) return '<span class="badge badge-muted">—</span>';
  const map = { "Bonne affaire": "green", "À surveiller": "yellow", "Trop cher": "red" };
  const cls = map[u] || "muted";
  const tag = cls === "green" ? "+" : cls === "yellow" ? "~" : cls === "red" ? "-" : "";
  return `<span class="badge badge-${cls}">${tag} ${escapeAttr(u)}</span>`;
}

function prioriteStars(n) {
  if (n == null || n === "") return "—";
  const v = parseInt(n, 10);
  if (isNaN(v)) return "—";
  return "★".repeat(v) + "☆".repeat(5 - v);
}

let chartStock = null;
let chartCa = null;

function fmtEur(v) {
  if (v == null || v === "") return "—";
  return `${Number(v).toFixed(2)} €`;
}

function ebayCell(row) {
  if (row.prix_moyen_ebay == null) return "—";
  const cm = row.prix_actuel;
  let tag = "=";
  let cls = "ebay-neutral";
  if (cm != null && cm > 0) {
    const diff = Math.abs(row.prix_moyen_ebay - cm) / cm;
    if (diff >= 0.1) {
      if (row.prix_moyen_ebay > cm) {
        tag = "eBay+";
        cls = "ebay-under-cm";
      } else {
        tag = "eBay-";
        cls = "ebay-over-cm";
      }
    }
  }
  const tip = [
    `Min: ${fmtEur(row.prix_min_ebay)}`,
    `Max: ${fmtEur(row.prix_max_ebay)}`,
    `${row.nb_ventes_ebay ?? 0} vente(s) sur 30j`,
  ].join(" · ");
  return `<span class="ebay-price ${cls}" title="${escapeAttr(tip)}"><span class="ebay-tag">${tag}</span> ${fmtEur(row.prix_moyen_ebay)}</span>`;
}

function pct(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const v = Number(n);
  const sign = v > 0 ? "+" : "";
  return `${sign}${v.toFixed(2)}%`;
}

function diffPct(a, b) {
  if (a == null || b == null || b === 0) return null;
  return ((a - b) / b) * 100;
}

function diffBadge(v) {
  if (v == null) return `<span class="badge badge-muted">—</span>`;
  const cls = v >= 20 ? "green" : v <= -20 ? "red" : Math.abs(v) < 10 ? "muted" : v > 0 ? "yellow" : "yellow";
  const tag = cls === "green" ? "HAUSSE" : cls === "red" ? "BAISSE" : "=";
  return `<span class="badge badge-${cls}">${tag} ${pct(v)}</span>`;
}

function qtyCell(row) {
  const qty = row.quantite ?? 1;
  return `<div class="qty-stepper" title="Modifier la quantité">
    <button type="button" class="btn-qty btn-qty-minus" data-id="${row.id}" aria-label="Diminuer">−</button>
    <input type="number" class="qty-input stock-qty-edit" data-id="${row.id}"
      data-last="${qty}" min="1" step="1" value="${qty}" aria-label="Quantité" />
    <button type="button" class="btn-qty btn-qty-plus" data-id="${row.id}" aria-label="Augmenter">+</button>
  </div>`;
}

// Tendances
let trendingCat = "all";

async function loadTrending() {
  const data = await api(`/api/ebay/trending?categorie=${encodeURIComponent(trendingCat)}`);
  const dateEl = $("#trending-date");
  dateEl.textContent = data.date_snapshot ? `Snapshot: ${data.date_snapshot}` : "Snapshot: (live / non sauvegardé)";
  const tb = $("#trending-tbody");
  const items = data.items || [];
  if (!items.length) {
    tb.innerHTML = `<tr><td colspan="5" class="empty">Aucune donnée</td></tr>`;
    return;
  }
  tb.innerHTML = items.map((it) => `
    <tr>
      <td>${escapeAttr(it.titre || "—")}<br><small style="color:var(--muted)">${escapeAttr(it.categorie || "")}</small></td>
      <td>${fmtEur(it.prix_moyen)}</td>
      <td>${it.nb_ventes_7j ?? "—"}</td>
      <td>${it.variation_prix_pct != null ? diffBadge(it.variation_prix_pct) : `<span class="badge badge-muted">—</span>`}</td>
      <td>${it.url_ebay ? `<a class="link" href="${escapeAttr(it.url_ebay)}" target="_blank" rel="noreferrer">Voir</a>` : "—"}</td>
    </tr>
  `).join("");
}

async function loadCompareAndOpps() {
  const rows = await api("/api/pokedex");
  const compareTb = $("#compare-tbody");
  const oppTb = $("#opps-tbody");

  if (!rows.length) {
    compareTb.innerHTML = `<tr><td colspan="5" class="empty">Pokédex vide</td></tr>`;
    oppTb.innerHTML = `<tr><td colspan="4" class="empty">Pokédex vide</td></tr>`;
    return;
  }

  const compareRows = [];
  const opps = [];
  for (const r of rows) {
    const cm = r.prix_actuel;
    const eb = r.prix_moyen_ebay;
    const vol = r.nb_ventes_ebay ?? 0;
    const d = diffPct(eb, cm);
    compareRows.push(`
      <tr>
        <td>${cardCell(r)}</td>
        <td>${fmtEur(cm)}</td>
        <td>${fmtEur(eb)}</td>
        <td>${diffBadge(d)}</td>
        <td>${vol}</td>
      </tr>
    `);
    if (d != null && d > 20 && cm != null && eb != null) {
      const gain = eb - cm;
      opps.push({ html: `
        <tr>
          <td>${cardCell(r)}</td>
          <td>${fmtEur(cm)}</td>
          <td>${fmtEur(eb)}</td>
          <td><span class="badge badge-green">OPP</span> ${fmtEur(gain)} (${pct(d)})</td>
        </tr>
      `, score: d });
    }
  }

  compareTb.innerHTML = compareRows.join("");
  opps.sort((a, b) => b.score - a.score);
  oppTb.innerHTML = opps.length ? opps.map((o) => o.html).join("") : `<tr><td colspan="4" class="empty">Aucune opportunité détectée</td></tr>`;
}

async function loadTendances() {
  try {
    await loadTrending();
    await loadCompareAndOpps();
  } catch (e) {
    alert(e.message);
  }
}

async function saveStockQty(id, value) {
  const v = parseInt(value, 10);
  if (!v || v < 1) {
    alert("Quantité invalide (minimum 1)");
    return false;
  }
  try {
    await api(`/api/stock/${id}`, {
      method: "PUT",
      body: JSON.stringify({ quantite: v }),
    });
    await loadStock();
    loadDashboard();
    return true;
  } catch (err) {
    alert(err.message);
    await loadStock();
    return false;
  }
}

function bindStockQtyControls(root = document) {
  root.querySelectorAll(".stock-qty-edit").forEach((input) => {
    const commit = () => {
      const last = input.dataset.last || input.value;
      if (String(input.value) !== String(last)) {
        saveStockQty(input.dataset.id, input.value);
      }
    };
    input.onchange = commit;
    input.onblur = commit;
    input.onkeydown = (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        input.blur();
      }
    };
  });

  root.querySelectorAll(".btn-qty-minus").forEach((btn) => {
    btn.onclick = () => {
      const input = btn.parentElement.querySelector(".stock-qty-edit");
      const next = Math.max(1, (parseInt(input.value, 10) || 1) - 1);
      saveStockQty(btn.dataset.id, next);
    };
  });

  root.querySelectorAll(".btn-qty-plus").forEach((btn) => {
    btn.onclick = () => {
      const input = btn.parentElement.querySelector(".stock-qty-edit");
      const next = (parseInt(input.value, 10) || 1) + 1;
      saveStockQty(btn.dataset.id, next);
    };
  });
}

function fmtDate(iso) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("fr-FR", { dateStyle: "short", timeStyle: "short" });
}

// Tabs
$$("#tabs button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("#tabs button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    $$(".panel").forEach((p) => p.classList.remove("active"));
    $(`#panel-${btn.dataset.tab}`).classList.add("active");
    loadTab(btn.dataset.tab);
  });
});

async function loadTab(tab) {
  if (tab === "dashboard") loadDashboard();
  if (tab === "pokedex") {
    await loadPokedex();
    await loadPokedexOptions();
  }
  if (tab === "stock") {
    await loadPokedexOptions();
    await loadStock();
  }
  if (tab === "radar") {
    await loadPokedexOptions();
    await loadRadar();
  }
  if (tab === "tendances") {
    await loadTendances();
  }
  if (tab === "vendus") {
    await loadStockForVenteOptions();
    await loadVendus();
  }
}

// Modal image
const modal = $("#img-modal");
$("#modal-close").onclick = () => modal.classList.remove("open");
modal.onclick = (e) => { if (e.target === modal) modal.classList.remove("open"); };
document.addEventListener("click", (e) => {
  const t = e.target.closest(".thumb");
  if (t && t.dataset.full) {
    $("#modal-img").src = t.dataset.full;
    modal.classList.add("open");
  }
});

async function deleteRow(endpoint, onDone) {
  if (!window.confirm("Supprimer cette carte ?")) return;
  try {
    await api(endpoint, { method: "DELETE" });
    await onDone();
    loadDashboard();
  } catch (err) {
    alert(err.message);
  }
}

// Dashboard
function kpiMargeClass(v) {
  if (v > 0) return "kpi-pos";
  if (v < 0) return "kpi-neg";
  return "";
}

async function loadDashboard() {
  try {
    const k = await api("/api/dashboard");
    const marge = k.marge_latente_totale ?? 0;
    const margeCls = kpiMargeClass(marge);
    $("#kpi-grid").innerHTML = `
    <div class="kpi-card"><div class="label">Capital investi</div><div class="value">${fmtEur(k.capital_investi)}</div></div>
    <div class="kpi-card"><div class="label">Valeur stock</div><div class="value">${fmtEur(k.valeur_stock)}</div></div>
    <div class="kpi-card kpi-card-highlight"><div class="label">Marge latente (cumul)</div><div class="value ${margeCls}">${fmtEur(marge)}</div></div>
    <div class="kpi-card"><div class="label">CA total</div><div class="value">${fmtEur(k.ca_total)}</div></div>
    <div class="kpi-card"><div class="label">Bénéfice net</div><div class="value">${fmtEur(k.benefice_net)}</div></div>
    <div class="kpi-card"><div class="label">Marge moyenne</div><div class="value">${k.marge_moyenne_pct}%</div></div>
    <div class="kpi-card"><div class="label">Cartes Pokédex</div><div class="value">${k.nb_cartes_pokedex}</div></div>
    <div class="kpi-card"><div class="label">En stock</div><div class="value">${k.nb_en_stock}</div></div>
    <div class="kpi-card"><div class="label">Radar</div><div class="value">${k.nb_radar}</div></div>
  `;
  } catch (err) {
    console.error(err);
    $("#kpi-grid").innerHTML = `<p class="empty">Impossible de charger le dashboard : ${err.message}</p>`;
  }

  try {
    const charts = await api("/api/dashboard/charts");
    renderCharts(charts);
  } catch (err) {
    console.error("Graphiques dashboard:", err);
  }
}

function renderCharts(data) {
  if (typeof Chart === "undefined") return;
  const labels = data.labels || [];
  const common = {
    responsive: true,
    maintainAspectRatio: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: "#8b9cb3", maxRotation: 45 }, grid: { color: "#2d3f56" } },
      y: { ticks: { color: "#8b9cb3" }, grid: { color: "#2d3f56" } },
    },
  };

  if (chartStock) chartStock.destroy();
  if (chartCa) chartCa.destroy();

  chartStock = new Chart($("#chart-stock"), {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: "Valeur stock (€)",
        data: data.valeur_stock || [],
        borderColor: "#3b82f6",
        backgroundColor: "rgba(59, 130, 246, 0.15)",
        fill: true,
        tension: 0.3,
      }],
    },
    options: common,
  });

  chartCa = new Chart($("#chart-ca"), {
    type: "bar",
    data: {
      labels,
      datasets: [{
        label: "CA (€)",
        data: data.chiffre_affaires || [],
        backgroundColor: "rgba(255, 203, 5, 0.7)",
        borderColor: "#ffcb05",
        borderWidth: 1,
      }],
    },
    options: common,
  });
}

// Pokédex
async function loadPokedex() {
  const rows = await api("/api/pokedex");
  const tb = $("#pokedex-tbody");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="8" class="empty">Aucune carte — ajoutez une URL CardMarket</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => `
    <tr>
      <td>${cardCell(r)}</td>
      <td>${r.extension || "—"}</td>
      <td>${r.etat || "—"}</td>
      <td>${fmtEur(r.prix_actuel)}</td>
      <td>${ebayCell(r)}</td>
      <td>${r.tendance_7j != null ? r.tendance_7j : "—"}</td>
      <td>${fmtDate(r.derniere_maj)}</td>
      <td class="actions-cell">
        <button type="button" class="btn btn-ghost btn-scrape-one btn-sm" data-id="${r.id}" title="Rescraper">Sync</button>
        <button type="button" class="btn-delete btn-delete-pokedex btn-sm" data-id="${r.id}" title="Supprimer">X</button>
      </td>
    </tr>
  `).join("");

  $$(".btn-delete-pokedex").forEach((btn) => {
    btn.onclick = () =>
      deleteRow(`/api/pokedex/${btn.dataset.id}`, async () => {
        await loadPokedex();
        await loadPokedexOptions();
      });
  });

  $$(".btn-scrape-one").forEach((btn) => {
    btn.onclick = async () => {
      const loader = $("#pokedex-loader");
      loader.classList.add("show");
      try {
        await api(`/api/pokedex/${btn.dataset.id}/scrape`, { method: "POST" });
        await loadPokedex();
      } catch (e) {
        alert(e.message);
      } finally {
        loader.classList.remove("show");
      }
    };
  });
}

$("#btn-add-url").onclick = async () => {
  const url = $("#url-input").value.trim();
  if (!url) return alert("URL requise");
  const loader = $("#pokedex-loader");
  loader.classList.add("show");
  try {
    await api("/api/pokedex", { method: "POST", body: JSON.stringify({ url_cardmarket: url }) });
    $("#url-input").value = "";
    await loadPokedex();
    await loadPokedexOptions();
  } catch (e) {
    alert(e.message);
  } finally {
    loader.classList.remove("show");
  }
};

$("#btn-scrape-all").onclick = async () => {
  const loader = $("#pokedex-loader");
  loader.classList.add("show");
  try {
    const res = await api("/api/scrape/all", { method: "POST" });
    alert(`Scraping terminé : ${res.scraped}/${res.total} OK`);
    await loadPokedex();
    await loadPokedexOptions();
    loadDashboard();
  } catch (e) {
    alert(e.message);
  } finally {
    loader.classList.remove("show");
  }
};

// Stock
async function loadStock() {
  const statut = $("#stock-filter").value;
  const q = statut ? `?statut=${encodeURIComponent(statut)}` : "";
  const rows = await api(`/api/stock${q}`);
  const tb = $("#stock-tbody");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="8" class="empty">Stock vide</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => {
    const m = r.marge_latente;
    const mCls = m > 0 ? "marge-pos" : m < 0 ? "marge-neg" : "";
    return `<tr>
      <td>${cardCell(r)}</td>
      <td>${r.ref || "—"}</td>
      <td>${fmtEur(r.prix_achat)}</td>
      <td class="qty-cell">${qtyCell(r)}</td>
      <td>${fmtEur(r.prix_actuel)}</td>
      <td class="${mCls}">${m != null ? fmtEur(m) : "—"}</td>
      <td><span class="badge badge-muted">${r.statut}</span></td>
      <td><button type="button" class="btn-delete btn-delete-stock btn-sm" data-id="${r.id}" title="Supprimer">X</button></td>
    </tr>`;
  }).join("");

  bindStockQtyControls(tb);

  $$(".btn-delete-stock").forEach((btn) => {
    btn.onclick = () => deleteRow(`/api/stock/${btn.dataset.id}`, loadStock);
  });
}

$("#stock-filter").onchange = loadStock;
$("#btn-refresh-stock").onclick = loadStock;

$("#form-stock").onsubmit = async (e) => {
  e.preventDefault();
  const pokedex_id = $("#stock-pokedex-id").value;
  if (!pokedex_id) return alert("Choisissez une carte du Pokédex");
  const body = {
    pokedex_id,
    ref: $("#stock-ref").value.trim() || null,
    prix_achat: parseFloat($("#stock-prix").value),
    quantite: parseInt($("#stock-quantite").value, 10) || 1,
    statut: $("#stock-statut").value,
    source: $("#stock-source").value.trim() || null,
  };
  const d = $("#stock-date").value;
  if (d) body.date_achat = d;
  try {
    await api("/api/stock", { method: "POST", body: JSON.stringify(body) });
    e.target.reset();
    $("#stock-statut").value = "En stock";
    $("#stock-quantite").value = "1";
    await loadPokedexOptions();
    await loadStock();
    loadDashboard();
  } catch (err) {
    alert(err.message);
  }
};

// Radar
async function loadRadar() {
  const rows = await api("/api/radar");
  const tb = $("#radar-tbody");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="7" class="empty">Radar vide</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => `
    <tr>
      <td>${cardCell(r)}</td>
      <td>
        <select class="priorite-select radar-priorite-edit" data-id="${r.id}" data-value="${r.priorite ?? ""}">
          <option value="">—</option>
          ${[5, 4, 3, 2, 1].map((n) =>
            `<option value="${n}" ${r.priorite == n ? "selected" : ""}>${prioriteStars(n)}</option>`
          ).join("")}
        </select>
      </td>
      <td>${fmtEur(r.prix_cible)}</td>
      <td>${fmtEur(r.prix_actuel)}</td>
      <td>${urgenceBadge(r.urgence)}</td>
      <td>${r.statut || "—"}</td>
      <td><button type="button" class="btn-delete btn-delete-radar btn-sm" data-id="${r.id}" title="Supprimer">X</button></td>
    </tr>
  `).join("");

  $$(".btn-delete-radar").forEach((btn) => {
    btn.onclick = () => deleteRow(`/api/radar/${btn.dataset.id}`, loadRadar);
  });

  $$(".radar-priorite-edit").forEach((sel) => {
    sel.onchange = async () => {
      const v = sel.value ? parseInt(sel.value, 10) : null;
      try {
        await api(`/api/radar/${sel.dataset.id}`, {
          method: "PUT",
          body: JSON.stringify({ priorite: v }),
        });
        await loadRadar();
      } catch (err) {
        alert(err.message);
      }
    };
  });
}

$("#form-radar").onsubmit = async (e) => {
  e.preventDefault();
  const pokedex_id = $("#radar-pokedex-id").value;
  if (!pokedex_id) return alert("Choisissez une carte du Pokédex");
  try {
    const body = {
      pokedex_id,
      prix_cible: parseFloat($("#radar-prix-cible").value),
      source_potentielle: $("#radar-source").value.trim() || null,
    };
    const pr = $("#radar-priorite").value;
    if (pr) body.priorite = parseInt(pr, 10);
    await api("/api/radar", { method: "POST", body: JSON.stringify(body) });
    e.target.reset();
    await loadPokedexOptions();
    await loadRadar();
  } catch (err) {
    alert(err.message);
  }
};

// Vendus
async function loadVendus() {
  const rows = await api("/api/ventes");
  const tb = $("#vendus-tbody");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="7" class="empty">Aucune vente</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => `
    <tr>
      <td>${r.nom || "—"}</td>
      <td>${r.ref || "—"}</td>
      <td>${fmtEur(r.prix_vente)}</td>
      <td>${fmtEur(r.frais_plateforme)}</td>
      <td class="${r.benefice >= 0 ? "marge-pos" : "marge-neg"}">${fmtEur(r.benefice)}</td>
      <td>${r.date_vente || "—"}</td>
      <td>${r.plateforme || "—"}</td>
    </tr>
  `).join("");
}

$("#form-vente").onsubmit = async (e) => {
  e.preventDefault();
  const stock_id = $("#vente-stock-id").value;
  if (!stock_id) return alert("Choisissez une ligne du stock");
  const body = {
    stock_id,
    prix_vente: parseFloat($("#vente-prix").value),
    frais_plateforme: parseFloat($("#vente-frais").value) || 0,
    plateforme: $("#vente-plateforme").value.trim() || null,
  };
  const d = $("#vente-date").value;
  if (d) body.date_vente = d;
  try {
    await api("/api/ventes", { method: "POST", body: JSON.stringify(body) });
    e.target.reset();
    $("#vente-frais").value = "0";
    await loadStockForVenteOptions();
    await loadVendus();
    loadDashboard();
  } catch (err) {
    alert(err.message);
  }
};

// Init
loadDashboard();
loadPokedexOptions().catch(() => {});

// Tendances UI bindings
$("#btn-refresh-trending")?.addEventListener("click", () => loadTendances());
$$(".segmented .seg").forEach((b) => {
  b.addEventListener("click", () => {
    $$(".segmented .seg").forEach((x) => x.classList.remove("active"));
    b.classList.add("active");
    trendingCat = b.dataset.cat || "all";
    loadTendances();
  });
});
