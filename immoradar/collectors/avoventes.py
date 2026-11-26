"""Avoventes (site des avocats, ventes judiciaires).

Constat du 26/11/2026 : robots.txt = « User-agent: * / Disallow: » (tout est autorisé). Aucune CGU
interdisant la collecte n'a été trouvée. Pas de CAPTCHA constaté sur la page d'accueil.
La structure HTML des pages de vente n'a PAS pu être vérifiée depuis l'environnement de développement :
ce collecteur est donc générique (il suit les liens internes et ne garde que les pages contenant une
« mise à prix »). S'il ne reconnaît aucune annonce, il le dit : il n'invente rien.
"""
import re
import urllib.parse

from .. import contacts, extract
from ..geo_fr import dep_from_text
from ..net import PoliteClient

NAME = "avoventes"
LABEL = "Avoventes"
HOME = "https://avoventes.fr"
MODE = "auto"
LEGAL = ("robots.txt autorise tout ; pas de CGU restrictive trouvée. Collecteur générique expérimental : "
         "la structure des pages n'a pas encore été validée sur le site réel.")
MAX_LIST_PAGES = 6
MAX_DETAILS = 60
SKIP = re.compile(r"(connexion|login|compte|annuaire|cabinet|mediateur|comment-acheter|contact|mentions|cgu|"
                  r"confidentialit|cookie|institutionnel|consulter|amiable|passees|\.pdf$|\.jpg$|\.png$|mailto:|tel:)", re.I)


def manual_links(deps):
    return [{"label": "Avoventes — ventes aux enchères", "url": HOME + "/"}]


def _internal(href, base):
    u = urllib.parse.urljoin(base, href)
    p = urllib.parse.urlparse(u)
    if p.netloc and p.netloc.replace("www.", "") != "avoventes.fr":
        return None
    return urllib.parse.urlunparse(p._replace(fragment=""))


def collect(deps, known, log):
    client = PoliteClient(HOME, min_delay=3.0, max_requests=MAX_LIST_PAGES + MAX_DETAILS + 5)
    html = client.get(HOME + "/")
    _, links, _, _ = extract.parse_html(html)
    list_pages, candidates = [], []
    for l in links:
        u = _internal(l["href"], HOME + "/")
        if not u or SKIP.search(u):
            continue
        label = (l["text"] + " " + l["title"]).lower()
        if "enchère" in label or "enchere" in u.lower() or "page=" in u:
            list_pages.append(u)
        elif re.search(r"/(vente|annonce|bien|lot|detail)[^/]*/|[-/]\d{3,}", u, re.I):
            candidates.append(u)
    for u in list(dict.fromkeys(list_pages))[:MAX_LIST_PAGES]:
        try:
            h = client.get(u)
        except Exception as e:
            log(f"Avoventes : page de liste {u} : {e}")
            continue
        for l in extract.parse_html(h)[1]:
            v = _internal(l["href"], u)
            if v and not SKIP.search(v) and re.search(r"/(vente|annonce|bien|lot|detail)[^/]*/|[-/]\d{3,}", v, re.I):
                candidates.append(v)
    candidates = [c for c in dict.fromkeys(candidates) if c.rstrip("/") != HOME]
    log(f"Avoventes : {len(candidates)} liens candidats")
    out = []
    for u in candidates[:MAX_DETAILS]:
        try:
            h = client.get(u)
        except Exception as e:
            if "protection" in str(e).lower():
                raise
            continue
        text, links, images, meta = extract.parse_html(h)
        if not re.search(r"mise\s+[àa]\s+prix", text, re.I):
            continue
        d = extract.analyse_text(text)
        if not d["mise_a_prix"]:
            continue
        cp = re.search(r"\b(\d{5})\s+([A-ZÉÈ][\w' -]{2,40})", text)
        d.update({
            "source": NAME, "source_id": urllib.parse.urlparse(u).path, "url": u,
            "title": extract.cleanup(meta.get("og:title") or (text.split("\n")[0] if text else "")),
            "postcode": cp.group(1) if cp else None,
            "city": cp.group(2).strip() if cp else None,
            "dep": dep_from_text(text[:4000]),
            "sale_kind": "judiciaire",
            "description": text[:1800],
            "photos": [urllib.parse.urljoin(u, meta["og:image"])] if meta.get("og:image") else [],
            "documents": [{"label": l["text"] or "Document", "url": urllib.parse.urljoin(u, l["href"])}
                          for l in links if l["href"].lower().endswith(".pdf")][:10],
        })
        d.update(contacts.extract(text, links, NAME))
        if d["contacts"]:
            d["lawyer"] = " ; ".join(c["name"] for c in d["contacts"])
        court = re.search(r"Tribunal [Jj]udiciaire d[e'’]\s?[^\n(]{2,60}", text)
        d["sale_place"] = court.group(0) if court else None
        if d["dep"] in deps or d["dep"] is None:
            out.append(d)
    log(f"Avoventes : {len(out)} annonces reconnues")
    return out
