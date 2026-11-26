"""Recherche à la demande : collecte -> déduplication -> géolocalisation -> estimation -> calculs -> notes."""
import json
import re
import threading
import traceback
from datetime import date

from . import avis, config, contacts, db, documents, estimate, finance, opendata, scoring
from .collectors import ALL as COLLECTORS
from .geo_fr import ZONES, dep_from_postcode, slugify
from .net import FetchError, PoliteClient, ProtectionDetected, RobotsDisallowed

STATE = {"running": False, "run_id": None, "log": [], "step": ""}
_run_lock = threading.Lock()

OVERRIDABLE = {"type", "surface", "rooms", "floor", "address", "city", "postcode", "mise_a_prix", "adjudication_price",
               "sale_date", "occupation", "works_level", "sale_kind", "prix_envisage_manuel", "loyer_manuel",
               "travaux_manuel", "notes", "status_hint", "valeur_marche_manuel"}


def criteria_deps(crit):
    deps = list(crit.get("departements") or [])
    if not deps:
        for z in crit.get("zones") or []:
            deps += ZONES.get(z, {}).get("deps", [])
    return sorted(set(deps)) or sorted(set(ZONES["idf"]["deps"] + ZONES["lyon"]["deps"]))


# ------------------------------------------------------------------ stockage / déduplication

def city_key(city):
    """« Paris 13ème », « Paris 13e Arrondissement » et « Paris » donnent la même clé (les sources varient)."""
    c = slugify(city or "")
    return re.sub(r"-(\d{1,2})(?:e|er|eme|ieme)?(?:-arrondissement)?$", "", c)


def dedup_key(d):
    city = city_key(d.get("city"))
    if d.get("sale_date") and d.get("mise_a_prix") and (city or d.get("dep")):
        return f"{d.get('dep') or ''}|{city}|{d['sale_date']}|{int(d['mise_a_prix'])}"
    return f"{d['source']}:{d['source_id']}"


LIST_FIELDS = ("photos", "documents")


def merge(old, new):
    """Fusionne une nouvelle lecture dans l'annonce. La même source écrase ses propres valeurs ;
    une autre source ne fait que compléter les valeurs inconnues. La provenance est conservée."""
    src = new["source"]
    prov = old.setdefault("prov", {})
    for k, v in new.items():
        if k in ("source", "source_id", "url", "prov"):
            continue
        if k in ("lots_count", "baisse_mise_a_prix") and prov.get(k) in (src, None):
            old[k] = v  # la source peut retirer un faux signal (ex. baisse de mise à prix lue comme 2e lot)
            prov[k] = src
            continue
        if v is None or v == [] or v == {}:
            continue
        if k == "features":
            f = old.setdefault("features", {})
            for fk, fv in v.items():
                if fv is not None and (f.get(fk) is None or prov.get("features." + fk) == src):
                    f[fk] = fv
                    prov["features." + fk] = src
            continue
        if k == "risks":
            codes = {r["code"] for r in old.get("risks") or []}
            old["risks"] = (old.get("risks") or []) + [r for r in v if r["code"] not in codes]
            continue
        if k in LIST_FIELDS:
            old[k] = list({json.dumps(x, sort_keys=True): x for x in (old.get(k) or []) + v}.values())
            continue
        if old.get(k) is None or prov.get(k) == src:
            old[k] = v
            prov[k] = src
    sources = [s for s in old.get("sources", []) if not (s["source"] == src and s["source_id"] == new["source_id"])]
    sources.append({"source": src, "source_id": new["source_id"], "url": new.get("url"), "collected_at": db.now_iso()})
    old["sources"] = sources
    old["collected_at"] = db.now_iso()
    return old


