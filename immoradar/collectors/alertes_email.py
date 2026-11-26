"""Alertes email des portails (SeLoger, Leboncoin, Jinka, Bien'ici, PAP, Logic-Immo…).

Voie autorisée pour suivre les annonces classiques : l'utilisateur crée ses alertes sur les sites (ou sur Jinka,
qui regroupe la plupart des portails), les reçoit dans SA boîte mail, et ImmoRadar lit ces emails :
- soit directement dans la boîte (IMAP, en lecture seule, avec un mot de passe d'application),
- soit à partir de fichiers .eml déposés sur la page Paramètres.
Aucun site n'est interrogé : seuls les emails reçus par l'utilisateur sont lus. Les liens des emails sont gardés
tels quels (ils peuvent passer par un lien de suivi du portail) et ne sont pas ouverts par ImmoRadar.
"""
import email
import email.header
import email.policy
import hashlib
import imaplib
import re
from datetime import date, timedelta
from html.parser import HTMLParser

from .. import config, db, extract
from ..geo_fr import dep_from_postcode

NAME = "alertes_email"
LABEL = "Alertes email (SeLoger, Leboncoin, Jinka, Bien'ici, PAP…)"
HOME = "https://www.jinka.fr"
MODE = "auto"
LEGAL = ("Lecture de vos propres emails d'alerte (IMAP en lecture seule ou fichiers .eml) : aucun portail n'est interrogé. "
         "Créez vos alertes sur SeLoger, Leboncoin, Bien'ici… ou une seule sur Jinka qui les regroupe.")
SENDERS = {"seloger": "SeLoger", "leboncoin": "Leboncoin", "jinka": "Jinka", "bienici": "Bien'ici", "pap.fr": "PAP",
           "logic-immo": "Logic-Immo", "figaro": "Figaro Immobilier", "superimmo": "Superimmo", "laforet": "Laforêt",
           "century21": "Century 21", "orpi": "Orpi", "explorimmo": "Explorimmo", "avendrealouer": "AVendreALouer"}
LAST_INFO = None


class NotConfigured(Exception):
    pass


def manual_links(deps):
    return [{"label": "Créer une alerte Jinka (regroupe SeLoger, Leboncoin, PAP, Bien'ici…)", "url": "https://www.jinka.fr/"},
            {"label": "Alertes SeLoger", "url": "https://www.seloger.com/"},
            {"label": "Alertes Leboncoin (recherche sauvegardée)", "url": "https://www.leboncoin.fr/"}]


# ------------------------------------------------------------------ lecture d'un email

