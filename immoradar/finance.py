"""Simulations achat-revente et locatif. Chaque ligne indique sa formule et si la valeur est
une donnée de l'annonce, une estimation ou une hypothèse modifiable."""


def emoluments_vente_judiciaire(prix):
    """Émoluments proportionnels de l'avocat poursuivant (art. A444-191 C. com.), HT puis TTC 20 %.
    Barème indicatif : le montant exact figure dans le cahier des conditions de vente."""
    tranches = [(6500, 7.256), (17000, 2.993), (60000, 1.995), (float("inf"), 1.497)]
    reste, bas, tot = prix, 0, 0.0
    for haut, taux in tranches:
        part = max(0, min(prix, haut) - bas)
        tot += part * taux / 100
        bas = haut
        if prix <= haut:
            break
    return tot * 1.2


def frais_acquisition(prix, sale_kind, c):
    lines = []
    if sale_kind in ("notariale", "domaniale"):
        v = prix * c["frais_acquisition_notariale_pct"] / 100
        lines.append(("Frais d'acquisition (notaire, droits, frais de vente)", v, f"{c['frais_acquisition_notariale_pct']} % du prix", "hypothèse"))
    else:
        dm = prix * (c["droits_mutation_pct"] + c["csi_pct"]) / 100
        lines.append(("Droits de mutation + CSI", dm, f"{c['droits_mutation_pct']} % + {c['csi_pct']} % du prix", "hypothèse (taux légal du département à vérifier)"))
        lines.append(("Frais préalables (taxés)", c["frais_prealables"], "montant forfaitaire saisi", "hypothèse (montant réel annoncé avant les enchères)"))
        if c["emoluments_bareme"]:
            lines.append(("Émoluments avocat poursuivant (TTC)", emoluments_vente_judiciaire(prix), "barème réglementé A444-191", "estimé"))
        lines.append(("Honoraires avocat enchérisseur", c["honoraires_avocat"], "montant saisi", "hypothèse"))
    return lines


def mensualite(capital, taux_pct, ans):
    if capital <= 0:
        return 0.0
    n = ans * 12
    t = taux_pct / 100 / 12
    if t == 0:
        return capital / n
    return capital * t / (1 - (1 + t) ** -n)


def travaux(item, c):
    lvl = item.get("works_level") or "inconnu"
    sfc = item.get("surface") or 0
    if item.get("travaux_manuel") is not None:
        return float(item["travaux_manuel"]), "montant saisi manuellement", "saisi"
    return c["travaux_m2"].get(lvl, c["travaux_m2"]["inconnu"]) * sfc, f"niveau « {lvl} » x {c['travaux_m2'].get(lvl)} €/m² x {sfc:g} m²", "hypothèse" if lvl == "inconnu" else "estimé (mots-clés de l'annonce)"


def prix_envisage(item, c):
    if item.get("prix_envisage_manuel"):
        return float(item["prix_envisage_manuel"]), "saisi manuellement"
    base = item.get("adjudication_price") if item.get("status_hint") == "adjugée" and item.get("adjudication_price") else item.get("mise_a_prix")
    if not base:
        return None, None
    if base == item.get("adjudication_price"):
        return base, "prix d'adjudication"
    return base * (1 + c["hausse_encheres_pct"] / 100), f"mise à prix + {c['hausse_encheres_pct']} %"


def _resale(prix, item, valeur, c):
    sfc = item.get("surface") or 0
    fa = frais_acquisition(prix, item.get("sale_kind"), c)
    tot_fa = sum(l[1] for l in fa)
    trv, trv_f, trv_s = travaux(item, c)
    mois = c["duree_detention_mois"]
    charges = (c["charges_copro_m2_an"] + c["taxe_fonciere_m2_an"]) * sfc * mois / 12 + c["energie_assurance_mois"] * mois
    base = prix + tot_fa + trv
    emprunt = base * (1 - c["apport_pct"] / 100)
    fin = emprunt * (c["taux_credit_pct"] + c["assurance_emprunteur_pct"]) / 100 * mois / 12 + emprunt * c["frais_dossier_garantie_pct"] / 100
    cout = base + charges + fin
    frais_rev = valeur * c["frais_revente_pct"] / 100
    brut = valeur - frais_rev - cout
    if c["fiscalite"] == "particulier":
        pv = valeur - frais_rev - prix - tot_fa - trv
        impot, impot_f = max(0, pv) * 0.362, "36,2 % de la plus-value (19 % IR + 17,2 % PS, détention < 5 ans)"
    elif c["fiscalite"] == "marchand":
        impot, impot_f = max(0, brut) * 0.25, "IS 25 % sur le bénéfice (approximation, hors TVA sur marge)"
    else:
        impot, impot_f = 0, "non calculée"
    net = brut - impot
    return {"fa": fa, "tot_fa": tot_fa, "trv": (trv, trv_f, trv_s), "charges": charges, "fin": fin, "emprunt": emprunt,
            "cout": cout, "frais_rev": frais_rev, "brut": brut, "impot": (impot, impot_f), "net": net}


