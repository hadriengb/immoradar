"""Licitor — annonces de ventes judiciaires (et quelques ventes notariales).

Constat du 26/11/2026 : robots.txt n'interdit pas /annonce/ ni /ventes-aux-encheres-immobilieres/
(seul /data/pub/ est restreint, hors médias d'annonces). Aucune clause des pages « Responsabilité »
et « Droits d'auteur » n'interdit la consultation automatisée. Pas de CAPTCHA constaté.
La collecte reste lente (≥ 3 s entre deux pages) et robots.txt est relu à chaque recherche.
"""
import re
import urllib.parse

from .. import contacts, extract
from ..geo_fr import ZONES, dep_from_slug, dep_from_text
from ..net import FetchError, PoliteClient, ProtectionDetected, RobotsDisallowed

NAME = "licitor"
LABEL = "Licitor"
HOME = "https://www.licitor.com"
MODE = "auto"
LEGAL = ("robots.txt autorise les pages d'annonces ; pas de clause interdisant la collecte dans les mentions "
         "du site ; pas de CAPTCHA constaté. Collecte lente, usage personnel.")

LISTINGS = {
    "idf": "/ventes-aux-encheres-immobilieres/paris-et-ile-de-france/prochaines-ventes.html",
    "lyon": "/ventes-aux-encheres-immobilieres/rhone-alpes.html",
}
ANNONCE_RX = re.compile(r"/annonce/\d+/\d+/\d+/vente-aux-encheres/([^/\"'?#]+)/([^/\"'?#]+)/([^/\"'?#]+)/(\d+)\.html")
MAX_PAGES = 8
MAX_DETAILS = 70
LAST_WARNING = None


def manual_links(deps):
    return [{"label": "Licitor — Île-de-France", "url": HOME + LISTINGS["idf"]},
            {"label": "Licitor — Rhône-Alpes", "url": HOME + LISTINGS["lyon"]}]


def _pretty(slug):
    s = slug.replace("-", " ")
    return re.sub(r"\b(\w)", lambda m: m.group(1).upper(), s)


def list_announcements(client, zone, log):
    found, seen_pages = {}, 0
    for page in range(1, MAX_PAGES + 1):
        url = LISTINGS[zone] + (f"?p={page}" if page > 1 else "")
        try:
            html = client.get(url)
        except FetchError as e:
            if page == 1 or isinstance(e, (ProtectionDetected, RobotsDisallowed)):
                raise
            log(f"Licitor {zone} : page {page} illisible ({e}), on garde les {len(found)} annonces déjà listées")
            break
        seen_pages += 1
        before = len(found)
        for m in ANNONCE_RX.finditer(html):
            sid = m.group(4)
            if sid not in found:
                found[sid] = {"path": m.group(0), "type_slug": m.group(1), "city_slug": m.group(2), "dep_slug": m.group(3)}
        if len(found) == before:
            break
    log(f"Licitor {zone} : {len(found)} annonces listées sur {seen_pages} page(s)")
    return found