class _Linear(HTMLParser):
    """Texte linéaire de l'email où chaque lien est remplacé par un repère ⟦n⟧ (pour rattacher lien et annonce)."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self._skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("style", "script", "head"):
            self._skip += 1
        elif tag == "a" and a.get("href", "").startswith("http"):
            self.links.append(a["href"])
            self.parts.append(f" ⟦{len(self.links) - 1}⟧ ")
        elif tag in ("br", "p", "div", "tr", "td", "li", "h1", "h2", "h3", "table"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


PRICE = re.compile(r"(\d{1,3}(?:[\s  .]\d{3})+|\d{5,7})\s*(?:€|euros?\b)", re.I)
LISTING_LINK = re.compile(r"(annonce|/ad/|/ads?/|detail|/vente/|listing|alert_result|/bien|/offre|classified|realestate|/achat)", re.I)
SURF = re.compile(r"(\d{1,4}(?:[,.]\d{1,2})?)\s*m(?:²|2)\b")
NOISE_LINK = re.compile(r"(unsubscribe|desinscri|d[ée]sabonn|preferences|privacy|confidentialit|mentions|aide|help|facebook|"
                        r"twitter|instagram|apple\.com|google\.com|play\.google|app-?store|logo|mailto)", re.I)


def _decode(h):
    try:
        return str(email.header.make_header(email.header.decode_header(h or "")))
    except Exception:
        return h or ""


def portal_of(sender):
    s = (sender or "").lower()
    return next((label for key, label in SENDERS.items() if key in s), None)


def parse_message(raw):
    """Renvoie les annonces trouvées dans un email d'alerte (liste de dict prêts pour l'upsert)."""
    msg = email.message_from_bytes(raw, policy=email.policy.default) if isinstance(raw, bytes) else raw
    sender = _decode(msg.get("From"))
    portal = portal_of(sender) or portal_of(_decode(msg.get("Subject"))) or "Alerte email"
    html = text = None
    for part in msg.walk():
        ctype = part.get_content_type()
        try:
            content = part.get_content()
        except Exception:
            continue
        if ctype == "text/html" and html is None:
            html = content
        elif ctype == "text/plain" and text is None:
            text = content
    p = _Linear()
    if html:
        p.feed(html)
        lin = "".join(p.parts)
    else:
        lin = text or ""
        for u in re.findall(r"https?://\S+", lin):
            p.links.append(u.rstrip(">)."))
            lin = lin.replace(u, f" ⟦{len(p.links) - 1}⟧ ", 1)
    lin = re.sub(r"[ \t  ]+", " ", lin)
    lin = re.sub(r"\s*\n\s*", "\n", lin)
    # 1) blocs par lien d'annonce (cas général) ; 2) à défaut, blocs autour de chaque prix
    marks = []
    for m in re.finditer(r"⟦(\d+)⟧", lin):
        u = p.links[int(m.group(1))]
        if NOISE_LINK.search(u):
            continue
        if marks and marks[-1][2] == u:
            marks[-1] = (marks[-1][0], m.end(), u)          # même lien répété (photo + titre) : un seul repère
        else:
            marks.append((m.start(), m.end(), u))
    blocks = []
    for i, (st, en, u) in enumerate(marks):
        after = lin[en:marks[i + 1][0] if i + 1 < len(marks) else len(lin)]
        before = lin[marks[i - 1][1] if i else 0:st]
        for blk in (after, before):
            if PRICE.search(blk) and SURF.search(blk):
                blocks.append((blk, u))
                break
    if not blocks:
        prices = list(PRICE.finditer(lin))
        for i, m in enumerate(prices):
            a2 = prices[i - 1].end() if i else 0
            b2 = prices[i + 1].start() if i + 1 < len(prices) else len(lin)
            blocks.append((lin[max(a2, m.start() - 400):min(b2, m.end() + 400)], None))
    out, seen = [], set()
    for block, url in blocks:
        clean = re.sub(r"⟦\d+⟧", " ", block)
        sfc = SURF.search(clean)
        pm = [x for x in PRICE.finditer(clean) if 15000 <= extract.to_number(re.sub(r"[\s\u00a0\u202f]", " ", x.group(1))) <= 20_000_000
              and not re.search(r"(jusqu|max|budget|entre|moins de)\W{0,3}\w{0,3}\W{0,3}$", clean[max(0, x.start() - 12):x.start()], re.I)]
        if not sfc or not pm:
            continue  # pas une annonce de bien (ex. rappel des critères « jusqu'à 400 000 € »)
        price = extract.to_number(re.sub(r"[\s\u00a0\u202f]", " ", pm[0].group(1)))
        d = extract.analyse_text(clean)
        city_m = list(re.finditer(r"([A-ZÉÈ][\w'’-]+(?:[ -](?:[A-Za-zÉÈéèêàç'’-]+|\d{1,2}(?:e|er|ème|eme)))*)\s*\(\s*(\d{5})\s*\)", clean))
        cp = re.search(r"\b(\d{5})\b", clean)
        postcode = city_m[0].group(2) if city_m else (cp.group(1) if cp else None)
        city = None
        if city_m:
            words = re.split(r"\s+-\s+|\s*[·•|,]\s*", city_m[0].group(1))
            city = words[-1].strip()
        key = url or f"{price}-{sfc.group(1)}-{postcode}"
        sid = hashlib.sha1(key.encode()).hexdigest()[:16]
        if sid in seen:
            continue
        seen.add(sid)
        title = next((ln.strip() for ln in clean.split("\n") if re.search(r"appartement|maison|studio|loft|duplex|pi[èe]ce", ln, re.I)), None)
        d.update({
            "source": "alerte:" + portal, "source_id": sid, "url": url,
            "title": (title or f"Annonce {portal}")[:140],
            "mise_a_prix": price, "surface": float(sfc.group(1).replace(",", ".")),
            "postcode": postcode, "city": city, "dep": dep_from_postcode(postcode) if postcode else None,
            "sale_kind": "amiable", "description": " ".join(clean.split())[:1500],
            "photos": [], "documents": [], "alert_date": _decode(msg.get("Date")),
        })
        if not d.get("type"):
            d["type"] = "appartement" if re.search(r"appartement|studio|duplex|loft", clean, re.I) else \
                "maison" if re.search(r"maison|villa|pavillon", clean, re.I) else None
        out.append(d)
    return out


# ------------------------------------------------------------------ boîte mail (IMAP)

def settings(params=None):
    stored = (params or {}).get(NAME) if params else (db.get_setting("connecteurs", {}) or {}).get(NAME)
    return {**{"active": False, "serveur": "imap.gmail.com", "port": 993, "utilisateur": "", "mot_de_passe": "",
               "dossier": "INBOX", "jours": 7}, **(stored or {})}


def fetch_imap(s, log=print, limit=200):
    if not (s.get("utilisateur") and s.get("mot_de_passe")):
        raise NotConfigured("boîte mail non configurée (Paramètres > Alertes email)")
    box = imaplib.IMAP4_SSL(s["serveur"], int(s.get("port") or 993), timeout=60)
    try:
        box.login(s["utilisateur"], s["mot_de_passe"])
        box.select(s.get("dossier") or "INBOX", readonly=True)   # lecture seule : rien n'est modifié ni marqué lu
        since = (date.today() - timedelta(days=int(s.get("jours") or 7))).strftime("%d-%b-%Y")
        ids = set()
        for key in SENDERS:
            typ, data = box.search(None, "SINCE", since, "FROM", f'"{key}"')
            if typ == "OK" and data and data[0]:
                ids.update(data[0].split())
        msgs = []
        for i in sorted(ids, key=int)[-limit:]:
            typ, data = box.fetch(i, "(BODY.PEEK[])")
            if typ == "OK" and data and isinstance(data[0], tuple):
                msgs.append(data[0][1])
        log(f"Alertes email : {len(msgs)} email(s) d'alerte reçus ces {s.get('jours') or 7} derniers jours")
        return msgs
    finally:
        try:
            box.logout()
        except Exception:
            pass


def collect(deps, known, log):
    global LAST_INFO
    LAST_INFO = None
    s = settings()
    if not s["active"]:
        raise NotConfigured("non activé : Paramètres > Alertes email")
    out = []
    for raw in fetch_imap(s, log):
        try:
            out += parse_message(raw)
        except Exception as e:
            log(f"Alertes email : un email illisible ({type(e).__name__})")
    kept = [d for d in out if not d.get("dep") or d["dep"] in deps]
    if len(kept) < len(out):
        LAST_INFO = f"{len(out)} annonce(s) dans les alertes, {len(out) - len(kept)} hors de vos départements ignorée(s)"
    return kept
