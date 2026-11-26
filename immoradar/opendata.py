"""Données ouvertes : DVF géolocalisées (Etalab), géocodage BAN / Géoplateforme, Carte des loyers (ANIL).

Toutes les données viennent de fichiers ou d'API publiques sous Licence Ouverte 2.0.
En cas d'échec réseau, on enregistre l'erreur : aucune donnée de remplacement n'est fabriquée.
"""
import csv
import gzip
import io
import json
import re
import urllib.parse
from collections import defaultdict
from datetime import date

from . import db
from .net import FetchError, raw_get

DVF_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/{year}/departements/{dep}.csv.gz"
DVF_DATASET_API = "https://www.data.gouv.fr/api/1/datasets/demandes-de-valeurs-foncieres-geolocalisees/"
DVF_TYPES = {"Appartement", "Maison", "Local industriel. commercial ou assimilé"}

LOYERS_URLS = {  # Carte des loyers 2025 (ANIL, réf. T3 2025) - Licence Ouverte 2.0
    "app": "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145010/pred-app-mef-dhup.csv",
    "app12": "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-144934/pred-app12-mef-dhup.csv",
    "app3": "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-144951/pred-app3-mef-dhup.csv",
    "mai": "https://static.data.gouv.fr/resources/carte-des-loyers-indicateurs-de-loyers-dannonce-par-commune-en-2025/20251211-145039/pred-mai-mef-dhup.csv",
}
LOYERS_SOURCE = "Carte des loyers 2025 — Estimations ANIL, à partir des données du Groupe SeLoger et de leboncoin"

GEOCODERS = [
    "https://data.geopf.fr/geocodage/search?",
    "https://api-adresse.data.gouv.fr/search/?",
]
REVERSE = [
    "https://data.geopf.fr/geocodage/reverse?",
    "https://api-adresse.data.gouv.fr/reverse/?",
]


# ---------------------------------------------------------------- DVF

def _col(row, *names):
    for n in names:
        if n in row and row[n] not in (None, ""):
            return row[n]
    return None


def _f(v):
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _mutation_row(rows, dep):
    if any((r.get("nature_mutation") or "") != "Vente" for r in rows):
        return None
    locaux = [r for r in rows if (r.get("type_local") or "") in DVF_TYPES]
    if len(locaux) != 1:
        return None  # vente multi-locaux : prix global non attribuable à un local
    r = locaux[0]
    prix = _f(r.get("valeur_fonciere"))
    sfc = _f(r.get("surface_reelle_bati"))
    if not prix or not sfc or sfc < 8 or prix < 5000:
        return None
    carrez = _f(_col(r, "lot1_surface_carrez", "lot_1_surface_carrez"))
    typ = {"Appartement": "Appartement", "Maison": "Maison"}.get(r["type_local"], "Local")
    return (
        r.get("id_mutation"), r.get("date_mutation"), prix, typ, sfc, int(_f(r.get("nombre_pieces_principales")) or 0),
        (r.get("adresse_numero") or "") + (r.get("adresse_suffixe") or ""), r.get("adresse_nom_voie"),
        r.get("adresse_code_voie"), r.get("code_postal"), r.get("code_commune"), r.get("nom_commune"),
        r.get("code_departement") or dep, _f(r.get("latitude")), _f(r.get("longitude")),
        int(_f(r.get("nombre_lots")) or 0), carrez,
    )


def parse_dvf_stream(fh, dep):
    """Lecture en flux (mémoire faible) : les lignes d'une même mutation se suivent dans les fichiers.
    Ne garde que les ventes portant sur un seul local (appartement, maison ou local commercial)
    pour que le prix corresponde bien à ce local."""
    out, cur_id, cur, done, split = [], None, [], set(), set()

    def flush():
        if cur_id is None:
            return
        if cur_id in done:
            split.add(cur_id)
            return
        done.add(cur_id)
        row = _mutation_row(cur, dep)
        if row:
            out.append(row)

    for r in csv.DictReader(fh):
        mid = r.get("id_mutation")
        if not mid:
            continue
        if mid != cur_id:
            flush()
            cur_id, cur = mid, []
        cur.append(r)
    flush()
    if split:  # mutation non contiguë (rare) : écartée par prudence
        out = [r for r in out if r[0] not in split]
    return out


