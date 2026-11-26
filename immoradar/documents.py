"""Analyse des documents de vente : PV descriptif, cahier des conditions de vente, diagnostics, copropriété.

Lecture du texte :
  1. pdftotext (poppler) si installé ;
  2. si le PDF est scanné (pas de texte) et que tesseract est installé : OCR en français ;
  3. sinon : statut « scanné, OCR indisponible » -> l'utilisateur peut coller le texte.
Chaque fait extrait garde : document, page, citation exacte. Rien n'est déduit hors du texte.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.robotparser  # noqa: F401

from . import contacts, db, extract
from .net import USER_AGENT, FetchError, PoliteClient, raw_get

MAX_PAGES_OCR = 40


# ------------------------------------------------------------------ lecture du PDF

MAC_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "macos_pdf.applescript")


def tools():
    return {"macos": sys.platform == "darwin" and bool(shutil.which("osascript")),
            "pdftotext": bool(shutil.which("pdftotext")), "pdftoppm": bool(shutil.which("pdftoppm")),
            "tesseract": bool(shutil.which("tesseract"))}


def reader_label():
    t = tools()
    if t["macos"]:
        return "macOS intégré (PDFKit + OCR Vision) : texte et scans lisibles"
    if t["pdftotext"] and t["tesseract"]:
        return "poppler + tesseract : texte et scans lisibles"
    if t["pdftotext"]:
        return "poppler : PDF numériques lisibles, scans non lisibles (OCR absent)"
    return "aucun lecteur PDF : collez le texte des documents"


def parse_mac_output(out):
    """Découpe la sortie du script macOS en pages ; remet les lignes OCR dans l'ordre de lecture."""
    pages, cur, mode = [], None, None
    for line in out.splitlines():
        m = re.match(r"<<<PAGE (\d+) (texte|ocr)>>>", line)
        if m:
            if cur is not None:
                pages.append((mode, cur))
            cur, mode = [], m.group(2)
            continue
        if line.startswith("<<<ERREUR"):
            continue
        if cur is not None:
            cur.append(line)
    if cur is not None:
        pages.append((mode, cur))
    texts, n_ocr = [], 0
    for mode, lines in pages:
        if mode == "ocr":
            n_ocr += 1
            rows = []
            for l in lines:
                parts = l.split("\t", 2)
                if len(parts) == 3 and parts[0].lstrip("-").isdigit() and parts[1].lstrip("-").isdigit():
                    rows.append((-int(parts[0]), int(parts[1]), parts[2]))
                else:
                    rows.append((0, 0, l))
            rows.sort(key=lambda r: (round(r[0] / 60), r[1]))  # même ligne visuelle : gauche -> droite
            texts.append("\n".join(r[2] for r in rows))
        else:
            texts.append("\n".join(lines))
    return texts, n_ocr


def mac_pdf_pages(path):
    r = subprocess.run(["osascript", MAC_SCRIPT, path], capture_output=True, timeout=900)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", "replace")[:300])
    return parse_mac_output(r.stdout.decode("utf-8", "replace"))


def pdf_pages(data):
    """Renvoie (pages:list[str], méthode, message)."""
    t = tools()
    if not data.startswith(b"%PDF"):
        try:
            return data.decode("utf-8").split("\f"), "texte", None
        except UnicodeDecodeError:
            return [], None, "Fichier non reconnu (ni PDF ni texte)."
    mac_error = None
    if t["macos"]:
        with tempfile.TemporaryDirectory() as d:
            pdf = os.path.join(d, "doc.pdf")
            with open(pdf, "wb") as fh:
                fh.write(data)
            try:
                pages, n_ocr = mac_pdf_pages(pdf)
                if sum(len(p.strip()) for p in pages) > 50:
                    return pages, "macOS " + (f"(OCR Vision sur {n_ocr} page(s))" if n_ocr else "(texte du PDF)"), None
                mac_error = "aucun texte reconnu"
            except Exception as e:
                mac_error = str(e)
    if not t["pdftotext"]:
        return [], None, (f"Lecture macOS impossible ({mac_error}). " if mac_error else "") + \
            "Aucun lecteur PDF disponible : collez le texte du document sur la fiche."
    with tempfile.TemporaryDirectory() as d:
        pdf = os.path.join(d, "doc.pdf")
        with open(pdf, "wb") as fh:
            fh.write(data)
        out = subprocess.run(["pdftotext", "-layout", pdf, "-"], capture_output=True, timeout=120)
        pages = out.stdout.decode("utf-8", "replace").split("\f")
        if pages and not pages[-1].strip():
            pages = pages[:-1]
        if sum(len(p.strip()) for p in pages) >= 150 * max(1, len(pages)) * 0.3:
            return pages, "texte du PDF", None
        if not (t["tesseract"] and t["pdftoppm"]):
            return pages, None, ("PDF scanné (image) : le texte n'est pas lisible sans OCR. Installez tesseract "
                                 "(avec le français) ou collez le texte sur la fiche.")
        subprocess.run(["pdftoppm", "-r", "200", "-gray", "-l", str(MAX_PAGES_OCR), "-png", pdf, os.path.join(d, "p")],
                       capture_output=True, timeout=600)
        imgs = sorted(f for f in os.listdir(d) if f.startswith("p") and f.endswith(".png"))
        ocr = []
        for f in imgs:
            r = subprocess.run(["tesseract", os.path.join(d, f), "-", "-l", "fra"], capture_output=True, timeout=180)
            if r.returncode != 0:
                r = subprocess.run(["tesseract", os.path.join(d, f), "-"], capture_output=True, timeout=180)
            ocr.append(r.stdout.decode("utf-8", "replace"))
        return ocr, f"OCR tesseract ({len(ocr)} pages)", None