def upsert(d, run_id):
    """Renvoie (listing_id, est_nouvelle)."""
    d = {k: v for k, v in d.items()}
    if d.get("postcode") and not d.get("dep"):
        d["dep"] = dep_from_postcode(d["postcode"])
    hit = db.q("SELECT listing_id FROM listing_sources WHERE source=? AND source_id=?", (d["source"], str(d["source_id"])))
    key = dedup_key(d)
    row = None
    if hit:
        row = db.q("SELECT * FROM listings WHERE id=?", (hit[0]["listing_id"],))
    if not row:
        row = db.q("SELECT * FROM listings WHERE dedup_key=?", (key,))
    now = db.now_iso()
    if row:
        r = row[0]
        data = merge(json.loads(r["data"]), d)
        db.x("UPDATE listings SET data=?, last_seen=?, last_run_id=? WHERE id=?", (json.dumps(data, ensure_ascii=False), now, run_id, r["id"]))
        if r["dedup_key"] != key and ":" not in key and not db.q("SELECT id FROM listings WHERE dedup_key=?", (key,)):
            db.x("UPDATE listings SET dedup_key=? WHERE id=?", (key, r["id"]))  # clé recalculée (règle de ville harmonisée)
        lid, new = r["id"], False
    else:
        data = merge({}, d)
        lid = db.x("INSERT INTO listings(dedup_key,data,first_seen,last_seen,last_run_id,first_run_id) VALUES (?,?,?,?,?,?)",
                   (key, json.dumps(data, ensure_ascii=False), now, now, run_id, run_id))
        new = True
    db.x("INSERT INTO listing_sources VALUES (?,?,?,?,?,?) ON CONFLICT(source, source_id) DO UPDATE SET "
         "listing_id=excluded.listing_id, url=excluded.url, collected_at=excluded.collected_at, raw=excluded.raw",
         (lid, d["source"], str(d["source_id"]), d.get("url"), now, json.dumps(d, ensure_ascii=False)[:20000]))
    return lid, new


# ------------------------------------------------------------------ analyse

def effective(row):
    data = json.loads(row["data"])
    ov = json.loads(row["overrides"] or "{}")
    item = json.loads(row["data"])
    item["doc_conflicts"] = documents.apply_to_item(item)
    for k, v in ov.items():
        if v not in (None, ""):
            item[k] = v
            item.setdefault("prov", {})[k] = "saisie manuelle"
    if item.get("postcode") and not item.get("dep"):
        item["dep"] = dep_from_postcode(item["postcode"])
    return item, data, ov


