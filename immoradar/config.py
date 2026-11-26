"""Paramètres par défaut. Tous sont modifiables dans l'écran Paramètres et sont affichés dans chaque calcul."""
import copy

from . import db

DEFAULTS = {
    "criteres": {
        "zones": ["idf", "lyon"],
        "departements": [],          # vide = tous les départements des zones choisies
        "villes": [],                # vide = toutes
        "types": [],                 # appartement, maison, studio, t1, t2, t3, t4+, immeuble, local_commercial
        "surface_min": None, "surface_max": None,
        "pieces_min": None, "pieces_max": None,
        "mise_a_prix_min": None, "mise_a_prix_max": None,
        "adjudication_min": None, "adjudication_max": None,
        "occupation": "",            # "", libre, occupé
        "exterieurs": [],            # terrasse, balcon, jardin, parking
        "date_min": None, "date_max": None,
        "types_vente": [],           # judiciaire, notariale, domaniale
        "inclure_passees": False,
        "selection": True,           # n'afficher que les biens pertinents (voir pipeline.pertinence)
        "note_min": 60,              # note minimale (meilleure des deux) pour la sélection
    },
    "sources": {"licitor": True, "vench": True, "alertes_email": True, "streamestate": True, "avoventes": True, "notaires": False, "domaine": False},
    "signature": "",                 # signature des mails de demande d'informations
    "actualisation_auto_heures": 0,   # ancien réglage, remplacé par recherche_auto
    "recherche_auto": {"active": True, "heure": 7},   # une recherche par jour, à partir de cette heure
    "estimation": {
        "annees": 3,
        "tolerance_surface": 0.4,
        "min_comparables": 6,
        "ajust_rdc_pct": -5,
        "ajust_etage_sans_asc_pct": -5,
        "ajust_exterieur_pct": 3,
    },
    "calculs": {
        "hausse_encheres_pct": 0,          # prix envisagé = mise à prix x (1 + x%)
        "droits_mutation_pct": 6.32,       # DMTO 2025 (taux majoré adopté par la plupart des départements) + CSI ~0,1 %
        "csi_pct": 0.10,
        "frais_prealables": 5000,          # vente judiciaire : montant réel annoncé à l'audience
        "emoluments_bareme": True,         # barème réglementé des émoluments de l'avocat (ventes judiciaires)
        "honoraires_avocat": 1500,         # honoraires libres de l'avocat enchérisseur
        "frais_acquisition_notariale_pct": 8.5,  # ventes notariales / domaniales : frais de notaire + frais de vente
        "travaux_m2": {"aucun": 0, "léger": 250, "moyen": 650, "lourd": 1300, "inconnu": 650},
        # financement
        "apport_pct": 100, "taux_credit_pct": 3.6, "duree_credit_ans": 20, "assurance_emprunteur_pct": 0.30,
        "frais_dossier_garantie_pct": 1.5,
        # achat-revente
        "duree_detention_mois": 9,
        "charges_copro_m2_an": 35, "taxe_fonciere_m2_an": 15, "energie_assurance_mois": 60,
        "frais_revente_pct": 2.0,
        "fiscalite": "particulier",        # particulier (PV 36,2 %), marchand (IS 25 %), aucune
        "marge_cible_pct": 15,
        # locatif
        "loyer_m2_manuel": None,           # si renseigné, remplace la Carte des loyers
        "charges_recuperables_pct": 10,    # Carte des loyers = loyers charges comprises
        "vacance_mois_an": 1,
        "gestion_pct": 0,
        "assurance_pno_an": 180,
        "charges_copro_non_recup_m2_an": 12,
        "entretien_pct_loyer": 3,
        "rendement_net_cible_pct": 5.5,
    },
    "scoring": {"seuil_opportunite": 80},
    "_version": 2,
}

TYPE_LABELS = {"appartement": "Appartement", "maison": "Maison", "studio": "Studio", "immeuble": "Immeuble",
               "local_commercial": "Local commercial", "parking": "Parking", "terrain": "Terrain", None: "Inconnu"}


def deep_merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    stored = db.get_setting("params", {})
    # 26/11/2026 : le seuil « opportunité intéressante » passe de 60 à 80 (demande de l'utilisateur).
    # Une ancienne valeur 60 enregistrée (simple défaut de l'époque) est mise à jour une fois.
    if stored and stored.get("_version", 1) < 2:
        if (stored.get("scoring") or {}).get("seuil_opportunite") == 60:
            stored.setdefault("scoring", {})["seuil_opportunite"] = 80
        stored["_version"] = 2
        db.set_setting("params", stored)
    return deep_merge(DEFAULTS, stored)


def save(params):
    db.set_setting("params", deep_merge(DEFAULTS, params))