# ------------------------------------------------------------------ analyse du texte

def _norm(s):
    """Espaces normalisés et retours à la ligne « de mise en page » recollés : dans un PDF, une phrase est coupée
    en fin de ligne (« libre de\ntoute occupation ») ; sans ce recollage, les recherches échoueraient."""
    s = re.sub(r"[ \t  ]+", " ", s)
    s = re.sub(r"(?<=[a-zà-ÿ])-\n(?=[a-zà-ÿ])", "", s)                      # césure : « occu-\npation »
    s = re.sub(r"(?<=[a-zà-ÿ0-9,;'’«(])[ ]?\n[ ]?(?!\s|\d+[.)] |[-•*] )", " ", s)
    return re.sub(r"[ \t  ]+", " ", s)


def _find(pages, rx, flags=re.I):
    """Premier match : (match, numéro de page 1-based, citation)."""
    for i, p in enumerate(pages):
        m = re.search(rx, p, flags)
        if m:
            a, b = max(0, m.start() - 80), min(len(p), m.end() + 80)
            quote = " ".join(p[a:b].split())
            return m, i + 1, quote
    return None, None, None


def doc_kind(text, label=""):
    t = (label + " " + text[:3000]).lower()
    if "proc" in t and "verbal" in t and "description" in t or "pv descriptif" in t:
        return "PV descriptif"
    if "cahier des conditions de vente" in t or "cahier des charges" in t:
        return "Cahier des conditions de vente"
    if "diagnostic" in t or "dpe" in t or "performance énergétique" in t:
        return "Diagnostics"
    if "assemblée générale" in t or "syndic" in t and "budget" in t:
        return "Documents de copropriété"
    return "Document"


def _money(s):
    m = re.search(extract.NUM + r"\s*(?:€|euros?|eur\b)", s, re.I)
    return extract.to_number(m.group(1), m.group(2)) if m else None


