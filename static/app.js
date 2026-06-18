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
let searchTimers = new Map();
let trendingCat = "all";
let chartStock = null;
let chartCa = null;
let chartView = "mediane";
let lastChartData = null;

function prixReference(row) {
  if (row.prix_reference_mediane != null) return row.prix_reference_mediane;
  const prices = [row.prix_actuel, row.prix_actif_ebay, row.prix_moyen_vinted]
    .filter((v) => v != null && v > 0)
    .sort((a, b) => a - b);
  if (!prices.length) return null;
  const mid = Math.floor(prices.length / 2);
  return prices.length % 2 ? prices[mid] : (prices[mid - 1] + prices[mid]) / 2;
}

function marketPricesBlock(row) {
  const tend = row.tendance_7j != null ? ` <small class="muted">(7j: ${fmtEur(row.tendance_7j)})</small>` : "";
  const cm = row.prix_actuel != null ? fmtEur(row.prix_actuel) : "—";
  const ebay = row.prix_actif_ebay != null ? fmtEur(row.prix_actif_ebay) : "—";
  const ebayNb = row.nb_annonces_ebay_actif ?? 0;
  const vinted = row.prix_moyen_vinted != null ? fmtEur(row.prix_moyen_vinted) : "—";
  const vintedNb = row.nb_annonces_vinted ?? 0;
  const ref = prixReference(row);
  const refLine = ref != null
    ? `<div class="market-price-row market-price-ref"><span>📐 Réf. médiane</span><span><strong>${fmtEur(ref)}</strong></span></div>`
    : "";
  return `<div class="market-prices">
    <div class="market-price-row"><span>📊 CardMarket</span><span>${cm}${tend}</span></div>
    <div class="market-price-row"><span>🛒 eBay actif</span><span>${ebay} <small class="muted">(${ebayNb})</small></span></div>
    <div class="market-price-row"><span>👗 Vinted</span><span>${vinted} <small class="muted">(${vintedNb})</small></span></div>
    ${refLine}
  </div>`;
}

function opportunityScore(row) {
  const ref = prixReference(row);
  const achat = row.prix_achat;
  if (ref == null || ref <= 0) {
    return { emoji: "⚪", label: "Pas de données", pct: null, cls: "score-none" };
  }
  if (achat != null) {
    const ratio = achat / ref;
    const pct = Math.round((1 - ratio) * 100);
    if (ratio < 0.8) return { emoji: "🟢", label: "Excellente affaire", pct, cls: "score-good" };
    if (ratio < 0.9) return { emoji: "🟡", label: "Bonne affaire", pct, cls: "score-ok" };
    if (ratio > 1) return { emoji: "🔴", label: "Prix élevé", pct, cls: "score-bad" };
    return { emoji: "⚪", label: "Neutre", pct, cls: "score-none" };
  }
  const cm = row.prix_actuel;
  const ebay = row.prix_actif_ebay;
  const vinted = row.prix_moyen_vinted;
  if (ebay != null && cm != null && cm > 0) {
    const pct = Math.round((ebay / cm - 1) * 100);
    if (pct > 20) return { emoji: "🟢", label: `Opportunité (+${pct}%)`, pct, cls: "score-good" };
  }
  if (vinted != null && ref > 0 && vinted < ref * 0.85) {
    return { emoji: "🟢", label: "Vinted bas", pct: null, cls: "score-good" };
  }
  return { emoji: "⚪", label: "Neutre", pct: null, cls: "score-none" };
}

function openAppModal(html) {
  $("#app-modal-body").innerHTML = html;
  $("#app-modal").classList.add("open");
}

function closeAppModal() {
  $("#app-modal").classList.remove("open");
  $("#app-modal-body").innerHTML = "";
}

$("#app-modal-close")?.addEventListener("click", closeAppModal);
$("#app-modal")?.addEventListener("click", (e) => {
  if (e.target.id === "app-modal") closeAppModal();
});

