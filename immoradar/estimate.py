"""Estimation de la valeur de marché à partir des ventes DVF réellement enregistrées.

Méthode (entièrement visible dans la fiche) :
1. Comparables = ventes DVF d'un seul local, même type, surface proche, sur N années.
2. On cherche d'abord dans le même immeuble, puis la même rue, puis des rayons croissants,
   puis la commune, jusqu'à atteindre le nombre minimal de comparables.
3. Prix au m² : médiane pondérée (proximité + ancienneté), bas = 1er quartile, haut = 3e quartile.
4. Ajustements optionnels (étage, extérieur) listés un par un.
Si les données sont insuffisantes, on renvoie un statut « insuffisant » avec la raison : jamais de chiffre inventé.
"""
import math
import re
from datetime import date

from . import db
from .geo_fr import slugify

DVF_TYPE = {"appartement": "Appartement", "studio": "Appartement", "maison": "Maison", "local_commercial": "Local"}


def haversine(lat1, lon1, lat2, lon2):
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def wquantile(values, weights, q):
    pairs = sorted(zip(values, weights))
    tot = sum(w for _, w in pairs)
    acc = 0
    for v, w in pairs:
        acc += w
        if acc >= q * tot:
            return v
    return pairs[-1][0]


def norm_voie(s):
    s = slugify(s or "")
    rep = {"bd": "boulevard", "av": "avenue", "ave": "avenue", "r": "rue", "pl": "place", "imp": "impasse",
           "che": "chemin", "rte": "route", "all": "allee", "sq": "square", "crs": "cours", "pass": "passage"}
    parts = s.split("-")
    if parts and parts[0] in rep:
        parts[0] = rep[parts[0]]
    return "-".join(p for p in parts if p not in ("de", "du", "des", "la", "le", "les", "l", "d"))