def analyse_pages(pages, label=""):
    pages = [_norm(p) for p in pages]
    full = "\n".join(pages)
    kind = doc_kind(full, label)
    facts, risks, warnings = [], [], []

    def fact(field, value, title, rx, pg=None, quote=None, apply=True):
        facts.append({"field": field, "value": value, "label": title, "page": pg, "quote": quote, "apply": apply})

    def risk(code, title, sev, pg, quote, scope="all"):
        risks.append({"code": code, "label": title, "severity": sev, "page": pg, "quote": quote, "scope": scope})

    # occupation
    m, pg, q = _find(pages, r"(?:vide et )?libre de toute occupation|inoccup[ée]|n['’]est pas occup")
    if m:
        fact("occupation", "libre", "Occupation", None, pg, q)
    else:
        m, pg, q = _find(pages, r"occup[ée]e? par|bail (?:d['’]habitation|commercial)|locataire|loyer mensuel")
        if m:
            fact("occupation", "occupé", "Occupation", None, pg, q)
            lm, lpg, lq = _find(pages, r"loyer (?:mensuel )?(?:de |d['’]un montant de )?" + extract.NUM + r"\s*(?:€|euros)")
            if lm:
                fact("loyer_en_place", extract.to_number(lm.group(1), lm.group(2)), "Loyer en place (mensuel)", None, lpg, lq)
        m2, pg2, q2 = _find(pages, r"sans droit ni titre|squat")
        if m2:
            risk("squat", "Occupation sans droit ni titre", 3, pg2, q2)

    # état / travaux
    defects = len(re.findall(r"cass[ée]e?s?|fissur[ée]e?s?|absente?s?\b|d[ée]croch[ée]e?s?|non fonctionnel|infiltration|humidit[ée]|moisissure|"
                             r"v[ée]tuste|d[ée]grad[ée]e?s?|hors d['’]usage|fuite", full, re.I))
    def classify(sentence):
        lv = extract.works_level(sentence)
        if lv is None and re.search(r"[ée]tat d['’]usage", sentence, re.I):
            lv = "aucun"
        return lv

    m, pg, q = _find(pages, r"(?:de mani[èe]re g[ée]n[ée]rale|dans (?:son|l['’])ensemble|globalement|d['’]une fa[çc]on g[ée]n[ée]rale)[^\n.]{0,100}")
    lvl = classify(m.group(0)) if m else None
    if lvl is None:
        m, pg, q = _find(pages, r"en (?:bon |mauvais )?[ée]tat d['’]usage|en bon [ée]tat|en mauvais [ée]tat|tr[èe]s d[ée]grad[ée]|[àa] r[ée]nover|[àa] r[ée]habiliter")
        lvl = classify(m.group(0)) if m else None
    if lvl is not None:
        order = ["aucun", "léger", "moyen", "lourd"]
        bump = 1 if defects >= 3 else 0
        bump += 1 if defects >= 12 else 0
        lvl = order[min(3, order.index(lvl) + bump)]
        fact("works_level", lvl, f"État général ({defects} défaut(s) relevé(s) dans le document)", None, pg, q)

    # surface
    m, pg, q = _find(pages, r"(?:superficie|surface)[^\n]{0,40}(?:loi )?carrez[^\n]{0,30}?" + extract.NUM + r"\s*m\s*[²2]")
    if m:
        fact("surface", extract.to_number(m.group(1), m.group(2)), "Surface Carrez (document)", None, pg, q)
    else:
        m, pg, q = _find(pages, r"(?:d['’]environ|environ|d['’]une superficie (?:de|d['’]environ))\s*" + extract.NUM + r"\s*m\s*[²2]")
        if m:
            fact("surface_document", extract.to_number(m.group(1), m.group(2)),
                 "Surface indiquée (approximative)", None, pg, q, apply=False)

    # lot unique / plusieurs lots / baisse de mise à prix
    m, pg, q = _find(pages, r"en un (?:seul )?lot(?: d['’]ench[èe]res| de vente)?")
    if m:
        fact("lots_count", None, "Vente en un seul lot", None, pg, q)
    m, pg, q = _find(pages, r"facult[ée] de baisse[^\n.]{0,80}")
    if m:
        fact("baisse_mise_a_prix", m.group(0).strip(), "Baisse possible de la mise à prix en cas de carence d'enchères", None, pg, q)
    m, pg, q = _find(pages, r"mise [àa] prix\s*(?:de|:)?\s*(?:[A-Z ]+\()?" + extract.NUM + r"\s*(?:€|euros)")
    if m:
        fact("mise_a_prix_document", extract.to_number(m.group(1), m.group(2)), "Mise à prix (document)", None, pg, q, apply=False)

    # frais préalables, charges, taxe foncière
    m, pg, q = _find(pages, r"frais pr[ée]alables[^\n]{0,80}?" + extract.NUM + r"\s*(?:€|euros)")
    if m:
        fact("frais_prealables", extract.to_number(m.group(1), m.group(2)), "Frais préalables (document)", None, pg, q)
    m, pg, q = _find(pages, r"charges (?:de copropri[ée]t[ée] )?(?:annuelles|courantes)?[^\n]{0,60}?" + extract.NUM + r"\s*(?:€|euros)[^\n]{0,20}(?:par an|annuel|/ ?an)")
    if m:
        fact("charges_copro_an", extract.to_number(m.group(1), m.group(2)), "Charges de copropriété annuelles", None, pg, q)
    m, pg, q = _find(pages, r"taxe fonci[èe]re[^\n]{0,50}?" + extract.NUM + r"\s*(?:€|euros)")
    if m:
        fact("taxe_fonciere_an", extract.to_number(m.group(1), m.group(2)), "Taxe foncière annuelle", None, pg, q)

    # DPE
    m, pg, q = _find(pages, r"(?:classe (?:[ée]nergie|DPE)|[ée]tiquette (?:[ée]nergie|DPE)|DPE)\s*:?\s*([A-G])\b", re.I)
    if m:
        cls = m.group(1).upper()
        fact("dpe", cls, "Classe énergie (DPE)", None, pg, q)
        if cls == "G":
            risk("dpe_g", "DPE G : location interdite comme résidence principale depuis 2025", 3, pg, q, scope="locatif")
        elif cls == "F":
            risk("dpe_f", "DPE F : location interdite à partir de 2028", 2, pg, q, scope="locatif")

    # risques juridiques et contextuels
    rules = [
        ("liquidation", "Vendeur en liquidation judiciaire (vente ordonnée par le juge-commissaire)", 1, r"liquidation judiciaire", "all"),
        ("nullite", "Antécédent de vente annulée en justice (historique de propriété à vérifier)", 2, r"nullit[ée] de la vente|annulation de la vente", "all"),
        ("residence_sociale", "Lot situé dans une résidence sociale : comparables DVF probablement inadaptés, revente et location à vérifier", 2, r"r[ée]sidence sociale|logements? sociaux", "all"),
        ("chambre", "Bien décrit comme une chambre (et non un appartement) : vérifier qu'il constitue un logement", 2, r"\bune chambre\b|\bchambre d['’]environ", "all"),
        ("hypotheque_ok", "Aucune inscription d'hypothèque signalée", 0, r"(?:aucune|pas d['’])\s*inscription d['’]hypoth[èe]que", "info"),
        ("purge", "L'adjudication purge les hypothèques", 0, r"purge des hypoth[èe]ques", "info"),
        ("surenchere", "Surenchère possible après l'adjudication", 0, r"surench[èe]re", "info"),
        ("amiante", "Amiante signalé", 1, r"pr[ée]sence d['’]amiante|mat[ée]riaux? contenant de l['’]amiante", "all"),
        ("plomb", "Plomb au-delà du seuil", 1, r"plomb[^\n]{0,40}(?:sup[ée]rieure?|au-del[àa]|positif)", "all"),
        ("termites", "Termites ou insectes xylophages", 2, r"termites|insectes xylophages[^\n]{0,30}pr[ée]sence", "all"),
        ("peril", "Arrêté de péril / insalubrité", 3, r"arr[êe]t[ée] de p[ée]ril|insalubrit[ée]|mise en s[ée]curit[ée]", "all"),
        ("copro_difficulte", "Copropriété en difficulté", 3, r"administrateur provisoire|plan de sauvegarde", "all"),
        ("usufruit", "Démembrement de propriété", 3, r"usufruit|nue[- ]propri[ée]t[ée]", "all"),
        ("servitude", "Servitude mentionnée", 1, r"servitude", "all"),
        ("balcon_partage", "Balcon partagé avec d'autres lots", 1, r"balcon[^\n.]{0,40}partag[ée]", "all"),
    ]
    for code, title, sev, rx, scope in rules:
        m, pg, q = _find(pages, rx)
        if m:
            risk(code, title, sev, pg, q, scope)

    # valeurs citées dans le document (affichées, jamais substituées à l'estimation)
    m, pg, q = _find(pages, r"prix moyen du m[èe]tre carr[ée][^\n]{0,40}?" + extract.NUM + r"\s*(?:€|euros)")
    if m:
        fact("prix_m2_document", extract.to_number(m.group(1), m.group(2)), "Prix au m² cité dans le document", None, pg, q, apply=False)
    for m_ in re.finditer(r"propos[ée]e? [àa] la vente [àa]\s*" + extract.NUM + r"\s*(?:€|euros)", full, re.I):
        _, pg, q = _find(pages, re.escape(m_.group(0)))
        fact("comparable_document", extract.to_number(m_.group(1), m_.group(2)), "Comparable cité dans le document", None, pg, q, apply=False)

    emails = {}
    for i, pg_txt in enumerate(pages):
        for e in contacts.EMAIL.findall(pg_txt):
            emails.setdefault(e.lower(), i + 1)
    return {"kind": kind, "facts": facts, "risks": risks, "warnings": warnings, "pages": len(pages),
            "chars": len(full), "emails": [{"email": e, "page": p} for e, p in list(emails.items())[:10]]}