function initUniversalSearchBars() {
  $$(".search-bar-wrap").forEach((wrap) => {
    if (wrap.dataset.initialized) return;
    wrap.dataset.initialized = "1";
    wrap.innerHTML = `
      <input type="search" class="universal-search" placeholder="Rechercher une carte Pokémon…" autocomplete="off" />
      <div class="search-dropdown" role="listbox"></div>
    `;
    const input = wrap.querySelector(".universal-search");
    const dropdown = wrap.querySelector(".search-dropdown");

    input.addEventListener("input", () => {
      const q = input.value.trim();
      clearTimeout(searchTimers.get(wrap));
      if (q.length < 2) {
        dropdown.classList.remove("open");
        dropdown.innerHTML = "";
        return;
      }
      searchTimers.set(
        wrap,
        setTimeout(async () => {
          try {
            const results = await api(`/api/search?q=${encodeURIComponent(q)}`);
            if (!results.length) {
              dropdown.innerHTML = `<div class="empty" style="padding:1rem">Aucun résultat</div>`;
            } else {
              dropdown.innerHTML = results.map((r, i) => `
                <div class="search-result-item" tabindex="0" data-idx="${i}">
                  ${r.image_url ? `<img src="/api/image-proxy?url=${encodeURIComponent(r.image_url)}" alt="" loading="lazy" />` : `<div class="thumb-placeholder" style="width:40px;height:54px;font-size:0.7rem">IMG</div>`}
                  <div class="search-result-meta">
                    <strong>${escapeAttr(displayNom(r.nom))}</strong>
                    <small>${escapeAttr(r.extension || "")} ${r.prix_actuel != null ? `· ${fmtEur(r.prix_actuel)}` : ""}</small>
                  </div>
                </div>
              `).join("");
              dropdown.querySelectorAll(".search-result-item").forEach((el, idx) => {
                const item = results[idx];
                const handler = () => onSearchResultClick(item);
                el.onclick = handler;
                el.onkeydown = (ev) => { if (ev.key === "Enter") handler(); };
              });
            }
            dropdown.classList.add("open");
          } catch (err) {
            dropdown.innerHTML = `<div class="empty" style="padding:1rem">${escapeAttr(err.message)}</div>`;
            dropdown.classList.add("open");
          }
        }, 500)
      );
    });

    document.addEventListener("click", (e) => {
      if (!wrap.contains(e.target)) dropdown.classList.remove("open");
    });
  });
}

async function onSearchResultClick(item) {
  const existing = pokedexCache.find((c) => c.url_cardmarket?.split("?")[0] === item.url_cardmarket?.split("?")[0]);
  if (existing) {
    openAppModal(`
      <h3>${escapeAttr(displayNom(existing.nom))}</h3>
      <p class="muted">Déjà dans le Pokédex.</p>
      <div class="modal-actions">
        <button class="btn btn-accent btn-touch" data-act="stock" data-id="${existing.id}">Ajouter au Stock</button>
        <button class="btn btn-touch" data-act="radar" data-id="${existing.id}">Ajouter au Radar</button>
        <button class="btn btn-ghost btn-touch" data-act="close">Fermer</button>
      </div>
    `);
    bindSearchActionButtons(existing);
    return;
  }

  openAppModal(`<p class="muted">Ajout au Pokédex…</p>`);
  try {
    const res = await api("/api/pokedex", {
      method: "POST",
      body: JSON.stringify({ url_cardmarket: item.url_cardmarket, langue: "FR" }),
    });
    await loadPokedexOptions();
    openAppModal(`
      <h3>${escapeAttr(displayNom(res.nom || item.nom))}</h3>
      <p class="muted">Carte ajoutée au Pokédex.</p>
      <div class="modal-actions">
        <button class="btn btn-accent btn-touch" data-act="stock" data-id="${res.pokedex_id}">Ajouter au Stock</button>
        <button class="btn btn-touch" data-act="radar" data-id="${res.pokedex_id}">Ajouter au Radar</button>
        <button class="btn btn-ghost btn-touch" data-act="close">Juste suivre</button>
      </div>
    `);
    bindSearchActionButtons({ id: res.pokedex_id, nom: res.nom || item.nom });
    await loadPokedex();
    loadDashboard();
  } catch (err) {
    openAppModal(`<p class="empty">Erreur : ${escapeAttr(err.message)}</p>`);
  }
}