def _rental(prix, item, loyer_mensuel_hc, c):
    sfc = item.get("surface") or 0
    fa = frais_acquisition(prix, item.get("sale_kind"), c)
    tot_fa = sum(l[1] for l in fa)
    trv, trv_f, trv_s = travaux(item, c)
    cout = prix + tot_fa + trv
    annuel = loyer_mensuel_hc * 12
    vacance = annuel * c["vacance_mois_an"] / 12
    encaisse = annuel - vacance
    gestion = encaisse * c["gestion_pct"] / 100
    copro = c["charges_copro_non_recup_m2_an"] * sfc
    tf = c["taxe_fonciere_m2_an"] * sfc
    pno = c["assurance_pno_an"]
    entretien = annuel * c["entretien_pct_loyer"] / 100
    net_avant_impot = encaisse - gestion - copro - tf - pno - entretien
    emprunt = cout * (1 - c["apport_pct"] / 100)
    mens = mensualite(emprunt, c["taux_credit_pct"], c["duree_credit_ans"])
    ass = emprunt * c["assurance_emprunteur_pct"] / 100 / 12
    cashflow = net_avant_impot / 12 - mens - ass
    return {"fa": fa, "tot_fa": tot_fa, "trv": (trv, trv_f, trv_s), "cout": cout, "annuel": annuel, "vacance": vacance,
            "gestion": gestion, "copro": copro, "tf": tf, "pno": pno, "entretien": entretien, "net": net_avant_impot,
            "emprunt": emprunt, "mens": mens, "ass": ass, "cashflow": cashflow}


def _bisect(fn, target, lo=1000, hi=None):
    """Plus grand prix p tel que fn(p) >= target (fn décroissante en p)."""
    hi = hi or 20_000_000
    if fn(lo) < target:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if fn(mid) >= target:
            lo = mid
        else:
            hi = mid
    return round(lo, -2)


def L(label, value, formule="", nature="calculé"):
    return {"label": label, "value": None if value is None else round(value, 2), "formule": formule, "nature": nature}


