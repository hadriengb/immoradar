"""Stream Estate (ex-Melo / Fluximmo) — agrégateur sous licence des annonces classiques (Leboncoin, SeLoger, PAP,
Bien'ici, Logic-Immo, Figaro Immo… « 3 000+ sources »), via son API officielle et VOTRE clé API.

Constat du 26/11/2026 (docs v2 : next.docs.stream.estate) : POST https://api-v2.stream.estate/properties,
en-tête X-API-KEY, critères { property: { transaction: { type: "SELL" }, type: { in: [...] },
pricing: { displayed: { lte } }, locations: { countryCode: "FR", in: { uniqueCodes: [INSEE] } } } },
pagination page/size (100 max). Facturation au résultat renvoyé (0,01 €/annonce sans abonnement, ou forfait
dès 99 €/mois). Melo renvoie désormais vers Stream Estate.
Certains chemins de la réponse (ville, code postal, coordonnées) ne sont pas détaillés dans la documentation
publique : la lecture essaie plusieurs noms et laisse « inconnu » ce qu'elle ne trouve pas. Rien n'est inventé.
"""
import json
import urllib.error
import urllib.request

from .. import config, db
from ..geo_fr import dep_from_postcode
from ..net import USER_AGENT, FetchError

NAME = "streamestate"
LABEL = "Stream Estate (API : Leboncoin, SeLoger, PAP, Bien'ici…)"
HOME = "https://stream.estate"
MODE = "auto"
API = "https://api-v2.stream.estate"
LEGAL = ("API officielle d'un agrégateur sous licence, avec votre clé (payant : 0,01 € par annonce reçue, ou forfait). "
         "Limite d'annonces par recherche réglable pour maîtriser le coût.")
TYPES = {"FLAT": "appartement", "HOUSE": "maison", "BUILDING": "immeuble", "PREMISES": "local_commercial",
         "COMMERCIAL": "local_commercial", "PARKING": "parking", "LAND": "terrain"}
LAST_INFO = None


class NotConfigured(Exception):
    pass


def manual_links(deps):
    return [{"label": "Stream Estate : créer un compte et une clé API", "url": "https://console.stream.estate/signup"},
            {"label": "Tarifs", "url": "https://stream.estate/pricing"}]


def settings(params=None):
    stored = (params or {}).get(NAME) if params else (db.get_setting("connecteurs", {}) or {}).get(NAME)
    return {**{"cle_api": "", "communes_insee": ["75056", "69123"], "max_annonces": 200, "prix_max": None},
            **(stored or {})}


def _get(obj, *paths):
    """Première valeur trouvée parmi plusieurs chemins « a.b.c » (la doc publique ne fixe pas tout)."""
    for path in paths:
        cur = obj
        for k in path.split("."):
            if isinstance(cur, list):
                cur = cur[0] if cur else None
            cur = cur.get(k) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, "", []):
            return cur
    return None


def _num(v):
    if isinstance(v, dict):
        v = v.get("value") or v.get("amount") or v.get("displayed")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def to_item(p):
    listings = p.get("listings") or []
    first = listings[0] if listings else {}
    pc = _get(p, "location.postalCode", "locations.postalCode", "address.postalCode", "location.zipcode", "zipcode", "postalCode",
              "location.city.postalCodes", "locations.city.postalCodes")
    if isinstance(pc, list):
        pc = pc[0] if pc else None
    city = _get(p, "location.city.name", "locations.city.name", "location.cityName", "address.city", "city.name", "city")
    if isinstance(city, dict):
        city = city.get("name")
    price = _num(_get(p, "pricing.displayed", "pricing.price", "price"))
    url = first.get("url") or _get(p, "url")
    portal = _get(first, "source.slug", "source.name") or "stream.estate"
    d = {
        "source": NAME, "source_id": str(p.get("id")), "url": url,
        "title": _get(p, "body.title", "title") or first.get("title") or "Annonce",
        "description": (_get(p, "body.description", "description") or first.get("description") or "")[:2000],
        "type": TYPES.get(str(_get(p, "propertyType", "type") or "").upper()),
        "surface": _num(_get(p, "area.displayed", "areas.displayed", "surface")),
        "rooms": int(_num(_get(p, "unit.rooms", "rooms")) or 0) or None,
        "mise_a_prix": price, "sale_kind": "amiable",
        "postcode": str(pc) if pc else None, "city": city,
        "lat": _num(_get(p, "location.coordinates.latitude", "location.latitude", "geo.latitude", "latitude")),
        "lon": _num(_get(p, "location.coordinates.longitude", "location.longitude", "geo.longitude", "longitude")),
        "photos": [x.get("url") for x in (p.get("pictures") or []) if isinstance(x, dict) and x.get("url")][:6],
        "documents": [], "portail": portal,
        "published": (first.get("publishedAt") or p.get("createdAt") or "")[:10] or None,
    }
    if d["lat"] is not None:
        d["geo_precision"] = "coordonnées source"
    if d["postcode"]:
        d["dep"] = dep_from_postcode(d["postcode"])
    return d


def search(s, crit=None, log=print, opener=urllib.request.urlopen):
    if not s.get("cle_api"):
        raise NotConfigured("clé API non renseignée (Paramètres > Stream Estate)")
    prop = {"transaction": {"type": "SELL"}, "type": {"in": ["FLAT", "HOUSE"]},
            "locations": {"countryCode": "FR", "in": {"uniqueCodes": list(s["communes_insee"])}}}
    pmax = s.get("prix_max") or (crit or {}).get("mise_a_prix_max")
    if pmax:
        prop["pricing"] = {"displayed": {"lte": float(pmax)}}
    out, page, cap = [], 1, int(s.get("max_annonces") or 200)
    while len(out) < cap:
        size = min(100, cap - len(out))
        body = json.dumps({"criteria": {"property": prop}, "paginationType": "PAGE", "page": page, "size": size}).encode()
        req = urllib.request.Request(API + "/properties", data=body, method="POST", headers={
            "X-API-KEY": s["cle_api"], "Content-Type": "application/json", "Accept": "application/json", "User-Agent": USER_AGENT})
        try:
            with opener(req, timeout=60) as r:
                res = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            msg = {401: "clé API refusée", 403: "clé API refusée", 402: "crédit épuisé sur votre compte Stream Estate",
                   429: "trop de requêtes, réessayer plus tard"}.get(e.code, f"HTTP {e.code}")
            raise FetchError(f"Stream Estate : {msg}") from e
        except Exception as e:
            raise FetchError(f"Stream Estate injoignable : {e}") from e
        data = res.get("data") or []
        for p in data:
            if isinstance(p, dict) and "attributes" in p:      # format JSON:API : on aplatit
                p = {"id": p.get("id"), **p["attributes"]}
            out.append(to_item(p))
        if not data or not (res.get("meta") or {}).get("hasNextPage"):
            break
        page += 1
    log(f"Stream Estate : {len(out)} annonce(s) reçue(s) (limite {cap} par recherche)")
    return out


def collect(deps, known, log):
    global LAST_INFO
    LAST_INFO = None
    s = settings()
    items = search(s, config.load().get("criteres"), log)
    kept = [d for d in items if not d.get("dep") or d["dep"] in deps]
    if len(kept) < len(items):
        LAST_INFO = f"{len(items)} annonce(s) reçue(s), {len(items) - len(kept)} hors de vos départements ignorée(s)"
    return kept