function bindSearchActionButtons(card) {
  $("#app-modal-body").querySelectorAll("[data-act]").forEach((btn) => {
    btn.onclick = () => {
      const act = btn.dataset.act;
      closeAppModal();
      if (act === "stock") openStockModal(card.id);
      if (act === "radar") openRadarModal(card.id);
    };
  });
}

function pokedexAutocompleteInput(id = "modal-pokedex-search") {
  const opts = pokedexCache.map((c) => {
    const label = `${displayNom(c.nom)}${c.extension ? ` — ${c.extension}` : ""}`;
    return `<option value="${c.id}">${escapeAttr(label)}</option>`;
  }).join("");
  return `
    <label for="${id}">Carte (Pokédex)</label>
    <select id="${id}" required class="btn-touch"><option value="">— Choisir —</option>${opts}</select>
  `;
}

function openStockModal(preselectId = null) {
  openAppModal(`
    <h3>Ajouter au stock</h3>
    <form class="modal-form" id="modal-form-stock">
      ${pokedexAutocompleteInput()}
      <label>Prix d'achat (€)</label>
      <input type="number" id="modal-stock-prix" step="0.01" min="0" required />
      <label>Date d'achat</label>
      <input type="date" id="modal-stock-date" value="${new Date().toISOString().slice(0, 10)}" />
      <label>Source</label>
      <select id="modal-stock-source">
        <option value="Leboncoin">Leboncoin</option>
        <option value="Vinted">Vinted</option>
        <option value="CardMarket">CardMarket</option>
        <option value="Salon">Salon</option>
        <option value="Autre">Autre</option>
      </select>
      <label>Quantité</label>
      <input type="number" id="modal-stock-qty" min="1" value="1" />
      <label>Notes</label>
      <input type="text" id="modal-stock-notes" placeholder="Optionnel" />
      <div class="modal-actions">
        <button type="submit" class="btn btn-accent btn-touch">Enregistrer</button>
        <button type="button" class="btn btn-ghost btn-touch" id="modal-stock-cancel">Annuler</button>
      </div>
    </form>
  `);
  if (preselectId) $("#modal-pokedex-search").value = preselectId;
  $("#modal-stock-cancel").onclick = closeAppModal;
  $("#modal-form-stock").onsubmit = async (e) => {
    e.preventDefault();
    const pid = $("#modal-pokedex-search").value;
    if (!pid) return alert("Choisissez une carte");
    try {
      await api("/api/stock", {
        method: "POST",
        body: JSON.stringify({
          pokedex_id: pid,
          prix_achat: parseFloat($("#modal-stock-prix").value),
          date_achat: $("#modal-stock-date").value || null,
          source: $("#modal-stock-source").value,
          quantite: parseInt($("#modal-stock-qty").value, 10) || 1,
          notes: $("#modal-stock-notes").value.trim() || null,
          statut: "En stock",
        }),
      });
      closeAppModal();
      await loadStock();
      loadDashboard();
    } catch (err) {
      alert(err.message);
    }
  };
}

