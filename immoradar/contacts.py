"""Contacts de la vente (avocat, notaire, greffe) et mail de demande des informations manquantes.

Règle : aucune coordonnée n'est inventée. Un email, un téléphone ou un site n'apparaît que s'il est lu
dans l'annonce (ou un document), avec sa source. L'application n'envoie jamais de mail : elle prépare
le texte, que l'utilisateur envoie lui-même.
"""
import re
import urllib.parse

PHONE = re.compile(r"(?:\+33\s?\(?0?\)?\s?|\b0)[1-9](?:[\s.\-]?\d{2}){4}\b")
EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)*\.[a-z]{2,}", re.I)
WEB = re.compile(r"\b(?:https?://)?www\.[\w\-]+(?:\.[\w\-]+)+(?:/[^\s)]*)?", re.I)
HEAD = re.compile(r"^\s*(?:Ma[iî]tre|Me|M[eᵉ]\.?)\s+.{2,160}?\b(Avocats?|Notaires?)\b", re.I)
FIRM = re.compile(r"\b(SELARL|SELAS|SELAFA|SCP|AARPI|SELURL|SAS|Cabinet)\b[^,\n]{1,70}", re.I)
STOP = re.compile(r"cahier des conditions|annonce non|contenu non|prix constat|\bvisite\b|mise [àa] prix", re.I)


def _clean_phone(p):
    digits = re.sub(r"\D", "", p)
    if digits.startswith("33"):
        digits = "0" + digits[2:].lstrip("0")
    return " ".join(digits[i:i + 2] for i in range(0, len(digits), 2)) if len(digits) == 10 else p.strip()


def extract(text, links=(), source="annonce"):
    """Renvoie {"contacts": [...], "greffe": {...} | None} à partir du texte d'une annonce."""
    lines = [ln.strip() for ln in (text or "").split("\n")]
    contacts, cur = [], None
    for ln in lines:
        if not ln:
            continue
        h = HEAD.match(ln)
        if h and len(ln) < 220:
            name = re.split(r",| de la | membre | du cabinet ", ln, maxsplit=1)[0].strip()
            firm = FIRM.search(ln)
            cur = {"role": "notaire" if "notaire" in h.group(1).lower() else "avocat", "name": name,
                   "firm": firm.group(0).strip(" ,") if firm and not firm.group(0).lower().startswith(("avocat", "notaire")) else None,
                   "header": ln, "address": None, "phones": [], "emails": [], "websites": [], "hours": None, "source": source}
            contacts.append(cur)
            continue
        if cur is None:
            continue
        if STOP.search(ln) or len(ln) > 200:
            cur = None
            continue
        found = False
        for p in PHONE.findall(ln):
            cur["phones"].append(_clean_phone(p))
            found = True
            hrs = re.search(r"\(([^)]*\d+\s?h[^)]*)\)", ln)
            if hrs:
                cur["hours"] = hrs.group(1)
        for e in EMAIL.findall(ln):
            cur["emails"].append(e.lower())
            found = True
        for w in WEB.findall(ln):
            if "@" not in w:
                cur["websites"].append(w.rstrip(".,"))
                found = True
        if not found and re.search(r"\b\d{5}\b", ln) and not cur["address"]:
            cur["address"] = ln
    # liens mailto: / sites dans le HTML, rattachés au contact dont le bloc les mentionne, sinon au premier
    for ln in links or []:
        href = (ln.get("href") or "").strip()
        if href.lower().startswith("mailto:") and contacts:
            mail = urllib.parse.unquote(href[7:].split("?")[0]).lower()
            if EMAIL.fullmatch(mail) and not any(mail in c["emails"] for c in contacts):
                target = next((c for c in contacts if (ln.get("text") or "").strip().lower() in c["header"].lower()), contacts[0])
                target["emails"].append(mail)
    for c in contacts:
        for k in ("phones", "emails", "websites"):
            c[k] = list(dict.fromkeys(c[k]))
        c.pop("header", None)

    greffe = None
    ref = re.search(r"r[ée]f[ée]rence\s+(?:du\s+)?(?:greffe\s+)?(?:RG\s+)?n[°o]?\s?([\w/.\-]+)", text or "", re.I)
    where = re.search(r"consult[ée]\s+au\s+(Greffe[^.\n]{5,200}?)(?:\s+et\s+au\s+[Cc]abinet|\.|\n|$)", text or "")
    if ref or where:
        greffe = {"reference": ref.group(1).rstrip(".,") if ref else None,
                  "lieu": " ".join(where.group(1).split()) if where else None,
                  "cabinet_aussi": bool(re.search(r"et au [Cc]abinet", text or "")), "source": source}
    return {"contacts": contacts, "greffe": greffe}


# ------------------------------------------------------------------ informations manquantes

def _analysed(item):
    return [d for d in item.get("doc_analyses") or [] if d.get("status") == "analysé"]


def _facts(item):
    return {f["field"] for d in _analysed(item) for f in d.get("facts", [])}


