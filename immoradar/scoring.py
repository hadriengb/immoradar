"""Notes sur 100 : achat-revente et locatif. Le détail de chaque composante est renvoyé et affiché.
Un risque juridique majeur plafonne la note à 40, quelle que soit la rentabilité."""


def lin(x, x0, x1, pts):
    if x is None:
        return 0
    if x <= x0:
        return 0
    if x >= x1:
        return pts
    return (x - x0) / (x1 - x0) * pts


def completeness(item):
    checks = [item.get("surface"), item.get("rooms") is not None, item.get("geo_precision") == "adresse",
              item.get("occupation"), item.get("works_level"), item.get("sale_date"), item.get("type")]
    return sum(1 for c in checks if c) / len(checks)


def penalties(item, mode):
    out = []
    lvl = item.get("works_level")
    if lvl == "lourd":
        out.append(("Travaux lourds", -10))
    elif lvl == "moyen":
        out.append(("Travaux moyens", -4))
    elif not lvl:
        out.append(("État du bien inconnu", -3))
    occ = item.get("occupation")
    if occ == "occupé":
        out.append(("Bien occupé", -15 if mode == "revente" else -6))
    elif not occ:
        out.append(("Occupation inconnue", -4))
    for r in item.get("risks") or []:
        if r["code"] in ("occupe", "bail") :
            continue  # déjà compté via l'occupation
        out.append((r["label"], {3: -20, 2: -8, 1: -2}[r["severity"]]))
    return out


def _finish(parts, pens, item):
    score = sum(p[1] for p in parts) + sum(p[1] for p in pens)
    score = max(0, min(100, score))
    major = [r["label"] for r in item.get("risks") or [] if r["severity"] >= 3]
    capped = False
    if major and score > 40:
        score, capped = 40, True
    return {"score": round(score), "parts": [{"label": a, "pts": round(b, 1)} for a, b in parts],
            "penalties": [{"label": a, "pts": b} for a, b in pens],
            "cap": f"Plafonnée à 40 : risque juridique majeur ({', '.join(major)})" if capped else None}


def score(item, est, sim):
    res = {"revente": None, "locatif": None, "decote_pct": None}
    prix = (sim.get("achat_revente") or sim.get("locatif") or {}).get("prix_envisage") or item.get("mise_a_prix")
    decote = None
    if est and est.get("status") == "ok" and prix:
        decote = (1 - prix / est["valeur"]["central"]) * 100
        res["decote_pct"] = round(decote, 1)
    conf = (est or {}).get("confidence") or 0
    comp = completeness(item)

    ar = sim.get("achat_revente")
    if ar:
        head = (ar["prix_max_conseille"] / prix - 1) * 100 if ar["prix_max_conseille"] else -100
        parts = [("Décote apparente vs marché", lin(decote, 0, 50, 30)),
                 ("Marge nette au prix envisagé", lin(ar["marge_nette_pct"], 0, 25, 30)),
                 ("Marge de manœuvre (prix max / prix envisagé)", lin(head, 0, 60, 15)),
                 ("Qualité des comparables", conf / 100 * 15),
                 ("Fiabilité des informations", comp * 10)]
        res["revente"] = _finish(parts, penalties(item, "revente"), item)

    lo = sim.get("locatif")
    if lo:
        head = (lo["prix_max_conseille"] / prix - 1) * 100 if lo["prix_max_conseille"] else -100
        parts = [("Rendement net avant impôt", lin(lo["rendement_net_pct"], 3, 9, 30)),
                 ("Cash-flow mensuel", lin(lo["cashflow_mensuel"], -300, 300, 10)),
                 ("Décote apparente vs marché", lin(decote, 0, 50, 20)),
                 ("Marge de manœuvre (prix max / prix envisagé)", lin(head, 0, 60, 15)),
                 ("Qualité des comparables / loyers", (conf / 100 * 15) if est and est.get("status") == "ok" else 5),
                 ("Fiabilité des informations", comp * 10)]
        res["locatif"] = _finish(parts, penalties(item, "locatif"), item)
    return res