def estimate(item, params):
    """item : dict normalisé de l'annonce (+ géoloc). Renvoie un dict d'estimation."""
    p = params["estimation"]
    reasons = []
    typ = DVF_TYPE.get(item.get("type"))
    sfc = item.get("surface")
    lat, lon = item.get("lat"), item.get("lon")
    commune = item.get("code_commune")
    if item.get("type") == "immeuble":
        reasons.append("Immeuble entier : DVF ne fournit pas de comparables mono-local fiables, estimation à faire manuellement.")
    elif not typ:
        reasons.append("Type de bien inconnu ou non estimable (parking, terrain, autre).")
    if not sfc:
        reasons.append("Surface inconnue.")
    if lat is None and not commune:
        reasons.append("Localisation inconnue (ni coordonnées ni commune).")
    if reasons:
        return {"status": "insuffisant", "reasons": reasons}

    dep = item.get("dep")
    since = date(date.today().year - int(p["annees"]), date.today().month, 1).isoformat()
    lo_s, hi_s = sfc * (1 - p["tolerance_surface"]), sfc * (1 + p["tolerance_surface"])
    rows = db.q("SELECT * FROM dvf WHERE dep=? AND type=? AND date>=? AND surface BETWEEN ? AND ?",
                (dep, typ, since, lo_s, hi_s))
    if not db.q("SELECT 1 FROM dvf WHERE dep=? LIMIT 1", (dep,)):
        return {"status": "insuffisant", "reasons": [f"Données DVF du département {dep} non chargées (voir Paramètres > Données DVF)."]}

    voie = norm_voie(item.get("street"))
    num = (item.get("housenumber") or "").lower()
    comps = []
    for r in rows:
        d = None
        if lat is not None and r["lat"] is not None:
            d = haversine(lat, lon, r["lat"], r["lon"])
        same_street = bool(voie) and r["code_commune"] == commune and norm_voie(r["voie"]) == voie
        same_building = same_street and num and (r["numero"] or "").lower() == num
        if d is not None and d < 20:
            same_building = True
        if same_building:
            level = 0
        elif same_street:
            level = 1
        elif d is not None and d <= 300:
            level = 2
        elif d is not None and d <= 600:
            level = 3
        elif d is not None and d <= 1000:
            level = 4
        elif r["code_commune"] == commune:
            level = 5
        else:
            continue
        comps.append((level, d, r))

    labels = ["même immeuble", "même rue", "rayon 300 m", "rayon 600 m", "rayon 1 km", "même commune"]
    need = int(p["min_comparables"])
    chosen, used_level = [], None
    for lvl in range(6):
        chosen = [c for c in comps if c[0] <= lvl]
        if len(chosen) >= need:
            used_level = lvl
            break
    if used_level is None:
        return {"status": "insuffisant",
                "reasons": [f"Seulement {len(comps)} vente(s) comparable(s) trouvée(s) dans DVF (minimum requis : {need})."],
                "n_found": len(comps)}

    # écart des valeurs aberrantes (IQR)
    ppm = [c[2]["prix"] / c[2]["surface"] for c in chosen]
    s = sorted(ppm)
    q1, q3 = s[len(s) // 4], s[(3 * len(s)) // 4]
    iqr = q3 - q1
    kept = [c for c in chosen if q1 - 1.5 * iqr <= c[2]["prix"] / c[2]["surface"] <= q3 + 1.5 * iqr]
    if len(kept) < need:
        kept = chosen

    today = date.today()
    vals, weights = [], []
    for lvl, d, r in kept:
        try:
            age = (today - date.fromisoformat(r["date"][:10])).days / 365
        except ValueError:
            age = 3
        w = (1.0 / (1 + lvl)) * (1.0 / (1 + 0.25 * age))
        vals.append(r["prix"] / r["surface"])
        weights.append(w)
    med = wquantile(vals, weights, 0.5)
    low = wquantile(vals, weights, 0.25)
    high = wquantile(vals, weights, 0.75)

    adjustments = []
    f = item.get("floor")
    feats = item.get("features") or {}
    if typ == "Appartement":
        if f == 0 and p["ajust_rdc_pct"]:
            adjustments.append(("Rez-de-chaussée", p["ajust_rdc_pct"]))
        if f is not None and f >= 4 and feats.get("ascenseur") is False and p["ajust_etage_sans_asc_pct"]:
            adjustments.append((f"{f}e étage sans ascenseur", p["ajust_etage_sans_asc_pct"]))
        if (feats.get("terrasse") or feats.get("balcon") or feats.get("jardin")) and p["ajust_exterieur_pct"]:
            adjustments.append(("Espace extérieur", p["ajust_exterieur_pct"]))
    k = 1 + sum(a[1] for a in adjustments) / 100

    dispersion = (high - low) / med if med else 1
    ages = sorted((today - date.fromisoformat(c[2]["date"][:10])).days / 365 for c in kept)
    med_age = ages[len(ages) // 2]
    conf = min(len(kept), 20) / 20 * 30
    conf += [25, 25, 20, 15, 10, 5][used_level]
    conf += 20 if dispersion < 0.2 else 12 if dispersion < 0.35 else 6 if dispersion < 0.5 else 0
    conf += 15 if med_age < 1 else 10 if med_age < 2 else 5
    precision = item.get("geo_precision")
    conf += 10 if precision == "adresse" else 6 if precision == "coordonnées source" else 2
    conf = round(min(conf, 100))

    kept_sorted = sorted(kept, key=lambda c: (c[0], c[1] if c[1] is not None else 1e9))
    comps_out = [{
        "date": r["date"], "adresse": f"{r['numero']} {r['voie'] or ''}".strip(), "commune": r["commune"],
        "surface": r["surface"], "pieces": r["pieces"], "prix": r["prix"], "prix_m2": round(r["prix"] / r["surface"]),
        "distance_m": round(d) if d is not None else None, "niveau": labels[lvl], "id_mutation": r["id_mutation"],
    } for lvl, d, r in kept_sorted[:25]]
    dates = [c[2]["date"] for c in kept]
    return {
        "status": "ok",
        "basis": "Ventes DVF (DGFiP / Etalab, Licence Ouverte 2.0)",
        "level": labels[used_level],
        "n_comparables": len(kept),
        "n_outliers_removed": len(chosen) - len(kept),
        "comparables_m2": {"q1": round(low), "mediane": round(med), "q3": round(high)},
        "adjustments": [{"label": a, "pct": v} for a, v in adjustments],
        "prix_m2": {"bas": round(low * k), "central": round(med * k), "haut": round(high * k)},
        "valeur": {"bas": round(low * k * sfc, -3), "central": round(med * k * sfc, -3), "haut": round(high * k * sfc, -3)},
        "surface_utilisee": sfc,
        "periode": {"du": min(dates), "au": max(dates)},
        "confidence": conf,
        "confidence_label": "élevée" if conf >= 70 else "moyenne" if conf >= 45 else "faible",
        "comparables": comps_out,
        "note": "Valeur de marché en état standard, hors frais. Les travaux sont déduits séparément dans les simulations.",
    }