def missing(item):
    """Liste des informations à demander, chacune avec la raison (ce qui manque sur la fiche)."""
    kinds = {(d.get("kind") or d.get("label") or "").lower() for d in _analysed(item)}
    linked = " ".join((d.get("label") or "") for d in item.get("documents") or []).lower()
    facts = _facts(item)
    out = []

    def add(what, why):
        out.append({"demande": what, "raison": why})

    has = lambda word: any(word in k for k in kinds)  # noqa: E731
    if not has("pv") and not has("procès"):
        add("le procès-verbal descriptif (PV de description) du bien",
            "lien présent dans l'annonce mais non lisible ici" if "pv" in linked or "descriptif" in linked else "aucun PV dans l'annonce")
    if not has("cahier"):
        add("le cahier des conditions de vente (avec ses annexes)",
            "consultable seulement au greffe ou au cabinet" if (item.get("greffe") or {}).get("lieu") else "non disponible dans l'annonce")
    if "dpe" not in facts:
        add("le dossier de diagnostics techniques (DPE, amiante, plomb, électricité, gaz, termites, ERP)", "DPE et diagnostics inconnus")
    if item.get("type") in ("appartement", "studio", None) or item.get("lots_copro"):
        if "charges_copro_an" not in facts:
            add("le montant des charges de copropriété annuelles et l'éventuel arriéré du lot", "charges inconnues")
        add("les trois derniers procès-verbaux d'assemblée générale et les travaux votés ou à venir", "situation de la copropriété inconnue")
    if "taxe_fonciere_an" not in facts:
        add("le montant de la dernière taxe foncière", "taxe foncière inconnue")
    occ = item.get("occupation")
    if occ in (None, "inconnu"):
        add("la situation d'occupation actuelle du bien (libre, occupé, bail en cours, loyer)", "occupation inconnue")
    elif occ == "occupé" and "loyer_en_place" not in facts:
        add("le bail en cours, le montant du loyer et la situation de paiement de l'occupant", "bien occupé, bail inconnu")
    if "frais_prealables" not in facts:
        add("le montant des frais préalables (taxés) à régler en sus du prix", "frais préalables inconnus")
    if not item.get("visit"):
        add("la ou les dates de visite prévues", "aucune visite indiquée")
    if item.get("surface_kind") not in ("carrez", "Carrez") and "surface" not in facts:
        add("la surface loi Carrez certifiée", "surface Carrez non confirmée par un document")
    return out


def _fmt_eur(v):
    return f"{round(v):,} €".replace(",", " ") if v else None


def _date_fr(iso):
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    try:
        y, m, d = (int(x) for x in iso[:10].split("-"))
        return f"{d} {mois[m - 1]} {y}"
    except Exception:
        return None


TYPE_TXT = {"appartement": "l'appartement", "maison": "la maison", "studio": "le studio", "immeuble": "l'immeuble",
            "local_commercial": "le local commercial", "parking": "le parking", "terrain": "le terrain"}


def request_mail(item, signature=""):
    """Prépare le mail de demande. Renvoie {to, to_source, subject, body, mailto, demandes, contacts, greffe}."""
    info = {"contacts": item.get("contacts") or [], "greffe": item.get("greffe")}
    dem = missing(item)
    contacts = info["contacts"]
    first = contacts[0] if contacts else None
    to = [e for c in contacts for e in c["emails"]][:2]
    bien = TYPE_TXT.get(item.get("type"), "le bien")
    lieu = ", ".join(x for x in (item.get("address"), item.get("city")) if x)
    date = _date_fr(item.get("sale_date") or "")
    ref = (info["greffe"] or {}).get("reference")
    subject = " – ".join(x for x in ("Vente aux enchères" + (f" du {date}" if date else ""), lieu or None,
                                      f"Réf. greffe {ref}" if ref else None, "demande d'informations") if x)
    salut = "Maître," if first else "Madame, Monsieur,"
    phrase = bien + (f" ({lieu})" if lieu else "") + (f" de {item['surface']:g} m²".replace(".", ",") if item.get("surface") else "")
    vente = []
    if date:
        vente.append(f"le {date}" + (f" à {item['sale_time'].replace(':', 'h')}" if item.get("sale_time") else ""))
    if item.get("sale_place"):
        vente.append(f"devant le {item['sale_place']}" if item["sale_place"].lower().startswith("tribunal") else f"({item['sale_place']})")
    mp = _fmt_eur(item.get("mise_a_prix"))
    lines = [salut, "",
             f"Je suis intéressé(e) par la vente aux enchères de {phrase}" + (", prévue " + " ".join(vente) if vente else "") +
             (f", sur une mise à prix de {mp}" if mp else "") + (f" (référence greffe {ref})" if ref else "") + ".",
             "", "Afin de préparer une éventuelle enchère, pourriez-vous me transmettre, si possible par retour de mail :"]
    lines += [f"- {d['demande']} ;" for d in dem] or ["- tout document complémentaire utile à l'appréciation du bien ;"]
    if lines[-1].endswith(" ;"):
        lines[-1] = lines[-1][:-2] + "."
    lines += ["", "Je vous remercie par avance pour votre aide.", "", "Cordialement,", signature.strip() or "[Votre nom et votre téléphone]"]
    if item.get("url"):
        lines += ["", f"Annonce : {item['url']}"]
    body = "\n".join(lines)
    mailto = "mailto:" + ",".join(to) + "?subject=" + urllib.parse.quote(subject) + "&body=" + urllib.parse.quote(body)
    doc_emails = [{"email": e["email"], "source": f"{d.get('kind') or d.get('label')} p.{e['page']}"}
                  for d in _analysed(item) for e in d.get("emails") or [] if e["email"] not in to]
    return {"doc_emails": doc_emails, "to": to, "to_source": [c.get("source") for c in contacts if c["emails"]][:1], "subject": subject, "body": body,
            "mailto": mailto, "demandes": dem, "contacts": contacts, "greffe": info["greffe"]}
