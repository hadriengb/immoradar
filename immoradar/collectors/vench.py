"""Vench — annonces de ventes aux enchères judiciaires (tous tribunaux).

Constat du 26/11/2026 : robots.txt = « User-agent: * / Disallow: /upload/* » ; les pages par département
(/ventes-aux-encheres-par-departement-{Département}.html) et les fiches (/vente-{id}-{slug}.html) sont
autorisées et lisibles sans JavaScript. Les mentions légales ne contiennent pas de clause interdisant la
collecte. Certaines descriptions sont réservées aux abonnés : ImmoRadar ne lit que ce qui est public,
et les emails masqués par le site ne sont pas décodés.
"""
import re
import urllib.parse

from .. import contacts, extract
from ..geo_fr import DEPARTEMENTS, dep_from_postcode, dep_from_text
from ..net import PoliteClient

NAME = "vench"
LABEL = "Vench"
HOME = "https://www.vench.fr"
MODE = "auto"
LEGAL = ("robots.txt autorise les pages de vente (seul /upload/ est exclu) ; mentions légales sans clause interdisant "
         "la collecte. Descriptions réservées aux abonnés non lues ; emails masqués non décodés.")
VENTE_RX = re.compile(r"(?:https?://(?:www\.)?vench\.fr)?/vente-(\d+)-([a-z0-9-]+)\.html", re.I)
MAX_PAGES_DEP = 4
MAX_DETAILS = 60


def dep_url(dep):
    return f"{HOME}/" + urllib.parse.quote(f"ventes-aux-encheres-par-departement-{DEPARTEMENTS[dep]}.html", safe="-'.")


def manual_links(deps):
    return [{"label": f"Vench — {DEPARTEMENTS[d]}", "url": dep_url(d)} for d in deps if d in DEPARTEMENTS]


def list_department(client, dep, log):
    found = {}
    for page in range(1, MAX_PAGES_DEP + 1):
        html = client.get(dep_url(dep) + (f"?p={page}" if page > 1 else ""))
        before = len(found)
        for m in VENTE_RX.finditer(html):
            found.setdefault(m.group(1), {"path": f"/vente-{m.group(1)}-{m.group(2)}.html", "slug": m.group(2), "dep": dep})
        if len(found) == before or f"?p={page + 1}" not in html:
            break
    log(f"Vench {DEPARTEMENTS[dep]} : {len(found)} annonce(s) listée(s)")
    return found


def _after(label, text, n=120):
    m = re.search(label + r"\s*:?\s*\n?\s*([^\n]{1,%d})" % n, text, re.I)
    return m.group(1).strip() if m else None


def parse_detail(html, url, hint=None):
    text, links, images, meta = extract.parse_html(html)
    hint = hint or {}
    d = extract.analyse_text(text)
    m = VENTE_RX.search(url)
    title = extract.cleanup(meta.get("og:title") or "") or (text.split("\n")[0] if text else "")
    d.update({"source": NAME, "source_id": m.group(1) if m else url, "url": url, "title": title})
    # adresse et ville : ligne « 91350 Grigny » et la ligne qui la précède
    lines = text.split("\n")
    for i, ln in enumerate(lines[:200]):
        cp = re.fullmatch(r"\s*(\d{2}\s?\d{3})\s+([A-ZÉÈÀ][\w'’ -]{1,50})\s*", ln)
        if cp:
            d["postcode"] = cp.group(1).replace(" ", "")
            d["city"] = cp.group(2).strip()
            if i and re.search(r"\d|rue|avenue|bd|boulevard|place|square|chemin|all[ée]e|impasse|quai|route", lines[i - 1], re.I):
                d["address"] = extract.cleanup(lines[i - 1])
            break
    d["dep"] = dep_from_postcode(d.get("postcode")) if d.get("postcode") else hint.get("dep")
    court = re.search(r"Tribunal judiciaire d[e'’]\s?([^\n,.]{2,60})", text, re.I)
    if court:
        d["sale_place"] = "Tribunal judiciaire de " + court.group(1).strip().title()
    d["sale_kind"] = "judiciaire"
    aud = re.search(r"Date de l['’]audience\s*:?\s*\n?\s*(\d{2})/(\d{2})/(\d{4})(?:\s*à\s*(\d{1,2})[:h](\d{2}))?", text, re.I)
    if aud:
        d["sale_date"] = f"{aud.group(3)}-{aud.group(2)}-{aud.group(1)}"
        if aud.group(4):
            d["sale_time"] = f"{int(aud.group(4)):02d}:{aud.group(5)}"
    vis = re.search(r"Date\(s\) de visite\s*:?\s*\n?\s*([^\n]{4,80})", text, re.I)
    if vis and re.search(r"\d", vis.group(1)):
        d["visit"] = "Visite : " + vis.group(1).strip()
    occ = re.search(r"\bBien (occup[ée]|libre|lou[ée])\b", text, re.I)
    if occ:
        d["occupation"] = "libre" if occ.group(1).lower() == "libre" else "occupé"
    if re.search(r"r[ée]serv[ée]e?s? aux abonn[ée]s|devez [êe]tre abonn[ée]", text, re.I):
        d["note_source"] = "Une partie de la fiche est réservée aux abonnés Vench (non lue)."
    d.update(contacts.extract(text, links, NAME))
    if d["contacts"]:
        d["lawyer"] = " ; ".join(c["name"] + (f" ({c['firm']})" if c.get("firm") else "") for c in d["contacts"])
    start = text.find("Description")
    d["description"] = text[start:start + 1500] if start >= 0 else text[:1500]
    photos = [urllib.parse.urljoin(url, meta["og:image"])] if meta.get("og:image") else []
    photos += [urllib.parse.urljoin(url, im["src"]) for im in images if "/vignettes/" in im["src"]]
    d["photos"] = [p for p in dict.fromkeys(photos) if "/Design/Img/replace/" not in p][:6]
    d["documents"] = [{"label": (l["text"] or "Document").replace(".pdf", ""), "url": urllib.parse.urljoin(url, l["href"])}
                      for l in links if l["href"].lower().split("?")[0].endswith(".pdf")][:10]
    if not d.get("dep"):
        d["dep"] = dep_from_text(d.get("sale_place") or "")
    return d


def collect(deps, known, log):
    deps = [d for d in deps if d in DEPARTEMENTS]
    client = PoliteClient(HOME, min_delay=3.0, max_requests=len(deps) * MAX_PAGES_DEP + MAX_DETAILS + 5)
    listed = {}
    for dep in deps:
        listed.update(list_department(client, dep, log))
    order = sorted(listed, key=lambda s: (s in known, known.get(s) or ""))
    out, errors = [], []
    for sid in order[:MAX_DETAILS]:
        url = HOME + listed[sid]["path"]
        try:
            out.append(parse_detail(client.get(url), url, listed[sid]))
        except Exception as e:
            errors.append(f"{url} : {e}")
            if "protection" in str(e).lower():
                raise
    if len(order) > MAX_DETAILS:
        log(f"Vench : {len(order) - MAX_DETAILS} annonces reportées à la prochaine recherche (limite de {MAX_DETAILS} pages)")
    for e in errors[:5]:
        log("Vench erreur : " + e)
    return out