# ------------------------------------------------------------------ orchestration

def fetch_document(url):
    """Téléchargement poli : robots.txt du site respecté."""
    p = urllib.parse.urlparse(url)
    client = PoliteClient(f"{p.scheme}://{p.netloc}", min_delay=1.0, max_requests=3)
    client._load_robots()
    if not client._robots.can_fetch(USER_AGENT, url):
        raise FetchError("robots.txt du site interdit le téléchargement automatique de ce document : "
                         "téléchargez-le vous-même puis déposez-le sur la fiche.")
    status, body, _ = raw_get(url, timeout=90, accept="application/pdf,*/*")
    if status != 200:
        raise FetchError(f"HTTP {status}")
    if b"<html" in body[:500].lower():
        raise FetchError("le lien renvoie une page web et non un document (connexion ou protection ?)")
    return body


def analyse_bytes(data, label, origin):
    pages, method, msg = pdf_pages(data)
    rec = {"label": label, "origin": origin, "analysed_at": db.now_iso(), "method": method}
    if not method:
        rec.update(status="illisible", message=msg, facts=[], risks=[])
        return rec
    res = analyse_pages(pages, label)
    rec.update(status="analysé", message=None, **res)
    if res["chars"] < 200:
        rec.update(status="illisible", message="Texte trop court après lecture : document probablement scanné de mauvaise qualité.")
    return rec