def geolocate(lid, log=lambda m: None):
    row = db.q("SELECT * FROM listings WHERE id=?", (lid,))[0]
    item, data, ov = effective(row)
    changed = False
    try:
        if (ov.get("address") or not data.get("geo_tried")) and (item.get("address") or item.get("city")):
            q = " ".join(x for x in [item.get("address"), item.get("postcode"), item.get("city")] if x)
            g = opendata.geocode(q)
            data["geo_tried"] = True
            changed = True
            if g and (g.get("score") or 0) >= 0.5:
                precise = g.get("type") in ("housenumber", "street") and bool(item.get("address"))
                if precise or data.get("lat") is None:
                    data.update({"lat": g["lat"], "lon": g["lon"],
                                 "geo_precision": "adresse" if g.get("type") == "housenumber" and item.get("address") else
                                 "rue" if g.get("type") == "street" else "commune"})
                data.update({"code_commune": g.get("citycode"), "postcode": data.get("postcode") or g.get("postcode"),
                             "street": g.get("street") if g.get("type") in ("housenumber", "street") else data.get("street"),
                             "housenumber": g.get("housenumber"), "geocoder": g.get("service") + f" (score {g.get('score'):.2f})"})
                if not data.get("dep"):
                    data["dep"] = dep_from_postcode(g.get("postcode"))
        if data.get("lat") is not None and not data.get("code_commune") and not data.get("rev_tried"):
            g = opendata.reverse(data["lat"], data["lon"])
            data["rev_tried"] = True
            changed = True
            if g:
                data["code_commune"] = g.get("citycode")
                data["postcode"] = data.get("postcode") or g.get("postcode")
                data["dep"] = data.get("dep") or dep_from_postcode(g.get("postcode"))
    except FetchError as e:
        data["geo_error"] = str(e)
        changed = True
        log(f"Géocodage indisponible : {e}")
    if changed:
        db.x("UPDATE listings SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), lid))


def analyse(lid, params=None):
    params = params or config.load()
    row = db.q("SELECT * FROM listings WHERE id=?", (lid,))[0]
    item, _, _ = effective(row)
    est = estimate.estimate(item, params)
    rent = opendata.rent_indicator(item.get("code_commune"), item.get("type"), item.get("rooms"), item.get("surface"))
    sim = finance.simulate(item, est, rent, params["calculs"])
    sc = scoring.score(item, est, sim)
    an = {"estimation": est, "loyer": rent, "simulation": sim, "scores": sc, "computed_at": db.now_iso()}
    try:
        an["avis"] = avis.build(item, est, sim, sc, params)
    except Exception as e:  # l'avis ne doit jamais bloquer l'analyse
        an["avis"] = {"verdict": None, "error": f"Avis non généré : {e}"}
    db.x("UPDATE listings SET analysis=? WHERE id=?", (json.dumps(an, ensure_ascii=False), lid))
    return an


def analyse_all():
    params = config.load()
    for r in db.q("SELECT id FROM listings"):
        analyse(r["id"], params)


# ------------------------------------------------------------------ recherche

def log(msg):
    STATE["log"].append(f"{db.now_iso()[11:19]}  {msg}")
    print(msg, flush=True)


DOCS_PER_RUN = 30


def run_search(trigger="manuelle"):
    if not _run_lock.acquire(blocking=False):
        return None
    STATE["trigger"] = trigger
    STATE.update(running=True, log=[], step="démarrage")
    run_id = db.x("INSERT INTO runs(started_at,status) VALUES (?,?)", (db.now_iso(), "en cours"))
    STATE["run_id"] = run_id
    threading.Thread(target=_run, args=(run_id,), daemon=True).start()
    return run_id


def _run(run_id):
    sources, summary = [], {"trouvees": 0, "nouvelles": 0, "mises_a_jour": 0, "declencheur": STATE.get("trigger", "manuelle")}
    try:
        params = config.load()
        deps = criteria_deps(params["criteres"])
        log(f"Recherche n°{run_id} sur les départements {', '.join(deps)}")
        touched = set()
        for name, mod in COLLECTORS.items():
            st = {"name": name, "label": mod.LABEL, "mode": mod.MODE, "legal": mod.LEGAL, "home": mod.HOME,
                  "at": db.now_iso(), "found": 0, "new": 0, "links": mod.manual_links(deps)}
            if mod.MODE != "auto":
                st.update(status="manuel", message="Collecte automatique non autorisée ou impossible : import manuel.")
            elif not params["sources"].get(name, True):
                st.update(status="désactivée", message="Source désactivée dans les paramètres.")
            else:
                STATE["step"] = f"collecte {mod.LABEL}"
                known = {r["source_id"]: r["collected_at"] for r in db.q("SELECT source_id, collected_at FROM listing_sources WHERE source=?", (name,))}
                try:
                    items = mod.collect(deps, known, log)
                    for it in items:
                        lid, new = upsert(it, run_id)
                        touched.add(lid)
                        st["found"] += 1
                        st["new"] += int(new)
                    st.update(status="ok" if items else "vide",
                              message=f"{len(items)} annonce(s) lue(s), dont {st['new']} nouvelle(s)" if items else
                              "La source a répondu mais aucune annonce n'a été reconnue.")
                    info = getattr(mod, "LAST_INFO", None)
                    if info:
                        st.update(status="ok", message=(f"{len(items)} annonce(s) lue(s), dont {st['new']} nouvelle(s) ; " if items else "") + info)
                    warn = getattr(mod, "LAST_WARNING", None)
                    if warn:
                        st.update(status="partiel", message=st["message"] + " — " + warn)
                except RobotsDisallowed as e:
                    st.update(status="bloquée", message=f"Interdit par robots.txt : {e}")
                except ProtectionDetected as e:
                    st.update(status="bloquée", message=f"Protection anti-robot détectée, collecte arrêtée sans contournement : {e}")
                except FetchError as e:
                    n_old = db.q("SELECT COUNT(*) AS n FROM listing_sources WHERE source=?", (name,))[0]["n"]
                    st.update(status="erreur", message=f"Source injoignable : {e}. Nouvel essai automatique à la prochaine "
                              f"recherche ; les {n_old} annonce(s) déjà lues restent affichées.")
                except Exception as e:
                    st.update(status="erreur", message=f"Erreur du collecteur : {type(e).__name__}: {e}")
                    log(traceback.format_exc())
                log(f"{mod.LABEL} : {st['status']} — {st['message']}")
            summary["trouvees"] += st["found"]
            summary["nouvelles"] += st["new"]
            sources.append(st)

        STATE["step"] = "lecture des documents"
        analysed = 0
        crit = params["criteres"]
        def _promise(r):
            sc = (json.loads(r["analysis"] or "{}").get("scores") or {})
            return -max((sc.get("revente") or {}).get("score") or 0, (sc.get("locatif") or {}).get("score") or 0)
        for r in sorted(db.q("SELECT * FROM listings"), key=_promise):   # les biens les mieux notés d'abord
            if analysed >= DOCS_PER_RUN:
                log(f"Documents : limite de {DOCS_PER_RUN} annonces par recherche atteinte, la suite à la prochaine recherche")
                break
            data = json.loads(r["data"])
            if not data.get("documents") or {d.get("url") for d in data["documents"]} <= {d.get("origin") for d in data.get("doc_analyses", [])}:
                continue
            item, _, _ = effective(r)
            if not matches(item, crit, deps):
                continue
            n = documents.analyse_listing_documents(data)
            if n:
                analysed += 1
                db.x("UPDATE listings SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), r["id"]))
        if analysed:
            log(f"Documents : {analysed} annonce(s) analysée(s)")

        STATE["step"] = "géolocalisation"
        ids = [r["id"] for r in db.q("SELECT id FROM listings")]
        for lid in ids:
            geolocate(lid, log)

        STATE["step"] = "données DVF et loyers"
        listing_deps = set()
        for r in db.q("SELECT data FROM listings"):
            d = json.loads(r["data"]).get("dep")
            if d in deps:
                listing_deps.add(d)
        dvf = {}
        for d in sorted(listing_deps):
            res = opendata.load_dvf_department(d, log=log)
            dvf[d] = res
            if res["errors"]:
                log(f"DVF {d} : {'; '.join(res['errors'][:3])}")
        loy = opendata.load_loyers(log=log)

        STATE["step"] = "estimations et calculs"
        analyse_all()
        summary["dvf"] = dvf
        summary["loyers"] = loy
        db.x("UPDATE runs SET finished_at=?, status=?, summary=?, sources=?, log=? WHERE id=?",
             (db.now_iso(), "terminée", json.dumps(summary, ensure_ascii=False), json.dumps(sources, ensure_ascii=False),
              "\n".join(STATE["log"]), run_id))
        log("Recherche terminée")
    except Exception as e:
        log(f"Erreur : {e}\n{traceback.format_exc()}")
        db.x("UPDATE runs SET finished_at=?, status=?, summary=?, sources=?, log=? WHERE id=?",
             (db.now_iso(), "échec", json.dumps(summary), json.dumps(sources, ensure_ascii=False), "\n".join(STATE["log"]), run_id))
    finally:
        STATE.update(running=False, step="")
        _run_lock.release()


# ------------------------------------------------------------------ présentation et filtres

def listing_status(row, item, last_run):
    hint = item.get("status_hint")
    if hint in ("adjugée", "retirée"):
        return hint
    seen = _seen_run()
    if row["first_run_id"] and (row["first_run_id"] > seen if seen is not None else row["first_run_id"] == last_run):
        return "nouvelle"
    sd = item.get("sale_date")
    if sd:
        return "à venir" if sd >= date.today().isoformat() else "inconnue (date passée)"
    return "inconnue"


def matches(item, crit, deps):
    def known_or(v, cond):
        return cond if v is not None else crit.get("inclure_inconnus", True)

    if item.get("dep") and item["dep"] not in deps:
        return False
    if not item.get("dep") and not crit.get("inclure_inconnus", True):
        return False
    villes = [slugify(v) for v in crit.get("villes") or []]
    if villes and not any(v in slugify(item.get("city") or "") for v in villes):
        return False
    types = crit.get("types") or []
    if types:
        t, r = item.get("type"), item.get("rooms")
        ok = False
        for want in types:
            if want in ("appartement", "maison", "immeuble", "local_commercial", "studio") and t == want:
                ok = True
            elif want == "studio" and t == "appartement" and r == 1:
                ok = True
            elif want in ("t1", "t2", "t3") and t in ("appartement", "studio") and r == int(want[1]):
                ok = True
            elif want == "t4+" and t in ("appartement", "maison") and r and r >= 4:
                ok = True
        if not ok:
            return False
    rng = [("surface", "surface_min", "surface_max"), ("rooms", "pieces_min", "pieces_max"),
           ("mise_a_prix", "mise_a_prix_min", "mise_a_prix_max"), ("adjudication_price", "adjudication_min", "adjudication_max")]
    for field, lo, hi in rng:
        v = item.get(field)
        if crit.get(lo) not in (None, "") and not known_or(v, v is not None and v >= float(crit[lo])):
            return False
        if crit.get(hi) not in (None, "") and not known_or(v, v is not None and v <= float(crit[hi])):
            return False
    if crit.get("occupation") and not known_or(item.get("occupation"), item.get("occupation") == crit["occupation"]):
        return False
    feats = item.get("features") or {}
    for f in crit.get("exterieurs") or []:
        if not feats.get(f):
            return False
    sd = item.get("sale_date")
    if crit.get("date_min") and not known_or(sd, sd and sd >= crit["date_min"]):
        return False
    if crit.get("date_max") and not known_or(sd, sd and sd <= crit["date_max"]):
        return False
    if not crit.get("inclure_passees") and sd and sd < date.today().isoformat():
        return False
    tv = crit.get("types_vente") or []
    if tv and item.get("sale_kind") not in tv:
        return False
    return True


COMPLETUDE = (  # (points, libellé de ce qui manque, test)
    (20, "PV descriptif", lambda it, k: any("pv" in x for x in k)),
    (20, "cahier des conditions de vente", lambda it, k: any("cahier" in x for x in k)),
    (10, "diagnostics / DPE", lambda it, k: any("diagnostic" in x for x in k) or bool(it.get("dpe"))),
    (15, "estimation DVF", lambda it, k: it.get("_est_ok")),
    (8, "surface", lambda it, k: bool(it.get("surface"))),
    (8, "occupation", lambda it, k: it.get("occupation") not in (None, "inconnu")),
    (5, "état / travaux", lambda it, k: it.get("works_level") not in (None, "inconnu")),
    (5, "charges et taxe foncière", lambda it, k: bool(it.get("charges_copro_an") or it.get("taxe_fonciere_an"))),
    (4, "date de vente", lambda it, k: bool(it.get("sale_date"))),
    (5, "contact de l'avocat ou du notaire", lambda it, k: bool(it.get("contacts") or it.get("lawyer"))),
)


def completude(item, est_ok):
    """Note sur 100 de la complétude du dossier : documents lus et données connues."""
    kinds = [(d.get("kind") or d.get("label") or "").lower() for d in item.get("doc_analyses") or [] if d.get("status") == "analysé"]
    it = {**item, "_est_ok": est_ok}
    got, missing = 0, []
    for pts, label, test in COMPLETUDE:
        if test(it, kinds):
            got += pts
        else:
            missing.append(label)
    return got, missing


def pertinence(c, note_min=60):
    """Sélection : None si le bien est pertinent, sinon la raison de l'écarter (affichée dans « Tout voir »)."""
    if c["status"] in ("adjugée", "retirée"):
        return "vente terminée"
    if any(r.get("severity", 1) >= 3 and r.get("scope") in (None, "all") for r in c.get("risks") or []):
        return "risque juridique majeur"
    best = max(c["score_revente"] or -1, c["score_locatif"] or -1)
    if best < 0:
        return "non notée (estimation impossible)"
    if best < note_min:
        return f"note {best} < {note_min}"
    return None


def classement(c):
    """Classement par défaut : 70 % la meilleure note, 30 % la complétude du dossier."""
    best = max(c["score_revente"] or 0, c["score_locatif"] or 0)
    return round(0.7 * best + 0.3 * (c.get("completude") or 0), 1)


def card(row, last_run):
    item, data, ov = effective(row)
    an = json.loads(row["analysis"] or "{}")
    est = an.get("estimation") or {}
    sim = an.get("simulation") or {}
    sc = an.get("scores") or {}
    rv, lo = sc.get("revente") or {}, sc.get("locatif") or {}
    return {
        "id": row["id"], "favorite": bool(row["favorite"]), "hidden": bool(row["hidden"]),
        "status": listing_status(row, item, last_run),
        "title": item.get("title"), "type": item.get("type"), "rooms": item.get("rooms"), "surface": item.get("surface"),
        "floor": item.get("floor"), "city": item.get("city"), "dep": item.get("dep"), "address": item.get("address"),
        "mise_a_prix": item.get("mise_a_prix"), "adjudication_price": item.get("adjudication_price"),
        "sale_date": item.get("sale_date"), "sale_time": item.get("sale_time"), "sale_place": item.get("sale_place"),
        "doc_count": len([d for d in item.get("doc_analyses") or [] if d.get("status") == "analysé"]),
        "doc_conflicts": item.get("doc_conflicts") or [],
        "verdict": (an.get("avis") or {}).get("verdict"),
        **dict(zip(("completude", "manque"), completude(item, est.get("status") == "ok"))),
        "sale_kind": item.get("sale_kind"), "occupation": item.get("occupation"), "photo": (item.get("photos") or [None])[0],
        "sources": item.get("sources"), "first_seen": row["first_seen"], "collected_at": item.get("collected_at"),
        "risks": [r for r in item.get("risks") or [] if r.get("severity", 1) > 0], "works_level": item.get("works_level"), "features": item.get("features") or {},
        "estimation": {"status": est.get("status"), "central": (est.get("valeur") or {}).get("central"),
                       "confidence": est.get("confidence"), "confidence_label": est.get("confidence_label")},
        "score_revente": rv.get("score"), "score_locatif": lo.get("score"), "decote_pct": sc.get("decote_pct"),
        "marge_nette_pct": (sim.get("achat_revente") or {}).get("marge_nette_pct"),
        "rendement_net_pct": (sim.get("locatif") or {}).get("rendement_net_pct"),
        "prix_max_revente": (sim.get("achat_revente") or {}).get("prix_max_conseille"),
        "prix_max_locatif": (sim.get("locatif") or {}).get("prix_max_conseille"),
    }


SORTS = {
    "interet": lambda c: (-classement(c), -max(c["score_revente"] or -1, c["score_locatif"] or -1)),
    "note": lambda c: -max(c["score_revente"] or -1, c["score_locatif"] or -1),
    "complet": lambda c: (-(c.get("completude") or 0), -max(c["score_revente"] or -1, c["score_locatif"] or -1)),
    "revente": lambda c: -(c["score_revente"] if c["score_revente"] is not None else -1),
    "locatif": lambda c: -(c["score_locatif"] if c["score_locatif"] is not None else -1),
    "rentabilite": lambda c: -(c["rendement_net_pct"] if c["rendement_net_pct"] is not None else -999),
    "marge": lambda c: -(c["marge_nette_pct"] if c["marge_nette_pct"] is not None else -999),
    "decote": lambda c: -(c["decote_pct"] if c["decote_pct"] is not None else -999),
    "prix": lambda c: c["mise_a_prix"] if c["mise_a_prix"] is not None else 9e12,
    "prix_desc": lambda c: -(c["mise_a_prix"] or 0),
    "date": lambda c: c["sale_date"] or "9999",
    "surface": lambda c: -(c["surface"] or 0),
}


_seen_cache = {"t": 0, "v": None}


def _seen_run():
    """Dernière recherche dont les nouveautés ont été vues (bouton « Marquer comme vues ») ; mis en cache 2 s."""
    import time as _t
    if _t.time() - _seen_cache["t"] > 2:
        _seen_cache.update(t=_t.time(), v=db.get_setting("seen_run_id"))
    return _seen_cache["v"]


def last_run_id():
    r = db.q("SELECT max(id) m FROM runs WHERE status='terminée'")
    return r[0]["m"] if r else None


def list_cards(crit=None, sort="interet", favorites=False, show_hidden=False):
    params = config.load()
    crit = crit if crit is not None else params["criteres"]
    deps = criteria_deps(crit)
    lr = last_run_id()
    out = []
    for row in db.q("SELECT * FROM listings" + (" WHERE favorite=1" if favorites else "")):
        if row["hidden"] and not show_hidden and not favorites:
            continue
        item, _, _ = effective(row)
        if not favorites and not matches(item, crit, deps):
            continue
        out.append(card(row, lr))
    note_min = float(crit.get("note_min") or 0)
    for c in out:
        c["classement"] = classement(c)
        c["ecartee"] = pertinence(c, note_min)
    if crit.get("selection") and not favorites:
        out = [c for c in out if not c["ecartee"]]
    out.sort(key=SORTS.get(sort, SORTS["interet"]))
    return out


def _host(url):
    import urllib.parse
    return urllib.parse.urlparse(url or "").netloc.lower().replace("www.", "")


def source_for_url(url):
    h = _host(url)
    if not h:
        return None
    return next((m for m in COLLECTORS.values() if _host(m.HOME) == h or h in getattr(m, "DOMAINS", {})), None)


def _finish_import(d):
    lid, new = upsert(d, last_run_id())
    geolocate(lid)
    dep = json.loads(db.q("SELECT data FROM listings WHERE id=?", (lid,))[0]["data"]).get("dep")
    if dep:
        opendata.load_dvf_department(dep)
    opendata.load_loyers()
    analyse(lid)
    return lid


def import_url(url):
    """Import d'une annonce à partir de son seul lien, pour les sources dont la lecture automatique est autorisée
    (robots.txt vérifié). Pour les autres, on demande de coller le texte : rien n'est lu en douce."""
    mod = source_for_url(url)
    if mod is None or mod.MODE != "auto" or not hasattr(mod, "parse_detail"):
        label = mod.LABEL if mod else "Ce site"
        raise ValueError(f"{label} : lecture automatique non autorisée ou non prise en charge. Ouvrez l'annonce, "
                         "sélectionnez tout (Cmd+A), copiez (Cmd+C) et collez le texte dans « Texte de l'annonce », avec le lien.")
    client = PoliteClient(mod.HOME, min_delay=1.0, max_requests=3)
    d = mod.parse_detail(client.get(url), url)
    return _finish_import(d)


def manual_import(fields):
    """Import manuel : les valeurs saisies sont marquées « import manuel », le texte collé est analysé.
    Un lien seul (sans texte) d'une source autorisée est lu directement."""
    text = fields.get("texte") or ""
    url = (fields.get("url") or "").strip()
    if url and not text.strip() and not any(fields.get(k) for k in ("title", "mise_a_prix", "city")):
        return import_url(url)
    if url and not fields.get("source") or fields.get("source") == "auto":
        mod = source_for_url(url)
        fields = {**fields, "source": (getattr(mod, "DOMAINS", {}).get(_host(url)) or mod.NAME) if mod else "autre"}
    d = extract.analyse_text(text) if text else {}
    if text and not fields.get("city"):
        cp = re.search(r"\b(\d{5})\s+([A-ZÉÈÀ][\w'’ -]{1,40}?)\s*(?:\n|$|,|\()", text)
        if cp:
            d.setdefault("postcode", cp.group(1))
            d.setdefault("city", cp.group(2).strip())
    for k in ("title", "type", "surface", "rooms", "floor", "address", "city", "postcode", "mise_a_prix",
              "adjudication_price", "sale_date", "sale_place", "sale_kind", "occupation", "url"):
        v = fields.get(k)
        if v not in (None, ""):
            d[k] = float(v) if k in ("surface", "mise_a_prix", "adjudication_price") else int(v) if k in ("rooms", "floor") else v
    if not d.get("url") and not d.get("title"):
        raise ValueError("Indiquez au moins le lien de l'annonce ou un titre.")
    src = fields.get("source") or "manuel"
    d["source"] = "manuel:" + src
    d["source_id"] = d.get("url") or f"{src}-{db.now_iso()}"
    d["description"] = text[:3000] if text else None
    if text:
        d.update(contacts.extract(text, (), "import manuel"))
    d.setdefault("photos", [])
    d.setdefault("documents", [])
    if d.get("postcode"):
        d["dep"] = dep_from_postcode(d["postcode"])
    if not d.get("sale_kind"):
        d["sale_kind"] = {"notaires": "notariale", "domaine": "domaniale", "seloger": "amiable", "leboncoin": "amiable",
                          "pap": "amiable", "bienici": "amiable", "classiques": "amiable"}.get(src, "judiciaire")
    if d["sale_kind"] == "amiable" and not d.get("mise_a_prix") and text:
        pm = re.search(r"(?:prix|vendu|€)\D{0,20}?" + extract.NUM + r"\s*(?:€|euros)", text, re.I)
        if pm and extract.to_number(pm.group(1), pm.group(2)) >= 10000:
            d["mise_a_prix"] = extract.to_number(pm.group(1), pm.group(2))
    return _finish_import(d)


from . import extract  # noqa: E402  (import tardif pour éviter un cycle)
