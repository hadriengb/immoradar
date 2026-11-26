"use strict";
// ImmoRadar — interface sans framework.
const $ = (s, el = document) => el.querySelector(s);
const app = $("#app");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const eur = v => v == null ? '<span class="unk">inconnu</span>' : (Math.round(v) || 0).toLocaleString("fr-FR") + " €";
const num = (v, u = "") => v == null ? '<span class="unk">inconnu</span>' : (+v).toLocaleString("fr-FR", {maximumFractionDigits: 2}) + u;
const pct = v => v == null ? "—" : (+v).toLocaleString("fr-FR", {maximumFractionDigits: 1}) + " %";
const dfr = s => s ? new Date(s.length <= 10 ? s + "T12:00:00" : s).toLocaleDateString("fr-FR", {weekday: s.length <= 10 ? "short" : undefined, day: "numeric", month: "short", year: "numeric"}) : null;
const dtfr = s => s ? new Date(s).toLocaleString("fr-FR", {day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit"}) : "jamais";
const TYPES = {appartement: "Appartement", maison: "Maison", studio: "Studio", immeuble: "Immeuble", local_commercial: "Local commercial", parking: "Parking", terrain: "Terrain"};
const STATUS_CLS = {"nouvelle": "b-ok", "à venir": "", "adjugée": "b-mute", "retirée": "b-bad"};
const SRC_CLS = {ok: "b-ok", vide: "b-warn", erreur: "b-bad", "bloquée": "b-bad", manuel: "b-mute", "désactivée": "b-mute"};

async function api(path, opts = {}) {
  const r = await fetch(path, {headers: {"Content-Type": "application/json"}, ...opts});
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
const post = (p, body) => api(p, {method: "POST", body: JSON.stringify(body)});

let PARAMS = null, DEFS = null, compareSel = new Set(), pollTimer = null;

async function loadParams() { const j = await api("/api/params"); PARAMS = j.params; DEFS = j; return j; }

function scoreBox(label, v) {
  if (v == null) return `<div class="score s-mute"><span>${label}</span><span class="muted small">non notée</span></div>`;
  const cls = v >= 60 ? "s-hi" : v >= 40 ? "s-mid" : "s-lo";
  return `<div class="score ${cls}"><span>${label}</span><b>${v}</b></div>`;
}

function cardHtml(c, opts = {}) {
  const type = TYPES[c.type] || "Type inconnu";
  const srcs = (c.sources || []).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener" onclick="event.stopPropagation()">${esc(s.source.replace("manuel:", "import manuel · "))}</a>`).join(", ");
  const risks = c.risks.filter(r => r.severity >= 2).map(r => `<span class="badge ${r.severity >= 3 ? "b-bad" : "b-warn"}">${esc(r.label)}</span>`).join(" ");
  const est = c.estimation.status === "ok" ? `Estimation ${eur(c.estimation.central)} · confiance ${esc(c.estimation.confidence_label)} (${c.estimation.confidence})` : `<span class="unk">Estimation impossible (données insuffisantes)</span>`;
  return `<div class="card" data-id="${c.id}">
    <div>${c.photo ? `<img class="thumb" loading="lazy" src="${esc(c.photo)}" alt="" onerror="this.outerHTML='<div class=thumb>photo indisponible</div>'">` : `<div class="thumb">pas de photo</div>`}</div>
    <div>
      <div class="row space"><span class="t">${esc(type)}${c.rooms ? " · " + c.rooms + " p." : ""}${c.surface ? " · " + num(c.surface, " m²") : ""}</span>
        <span class="row"><span class="badge ${STATUS_CLS[c.status] ?? "b-mute"}">${esc(c.status)}</span>
        <button class="star ${c.favorite ? "on" : ""}" data-fav="${c.id}" title="Favori">${c.favorite ? "★" : "☆"}</button></span></div>
      <div>${esc(c.address || "")} ${esc(c.city || "Ville inconnue")} ${c.dep ? "(" + esc(c.dep) + ")" : ""}</div>
      <div class="meta">Mise à prix <b>${eur(c.mise_a_prix)}</b>${c.adjudication_price ? " · adjugé " + eur(c.adjudication_price) : ""} · vente ${c.sale_date ? esc(dfr(c.sale_date)) + (c.sale_time ? " " + esc(c.sale_time) : "") : '<span class="unk">date inconnue</span>'}${c.sale_place ? " · " + esc(c.sale_place) : ""}</div>
      <div class="meta">${est}${c.decote_pct != null ? " · décote apparente " + pct(c.decote_pct) : ""}</div>
      <div class="meta">Source : ${srcs || "—"} · ${esc(c.sale_kind || "type de vente inconnu")}</div>
      ${c.verdict ? `<div class="meta verdict-line"><b>Avis :</b> ${esc(c.verdict)}</div>` : ""}
      ${c.doc_count ? `<div class="meta">📄 ${c.doc_count} document(s) analysé(s)${c.doc_conflicts.length ? ` · <span style="color:var(--warn)">${c.doc_conflicts.length} écart(s) avec l'annonce</span>` : ""}</div>` : ""}
      ${risks ? `<div class="row" style="margin-top:4px">${risks}</div>` : ""}
    </div>
    <div class="scores">
      ${scoreBox("Achat-revente", c.score_revente)}
      ${scoreBox("Locatif", c.score_locatif)}
      <div class="small muted">${c.marge_nette_pct != null ? "Marge nette " + pct(c.marge_nette_pct) + "<br>" : ""}${c.rendement_net_pct != null ? "Rendement net " + pct(c.rendement_net_pct) + "<br>" : ""}${c.prix_max_revente || c.prix_max_locatif ? "Prix max : " + [c.prix_max_revente && "revente " + eur(c.prix_max_revente), c.prix_max_locatif && "locatif " + eur(c.prix_max_locatif)].filter(Boolean).join(" · ") : ""}</div>
      ${opts.compare ? `<label class="inline" onclick="event.stopPropagation()"><input type="checkbox" data-cmp="${c.id}" ${compareSel.has(c.id) ? "checked" : ""}> comparer</label>` : ""}
    </div>
  </div>`;
}

function bindCards(root, reload) {
  root.querySelectorAll(".card").forEach(el => el.addEventListener("click", () => openDetail(+el.dataset.id, reload)));
  root.querySelectorAll("[data-fav]").forEach(b => b.addEventListener("click", async e => {
    e.stopPropagation();
    await post("/api/listing/" + b.dataset.fav, {favorite: !b.classList.contains("on")});
    reload();
  }));
  root.querySelectorAll("[data-cmp]").forEach(cb => cb.addEventListener("change", () => {
    cb.checked ? compareSel.add(+cb.dataset.cmp) : compareSel.delete(+cb.dataset.cmp);
    const b = $("#cmp-btn"); if (b) b.textContent = `Comparer (${compareSel.size})`;
  }));
}

// ------------------------------------------------------------------ Tableau de bord
async function viewDash() {
  const s = await api("/api/state");
  const lr = s.last_run;
  const srcRows = (lr ? lr.sources : s.sources_def.map(d => ({...d, status: "jamais interrogée", message: ""}))).map(x => `
    <tr><td><b>${esc(x.label)}</b><div class="small muted">${esc(x.legal)}</div></td>
    <td><span class="badge ${SRC_CLS[x.status] || "b-mute"}">${esc(x.status)}</span></td>
    <td class="small">${esc(x.message)}${x.at ? `<div class="muted">${dtfr(x.at)}</div>` : ""}
    ${x.mode === "manuel" && x.links ? `<details><summary>Ouvrir la source et importer à la main</summary>${x.links.map(l => `<a href="${esc(l.url)}" target="_blank" rel="noopener">${esc(l.label)}</a>`).join("<br>")}<br><a href="#/parametres" >→ formulaire d'import manuel</a></details>` : ""}</td></tr>`).join("");
  const dvf = s.dvf.departements.length ? s.dvf.departements.map(d => `${esc(d.dep)} (${esc(d.years)} · ${d.n.toLocaleString("fr-FR")} ventes)`).join(", ") : "aucune donnée chargée";
  app.innerHTML = `
  <button class="bigbtn" id="go" ${s.running ? "disabled" : ""}>${s.running ? "⏳ Recherche en cours… " + esc(s.step) : "🔎 LANCER UNE RECHERCHE"}</button>
  <div class="grid g4" style="margin-top:14px">
    <div class="panel kpi"><div class="v">${s.matching}</div><div class="l">annonces correspondant aux critères<br><span class="small">${s.total_stored} enregistrées au total</span></div></div>
    <div class="panel kpi"><div class="v">${s.new}</div><div class="l">nouvelles (dernière recherche)</div></div>
    <div class="panel kpi"><div class="v">${s.opportunities}</div><div class="l">opportunités (note ≥ ${s.seuil})</div></div>
    <div class="panel kpi"><div class="v" style="font-size:17px">${lr ? dtfr(lr.finished_at || lr.started_at) : "jamais"}</div><div class="l">dernière actualisation${lr ? " · " + esc(lr.status) : ""}</div></div>
  </div>
  ${s.running || (s.log && s.log.length) ? `<h2>Journal de la recherche</h2><div class="log" id="log">${esc(s.log.join("\n"))}</div>` : ""}
  <h2>État des sources</h2>
  <div class="panel"><table><thead><tr><th>Source</th><th>État</th><th>Détail</th></tr></thead><tbody>${srcRows}</tbody></table></div>
  <h2>Données d'estimation</h2>
  <div class="panel small">Stockage de vos annonces : ${esc(s.storage)}<br>Lecture des documents : ${esc(s.doc_reader)}<br>DVF (ventes réelles) : ${dvf}${s.dvf.derniere_vente ? ` · vente la plus récente : ${esc(dfr(s.dvf.derniere_vente))}` : ""}<br>
  Carte des loyers 2025 : ${s.loyers.length ? s.loyers.map(l => esc(l.kind) + " " + l.n + " communes").join(", ") : "non chargée"}
  ${lr && lr.summary && lr.summary.loyers ? `<div class="muted">${Object.entries(lr.summary.loyers).map(([k, v]) => esc(k) + " : " + esc(v)).join(" · ")}</div>` : ""}</div>
  <h2>Meilleures opportunités</h2>
  <div class="cards" id="top">${s.top.length ? s.top.map(c => cardHtml(c)).join("") : '<p class="muted">Aucune annonce enregistrée pour le moment. Lancez une recherche ou importez une annonce.</p>'}</div>`;
  $("#go").onclick = async () => { await post("/api/search", {}); viewDash(); };
  bindCards($("#top"), viewDash);
  const lg = $("#log"); if (lg) lg.scrollTop = lg.scrollHeight;
  clearTimeout(pollTimer);
  if (s.running) pollTimer = setTimeout(() => { if (location.hash === "" || location.hash === "#/") viewDash(); }, 2000);
}

// ------------------------------------------------------------------ Annonces + filtres
function filtersHtml(c) {
  const ck = (name, val, label, arr) => `<label class="inline"><input type="checkbox" data-arr="${name}" value="${val}" ${(arr || []).includes(val) ? "checked" : ""}>${label}</label>`;
  const deps = Object.entries(DEFS.departements).filter(([k]) => Object.values(DEFS.zones).some(z => z.deps.includes(k)));
  const inp = (k, ph) => `<input type="number" data-k="${k}" value="${c[k] ?? ""}" placeholder="${ph}">`;
  return `<div class="panel filters" id="filters">
    <div class="row space"><b>Filtres</b><button id="f-reset" class="small">Réinitialiser</button></div>
    <label>Zones</label>${Object.entries(DEFS.zones).map(([k, z]) => ck("zones", k, z.label, c.zones)).join("")}
    <label>Départements (vide = toute la zone)</label>${deps.map(([k, n]) => ck("departements", k, k + " " + n, c.departements)).join("")}
    <label>Villes (séparées par des virgules)</label><input data-k="villes" value="${esc((c.villes || []).join(", "))}" placeholder="ex. Paris, Lyon, Villeurbanne">
    <label>Type de bien</label>${[["appartement", "Appartement"], ["maison", "Maison"], ["studio", "Studio"], ["t1", "T1"], ["t2", "T2"], ["t3", "T3"], ["t4+", "T4 et +"], ["immeuble", "Immeuble"], ["local_commercial", "Local commercial"]].map(([v, l]) => ck("types", v, l, c.types)).join("")}
    <div class="grid g2"><div><label>Surface min</label>${inp("surface_min", "m²")}</div><div><label>Surface max</label>${inp("surface_max", "m²")}</div>
    <div><label>Pièces min</label>${inp("pieces_min", "")}</div><div><label>Pièces max</label>${inp("pieces_max", "")}</div>
    <div><label>Mise à prix min</label>${inp("mise_a_prix_min", "€")}</div><div><label>Mise à prix max</label>${inp("mise_a_prix_max", "€")}</div>
    <div><label>Adjudication min</label>${inp("adjudication_min", "€")}</div><div><label>Adjudication max</label>${inp("adjudication_max", "€")}</div>
    <div><label>Vente après le</label><input type="date" data-k="date_min" value="${c.date_min || ""}"></div><div><label>Vente avant le</label><input type="date" data-k="date_max" value="${c.date_max || ""}"></div></div>
    <label>Occupation</label><select data-k="occupation"><option value="">Indifférent</option><option value="libre" ${c.occupation === "libre" ? "selected" : ""}>Libre</option><option value="occupé" ${c.occupation === "occupé" ? "selected" : ""}>Occupé</option></select>
    <label>Extérieurs / annexes (exigés)</label>${["terrasse", "balcon", "jardin", "parking"].map(v => ck("exterieurs", v, v, c.exterieurs)).join("")}
    <label>Type de vente</label>${["judiciaire", "notariale", "domaniale"].map(v => ck("types_vente", v, v, c.types_vente)).join("")}
    <label>Options</label>
    <label class="inline"><input type="checkbox" data-bool="inclure_passees" ${c.inclure_passees ? "checked" : ""}>inclure les ventes passées</label>
    <label class="inline"><input type="checkbox" data-bool="inclure_inconnus" ${c.inclure_inconnus !== false ? "checked" : ""}>garder les annonces dont l'info filtrée est inconnue</label>
    <p class="small muted">Pas de plafond budgétaire imposé. Les filtres sont enregistrés comme critères par défaut.</p>
  </div>`;
}

function readFilters() {
  const c = {...PARAMS.criteres};
  const f = $("#filters");
  ["zones", "departements", "types", "exterieurs", "types_vente"].forEach(k => c[k] = [...f.querySelectorAll(`[data-arr="${k}"]:checked`)].map(i => i.value));
  f.querySelectorAll("[data-k]").forEach(i => {
    const k = i.dataset.k;
    if (k === "villes") c[k] = i.value.split(",").map(s => s.trim()).filter(Boolean);
    else if (i.type === "number") c[k] = i.value === "" ? null : +i.value;
    else c[k] = i.value || null;
  });
  if (c.occupation === null) c.occupation = "";
  f.querySelectorAll("[data-bool]").forEach(i => c[i.dataset.bool] = i.checked);
  return c;
}

let sortSel = "interet", saveTimer = null;
async function viewAnnonces() {
  await loadParams();
  app.innerHTML = `<div class="layout">${filtersHtml(PARAMS.criteres)}<div>
    <div class="row space" style="margin-bottom:10px"><h1 style="margin:0">Annonces <span class="muted" id="count"></span></h1>
    <div class="row"><label class="inline">Trier par</label><select id="sort" style="width:auto">
      ${[["interet", "Intérêt (meilleure note)"], ["revente", "Note achat-revente"], ["locatif", "Note locatif"], ["rentabilite", "Rendement net"], ["marge", "Marge achat-revente"], ["decote", "Décote"], ["prix", "Prix croissant"], ["prix_desc", "Prix décroissant"], ["date", "Date de vente"], ["surface", "Surface"]].map(([v, l]) => `<option value="${v}" ${v === sortSel ? "selected" : ""}>${l}</option>`).join("")}
    </select><button id="imp">+ Import manuel</button></div></div>
    <div class="cards" id="list"></div></div></div>`;
  const refresh = async () => {
    const crit = readFilters();
    const list = await api("/api/listings?sort=" + sortSel + "&criteria=" + encodeURIComponent(JSON.stringify(crit)));
    $("#count").textContent = `(${list.length})`;
    $("#list").innerHTML = list.length ? list.map(c => cardHtml(c)).join("") : `<div class="panel muted">Aucune annonce ne correspond. Élargissez les filtres ou lancez une recherche depuis le tableau de bord.</div>`;
    bindCards($("#list"), refresh);
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => { PARAMS.criteres = crit; post("/api/params", {criteres: crit}); }, 800);
  };
  $("#filters").addEventListener("change", refresh);
  $("#f-reset").onclick = async () => { await post("/api/params", {criteres: DEFS.defaults.criteres}); viewAnnonces(); };
  $("#sort").onchange = e => { sortSel = e.target.value; refresh(); };
  $("#imp").onclick = () => openImport(refresh);
  refresh();
}

// ------------------------------------------------------------------ Favoris + comparaison
async function viewFavoris() {
  const list = await api("/api/listings?fav=1&sort=interet");
  app.innerHTML = `<div class="row space"><h1>Favoris (${list.length})</h1><button class="primary" id="cmp-btn">Comparer (${compareSel.size})</button></div>
  <p class="muted small">Cochez « comparer » sur 2 à 4 biens.</p><div class="cards" id="list">${list.length ? list.map(c => cardHtml(c, {compare: true})).join("") : '<div class="panel muted">Aucun favori. Cliquez sur ☆ sur une annonce.</div>'}</div><div id="cmp"></div>`;
  bindCards($("#list"), viewFavoris);
  $("#cmp-btn").onclick = async () => {
    if (compareSel.size < 2) { $("#cmp").innerHTML = '<div class="warn">Sélectionnez au moins 2 biens.</div>'; return; }
    const ds = await api("/api/compare?ids=" + [...compareSel].join(","));
    const rows = [
      ["Bien", d => esc((TYPES[d.type] || "?") + " " + (d.rooms ? d.rooms + "p" : ""))], ["Ville", d => esc(d.city || "?") + " (" + esc(d.dep || "?") + ")"],
      ["Surface", d => num(d.surface, " m²")], ["Mise à prix", d => eur(d.mise_a_prix)], ["Date de vente", d => esc(dfr(d.sale_date) || "inconnue")],
      ["Occupation", d => esc(d.occupation || "inconnue")], ["Estimation centrale", d => eur(d.estimation.central)], ["Confiance", d => esc(d.estimation.confidence_label || "—")],
      ["Décote apparente", d => pct(d.decote_pct)], ["Marge nette revente", d => pct(d.marge_nette_pct)], ["Rendement net", d => pct(d.rendement_net_pct)],
      ["Prix max (revente)", d => eur(d.prix_max_revente)], ["Prix max (locatif)", d => eur(d.prix_max_locatif)],
      ["Note achat-revente", d => d.score_revente ?? "—"], ["Note locatif", d => d.score_locatif ?? "—"],
      ["Risques", d => d.risks.map(r => esc(r.label)).join("<br>") || "aucun détecté"],
    ];
    $("#cmp").innerHTML = `<h2>Comparaison</h2><div class="panel" style="overflow:auto"><table><tbody>${rows.map(([l, f]) => `<tr><th>${l}</th>${ds.map(d => `<td>${f(d)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`;
  };
}

// ------------------------------------------------------------------ Fiche détaillée
function provTag(item, k) {
  const p = (item.prov || {})[k];
  if (item[k] == null) return "";
  return `<span class="prov">${p === "saisie manuelle" ? "✎ saisi" : "● " + esc(p || "source")}</span>`;
}
const NAT_CLS = {"hypothèse": "n-hyp", "estimé": "n-est", "donnée annonce": "n-don", "saisi": "n-don"};
function linesTable(lines) {
  return `<table><tbody>${lines.map(l => `<tr><td>${esc(l.label)}<div class="small muted">${esc(l.formule)}</div></td><td class="small ${NAT_CLS[l.nature.split(" ")[0]] || ""}">${esc(l.nature)}</td><td class="num">${eur(l.value)}</td></tr>`).join("")}</tbody></table>`;
}
function scoreDetail(s, label) {
  if (!s) return `<p class="muted">${label} : non notée (simulation impossible).</p>`;
  return `<h3>${label} : ${s.score}/100</h3><table><tbody>${s.parts.map(p => `<tr><td>${esc(p.label)}</td><td class="num">+${p.pts}</td></tr>`).join("")}${s.penalties.map(p => `<tr><td>${esc(p.label)}</td><td class="num" style="color:var(--bad)">${p.pts}</td></tr>`).join("")}</tbody></table>${s.cap ? `<div class="err">${esc(s.cap)}</div>` : ""}`;
}

const NATURE_CLS = { "confirmé": "n-conf", "annonce": "n-don", "estimé": "n-est", "saisi": "n-saisi" };
function avisHtml(a) {
  if (!a) return "";
  if (!a.verdict) return a.error ? `<div class="warn">${esc(a.error)}</div>` : "";
  const sec = (k, title) => (a.sections[k] || []).length ? `<h4>${title}</h4><ul class="avis">${a.sections[k].map(p =>
    `<li><span class="nat ${NATURE_CLS[p.nature] || ""}">${esc(p.nature)}</span> ${esc(p.text)}${p.ref ? ` <span class="prov">(${esc(p.ref)})</span>` : ""}</li>`).join("")}</ul>` : "";
  return `<div class="panel avis-panel"><h3>Avis ImmoRadar</h3><p class="verdict">${esc(a.verdict)}</p>
    ${sec("nature", "Ce qu'est vraiment le bien")}${sec("valeur", "Valeur réaliste")}${sec("positifs", "Points positifs")}${sec("vigilance", "Points de vigilance")}
    <h4>Prix plafond indicatif</h4><p>${esc(a.plafond_txt)}</p>
    <p class="small muted">${esc(a.note)} « confirmé » = lu dans un document (page citée) · « annonce » = lu dans l'annonce · « estimé » = calcul.</p></div>`;
}

const webUrl = w => /^https?:/i.test(w) ? w : "https://" + w;
function demandeHtml(m) {
  if (!m) return "";
  const contact = c => `<div class="contact"><b>${esc(c.name)}</b>${c.firm ? " · " + esc(c.firm) : ""} <span class="badge b-mute">${esc(c.role)}</span>
    ${c.address ? `<div class="small">${esc(c.address)}</div>` : ""}
    <div class="row small">${c.phones.map(p => `<a href="tel:${esc(p.replace(/\s/g, ""))}">📞 ${esc(p)}</a>`).join(" ")}${c.hours ? ` <span class="muted">(${esc(c.hours)})</span>` : ""}
    ${c.emails.map(e => `<a href="mailto:${esc(e)}">✉️ ${esc(e)}</a>`).join(" ")}
    ${c.websites.map(w => `<a href="${esc(webUrl(w))}" target="_blank" rel="noopener">🌐 ${esc(w)}</a>`).join(" ")}
    ${!c.phones.length && !c.emails.length && !c.websites.length ? '<span class="unk">ni téléphone, ni email, ni site dans l\'annonce</span>' : ""}</div>
    <div class="prov">lu dans : ${esc(c.source)}</div></div>`;
  const g = m.greffe;
  const noMail = !m.to.length;
  const firstPhone = m.contacts.flatMap(c => c.phones)[0], firstWeb = m.contacts.flatMap(c => c.websites)[0];
  return `<div class="panel" style="margin-top:12px" id="demande"><h3>Contacts et demande d'informations</h3>
    ${m.contacts.length ? m.contacts.map(contact).join("") : '<p class="unk">Aucun avocat ou notaire trouvé dans l\'annonce.</p>'}
    ${g ? `<p class="small">📁 Cahier des conditions de vente${g.reference ? ` (réf. greffe <b>${esc(g.reference)}</b>)` : ""}${g.lieu ? " consultable au " + esc(g.lieu) : ""}${g.cabinet_aussi ? " et au cabinet de l'avocat" : ""}. <span class="prov">lu dans : ${esc(g.source)}</span></p>` : ""}
    ${m.doc_emails.length ? `<p class="small">Adresses email lues dans les documents (vérifiez à qui elles appartiennent : souvent le commissaire de justice) : ${m.doc_emails.map(e => `<a href="#" data-addto="${esc(e.email)}">${esc(e.email)}</a> <span class="prov">(${esc(e.source)})</span>`).join(", ")}</p>` : ""}
    <h4>À demander (${m.demandes.length})</h4>
    <ul class="avis">${m.demandes.map(x => `<li>${esc(x.demande)} <span class="prov">(${esc(x.raison)})</span></li>`).join("")}</ul>
    <h4>Mail prêt à envoyer</h4>
    <div class="grid g1"><div><label>Destinataire${noMail ? " (aucune adresse email dans l'annonce : à compléter)" : " (lu dans l'annonce)"}</label><input id="dm-to" value="${esc(m.to.join(", "))}" placeholder="adresse email du cabinet"></div>
    <div><label>Objet</label><input id="dm-subject" value="${esc(m.subject)}"></div>
    <div><label>Message (modifiable)</label><textarea id="dm-body" rows="16">${esc(m.body)}</textarea></div></div>
    <div class="row" style="margin-top:8px"><button class="primary" id="dm-open">✉️ Ouvrir dans ma messagerie</button><button id="dm-copy">Copier le message</button><button id="dm-copy-subject">Copier l'objet</button><span id="dm-msg" class="small"></span></div>
    ${noMail ? `<p class="small muted">Pas d'email dans l'annonce : ${firstPhone ? `appelez le cabinet au <a href="tel:${esc(firstPhone.replace(/\s/g, ""))}">${esc(firstPhone)}</a> pour obtenir son adresse, ` : ""}${firstWeb ? `ou utilisez le formulaire de contact de <a href="${esc(webUrl(firstWeb))}" target="_blank" rel="noopener">${esc(firstWeb)}</a> en y collant le message, ` : ""}ou ouvrez le mail et ajoutez l'adresse vous-même.</p>` : ""}
    <p class="small muted">ImmoRadar n'envoie rien : le mail s'ouvre dans votre messagerie, vous relisez et vous l'envoyez. Votre signature se règle dans Paramètres.</p></div>`;
}
function bindDemande() {
  if (!$("#demande")) return;
  const copy = async (txt) => {
    try { await navigator.clipboard.writeText(txt); } catch (e) { const t = document.createElement("textarea"); t.value = txt; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); }
    $("#dm-msg").textContent = "Copié ✓"; setTimeout(() => $("#dm-msg").textContent = "", 2000);
  };
  $("#dm-open").onclick = () => {
    const to = $("#dm-to").value.split(/[,;\s]+/).filter(Boolean).map(encodeURIComponent).join(",");
    location.href = `mailto:${to}?subject=${encodeURIComponent($("#dm-subject").value)}&body=${encodeURIComponent($("#dm-body").value)}`;
  };
  $("#dm-copy").onclick = () => copy($("#dm-body").value);
  $("#dm-copy-subject").onclick = () => copy($("#dm-subject").value);
  document.querySelectorAll("[data-addto]").forEach(a => a.onclick = e => { e.preventDefault(); const v = $("#dm-to").value.trim(); $("#dm-to").value = v ? v + ", " + a.dataset.addto : a.dataset.addto; });
}

async function openDetail(id, onChange) {
  const d = await api("/api/listing/" + id);
  const it = d.item, an = d.analysis || {}, est = an.estimation || {}, sim = an.simulation || {}, sc = an.scores || {};
  const f = (k, v) => `<div>${k}</div><div>${v}</div>`;
  const val = (k, fmt) => it[k] == null ? '<span class="unk">inconnu</span>' : (fmt ? fmt(it[k]) : esc(it[k])) + provTag(it, k);
  const feats = Object.entries(it.features || {}).map(([k, v]) => `${k} : ${v === true ? "oui" : v === false ? "non" : '<span class="unk">?</span>'}`).join(" · ");
  const docs = (it.documents || []).length ? it.documents.map(x => `<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.label)}</a>`).join("<br>") :
    '<span class="unk">Aucun document trouvé dans l\'annonce (cahier des conditions de vente, PV descriptif, diagnostics, documents de copropriété : à demander à l\'avocat / au notaire ou à consulter au greffe).</span>';
  let estHtml;
  if (est.status === "ok") {
    estHtml = `<div class="est3"><div>Basse<b>${eur(est.valeur.bas)}</b>${num(est.prix_m2.bas, " €/m²")}</div><div>Centrale<b>${eur(est.valeur.central)}</b>${num(est.prix_m2.central, " €/m²")}</div><div>Haute<b>${eur(est.valeur.haut)}</b>${num(est.prix_m2.haut, " €/m²")}</div></div>
    <p class="small">Comparables : <b>${est.n_comparables}</b> ventes (${esc(est.level)}), prix/m² Q1 ${num(est.comparables_m2.q1)} · médiane ${num(est.comparables_m2.mediane)} · Q3 ${num(est.comparables_m2.q3)} €/m². ${est.n_outliers_removed} valeur(s) aberrante(s) écartée(s).<br>
    Données : ${esc(est.basis)}, ventes du ${esc(dfr(est.periode.du))} au ${esc(dfr(est.periode.au))}. Indice de confiance : <b>${est.confidence}/100 (${esc(est.confidence_label)})</b>.<br>
    ${est.adjustments.length ? "Ajustements : " + est.adjustments.map(a => esc(a.label) + " " + (a.pct > 0 ? "+" : "") + a.pct + " %").join(", ") + "<br>" : ""}<span class="muted">${esc(est.note)} Estimation statistique, jamais certaine.</span></p>
    <details><summary>Voir les ${est.comparables.length} comparables les plus proches</summary><div style="overflow:auto"><table><thead><tr><th>Date</th><th>Adresse</th><th class="num">Surface</th><th class="num">Pièces</th><th class="num">Prix</th><th class="num">€/m²</th><th class="num">Distance</th><th>Niveau</th></tr></thead><tbody>
    ${est.comparables.map(c => `<tr><td>${esc(c.date)}</td><td>${esc(c.adresse)} ${esc(c.commune)}</td><td class="num">${num(c.surface)}</td><td class="num">${c.pieces || "?"}</td><td class="num">${eur(c.prix)}</td><td class="num">${num(c.prix_m2)}</td><td class="num">${c.distance_m != null ? c.distance_m + " m" : "?"}</td><td class="small">${esc(c.niveau)}</td></tr>`).join("")}</tbody></table></div></details>`;
  } else {
    estHtml = `<div class="warn"><b>Pas d'estimation :</b> ${(est.reasons || ["analyse non encore effectuée"]).map(esc).join(" ")}</div>`;
  }
  const ar = sim.achat_revente, lo = sim.locatif;
  const arHtml = ar ? `<div class="grid g3"><div class="panel kpi"><div class="v">${eur(ar.benefice_net)}</div><div class="l">bénéfice net estimé</div></div><div class="panel kpi"><div class="v">${pct(ar.marge_nette_pct)}</div><div class="l">marge nette / revente · rentabilité ${pct(ar.rentabilite_pct)} du coût</div></div><div class="panel kpi"><div class="v">${eur(ar.prix_max_conseille)}</div><div class="l">prix max conseillé (${esc(ar.prix_max_regle)})</div></div></div>
    <p class="small muted">Coût total ${eur(ar.cout_total)} · revente estimée ${eur(ar.revente)} · scénario bas (revente ${eur(ar.scenario_bas.revente)}) : bénéfice ${eur(ar.scenario_bas.benefice_net)}</p>
    <details><summary>Détail du calcul</summary>${linesTable(ar.lines)}</details>` : `<p class="muted">Non calculé.</p>`;
  const loHtml = lo ? `<div class="grid g3"><div class="panel kpi"><div class="v">${eur(lo.loyer_mensuel)}</div><div class="l">loyer mensuel HC estimé${lo.loyer_cc ? " (" + eur(lo.loyer_cc) + " CC)" : ""}</div></div><div class="panel kpi"><div class="v">${pct(lo.rendement_net_pct)}</div><div class="l">rendement net avant impôt · brut ${pct(lo.rendement_brut_pct)} (brut/prix seul ${pct(lo.rendement_brut_sur_prix_pct)})</div></div><div class="panel kpi"><div class="v">${eur(lo.cashflow_mensuel)}</div><div class="l">cash-flow mensuel après financement</div></div></div>
    <p class="small muted">Revenus annuels ${eur(lo.revenus_annuels)} · prix max conseillé ${eur(lo.prix_max_conseille)} (${esc(lo.prix_max_regle)})</p>
    <details><summary>Détail du calcul</summary>${linesTable(lo.lines)}</details>` : `<p class="muted">Non calculé.</p>`;
  const ov = d.overrides || {};
  const oi = (k, label, type = "number") => `<div><label>${label}</label><input type="${type}" data-ov="${k}" value="${esc(ov[k] ?? "")}" placeholder="${esc(it[k] ?? "")}"></div>`;
  $("#modal-body").innerHTML = `
    <div class="row space"><h1 style="margin:0">${esc(TYPES[it.type] || "Bien")} ${it.rooms ? it.rooms + " pièces" : ""} — ${esc(it.city || "ville inconnue")}</h1>
      <div class="row"><span class="badge ${STATUS_CLS[d.status] ?? "b-mute"}">${esc(d.status)}</span><button id="d-fav">${d.favorite ? "★ Favori" : "☆ Ajouter aux favoris"}</button><button id="d-hide">${d.hidden ? "Ré-afficher" : "Masquer"}</button></div></div>
    <p class="small legend muted"><span class="n-don">● donnée lue dans l'annonce (source indiquée)</span><span class="n-est">estimé</span><span class="n-hyp">hypothèse modifiable</span><span>✎ saisi par vous</span><span class="unk">inconnu</span></p>
    ${avisHtml(an.avis)}
    ${(it.photos || []).length ? `<div class="photos">${it.photos.map(p => `<img src="${esc(p)}" alt="" loading="lazy" onerror="this.remove()">`).join("")}</div>` : '<p class="muted small">Pas de photo disponible.</p>'}
    <div class="grid g2" style="margin-top:12px">
      <div class="panel"><h3>Le bien</h3><div class="kv">
        ${f("Type", val("type", v => esc(TYPES[v] || v)))}${f("Adresse", val("address"))}${f("Ville", val("city"))}${f("Département", val("dep"))}
        ${f("Surface", val("surface", v => num(v, " m²")) + (it.surface_kind ? ` <span class="prov">(${esc(it.surface_kind)})</span>` : ""))}${f("Pièces", val("rooms"))}${f("Étage", val("floor", v => v === 0 ? "RDC" : v))}
        ${f("Occupation", val("occupation"))}${f("État / travaux", val("works_level"))}${f("Annexes", feats)}
        ${f("Géolocalisation", it.lat != null ? esc(it.geo_precision || "") + (it.geocoder ? " · " + esc(it.geocoder) : "") : '<span class="unk">non géolocalisé</span>' + (it.geo_error ? " · " + esc(it.geo_error) : ""))}
      </div></div>
      <div class="panel"><h3>La vente</h3><div class="kv">
        ${f("Mise à prix", val("mise_a_prix", eur))}${f("Prix d'adjudication", val("adjudication_price", eur))}
        ${f("Date", val("sale_date", v => esc(dfr(v))) + (it.sale_time ? " à " + esc(it.sale_time) : ""))}${f("Lieu", val("sale_place"))}${f("Type de vente", val("sale_kind"))}
        ${f("Avocat / contact", val("lawyer"))}${f("Visite", val("visit"))}${it.lots_count ? f("Lots", `<span class="badge b-warn">${it.lots_count} mises à prix dans l'annonce : vérifier le lot</span>`) : ""}
        ${f("Sources", (it.sources || []).map(s => `<a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.source)}</a> <span class="muted small">collecté ${dtfr(s.collected_at)}</span>`).join("<br>"))}
      </div></div>
    </div>
    <div class="panel" style="margin-top:12px"><h3>Documents</h3>${docs}${docAnalysisHtml(it, d.doc_tools, d.doc_reader)}</div>
    ${demandeHtml(d.demande)}
    ${(it.doc_conflicts || []).map(c => `<div class="warn"><b>Écart annonce / document :</b> ${esc(c)}</div>`).join("")}
    ${(it.risks || []).length ? `<div class="panel" style="margin-top:12px"><h3>Risques et points d'attention</h3>${it.risks.map(r => `<span class="badge ${r.severity >= 3 ? "b-bad" : r.severity === 2 ? "b-warn" : "b-mute"}" title="${esc(r.quote || "")}">${esc(r.label)}${r.scope === "locatif" ? " (locatif)" : ""}${r.source ? " · " + esc(r.source) + (r.page ? " p." + r.page : "") : ""}</span>`).join(" ")}<p class="small muted">Annonce : détection par mots-clés. Documents : citation et page visibles dans « Ce que disent les documents ».</p></div>` : ""}
    <h2>Estimation de la valeur de marché</h2><div class="panel">${estHtml}</div>
    ${(sim.warnings || []).map(w => `<div class="warn">${esc(w)}</div>`).join("")}
    <h2>A. Achat-revente</h2><div class="panel">${arHtml}</div>
    <h2>B. Investissement locatif</h2><div class="panel">${loHtml}${an.loyer ? `<p class="small muted">Loyer de référence : ${esc(an.loyer.source)}, ${esc(an.loyer.commune || an.loyer.code_commune)}.</p>` : ""}</div>
    <h2>Notes</h2><div class="grid g2"><div class="panel">${scoreDetail(sc.revente, "Achat-revente")}</div><div class="panel">${scoreDetail(sc.locatif, "Locatif")}</div></div>
    <h2>Corriger ou compléter (vos valeurs priment sur celles de la source)</h2>
    <div class="panel"><div class="grid g4">
      ${oi("surface", "Surface m²")}${oi("rooms", "Pièces")}${oi("floor", "Étage")}${oi("mise_a_prix", "Mise à prix €")}
      ${oi("prix_envisage_manuel", "Prix d'achat envisagé €")}${oi("travaux_manuel", "Travaux € (montant)")}${oi("loyer_manuel", "Loyer mensuel HC €")}${oi("adjudication_price", "Prix d'adjudication €")}${oi("valeur_marche_manuel", "Ma valeur de marché €")}
      ${oi("address", "Adresse", "text")}${oi("city", "Ville", "text")}${oi("postcode", "Code postal", "text")}${oi("sale_date", "Date de vente", "date")}
      <div><label>Type</label><select data-ov="type"><option value="">(source)</option>${Object.entries(TYPES).map(([k, v]) => `<option value="${k}" ${ov.type === k ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div><label>Occupation</label><select data-ov="occupation"><option value="">(source)</option><option ${ov.occupation === "libre" ? "selected" : ""}>libre</option><option ${ov.occupation === "occupé" ? "selected" : ""}>occupé</option></select></div>
      <div><label>Niveau de travaux</label><select data-ov="works_level"><option value="">(source)</option>${["aucun", "léger", "moyen", "lourd"].map(v => `<option ${ov.works_level === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
      <div><label>Type de vente</label><select data-ov="sale_kind"><option value="">(source)</option>${["judiciaire", "notariale", "domaniale"].map(v => `<option ${ov.sale_kind === v ? "selected" : ""}>${v}</option>`).join("")}</select></div>
    </div><label>Notes personnelles</label><textarea data-ov="notes" rows="2">${esc(ov.notes || "")}</textarea>
    <div class="row" style="margin-top:10px"><button class="primary" id="d-save">Enregistrer et recalculer</button><span class="small muted">Les hypothèses globales (frais, taux, travaux au m²…) se règlent dans Paramètres.</span></div></div>
    ${it.description ? `<details><summary>Texte de l'annonce (extrait)</summary><div class="panel small" style="white-space:pre-wrap">${esc(it.description)}</div></details>` : ""}`;
  $("#modal").classList.remove("hidden");
  const refresh = () => { openDetail(id, onChange); onChange && onChange(); };
  const docPost = async (body, headers) => {
    $("#doc-msg").textContent = "Lecture et analyse en cours…";
    try {
      const r = await fetch(`/api/listing/${id}/documents`, {method: "POST", headers, body});
      const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText);
      refresh();
    } catch (e) { $("#doc-msg").innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  };
  const da = $("#doc-analyse"); if (da) da.onclick = () => docPost(JSON.stringify({action: "analyse"}), {"Content-Type": "application/json"});
  $("#doc-file").onchange = e => { const f = e.target.files[0]; if (f) docPost(f, {"Content-Type": "application/pdf", "X-Filename": encodeURIComponent(f.name)}); };
  bindDemande();
  $("#doc-text-go").onclick = () => docPost(JSON.stringify({text: $("#doc-text").value, label: $("#doc-label").value || "Texte collé"}), {"Content-Type": "application/json"});
  document.querySelectorAll("[data-docrm]").forEach(b => b.onclick = () => docPost(JSON.stringify({remove: b.dataset.docrm}), {"Content-Type": "application/json"}));
  $("#d-fav").onclick = async () => { await post("/api/listing/" + id, {favorite: !d.favorite}); refresh(); };
  $("#d-hide").onclick = async () => { await post("/api/listing/" + id, {hidden: !d.hidden}); refresh(); };
  $("#d-save").onclick = async () => {
    const o = {};
    document.querySelectorAll("[data-ov]").forEach(i => { if (i.value !== "") o[i.dataset.ov] = i.value; });
    await post("/api/listing/" + id, {overrides: o});
    refresh();
  };
}
$("#modal-close").onclick = () => $("#modal").classList.add("hidden");
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

const FACT_FMT = v => typeof v === "number" ? (v >= 1000 ? eur(v) : num(v)) : esc(v ?? "—");
function docAnalysisHtml(it, tools, reader) {
  const das = it.doc_analyses || [];
  const rows = das.map(d => `<tr><td><b>${esc(d.kind || d.label)}</b><div class="small muted">${esc(d.label)} · ${esc(d.method || "")} · ${dtfr(d.analysed_at)}</div></td>
    <td><span class="badge ${d.status === "analysé" ? "b-ok" : "b-warn"}">${esc(d.status)}</span>${d.message ? `<div class="small">${esc(d.message)}</div>` : ""}</td>
    <td class="right"><button class="small" data-docrm="${esc(d.origin)}">retirer</button></td></tr>`).join("");
  const facts = das.filter(d => d.status === "analysé").flatMap(d => (d.facts || []).map(f => ({...f, doc: d.kind || d.label})));
  const risks = das.filter(d => d.status === "analysé").flatMap(d => (d.risks || []).map(r => ({...r, doc: d.kind || d.label})));
  const ocr = `<span class="small muted">Lecteur : ${esc(reader || "")}</span>`;
  return `<h3 style="margin-top:16px">Ce que disent les documents</h3>
    ${das.length ? `<table><tbody>${rows}</tbody></table>` : '<p class="small muted">Aucun document analysé pour l\'instant.</p>'}
    ${facts.length || risks.length ? `<div style="overflow:auto"><table><thead><tr><th>Information</th><th>Valeur</th><th>Document</th><th>Citation</th><th>Utilisée</th></tr></thead><tbody>
      ${facts.map(f => `<tr><td>${esc(f.label)}</td><td>${f.value === null ? "—" : FACT_FMT(f.value)}</td><td class="small">${esc(f.doc)}${f.page ? " p." + f.page : ""}</td><td class="small muted">« ${esc(f.quote)} »</td><td class="small">${f.apply ? "oui, prime sur l'annonce" : "affichée seulement"}</td></tr>`).join("")}
      ${risks.map(r => `<tr><td>${esc(r.label)}</td><td><span class="badge ${r.severity >= 3 ? "b-bad" : r.severity === 2 ? "b-warn" : "b-mute"}">${r.severity ? "risque " + r.severity + "/3" : "info"}</span></td><td class="small">${esc(r.doc)}${r.page ? " p." + r.page : ""}</td><td class="small muted">« ${esc(r.quote)} »</td><td class="small">${r.severity ? "dans la note" : "—"}</td></tr>`).join("")}
    </tbody></table></div>` : ""}
    <div class="row" style="margin-top:8px">${(it.documents || []).length ? '<button id="doc-analyse">Analyser les documents liés</button>' : ""}
      <label class="inline btn">Déposer un PDF<input type="file" id="doc-file" accept=".pdf,.txt" style="display:none"></label>${ocr}</div>
    <details><summary>Coller le texte d'un document (PV, cahier des conditions de vente, diagnostics…)</summary>
      <input id="doc-label" placeholder="Nom du document (ex. PV descriptif)"><textarea id="doc-text" rows="6" style="margin-top:6px"></textarea>
      <button id="doc-text-go" style="margin-top:6px">Analyser ce texte</button></details>
    <div id="doc-msg" class="small"></div>`;
}

// ------------------------------------------------------------------ Import manuel
function openImport(after) {
  $("#modal-body").innerHTML = `<h1>Importer une annonce manuellement</h1>
  <p class="small muted">Pour les sources qui n'autorisent pas la collecte automatique (Immobilier des notaires, Enchères du Domaine) ou une annonce trouvée ailleurs. Collez le texte de l'annonce : ImmoRadar en extrait surface, pièces, mise à prix, date, occupation… Les champs remplis à la main priment.</p>
  <div class="panel"><div class="grid g3">
    <div><label>Source</label><select id="i-source"><option value="notaires">Immobilier des notaires</option><option value="domaine">Enchères du Domaine</option><option value="licitor">Licitor</option><option value="avoventes">Avoventes</option><option value="autre">Autre</option></select></div>
    <div style="grid-column:span 2"><label>Lien de l'annonce</label><input id="i-url" type="url" placeholder="https://…"></div>
    <div><label>Titre</label><input id="i-title"></div>
    <div><label>Type</label><select id="i-type"><option value="">(détecter)</option>${Object.entries(TYPES).map(([k, v]) => `<option value="${k}">${v}</option>`).join("")}</select></div>
    <div><label>Adresse</label><input id="i-address"></div>
    <div><label>Ville</label><input id="i-city"></div><div><label>Code postal</label><input id="i-postcode"></div>
    <div><label>Surface m²</label><input id="i-surface" type="number"></div><div><label>Pièces</label><input id="i-rooms" type="number"></div>
    <div><label>Mise à prix €</label><input id="i-mise_a_prix" type="number"></div><div><label>Date de vente</label><input id="i-sale_date" type="date"></div>
    <div><label>Lieu de vente</label><input id="i-sale_place"></div>
    <div><label>Occupation</label><select id="i-occupation"><option value="">(détecter)</option><option>libre</option><option>occupé</option></select></div>
  </div><label>Texte de l'annonce (copier-coller)</label><textarea id="i-texte" rows="8"></textarea>
  <div class="row" style="margin-top:10px"><button class="primary" id="i-go">Importer et analyser</button><span id="i-msg" class="small"></span></div></div>`;
  $("#modal").classList.remove("hidden");
  $("#i-go").onclick = async () => {
    const body = {};
    ["source", "url", "title", "type", "address", "city", "postcode", "surface", "rooms", "mise_a_prix", "sale_date", "sale_place", "occupation", "texte"].forEach(k => body[k] = $("#i-" + k).value);
    $("#i-msg").textContent = "Analyse en cours (géocodage et DVF si nécessaire)…";
    try { const d = await post("/api/import", body); after && after(); openDetail(d.id, after); }
    catch (e) { $("#i-msg").innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  };
}

// ------------------------------------------------------------------ Paramètres
const CALC_LABELS = {
  hausse_encheres_pct: "Prix envisagé = mise à prix + (%)", droits_mutation_pct: "Droits de mutation (%)", csi_pct: "Contribution sécurité immobilière (%)",
  frais_prealables: "Frais préalables vente judiciaire (€)", emoluments_bareme: "Émoluments avocat au barème (oui/non)", honoraires_avocat: "Honoraires avocat enchérisseur (€)",
  frais_acquisition_notariale_pct: "Frais d'acquisition ventes notariales/domaniales (%)", apport_pct: "Apport (% du coût)", taux_credit_pct: "Taux du crédit (%)",
  duree_credit_ans: "Durée du crédit (ans)", assurance_emprunteur_pct: "Assurance emprunteur (%/an)", frais_dossier_garantie_pct: "Frais de dossier + garantie (% emprunt)",
  duree_detention_mois: "Durée de détention achat-revente (mois)", charges_copro_m2_an: "Charges de copropriété (€/m²/an)", taxe_fonciere_m2_an: "Taxe foncière (€/m²/an)",
  energie_assurance_mois: "Énergie + assurance pendant la détention (€/mois)", frais_revente_pct: "Frais de revente (%)", fiscalite: "Fiscalité (particulier / marchand / aucune)",
  marge_cible_pct: "Marge cible pour le prix max — revente (%)", loyer_m2_manuel: "Loyer au m² imposé (€ HC, vide = Carte des loyers)", charges_recuperables_pct: "Part de charges dans les loyers CC (%)",
  vacance_mois_an: "Vacance locative (mois/an)", gestion_pct: "Frais de gestion (%)", assurance_pno_an: "Assurance PNO (€/an)", charges_copro_non_recup_m2_an: "Charges non récupérables (€/m²/an)",
  entretien_pct_loyer: "Entretien (% des loyers)", rendement_net_cible_pct: "Rendement net cible pour le prix max — locatif (%)",
};
const EST_LABELS = {annees: "Ancienneté max des ventes DVF (années)", tolerance_surface: "Tolérance de surface (0,4 = ±40 %)", min_comparables: "Nombre minimal de comparables",
  ajust_rdc_pct: "Ajustement rez-de-chaussée (%)", ajust_etage_sans_asc_pct: "Ajustement étage ≥ 4 sans ascenseur (%)", ajust_exterieur_pct: "Ajustement espace extérieur (%)"};

async function viewParams() {
  await loadParams();
  const s = await api("/api/state");
  const p = PARAMS, c = p.calculs;
  const field = (grp, k, v, label) => {
    if (typeof v === "boolean") return `<div><label>${label}</label><select data-g="${grp}" data-k="${k}" data-t="bool"><option value="1" ${v ? "selected" : ""}>oui</option><option value="0" ${!v ? "selected" : ""}>non</option></select></div>`;
    if (k === "fiscalite") return `<div><label>${label}</label><select data-g="${grp}" data-k="${k}" data-t="str">${["particulier", "marchand", "aucune"].map(o => `<option ${v === o ? "selected" : ""}>${o}</option>`).join("")}</select></div>`;
    return `<div><label>${label}</label><input type="number" step="any" data-g="${grp}" data-k="${k}" data-t="num" value="${v ?? ""}"></div>`;
  };
  const deps = [...new Set(Object.values(DEFS.zones).flatMap(z => z.deps))];
  app.innerHTML = `<h1>Paramètres</h1>
  <h2>Sources</h2><div class="panel"><table><tbody>${s.sources_def.map(x => `<tr><td><b>${esc(x.label)}</b><div class="small"><a href="${esc(x.home)}" target="_blank" rel="noopener">${esc(x.home)}</a></div></td>
    <td class="small">${esc(x.legal)}</td><td>${x.mode === "auto" ? `<label class="inline"><input type="checkbox" data-src="${x.name}" ${p.sources[x.name] !== false ? "checked" : ""}>collecte automatique</label>` : '<span class="badge b-mute">import manuel</span>'}</td></tr>`).join("")}</tbody></table>
    <div class="grid g3"><div><label>Actualisation automatique (heures, 0 = désactivée, l'application doit rester ouverte)</label><input type="number" id="auto-h" value="${p.actualisation_auto_heures}"></div></div>
    <p class="small"><button id="imp2">+ Importer une annonce manuellement</button></p></div>
  <h2>Mails de demande d'informations</h2><div class="panel"><label>Signature ajoutée en bas des mails préparés (nom, téléphone, email)</label><textarea id="signature" rows="3" placeholder="Prénom Nom\n06 00 00 00 00">${esc(p.signature || "")}</textarea></div>
  <h2>Zones et critères</h2><div class="panel small">Les critères de recherche (zones, départements, villes, types, surfaces, prix, occupation, extérieurs, dates, types de vente) se règlent dans le panneau de filtres de l'onglet <a href="#/annonces">Annonces</a> et sont enregistrés automatiquement. Zones prédéfinies : ${Object.values(DEFS.zones).map(z => esc(z.label) + " (" + z.deps.join(", ") + ")").join(" · ")}.<br>
    <label>Seuil « opportunité intéressante » (note sur 100)</label><input type="number" id="seuil" value="${p.scoring.seuil_opportunite}" style="max-width:120px"></div>
  <h2>Hypothèses de calcul</h2><div class="panel"><div class="grid g3">${Object.entries(c).filter(([k]) => k !== "travaux_m2").map(([k, v]) => field("calculs", k, v, CALC_LABELS[k] || k)).join("")}</div>
    <h3>Coût des travaux par niveau (€/m²)</h3><div class="grid g4">${Object.entries(c.travaux_m2).map(([k, v]) => `<div><label>${k}</label><input type="number" data-g="travaux_m2" data-k="${k}" data-t="num" value="${v}"></div>`).join("")}</div>
    <p class="small muted">Le niveau de travaux est déduit de mots-clés de l'annonce (« à rénover », « bon état »…) ; « inconnu » utilise le coût indiqué. Vous pouvez saisir un montant précis sur chaque fiche.</p></div>
  <h2>Estimation (DVF)</h2><div class="panel"><div class="grid g3">${Object.entries(p.estimation).map(([k, v]) => field("estimation", k, v, EST_LABELS[k] || k)).join("")}</div></div>
  <div class="row" style="margin:12px 0"><button class="primary" id="save">Enregistrer et recalculer toutes les annonces</button><button id="reset">Revenir aux valeurs par défaut</button><span id="msg" class="small"></span></div>
  <h2>Données DVF et loyers</h2><div class="panel small">
    <p>Chargées : ${s.dvf.departements.length ? s.dvf.departements.map(d => `${esc(d.dep)} (${esc(d.years)})`).join(", ") : "aucune"}. La recherche télécharge automatiquement les départements où se trouvent des annonces (fichiers géo-DVF d'Etalab, une fois par année).</p>
    <div class="row">${deps.map(d => `<label class="inline"><input type="checkbox" data-dvf="${d}">${d}</label>`).join("")}<button id="dvf-go">Télécharger DVF + loyers maintenant</button></div>
    <p>Ou importer un fichier géo-DVF téléchargé à la main (csv ou csv.gz depuis files.data.gouv.fr/geo-dvf) : <input type="file" id="dvf-file" accept=".csv,.gz" style="width:auto"></p><div id="dvf-msg"></div></div>`;
  $("#save").onclick = async () => {
    const np = JSON.parse(JSON.stringify(p));
    document.querySelectorAll("[data-g]").forEach(i => {
      const v = i.dataset.t === "num" ? (i.value === "" ? null : +i.value) : i.dataset.t === "bool" ? i.value === "1" : i.value;
      if (i.dataset.g === "travaux_m2") np.calculs.travaux_m2[i.dataset.k] = v; else np[i.dataset.g][i.dataset.k] = v;
    });
    document.querySelectorAll("[data-src]").forEach(i => np.sources[i.dataset.src] = i.checked);
    np.actualisation_auto_heures = +$("#auto-h").value || 0;
    np.signature = $("#signature").value;
    np.scoring.seuil_opportunite = +$("#seuil").value || 60;
    $("#msg").textContent = "Recalcul…";
    await post("/api/params", np);
    $("#msg").textContent = "Enregistré, toutes les annonces ont été recalculées.";
  };
  $("#reset").onclick = async () => { await post("/api/params/reset", {}); viewParams(); };
  $("#imp2").onclick = () => openImport();
  $("#dvf-go").onclick = async () => {
    const ds = [...document.querySelectorAll("[data-dvf]:checked")].map(i => i.dataset.dvf);
    $("#dvf-msg").textContent = "Téléchargement en cours (peut prendre plusieurs minutes)…";
    try {
      const r = await post("/api/dvf/load", {deps: ds});
      $("#dvf-msg").innerHTML = r.dvf.map(x => `${esc(x.dep)} : chargé ${esc(x.loaded.join(", ") || "rien")}${x.errors.length ? ` — <span style="color:var(--bad)">${x.errors.map(esc).join(" ; ")}</span>` : ""}`).join("<br>") + "<br>Loyers : " + Object.entries(r.loyers).map(([k, v]) => esc(k + " " + v)).join(", ");
    } catch (e) { $("#dvf-msg").innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`; }
  };
  $("#dvf-file").onchange = async e => {
    const file = e.target.files[0]; if (!file) return;
    $("#dvf-msg").textContent = "Import…";
    const r = await fetch("/api/dvf/upload", {method: "POST", headers: {"X-Filename": file.name}, body: file});
    const j = await r.json();
    $("#dvf-msg").textContent = j.error ? "Erreur : " + j.error : j.rows + " ventes importées.";
  };
}

// ------------------------------------------------------------------ routeur
async function route() {
  const h = location.hash || "#/";
  const tab = h.startsWith("#/annonces") ? "annonces" : h.startsWith("#/favoris") ? "favoris" : h.startsWith("#/parametres") ? "parametres" : "dash";
  document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", a.dataset.tab === tab));
  clearTimeout(pollTimer);
  try {
    if (!DEFS) await loadParams();
    await ({dash: viewDash, annonces: viewAnnonces, favoris: viewFavoris, parametres: viewParams})[tab]();
  } catch (e) { app.innerHTML = `<div class="err">Erreur : ${esc(e.message)}</div>`; }
}
window.addEventListener("hashchange", route);
route();