def simulate(item, est, rent, c):
    out = {"achat_revente": None, "locatif": None, "warnings": []}
    prix, prix_src = prix_envisage(item, c)
    if not prix:
        out["warnings"].append("Mise à prix inconnue : aucune simulation possible.")
        return out
    if not item.get("surface"):
        out["warnings"].append("Surface inconnue : travaux et charges au m² non calculables, simulations désactivées.")
        return out
    if c["hausse_encheres_pct"] == 0 and prix_src and prix_src.startswith("mise à prix"):
        out["warnings"].append("Simulation au niveau de la mise à prix : les adjudications la dépassent souvent. "
                               "Le « prix maximum conseillé » est l'indicateur à retenir.")

    # ------------------------------------------------ achat-revente
    if est and est.get("status") == "ok":
        valeur = est["valeur"]["central"]
        r = _resale(prix, item, valeur, c)
        lines = [L("Prix d'acquisition envisagé", prix, prix_src, "donnée annonce" if "mise" in prix_src or "adjudication" in prix_src else "saisi")]
        lines += [L(a, v, f, n) for a, v, f, n in r["fa"]]
        lines += [
            L("Travaux estimés", r["trv"][0], r["trv"][1], r["trv"][2]),
            L("Charges pendant la détention", r["charges"], f"({c['charges_copro_m2_an']}+{c['taxe_fonciere_m2_an']}) €/m²/an x surface x {c['duree_detention_mois']} mois + {c['energie_assurance_mois']} €/mois", "hypothèse"),
            L("Frais de financement", r["fin"], f"emprunt {r['emprunt']:.0f} € x ({c['taux_credit_pct']}+{c['assurance_emprunteur_pct']}) % x durée + {c['frais_dossier_garantie_pct']} % dossier/garantie" if r["emprunt"] else "apport 100 % : pas de crédit", "hypothèse"),
            L("Coût total de l'opération", r["cout"], "somme des lignes ci-dessus"),
            L("Prix de revente estimé", valeur, "estimation centrale DVF", "estimé"),
            L("Frais de revente", r["frais_rev"], f"{c['frais_revente_pct']} % du prix de revente", "hypothèse"),
            L("Bénéfice avant impôt", r["brut"], "revente - frais de revente - coût total"),
            L("Fiscalité indicative", r["impot"][0], r["impot"][1], "estimé"),
            L("Bénéfice net estimé", r["net"], "bénéfice avant impôt - fiscalité"),
        ]
        marge = r["net"] / valeur * 100
        renta = r["net"] / r["cout"] * 100
        cible = c["marge_cible_pct"]
        maxbid = _bisect(lambda p: (lambda z: z["net"] / z["cout"] * 100)(_resale(p, item, valeur, c)), cible)
        pess = _resale(prix, item, est["valeur"]["bas"], c)
        out["achat_revente"] = {
            "lines": lines, "cout_total": round(r["cout"]), "revente": valeur, "benefice_net": round(r["net"]),
            "marge_nette_pct": round(marge, 1), "rentabilite_pct": round(renta, 1),
            "prix_max_conseille": maxbid, "prix_max_regle": f"bénéfice net / coût total ≥ {cible} %",
            "scenario_bas": {"revente": est["valeur"]["bas"], "benefice_net": round(pess["net"])},
            "prix_envisage": prix,
        }
    else:
        out["warnings"].append("Achat-revente non calculé : pas d'estimation de valeur fiable.")

    # ------------------------------------------------ locatif
    sfc = item.get("surface")
    loyer_cc = None
    if item.get("loyer_manuel"):
        loyer_hc, loyer_src, loyer_nat = float(item["loyer_manuel"]), "loyer mensuel saisi", "saisi"
    elif c.get("loyer_m2_manuel"):
        loyer_hc, loyer_src, loyer_nat = c["loyer_m2_manuel"] * sfc, f"{c['loyer_m2_manuel']} €/m² (paramètre) x {sfc:g} m²", "hypothèse"
    elif rent:
        loyer_cc = rent["loyer_m2"] * sfc
        loyer_hc = loyer_cc * (1 - c["charges_recuperables_pct"] / 100)
        loyer_src = (f"Carte des loyers {rent['loyer_m2']:.1f} €/m² CC x {sfc:g} m² - {c['charges_recuperables_pct']} % charges"
                     f" (intervalle {rent['bas'] or '?'}-{rent['haut'] or '?'} €/m², {rent['nb_obs'] or '?'} annonces)")
        loyer_nat = "estimé"
    else:
        out["warnings"].append("Locatif non calculé : aucun indicateur de loyer pour cette commune. Saisissez un loyer sur la fiche.")
        return out
    if item.get("occupation") == "occupé":
        out["warnings"].append("Bien occupé : le loyer réel (bail en cours) peut différer du loyer de marché estimé.")
    r = _rental(prix, item, loyer_hc, c)
    lines = [L("Prix d'acquisition", prix, prix_src, "donnée annonce")]
    lines += [L(a, v, f, n) for a, v, f, n in r["fa"]]
    lines += [
        L("Travaux", r["trv"][0], r["trv"][1], r["trv"][2]),
        L("Prix de revient total", r["cout"], "prix + frais + travaux"),
        L("Loyer mensuel hors charges", loyer_hc, loyer_src, loyer_nat),
        L("Revenus locatifs annuels", r["annuel"], "loyer x 12"),
        L("Vacance locative", -r["vacance"], f"{c['vacance_mois_an']} mois/an", "hypothèse"),
        L("Frais de gestion", -r["gestion"], f"{c['gestion_pct']} % des loyers encaissés", "hypothèse"),
        L("Charges de copropriété non récupérables", -r["copro"], f"{c['charges_copro_non_recup_m2_an']} €/m²/an", "hypothèse"),
        L("Taxe foncière", -r["tf"], f"{c['taxe_fonciere_m2_an']} €/m²/an", "hypothèse"),
        L("Assurance PNO", -r["pno"], "montant annuel saisi", "hypothèse"),
        L("Entretien", -r["entretien"], f"{c['entretien_pct_loyer']} % des loyers", "hypothèse"),
        L("Revenu net annuel avant impôt", r["net"], "loyers - vacance - charges"),
        L("Mensualité de crédit (+ assurance)", -(r["mens"] + r["ass"]), f"emprunt {r['emprunt']:.0f} € sur {c['duree_credit_ans']} ans à {c['taux_credit_pct']} %" if r["emprunt"] else "apport 100 % : pas de crédit", "hypothèse"),
    ]
    cible = c["rendement_net_cible_pct"]
    maxbid = _bisect(lambda p: (lambda z: z["net"] / z["cout"] * 100)(_rental(p, item, loyer_hc, c)), cible)
    out["locatif"] = {
        "lines": lines, "loyer_mensuel": round(loyer_hc), "loyer_cc": round(loyer_cc) if loyer_cc else None,
        "revenus_annuels": round(r["annuel"]),
        "rendement_brut_pct": round(r["annuel"] / r["cout"] * 100, 2),
        "rendement_brut_sur_prix_pct": round(r["annuel"] / prix * 100, 2),
        "rendement_net_pct": round(r["net"] / r["cout"] * 100, 2),
        "cashflow_mensuel": round(r["cashflow"]),
        "prix_max_conseille": maxbid, "prix_max_regle": f"rendement net avant impôt ≥ {cible} %",
        "prix_envisage": prix,
    }
    return out