DOC_RX = re.compile(r"cahier|conditions de vente|proc[eè]s|pv|descriptif|diagnostic|dpe|copropri|r[eè]glement|assembl", re.I)


def analyse_listing_documents(data, only_new=True):
    """Analyse les documents liés à l'annonce. Modifie data['doc_analyses'] en place. Renvoie le nombre analysé."""
    done = {d.get("origin") for d in data.get("doc_analyses", []) if d.get("status") not in ("erreur",)}  # on réessaie les erreurs
    out = list(data.get("doc_analyses", []))
    n = 0
    for doc in data.get("documents") or []:
        url = doc.get("url")
        if not url or (only_new and url in done):
            continue
        label = doc.get("label") or "Document"
        try:
            rec = analyse_bytes(fetch_document(url), label, url)
        except FetchError as e:
            rec = {"label": label, "origin": url, "analysed_at": db.now_iso(), "status": "non téléchargé",
                   "message": str(e), "facts": [], "risks": []}
        except Exception as e:
            rec = {"label": label, "origin": url, "analysed_at": db.now_iso(), "status": "erreur",
                   "message": f"{type(e).__name__}: {e}", "facts": [], "risks": []}
        out = [d for d in out if d.get("origin") != url] + [rec]
        n += 1
    data["doc_analyses"] = out
    return n


APPLY_FIELDS = ("occupation", "works_level", "surface", "lots_count", "frais_prealables", "charges_copro_an",
                "taxe_fonciere_an", "dpe", "baisse_mise_a_prix", "loyer_en_place")


def apply_to_item(item):
    """Priorité : saisie manuelle > documents > annonce. Renvoie la liste des écarts détectés."""
    conflicts = []
    ov_fields = {k for k, v in (item.get("prov") or {}).items() if v == "saisie manuelle"}
    for d in item.get("doc_analyses") or []:
        if d.get("status") != "analysé":
            continue
        src = f"{d.get('kind') or d['label']}" + ""
        for f in d.get("facts", []):
            fld = f["field"]
            where = f"{src} p.{f['page']}" if f.get("page") else src
            if fld == "surface_document" and item.get("surface") and f["value"] and abs(f["value"] - item["surface"]) / item["surface"] > 0.2:
                conflicts.append(f"Surface : annonce {item['surface']:g} m², {where} indique environ {f['value']:g} m².")
            if fld == "mise_a_prix_document" and item.get("mise_a_prix") and f["value"] and abs(f["value"] - item["mise_a_prix"]) > 1:
                conflicts.append(f"Mise à prix : annonce {item['mise_a_prix']:,.0f} €, {where} : {f['value']:,.0f} €.".replace(",", " "))
            if not f.get("apply") or fld not in APPLY_FIELDS or fld in ov_fields:
                continue
            old = item.get(fld)
            if fld == "occupation" and old and old != f["value"]:
                conflicts.append(f"Occupation : annonce « {old} », {where} « {f['value']} » (le document prime).")
            item[fld] = f["value"]
            item.setdefault("prov", {})[fld] = where
        codes = {r["code"] for r in item.get("risks") or []}
        for r in d.get("risks", []):
            if r["code"] not in codes:
                item.setdefault("risks", []).append({**r, "label": r["label"], "source": src})
                codes.add(r["code"])
    if any(f["field"] == "lots_count" for d in item.get("doc_analyses") or [] for f in d.get("facts", [])):
        item["lots_count"] = None
    # risques « occupé » de l'annonce contredits par un document qui dit « libre »
    if item.get("occupation") == "libre":
        item["risks"] = [r for r in item.get("risks") or [] if r["code"] not in ("occupe", "bail")]
    return conflicts