def parse_dvf_csv(text, dep):
    return parse_dvf_stream(io.StringIO(text), dep)


def dvf_years(n=5):
    y = date.today().year
    return [str(v) for v in range(y - n, y + 1)]


def load_dvf_department(dep, years=None, log=print):
    """Télécharge (une seule fois par année) les ventes DVF d'un département. Renvoie un état."""
    years = years or dvf_years()
    loaded, errors = [], []
    for year in years:
        if db.q("SELECT 1 FROM dvf_files WHERE dep=? AND year=?", (dep, year)):
            loaded.append(year)
            continue
        url = DVF_URL.format(year=year, dep=dep)
        try:
            status, body, _ = raw_get(url, timeout=120, accept="*/*")
        except FetchError as e:
            errors.append(f"{year}: {e}")
            continue
        if status == 404:
            errors.append(f"{year}: fichier non publié (404)")
            continue
        if status != 200:
            errors.append(f"{year}: HTTP {status}")
            continue
        try:
            raw = gzip.GzipFile(fileobj=io.BytesIO(body)) if body[:2] == b"\x1f\x8b" else io.BytesIO(body)
            rows = parse_dvf_stream(io.TextIOWrapper(raw, encoding="utf-8"), dep)
        except Exception as e:
            errors.append(f"{year}: fichier illisible ({e})")
            continue
        del body
        db.x("DELETE FROM dvf WHERE dep=? AND substr(date,1,4)=?", (dep, year))
        db.many("INSERT INTO dvf VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        db.x("INSERT OR REPLACE INTO dvf_files VALUES (?,?,?,?,?)", (dep, year, len(rows), db.now_iso(), url))
        log(f"DVF {dep} {year} : {len(rows)} ventes mono-local chargées")
        loaded.append(year)
    return {"dep": dep, "loaded": loaded, "errors": errors}


def import_dvf_file(path, dep=None):
    """Import manuel d'un fichier geo-dvf (csv ou csv.gz) téléchargé à la main."""
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        rows = parse_dvf_stream(fh, dep)
    by_key = defaultdict(int)
    for r in rows:
        by_key[(r[12], (r[1] or "")[:4])] += 1
    for (d, y), n in by_key.items():
        db.x("DELETE FROM dvf WHERE dep=? AND substr(date,1,4)=?", (d, y))
    db.many("INSERT INTO dvf VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    for (d, y), n in by_key.items():
        db.x("INSERT OR REPLACE INTO dvf_files VALUES (?,?,?,?,?)", (d, y, n, db.now_iso(), "import manuel " + path))
    return len(rows)


def dvf_status():
    rows = db.q("SELECT dep, group_concat(year) years, sum(rows) n, max(loaded_at) at FROM dvf_files GROUP BY dep ORDER BY dep")
    last = db.q("SELECT max(date) d FROM dvf")
    return {"departements": [dict(r) for r in rows], "derniere_vente": last[0]["d"] if last else None}


# ---------------------------------------------------------------- Géocodage

def _geo_req(bases, params):
    cache_key = json.dumps([bases[0], params], sort_keys=True)
    hit = db.q("SELECT result FROM geocache WHERE q=?", (cache_key,))
    if hit:
        return json.loads(hit[0]["result"])
    last_err = None
    for base in bases:
        try:
            status, body, _ = raw_get(base + urllib.parse.urlencode(params), timeout=15, accept="application/json")
        except FetchError as e:
            last_err = str(e)
            continue
        if status != 200:
            last_err = f"HTTP {status}"
            continue
        try:
            feats = json.loads(body).get("features") or []
        except ValueError:
            last_err = "réponse illisible"
            continue
        res = None
        if feats:
            p = feats[0]["properties"]
            lon, lat = feats[0]["geometry"]["coordinates"]
            res = {"lat": lat, "lon": lon, "score": p.get("score"), "type": p.get("type"),
                   "citycode": p.get("citycode"), "postcode": p.get("postcode"), "city": p.get("city"),
                   "housenumber": p.get("housenumber"), "street": p.get("street") or p.get("name"),
                   "label": p.get("label"), "service": base.split("/")[2]}
        db.x("INSERT OR REPLACE INTO geocache VALUES (?,?,?)", (cache_key, json.dumps(res), db.now_iso()))
        return res
    raise FetchError(f"géocodage indisponible : {last_err}")


def geocode(address, postcode=None, citycode=None):
    params = {"q": address[:200], "limit": 1}
    if postcode:
        params["postcode"] = postcode
    if citycode:
        params["citycode"] = citycode
    return _geo_req(GEOCODERS, params)


def reverse(lat, lon):
    return _geo_req(REVERSE, {"lat": round(lat, 6), "lon": round(lon, 6), "limit": 1})


# ---------------------------------------------------------------- Loyers

def parse_loyers_csv(text, kind):
    first = text.split("\n", 1)[0]
    sep = ";" if first.count(";") >= first.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=sep)
    cols = {c.lower().strip().strip('"'): c for c in (reader.fieldnames or [])}

    def find(*cands):
        for c in cands:
            for low, orig in cols.items():
                if low == c or low.startswith(c):
                    return orig
        return None

    c_code = find("insee_c", "insee", "code_commune", "codgeo")
    c_loy = find("loypredm2")
    c_lwr = find("lwr.ipm2", "lwr")
    c_upr = find("upr.ipm2", "upr")
    c_nb = find("nbobs_com", "nbobs")
    c_lib = find("libgeo", "nom")
    if not (c_code and c_loy):
        raise ValueError(f"colonnes attendues absentes (trouvées : {list(cols)[:12]})")
    out = []
    for r in reader:
        code = (r.get(c_code) or "").strip().zfill(5)
        v = _f(r.get(c_loy))
        if not v:
            continue
        out.append((code, kind, v, _f(r.get(c_lwr)) if c_lwr else None, _f(r.get(c_upr)) if c_upr else None,
                    int(_f(r.get(c_nb)) or 0) if c_nb else None, r.get(c_lib) if c_lib else None))
    return out


def load_loyers(log=print):
    status = {}
    for kind, url in LOYERS_URLS.items():
        if db.q("SELECT 1 FROM loyers WHERE kind=? LIMIT 1", (kind,)):
            status[kind] = "déjà chargé"
            continue
        try:
            code, body, _ = raw_get(url, timeout=60, accept="text/csv,*/*")
            if code != 200:
                status[kind] = f"HTTP {code}"
                continue
            try:
                text = body.decode("utf-8")
            except UnicodeDecodeError:
                text = body.decode("latin-1")
            rows = parse_loyers_csv(text, kind)
            db.many("INSERT OR REPLACE INTO loyers VALUES (?,?,?,?,?,?,?)", rows)
            status[kind] = f"{len(rows)} communes"
            log(f"Loyers {kind} : {len(rows)} communes")
        except Exception as e:
            status[kind] = f"erreur : {e}"
    return status


def rent_indicator(code_commune, prop_type, rooms, surface):
    if not code_commune:
        return None
    if prop_type == "maison":
        kinds = ["mai"]
    elif (rooms and rooms <= 2) or (not rooms and surface and surface < 45) or prop_type == "studio":
        kinds = ["app12", "app"]
    elif rooms and rooms >= 3:
        kinds = ["app3", "app"]
    else:
        kinds = ["app"]
    codes = [code_commune]
    if re.fullmatch(r"751\d\d", code_commune):
        codes.append("75056")
    if re.fullmatch(r"6938\d", code_commune):
        codes.append("69123")
    for c in codes:
        for k in kinds:
            r = db.q("SELECT * FROM loyers WHERE code_commune=? AND kind=?", (c, k))
            if r:
                d = dict(r[0])
                d["source"] = LOYERS_SOURCE
                return d
    return None
