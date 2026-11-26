"""Avis synthétique en langage clair, généré par règles à partir des données de la fiche.

Chaque point indique sa nature : « confirmé » (lu dans un document, avec page), « annonce » (lu dans
l'annonce), « estimé » (calcul ou estimation). Aucun point n'est inventé : sans donnée, pas de point.
"""
from . import finance

TYPE_TXT = {"appartement": "un appartement", "maison": "une maison", "studio": "un studio", "immeuble": "un immeuble",
            "local_commercial": "un local commercial", "parking": "un parking", "terrain": "un terrain"}


def _fmt(v):
    return f"{round(v):,} €".replace(",", " ")


def _facts(item):
    out = {}
    for d in item.get("doc_analyses") or []:
        if d.get("status") != "analysé":
            continue
        src = d.get("kind") or d.get("label")
        for f in d.get("facts", []):
            out.setdefault(f["field"], []).append({**f, "src": src})
        for r in d.get("risks", []):
            out.setdefault("risk:" + r["code"], []).append({**r, "src": src})
    return out


def _clip(q, n=110):
    """Citation courte, coupée sur des mots entiers (les extraits de document commencent parfois en milieu de mot)."""
    q = " ".join(str(q or "").split())
    words = q.split(" ")
    if len(words) > 1:  # l'extrait démarre en général au milieu d'un mot : on retire le premier
        words = words[1:]
    out = ""
    for w in words:
        if len(out) + len(w) + 1 > n:
            return out + "…"
        out = (out + " " + w).strip()
    return out


def _ref(x):
    return f"{x['src']} p.{x['page']}" if x.get("page") else x["src"]