function openRadarModal(preselectId = null) {
  openAppModal(`
    <h3>Surveiller une carte</h3>
    <form class="modal-form" id="modal-form-radar">
      ${pokedexAutocompleteInput("modal-radar-pokedex")}
      <label>Prix cible d'achat (€)</label>
      <input type="number" id="modal-radar-prix" step="0.01" min="0" required />
      <label>Marge minimum visée (%)</label>
      <input type="number" id="modal-radar-marge" step="1" min="0" placeholder="ex: 20" />
      <label>Source potentielle</label>
      <select id="modal-radar-source">
        <option value="eBay">eBay</option>
        <option value="Vinted">Vinted</option>
        <option value="Leboncoin">Leboncoin</option>
        <option value="Salon">Salon</option>
      </select>
      <label>Notes</label>
      <input type="text" id="modal-radar-notes" />
      <label><input type="checkbox" id="modal-radar-alerte" /> Alerte email quand CM ≤ prix cible</label>
      <div class="modal-actions">
        <button type="submit" class="btn btn-accent btn-touch">Enregistrer</button>
        <button type="button" class="btn btn-ghost btn-touch" id="modal-radar-cancel">Annuler</button>
      </div>
    </form>
  `);
  if (preselectId) $("#modal-radar-pokedex").value = preselectId;
  $("#modal-radar-cancel").onclick = closeAppModal;
  $("#modal-form-radar").onsubmit = async (e) => {
    e.preventDefault();
    const pid = $("#modal-radar-pokedex").value;
    if (!pid) return alert("Choisissez une carte");
    try {
      await api("/api/radar", {
        method: "POST",
        body: JSON.stringify({
          pokedex_id: pid,
          prix_cible: parseFloat($("#modal-radar-prix").value),
          marge_minimum: parseFloat($("#modal-radar-marge").value) || null,
          source_potentielle: $("#modal-radar-source").value,
          notes: $("#modal-radar-notes").value.trim() || null,
          alerte_active: $("#modal-radar-alerte").checked,
        }),
      });
      closeAppModal();
      await loadRadar();
    } catch (err) {
      alert(err.message);
    }
  };
}

$("#btn-open-stock-modal")?.addEventListener("click", () => openStockModal());
$("#btn-open-radar-modal")?.addEventListener("click", () => openRadarModal());