def parse_detail(html, url, meta_hint=None):
    text, links, images, meta = extract.parse_html(html)
    data = extract.analyse_text(text)
    hint = meta_hint or {}
    m = ANNONCE_RX.search(url)
    if m:
        hint = {"type_slug": m.group(1), "city_slug": m.group(2), "dep_slug": m.group(3), **hint}
    title = extract.cleanup(meta.get("og:title") or "")
    d = {
        "source": NAME, "source_id": m.group(4) if m else url, "url": url,
        "title": title or _pretty(hint.get("type_slug", "")),
        "city": _pretty(hint.get("city_slug", "")) or None,
        "dep": dep_from_slug(hint.get("dep_slug", "")),
        **data,
    }
    if not d["type"]:
        d["type"] = extract.property_type(hint.get("type_slug", "").replace("-", " "))
    court = re.search(r"Tribunal (?:Judiciaire|de Grande Instance) d[e'’]\s?[^\n(]{2,60}(?:\([^)]*\))?", text)
    chambre = re.search(r"Chambre (?:interd[ée]partementale )?des [Nn]otaires[^\n]{0,80}", text)
    d["sale_place"] = (court or chambre).group(0).strip() if (court or chambre) else None
    d["sale_kind"] = "notariale" if (chambre and not court) or re.search(r"\bnotaire\b", (d["sale_place"] or ""), re.I) else "judiciaire"
    # date de vente : la phrase qui suit le tribunal
    if court:
        sd, st = extract.parse_fr_date(text[court.end():court.end() + 300])
        if sd:
            d["sale_date"], d["sale_time"] = sd, st
    pub = re.search(r"publi[ée]e le ([^\n]{6,40})", text)
    if pub:
        d["published"] = extract.parse_fr_date(pub.group(1))[0]
    gm = re.search(r"maps\.google\.[a-z.]+/maps\?q=(-?\d+\.\d+),(-?\d+\.\d+)", html)
    if gm:
        d["lat"], d["lon"], d["geo_precision"] = float(gm.group(1)), float(gm.group(2)), "coordonnées source"
    addr = re.search(r"^\s*(\d+[\w ,'’-]{0,10}\s*)?(?:bis |ter )?(rue|avenue|av\.|boulevard|bd|place|all[ée]e|chemin|impasse|quai|cours|route|square|villa|passage|lieu[- ]?dit|lieudit|r[ée]sidence|cit[ée]|hameau)\b[^\n]{2,80}$", text, re.I | re.M)
    if addr:
        d["address"] = extract.cleanup(addr.group(0))
    d.update(contacts.extract(text, links, NAME))
    if d["contacts"]:
        d["lawyer"] = " ; ".join(c["name"] + (f" ({c['firm']})" if c.get("firm") else "") for c in d["contacts"])
    vis = re.search(r"Visite[^\n]{5,120}", text)
    if vis:
        d["visit"] = vis.group(0).strip()
    if not d.get("dep"):
        d["dep"] = dep_from_text(d.get("sale_place") or "")
    # description : bloc entre le titre et l'avocat
    start = text.find("Mise à prix")
    d["description"] = text[max(0, start - 1500):start + 200].strip()[-1800:] if start > 0 else text[:1500]
    photos = []
    if meta.get("og:image"):
        photos.append(urllib.parse.urljoin(url, meta["og:image"]))
    for im in images:
        if "/media/annonce/" in im["src"] or "/data/pub/pic/" in im["src"]:
            photos.append(urllib.parse.urljoin(url, im["src"]))
    d["photos"] = list(dict.fromkeys(photos))[:6]
    d["documents"] = [{"label": l["text"] or "Document", "url": urllib.parse.urljoin(url, l["href"])}
                      for l in links if l["href"].lower().endswith(".pdf")][:10]
    return d


def collect(deps, known, log):
    """Collecte tolérante aux coupures : une zone ou une fiche qui échoue n'annule pas le reste.
    Après 3 échecs réseau consécutifs, on s'arrête (le site est peut-être surchargé) et on garde ce qui a été lu."""
    global LAST_WARNING
    LAST_WARNING = None
    client = PoliteClient(HOME, min_delay=3.0, max_requests=MAX_PAGES * 2 + MAX_DETAILS + 5)
    zones = [z for z, cfg in ZONES.items() if set(cfg["deps"]) & set(deps)]
    listed, zone_errors = {}, []
    for z in zones:
        try:
            listed.update(list_announcements(client, z, log))
        except FetchError as e:
            if "protection" in str(e).lower() or isinstance(e, (ProtectionDetected, RobotsDisallowed)):
                raise
            zone_errors.append(f"{z} : {e}")
            log(f"Licitor {z} : liste illisible ({e})")
    if not listed and zone_errors:
        raise FetchError("; ".join(zone_errors))
    wanted = {sid: h for sid, h in listed.items() if (dep_from_slug(h["dep_slug"]) in deps)}
    log(f"Licitor : {len(wanted)} annonces dans les départements choisis (sur {len(listed)})")
    # nouvelles d'abord, puis les plus anciennement actualisées
    order = sorted(wanted, key=lambda s: (s in known, known.get(s) or ""))
    out, errors, consecutive = [], [], 0
    for sid in order[:MAX_DETAILS]:
        h = wanted[sid]
        url = HOME + h["path"]
        try:
            out.append(parse_detail(client.get(url), url, h))
            consecutive = 0
        except (ProtectionDetected, RobotsDisallowed):
            raise
        except Exception as e:
            errors.append(f"{url} : {e}")
            consecutive += 1
            if consecutive >= 3:
                log("Licitor : 3 coupures de suite, arrêt pour ne pas insister ; la suite sera lue à la prochaine recherche")
                break
    if len(order) > MAX_DETAILS:
        log(f"Licitor : {len(order) - MAX_DETAILS} annonces reportées à la prochaine recherche (limite de {MAX_DETAILS} pages par recherche)")
    for e in errors[:5]:
        log("Licitor erreur : " + e)
    if errors or zone_errors or client.retries:
        LAST_WARNING = (f"{len(out)} fiche(s) lue(s) ; {len(errors) + len(zone_errors)} page(s) en échec"
                        + (f", {client.retries} nouvel(s) essai(s) après coupure" if client.retries else "")
                        + ". Les annonces déjà enregistrées restent affichées.")
    return out