def build(item, est, sim, scores, params):
    c = params["calculs"]
    F = _facts(item)
    pts = {"nature": [], "valeur": [], "positifs": [], "vigilance": []}

    def add(section, text, nature, ref=None):
        pts[section].append({"text": text, "nature": nature, "ref": ref})

    # ---------------------------------------------------------------- ce qu'est vraiment le bien
    sfc = item.get("surface")
    typ = TYPE_TXT.get(item.get("type"), "un bien de type inconnu")
    add("nature", f"Annonce : {typ}" + (f" de {sfc:g} m²" if sfc else "") +
        (f", {item['rooms']} pièce(s)" if item.get("rooms") else "") + (f", {item['city']}" if item.get("city") else "") + ".", "annonce")
    for code in ("chambre", "residence_sociale"):
        for r in F.get("risk:" + code, [])[:1]:
            add("nature", r["label"].split(" : ")[0] + f" — « {_clip(r.get('quote'))} »", "confirmé", _ref(r))
    for f in F.get("surface_document", [])[:1]:
        if sfc and f["value"] and abs(f["value"] - sfc) / sfc > 0.2:
            add("nature", f"Surface : l'annonce indique {sfc:g} m², le document environ {f['value']:g} m².", "confirmé", _ref(f))
    for f in F.get("surface", [])[:1]:
        add("nature", f"Surface Carrez {f['value']:g} m².", "confirmé", _ref(f))

    # ---------------------------------------------------------------- valeur réaliste
    candidates = []
    if item.get("valeur_marche_manuel"):
        candidates.append(("votre valeur saisie", float(item["valeur_marche_manuel"]), "saisi", None))
    if est and est.get("status") == "ok":
        add("valeur", f"Estimation DVF : {_fmt(est['valeur']['bas'])} à {_fmt(est['valeur']['haut'])} (centrale {_fmt(est['valeur']['central'])}), "
            f"{est['n_comparables']} ventes {est['level']}, confiance {est['confidence_label']}.", "estimé")
        candidates.append(("estimation DVF centrale", est["valeur"]["central"], "estimé", None))
    for f in F.get("prix_m2_document", [])[:1]:
        base = next((x["value"] for x in F.get("surface_document", []) if x["value"]), None) or sfc
        if base:
            v = f["value"] * base
            add("valeur", f"Le document retient {_fmt(f['value'])}/m², soit environ {_fmt(v)} pour {base:g} m².", "confirmé", _ref(f))
            candidates.append(("prix au m² du document", v, "confirmé", _ref(f)))
    for f in F.get("comparable_document", [])[:2]:
        add("valeur", f"Comparable cité dans le document : {_fmt(f['value'])}.", "confirmé", _ref(f))
        candidates.append(("comparable du document", f["value"], "confirmé", _ref(f)))
    prudent = min(candidates, key=lambda x: x[1]) if candidates else None
    if prudent and est and est.get("status") == "ok" and prudent[1] < 0.75 * est["valeur"]["central"]:
        add("valeur", f"Les documents indiquent une valeur nettement plus basse que DVF : retenir plutôt {_fmt(prudent[1])} "
            f"({prudent[0]}). Les comparables DVF ne ressemblent probablement pas à ce bien.", "estimé", prudent[3])
    if not candidates:
        add("valeur", "Aucune valeur fiable : estimation DVF impossible et aucune valeur dans les documents.", "estimé")

    # ---------------------------------------------------------------- positifs
    occ = item.get("occupation")
    prov = item.get("prov") or {}
    if occ == "libre":
        src = prov.get("occupation", "annonce")
        add("positifs", "Bien libre de toute occupation.", "confirmé" if "p." in str(src) else "annonce", src if "p." in str(src) else None)
    wl = item.get("works_level")
    if wl in ("aucun", "léger"):
        src = prov.get("works_level")
        add("positifs", "Peu de travaux : " + ("bon état." if wl == "aucun" else "état d'usage, petites reprises."),
            "confirmé" if src and "p." in str(src) else "annonce", src if src and "p." in str(src) else None)
    for f in F.get("lots_count", [])[:1]:
        add("positifs", "Vente en un seul lot.", "confirmé", _ref(f))
    for code, txt in (("hypotheque_ok", "Aucune hypothèque inscrite."), ("purge", "L'adjudication purge les hypothèques.")):
        for r in F.get("risk:" + code, [])[:1]:
            add("positifs", txt, "confirmé", _ref(r))
    for f in F.get("dpe", [])[:1]:
        if f["value"] in "ABCD":
            add("positifs", f"DPE {f['value']}.", "confirmé", _ref(f))
    if scores.get("decote_pct") and scores["decote_pct"] > 30 and prudent and prudent[1] and item.get("mise_a_prix"):
        add("positifs", f"Mise à prix ({_fmt(item['mise_a_prix'])}) très inférieure à la valeur prudente ({_fmt(prudent[1])}) : "
            "les enchères monteront probablement.", "estimé")

    # ---------------------------------------------------------------- vigilance
    seen = set()
    for r in sorted(item.get("risks") or [], key=lambda r: -r.get("severity", 1)):
        if r.get("severity", 1) <= 0 or r["code"] in seen or r["code"] in ("chambre", "residence_sociale"):
            continue
        seen.add(r["code"])
        ref = f"{r['source']} p.{r['page']}" if r.get("source") and r.get("page") else r.get("source")
        add("vigilance", r["label"] + ("" if r.get("scope") != "locatif" else " (concerne la location)"), "confirmé" if ref else "annonce", ref)
    for code in ("chambre", "residence_sociale"):
        for r in F.get("risk:" + code, [])[:1]:
            add("vigilance", r["label"], "confirmé", _ref(r))
    if occ == "occupé":
        add("vigilance", "Bien occupé : libération non garantie, délai et coût à prévoir.", "annonce")
    if wl in ("moyen", "lourd"):
        add("vigilance", f"Travaux {wl}s à prévoir.", "annonce")
    for cfl in item.get("doc_conflicts") or []:
        add("vigilance", "Écart : " + cfl, "confirmé")
    for f in F.get("baisse_mise_a_prix", [])[:1]:
        add("vigilance", "Faculté de baisse de la mise à prix en cas de carence d'enchères.", "confirmé", _ref(f))
    if not item.get("doc_analyses"):
        add("vigilance", "Aucun document analysé (PV descriptif, cahier des conditions de vente) : avis fondé sur l'annonce seule.", "estimé")

    # ---------------------------------------------------------------- prix plafond
    plafond = None
    if prudent and item.get("surface"):
        value = prudent[1]
        target = c["marge_cible_pct"]
        plafond = finance._bisect(lambda p: (lambda z: z["net"] / z["cout"] * 100)(finance._resale(p, item, value, c)), target)
        if plafond:
            fees = finance._resale(plafond, item, value, c)
            plafond_txt = (f"Enchère maximale indicative : {_fmt(plafond)} (coût total {_fmt(fees['cout'])} frais et travaux compris), "
                           f"pour garder {target} % de marge nette en revendant à {_fmt(value)} ({prudent[0]}).")
        else:
            plafond_txt = f"Même à la mise à prix, la marge cible de {target} % n'est pas atteinte en revendant à {_fmt(value)}."
    else:
        plafond_txt = "Prix plafond non calculable : valeur ou surface inconnue."

    # ---------------------------------------------------------------- verdict
    major = [r for r in item.get("risks") or [] if r.get("severity", 1) >= 3 and r.get("scope") in (None, "all")]
    best = max((scores.get("revente") or {}).get("score") or 0, (scores.get("locatif") or {}).get("score") or 0)
    vig = len([p for p in pts["vigilance"] if p["nature"] != "estimé"])
    if major:
        verdict = "À éviter sauf expertise juridique : risque majeur (" + ", ".join(r["label"] for r in major) + ")."
    elif prudent and est and est.get("status") == "ok" and prudent[1] < 0.75 * est["valeur"]["central"]:
        verdict = "Bien piège possible : la valeur réaliste est bien inférieure à l'estimation automatique. N'enchérir que très en dessous."
    elif F.get("risk:chambre") or F.get("risk:residence_sociale"):
        verdict = ("Bien atypique (chambre / résidence sociale selon le document) : valeur de revente incertaine, "
                   "les estimations automatiques ne s'appliquent pas. N'enchérir que sous le prix plafond prudent.")
    elif best >= 60 and vig <= 2:
        verdict = "Opportunité intéressante, à confirmer par la lecture du cahier des conditions de vente et une visite."
    elif best >= 40:
        verdict = "À étudier : potentiel réel mais points de vigilance à lever."
    else:
        verdict = "Peu intéressant en l'état des informations disponibles."
    return {"verdict": verdict, "sections": pts, "plafond": plafond, "plafond_txt": plafond_txt,
            "valeur_prudente": prudent[1] if prudent else None, "valeur_prudente_source": prudent[0] if prudent else None,
            "note": "Avis généré automatiquement par règles à partir des données de la fiche ; ce n'est pas un conseil juridique."}