async function loadPokedexOptions() {
  pokedexCache = await api("/api/pokedex");
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

/** Nom affiché (sans suffixe « | Cardmarket » issu du scrape). */
function displayNom(nom) {
  if (nom == null || nom === "") return "—";
  const cleaned = String(nom)
    .replace(/\s*\|\s*Cardmarket\s*/gi, "")
    .trim();
  return cleaned || "—";
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
 * Cellule tableau : miniature + nom (+ actions optionnelles).
 * @param {object} row — doit contenir nom, image_url, extension (optionnels)
 * @param {string} [actionsHtml] — boutons Sync / supprimer (Pokédex)
 */
const LANGUE_BADGES = {
  FR: { flag: "🇫🇷", label: "FR", cls: "lang-fr" },
  EN: { flag: "🇬🇧", label: "EN", cls: "lang-en" },
  JP: { flag: "🇯🇵", label: "JP", cls: "lang-jp" },
  IT: { flag: "🇮🇹", label: "IT", cls: "lang-it" },
  DE: { flag: "🇩🇪", label: "DE", cls: "lang-de" },
  ES: { flag: "🇪🇸", label: "ES", cls: "lang-es" },
};

function langueBadge(langue) {
  const code = (langue || "FR").toUpperCase();
  const meta = LANGUE_BADGES[code] || LANGUE_BADGES.FR;
  return `<span class="lang-badge ${meta.cls}" title="Langue ${meta.label}">${meta.flag} ${meta.label}</span>`;
}

function cardCell(row, actionsHtml = "") {
  const name = displayNom(row?.nom);
  const imageUrl = row?.image_url || null;
  const tooltip = [name, row?.extension].filter((x) => x && x !== "—").join(" — ");
  const thumb = thumbHtml(imageUrl, tooltip);
  const extHtml = row?.extension
    ? `<br><small style="color:var(--muted)">${escapeAttr(row.extension)}</small>`
    : "";
  const langHtml = `<div class="card-lang">${langueBadge(row?.langue)}</div>`;
  const actions = actionsHtml
    ? `<div class="card-actions">${actionsHtml}</div>`
    : "";
  return (
    `<div class="card-cell">` +
    `${thumb}` +
    `<div class="card-cell-info">${langHtml}<strong>${escapeAttr(name)}</strong>${extHtml}</div>` +
    `${actions}` +
    `</div>`
  );
}

function iconSync() {
  return (
    `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">` +
    `<path fill="currentColor" d="M12 4V1L8 5l4 4V6c3.31 0 6 2.69 6 6 0 1.01-.25 1.97-.7 2.8l1.46 1.46A7.94 7.94 0 0 0 20 12c0-4.42-3.58-8-8-8zm0 14c-3.31 0-6-2.69-6-6 0-1.01.25-1.97.7-2.8L5.24 7.74A7.94 7.94 0 0 0 4 12c0 4.42 3.58 8 8 8v3l4-4-4-4v3z"/>` +
    `</svg>`
  );
}

function iconTrash() {
  return (
    `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">` +
    `<path fill="currentColor" d="M9 3h6l1 2h4v2H4V5h4l1-2zm1 6h2v9h-2V9zm4 0h2v9h-2V9zM7 9h2v9H7V9zm-1 11h10a2 2 0 0 0 2-2V9H4v9a2 2 0 0 0 2 2z"/>` +
    `</svg>`
  );
}

function pokedexActionsHtml(id) {
  return (
    `<div class="pokedex-actions">` +
    `<button type="button" class="btn-icon btn-icon-sync btn-scrape-one" data-id="${id}" title="Rescraper cette carte" aria-label="Rescraper">${iconSync()}</button>` +
    `<button type="button" class="btn-icon btn-icon-delete btn-delete btn-delete-pokedex" data-id="${id}" title="Supprimer" aria-label="Supprimer">${iconTrash()}</button>` +
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

function fmtEur(v) {
  if (v == null || v === "") return "—";
  return `${Number(v).toFixed(2)} €`;
}

function ebayLinkIcon(url) {
  if (!url) return "";
  return (
    `<a href="${escapeAttr(url)}" target="_blank" rel="noopener noreferrer" ` +
    `class="ebay-link" title="Recherche eBay personnalisée" aria-label="Ouvrir eBay">` +
    `<svg class="ebay-link-icon" viewBox="0 0 24 24" aria-hidden="true">` +
    `<path fill="currentColor" d="M4 6h16v2H4V6zm0 5h10v2H4v-2zm0 5h16v2H4v-2z"/>` +
    `</svg></a>`
  );
}

function ebayCell(row) {
  const icon = ebayLinkIcon(row.ebay_url);
  if (row.prix_moyen_ebay == null) {
    return icon ? `<span class="ebay-cell">${icon}<span class="muted">—</span></span>` : "—";
  }
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
  const nb = row.nb_ventes_ebay ?? 0;
  const tip = [
    `Min: ${fmtEur(row.prix_min_ebay)}`,
    `Max: ${fmtEur(row.prix_max_ebay)}`,
    `${nb} vente(s) sur 60j`,
    row.ebay_keyword ? `Keyword: ${row.ebay_keyword}` : null,
  ].filter(Boolean).join(" · ");
  return (
    `<span class="ebay-cell">` +
    `${icon}` +
    `<span class="ebay-price ${cls}" title="${escapeAttr(tip)}">` +
    `<span class="ebay-tag">${tag}</span> ${fmtEur(row.prix_moyen_ebay)} ` +
    `<small class="ebay-nb">(${nb})</small></span></span>`
  );
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

function switchTab(tab) {
  $$("#tabs button, #bottom-nav button").forEach((b) => {
    b.classList.toggle("active", b.dataset.tab === tab);
  });
  $$(".panel").forEach((p) => p.classList.remove("active"));
  const panel = $(`#panel-${tab}`);
  if (panel) panel.classList.add("active");
  loadTab(tab);
}

$$("#tabs button").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
});

$$("#bottom-nav button").forEach((btn) => {
  btn.addEventListener("click", () => switchTab(btn.dataset.tab));
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
    <div class="kpi-card kpi-card-highlight"><div class="label">Valeur stock (médiane)</div><div class="value">${fmtEur(k.valeur_stock)}</div></div>
    <div class="kpi-card"><div class="label">Valeur CM</div><div class="value">${fmtEur(k.valeur_stock_cm)}</div></div>
    <div class="kpi-card"><div class="label">Valeur eBay actif</div><div class="value">${fmtEur(k.valeur_stock_ebay)}</div></div>
    <div class="kpi-card"><div class="label">Valeur Vinted</div><div class="value">${fmtEur(k.valeur_stock_vinted)}</div></div>
    <div class="kpi-card kpi-card-highlight"><div class="label">Marge latente (réf.)</div><div class="value ${margeCls}">${fmtEur(marge)}</div></div>
    <div class="kpi-card"><div class="label">CA total</div><div class="value">${fmtEur(k.ca_total)}</div></div>
    <div class="kpi-card"><div class="label">Bénéfice net</div><div class="value">${fmtEur(k.benefice_net)}</div></div>
    <div class="kpi-card"><div class="label">Marge moyenne</div><div class="value">${k.marge_moyenne_pct}%</div></div>
    <div class="kpi-card"><div class="label">Cartes Pokédex</div><div class="value">${k.nb_cartes_pokedex}</div></div>
    <div class="kpi-card"><div class="label">En stock</div><div class="value">${k.nb_en_stock}</div></div>
    <div class="kpi-card"><div class="label">Radar</div><div class="value">${k.nb_radar}</div></div>
    <div class="kpi-card"><div class="label">Vendus</div><div class="value">${k.nb_vendus ?? 0}</div></div>
  `;

    const opps = k.opportunities || [];
    $("#dash-opportunities").innerHTML = opps.length
      ? opps.map((o) => `
        <div class="opp-item">
          <div><strong>${escapeAttr(displayNom(o.nom))}</strong><br><small class="muted">${escapeAttr(o.detail || "")}</small></div>
          <span class="badge badge-green">${o.type === "radar" ? "🎯 Radar" : "📦 Stock"}</span>
        </div>`).join("")
      : `<p class="muted">Aucune opportunité pour le moment.</p>`;

    const tops = k.top_marges || [];
    $("#dash-top-marges").innerHTML = tops.length
      ? `<table class="data-table"><thead><tr><th>Carte</th><th>Achat</th><th>Réf.</th><th>Marge lat.</th></tr></thead><tbody>
        ${tops.map((t) => `<tr>
          <td>${escapeAttr(displayNom(t.nom))}<br><small class="muted">${escapeAttr(t.extension || "")}</small></td>
          <td>${fmtEur(t.prix_achat)}</td>
          <td>${fmtEur(t.prix_reference ?? t.prix_actuel)}</td>
          <td class="marge-pos">${fmtEur(t.marge_latente)}</td>
        </tr>`).join("")}
        </tbody></table>`
      : `<p class="muted">Pas encore de marges en stock.</p>`;
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
  lastChartData = data;
  const labels = data.labels || [];
  const viewMap = {
    cm: { data: data.valeur_stock_cm || data.valeur_stock || [], label: "Valeur CM (€)", color: "#60a5fa", fill: "rgba(96, 165, 250, 0.15)" },
    ebay: { data: data.valeur_stock_ebay || [], label: "Valeur eBay actif (€)", color: "#4ade80", fill: "rgba(74, 222, 128, 0.15)" },
    mediane: { data: data.valeur_stock_mediane || data.valeur_stock || [], label: "Valeur médiane (€)", color: "#E8436A", fill: "rgba(232, 67, 106, 0.15)" },
  };
  const series = viewMap[chartView] || viewMap.mediane;
  const common = {
    responsive: true,
    maintainAspectRatio: true,
    plugins: { legend: { display: false } },
    scales: {
      x: { ticks: { color: "#9b95ad", maxRotation: 45 }, grid: { color: "#3d3d5c" } },
      y: { ticks: { color: "#9b95ad" }, grid: { color: "#3d3d5c" } },
    },
  };

  if (chartStock) chartStock.destroy();
  if (chartCa) chartCa.destroy();

  chartStock = new Chart($("#chart-stock"), {
    type: "line",
    data: {
      labels,
      datasets: [{
        label: series.label,
        data: series.data,
        borderColor: series.color,
        backgroundColor: series.fill,
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
        backgroundColor: "rgba(255, 183, 197, 0.7)",
        borderColor: "#FFB7C5",
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
    tb.innerHTML = `<tr><td colspan="5" class="empty">Aucune carte — recherchez un nom ci-dessus</td></tr>`;
    return;
  }
  const renderRow = (r) => `
    <tr>
      <td class="card-col">${cardCell(r)}</td>
      <td>${r.extension || "—"}</td>
      <td>${r.etat || "—"}</td>
      <td class="market-prices-col">${marketPricesBlock(r)}</td>
      <td>${fmtDate(r.derniere_maj)}</td>
      <td class="actions-col">${pokedexActionsHtml(r.id)}</td>
    </tr>`;

  tb.innerHTML = rows.map(renderRow).join("");

  const cardsEl = $("#pokedex-cards");
  if (cardsEl) {
    cardsEl.innerHTML = rows.map((r) => `
      <article class="item-card">
        <div class="item-card-head">${cardCell(r)}</div>
        <dl class="item-card-body">
          <dt>Prix de marché</dt><dd>${marketPricesBlock(r)}</dd>
          <dt>Extension</dt><dd>${escapeAttr(r.extension || "—")}</dd>
        </dl>
        <div class="item-card-actions">${pokedexActionsHtml(r.id)}</div>
      </article>
    `).join("");
  }

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
      btn.classList.add("is-loading");
      btn.disabled = true;
      try {
        await api(`/api/pokedex/${btn.dataset.id}/scrape`, { method: "POST" });
        await loadPokedex();
      } catch (e) {
        alert(e.message);
      } finally {
        loader.classList.remove("show");
        btn.classList.remove("is-loading");
        btn.disabled = false;
      }
    };
  });
}

$("#btn-scrape-all")?.addEventListener("click", async () => {
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
});

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
    const score = opportunityScore(r);
    return `<tr>
      <td>${thumbHtml(r.image_url, displayNom(r.nom))}</td>
      <td><strong>${escapeAttr(displayNom(r.nom))}</strong><br><small class="muted">${escapeAttr(r.extension || "")}</small></td>
      <td>${langueBadge(r.langue)}</td>
      <td>${fmtEur(r.prix_achat)}</td>
      <td class="market-prices-col">${marketPricesBlock(r)}</td>
      <td class="${mCls}">${m != null ? fmtEur(m) : "—"}</td>
      <td class="${score.cls}">${score.emoji} ${score.label}</td>
      <td><span class="badge badge-muted">${r.statut}</span></td>
      <td><button type="button" class="btn-delete btn-delete-stock btn-sm" data-id="${r.id}" title="Supprimer">X</button></td>
    </tr>`;
  }).join("");

  const cardsEl = $("#stock-cards");
  if (cardsEl) {
    cardsEl.innerHTML = rows.map((r) => {
      const score = opportunityScore(r);
      return `<article class="item-card">
        <div class="item-card-head">${cardCell(r)}</div>
        <dl class="item-card-body">
          <dt>Achat</dt><dd>${fmtEur(r.prix_achat)}</dd>
          <dt>Prix de marché</dt><dd>${marketPricesBlock(r)}</dd>
          <dt>Score</dt><dd class="${score.cls}">${score.emoji} ${score.label}</dd>
        </dl>
        <div class="item-card-actions">
          <button type="button" class="btn-delete btn-delete-stock btn-sm" data-id="${r.id}">Supprimer</button>
        </div>
      </article>`;
    }).join("");
  }

  bindStockQtyControls(tb);

  $$(".btn-delete-stock").forEach((btn) => {
    btn.onclick = () => deleteRow(`/api/stock/${btn.dataset.id}`, loadStock);
  });
}

$("#stock-filter")?.addEventListener("change", loadStock);

// Radar
async function loadRadar() {
  const rows = await api("/api/radar");
  const tb = $("#radar-tbody");
  if (!rows.length) {
    tb.innerHTML = `<tr><td colspan="7" class="empty">Radar vide</td></tr>`;
    return;
  }
  tb.innerHTML = rows.map((r) => {
    const ref = prixReference(r);
    const margeEst = ref != null && r.prix_cible
      ? Math.round(((ref - r.prix_cible) / r.prix_cible) * 100)
      : null;
    return `<tr>
      <td>${thumbHtml(r.image_url, displayNom(r.nom))}</td>
      <td><strong>${escapeAttr(displayNom(r.nom))}</strong></td>
      <td>${langueBadge(r.langue)}</td>
      <td class="market-prices-col">${marketPricesBlock(r)}</td>
      <td>${fmtEur(r.prix_cible)}</td>
      <td>${margeEst != null ? `${margeEst}%` : "—"}</td>
      <td>${urgenceBadge(r.urgence)}</td>
      <td>
        <label class="toggle-switch" title="Alerte email">
          <input type="checkbox" class="radar-alerte-toggle" data-id="${r.id}" ${r.alerte_active ? "checked" : ""} />
          <span class="toggle-slider"></span>
        </label>
      </td>
      <td><button type="button" class="btn-delete btn-delete-radar btn-sm" data-id="${r.id}">X</button></td>
    </tr>`;
  }).join("");

  const cardsEl = $("#radar-cards");
  if (cardsEl) {
    cardsEl.innerHTML = rows.map((r) => `
      <article class="item-card">
        <div class="item-card-head">${cardCell(r)}</div>
        <dl class="item-card-body">
          <dt>Cible</dt><dd>${fmtEur(r.prix_cible)}</dd>
          <dt>Prix de marché</dt><dd>${marketPricesBlock(r)}</dd>
          <dt>Urgence</dt><dd>${urgenceBadge(r.urgence)}</dd>
        </dl>
        <div class="item-card-actions">
          <button type="button" class="btn-delete btn-delete-radar btn-sm" data-id="${r.id}">Supprimer</button>
        </div>
      </article>
    `).join("");
  }

  $$(".btn-delete-radar").forEach((btn) => {
    btn.onclick = () => deleteRow(`/api/radar/${btn.dataset.id}`, loadRadar);
  });

  $$(".radar-alerte-toggle").forEach((cb) => {
    cb.onchange = async () => {
      try {
        await api(`/api/radar/${cb.dataset.id}`, {
          method: "PUT",
          body: JSON.stringify({ alerte_active: cb.checked }),
        });
        if (cb.checked) console.info("[Hajime] Alerte activée — notification email à brancher (Edge Function)");
      } catch (err) {
        alert(err.message);
        cb.checked = !cb.checked;
      }
    };
  });
}

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
      <td>${displayNom(r.nom)}</td>
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
initUniversalSearchBars();
loadDashboard();
loadPokedexOptions().catch(() => {});

$$(".chart-view-toggle .seg").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$(".chart-view-toggle .seg").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    chartView = btn.dataset.view || "mediane";
    if (lastChartData) renderCharts(lastChartData);
  });
});

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
