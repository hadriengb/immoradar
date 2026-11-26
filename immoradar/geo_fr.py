"""Référentiel minimal des départements utiles et des zones prédéfinies."""
import re
import unicodedata

DEPARTEMENTS = {
    "75": "Paris", "77": "Seine-et-Marne", "78": "Yvelines", "91": "Essonne",
    "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis", "94": "Val-de-Marne", "95": "Val-d'Oise",
    "69": "Rhône", "01": "Ain", "38": "Isère", "42": "Loire",
    # autres départements reconnus (pour ne pas perdre l'information si une annonce en vient)
    "13": "Bouches-du-Rhône", "06": "Alpes-Maritimes", "33": "Gironde", "31": "Haute-Garonne",
    "59": "Nord", "44": "Loire-Atlantique", "67": "Bas-Rhin", "34": "Hérault", "35": "Ille-et-Vilaine",
    "73": "Savoie", "74": "Haute-Savoie", "60": "Oise", "45": "Loiret", "28": "Eure-et-Loir",
    "27": "Eure", "51": "Marne", "10": "Aube", "89": "Yonne", "02": "Aisne", "76": "Seine-Maritime",
    "14": "Calvados", "37": "Indre-et-Loire", "63": "Puy-de-Dôme", "71": "Saône-et-Loire",
    "21": "Côte-d'Or", "26": "Drôme", "07": "Ardèche", "83": "Var", "84": "Vaucluse", "30": "Gard",
    "57": "Moselle", "54": "Meurthe-et-Moselle", "68": "Haut-Rhin", "25": "Doubs", "29": "Finistère",
    "56": "Morbihan", "49": "Maine-et-Loire", "72": "Sarthe", "86": "Vienne", "87": "Haute-Vienne",
    "64": "Pyrénées-Atlantiques", "66": "Pyrénées-Orientales", "17": "Charente-Maritime",
    "2A": "Corse-du-Sud", "2B": "Haute-Corse", "971": "Guadeloupe", "972": "Martinique", "974": "La Réunion",
}

ZONES = {
    "idf": {"label": "Île-de-France", "deps": ["75", "77", "78", "91", "92", "93", "94", "95"]},
    "lyon": {"label": "Région lyonnaise", "deps": ["69", "01", "38", "42"]},
}


def slugify(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


SLUG_TO_DEP = {slugify(v): k for k, v in DEPARTEMENTS.items()}


def dep_from_postcode(cp):
    cp = (cp or "").strip()
    if not re.fullmatch(r"\d{5}", cp):
        return None
    if cp.startswith("97"):
        return cp[:3]
    if cp.startswith("20"):
        return "2A" if int(cp) < 20200 else "2B"
    return cp[:2]


def dep_from_slug(slug):
    return SLUG_TO_DEP.get(slugify(slug))


def dep_from_text(text):
    """Cherche un nom de département entre parenthèses ou un code postal."""
    m = re.search(r"\b(\d{5})\b", text or "")
    if m:
        d = dep_from_postcode(m.group(1))
        if d:
            return d
    t = slugify(text or "")
    best = None
    for slug, code in SLUG_TO_DEP.items():
        if re.search(r"(^|-)" + re.escape(slug) + r"($|-)", t):
            if best is None or len(slug) > len(best[0]):
                best = (slug, code)
    return best[1] if best else None
