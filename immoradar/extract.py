"""Extraction d'informations depuis le texte des annonces.

Tout ce qui est extrait ici est marqué « extrait » (lu dans le texte de l'annonce) :
rien n'est inventé. Si un motif n'est pas trouvé, la valeur reste None (= inconnue).
"""
import html as htmlmod
import re
from datetime import date
from html.parser import HTMLParser

MOIS = {"janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
        "aout": 8, "août": 8, "septembre": 9, "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12}

NUM = r"(\d{1,3}(?:[   .]\d{3})+|\d+)(?:[,.](\d{1,2}))?"


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head"}
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "td", "th", "dd", "dt"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts, self.links, self.images, self.meta = [], [], [], {}
        self._skip = 0
        self._a = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self._skip += 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and a.get("href"):
            self._a = {"href": a["href"], "title": a.get("title") or "", "text": []}
        if tag == "img":
            src = a.get("data-src") or a.get("src")
            if src:
                self.images.append({"src": src, "alt": a.get("alt") or ""})
        if tag == "meta":
            k = a.get("property") or a.get("name")
            if k and a.get("content"):
                self.meta[k.lower()] = a["content"]

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        if tag in self.BLOCK:
            self.parts.append("\n")
        if tag == "a" and self._a is not None:
            self._a["text"] = " ".join("".join(self._a["text"]).split())
            self.links.append(self._a)
            self._a = None

    def handle_data(self, data):
        if self._skip:
            return
        self.parts.append(data)
        if self._a is not None:
            self._a["text"].append(data)


def parse_html(html):
    """Renvoie (texte, liens, images, meta)."""
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass
    text = "".join(p.parts)
    text = re.sub(r"[ \t  ]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    return text, p.links, p.images, p.meta


def to_number(intpart, dec=None):
    v = float(re.sub(r"[   .]", "", intpart))
    if dec:
        v += float("0." + dec)
    return v


def _prices_after(label_regex, text):
    out = []
    for m in re.finditer(label_regex + r"\s*(?:de\s*)?:?\s*" + NUM + r"\s*(?:€|euros?|eur\b)", text, re.I):
        v = to_number(m.group(1), m.group(2))
        if v >= 100:
            out.append(v)
    return out


def mises_a_prix(text):
    return _prices_after(r"mise\s+[àa]\s+prix", text)


def adjudication_price(text):
    v = _prices_after(r"(?:adjug[ée]e?s?(?:\s+pour)?|prix\s+d['’]adjudication|adjudication)", text)
    return v[0] if v else None


def surface(text):
    """Surface habitable : privilégie la surface Carrez si elle est mentionnée."""
    carrez = re.search(r"carrez[^0-9]{0,40}" + NUM + r"\s*m\s*[²2]", text, re.I)
    if carrez:
        v = to_number(carrez.group(1), carrez.group(2))
        if 5 <= v <= 5000:
            return v, "Carrez"
    for m in re.finditer(NUM + r"\s*m\s*[²2](?![0-9])", text, re.I):
        ctx = re.split(r"[,.;:\n()]", text[max(0, m.start() - 40):m.start()].lower())[-1]
        if any(k in ctx for k in ("terrain", "jardin", "parcelle", "cour ", "contenance", "terrasse", "balcon", "cave")):
            continue
        v = to_number(m.group(1), m.group(2))
        if 8 <= v <= 5000:
            return v, "texte"
    return None, None


def rooms(text):
    t = text.lower()
    m = re.search(r"\b(\d{1,2})\s*pi[èe]ces?(?:\s+principales)?\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b[tf]\s?(\d)\b", text)
    if m:
        return int(m.group(1))
    if re.search(r"\bstudio\b", t):
        return 1
    words = {"une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6, "sept": 7, "huit": 8}
    m = re.search(r"\b(une|deux|trois|quatre|cinq|six|sept|huit)\s+pi[èe]ces\b", t)
    if m:
        return words[m.group(1)]
    return None


TYPE_RULES = [
    ("immeuble", r"\bimmeuble\b(?!\s+en\s+copropri)|\bimmeuble de rapport\b"),
    ("local_commercial", r"local (?:commercial|d['’]activit|professionnel)|\bboutique\b|\bcommerce\b|\bbureaux?\b|fonds de commerce"),
    ("studio", r"\bstudio\b|\bstudette\b"),
    ("appartement", r"\bappartement\b|\bduplex\b|\btriplex\b|\bloft\b"),
    ("maison", r"\bmaison\b|\bpavillon\b|\bvilla\b|\bpropri[ée]t[ée]\b"),
    ("parking", r"\bparking\b|\bbox\b|\bgarage\b|\bemplacement\b"),
    ("terrain", r"\bterrain\b"),
]


def property_type(text):
    t = text.lower()
    for name, rx in TYPE_RULES:
        if re.search(rx, t):
            return name
    return None


def floor(text):
    t = text.lower()
    if re.search(r"rez[- ]de[- ]chauss[ée]e|\brdc\b", t):
        return 0
    m = re.search(r"\b(\d{1,2})\s*(?:er|ère|e|ème|eme)?\s+[ée]tage\b", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b[ée]tage\s*:?\s*(\d{1,2})\b", t)
    if m:
        return int(m.group(1))
    return None


def occupation(text):
    t = text.lower()
    if re.search(r"\b(?:bien\s+)?(?:occup[ée]e?s?|lou[ée]e?s?|bail\s+en\s+cours|squatt)", t):
        if re.search(r"\blibres?\s+(?:de\s+toute\s+occupation|d['’]occupation)|\binoccup[ée]", t):
            return "libre"
        return "occupé"
    if re.search(r"\blibres?\b(?:\s+de\s+toute\s+occupation|\s+d['’]occupation)?|\binoccup[ée]e?\b|\bvacant\b", t):
        return "libre"
    return None


FEATURES = {
    "terrasse": r"\bterrasses?\b",
    "balcon": r"\bbalcons?\b|\bloggias?\b",
    "jardin": r"\bjardins?\b|\bjardinet\b",
    "parking": r"\bparkings?\b|\bbox\b|\bgarages?\b|\bstationnement\b",
    "cave": r"\bcaves?\b|\bcellier\b",
    "ascenseur": r"\bascenseur\b",
}


def features(text):
    t = text.lower()
    out = {}
    for k, rx in FEATURES.items():
        if re.search(r"\bsans\s+(?:" + rx + ")", t):
            out[k] = False
        elif re.search(rx, t):
            out[k] = True
        else:
            out[k] = None
    return out


WORKS_RULES = [
    ("lourd", r"gros travaux|travaux importants|[àa] r[ée]habiliter|enti[èe]rement [àa] r[ée]nover|r[ée]novation (?:compl[èe]te|totale)|"
              r"mauvais [ée]tat|tr[èe]s mauvais|v[ée]tuste|insalubr|ruine|sinistr|incendi|d[ée]gât des eaux important"),
    ("moyen", r"[àa] r[ée]nover|travaux [àa] pr[ée]voir|n[ée]cessite des travaux|[ée]tat moyen|travaux de r[ée]novation"),
    ("léger", r"[àa] rafra[iî]chir|rafra[iî]chissement|travaux de d[ée]coration|quelques travaux"),
    ("aucun", r"bon [ée]tat|tr[èe]s bon [ée]tat|refait [àa] neuf|r[ée]cemment r[ée]nov|r[ée]nov[ée]|[ée]tat neuf"),
]


def works_level(text):
    t = text.lower()
    for level, rx in WORKS_RULES:
        if re.search(rx, t):
            return level
    return None


RISK_RULES = [
    # (code, libellé, gravité 1-3, regex)
    ("squat", "Occupation sans droit ni titre / squat", 3, r"sans droit ni titre|squat"),
    ("usufruit", "Démembrement (usufruit / nue-propriété) ou viager", 3, r"usufruit|nue[- ]propri[ée]t[ée]|viager|droit d['’]usage et d['’]habitation"),
    ("peril", "Arrêté de péril / insalubrité / mise en sécurité", 3, r"arr[êe]t[ée] de p[ée]ril|arr[êe]t[ée] d['’]insalubrit|mise en s[ée]curit[ée]|interdiction d['’]habiter"),
    ("copro_difficulte", "Copropriété en difficulté (administrateur provisoire, plan de sauvegarde)", 3, r"administrateur provisoire|plan de sauvegarde|copropri[ée]t[ée] en difficult"),
    ("bail", "Bien loué (bail en cours)", 2, r"\blou[ée]e?\b|bail (?:d['’]habitation|en cours)|locataire"),
    ("bail_commercial", "Bail commercial en cours", 2, r"bail commercial"),
    ("occupe", "Bien occupé", 2, r"\boccup[ée]e?\b(?! par le propri)"),
    ("indivision", "Indivision / licitation", 1, r"indivision|licitation"),
    ("sans_visite", "Visite impossible ou non prévue", 2, r"sans visite|pas de visite|visite impossible|aucune visite"),
    ("procedure", "Procédure ou contentieux mentionné", 2, r"contentieux|proc[ée]dure en cours|litige"),
    ("servitude", "Servitude mentionnée", 1, r"servitude"),
    ("diagnostics", "Amiante / plomb / termites signalés", 1, r"pr[ée]sence d['’]amiante|amiante d[ée]tect|plomb.{0,20}(?:positif|pr[ée]sence)|termites"),
    ("preemption", "Droit de préemption possible", 1, r"droit de pr[ée]emption"),
    ("inondable", "Zone inondable / risques", 1, r"zone inondable|plan de pr[ée]vention des risques"),
]


def risks(text):
    t = text.lower()
    out = []
    for code, label, sev, rx in RISK_RULES:
        if re.search(rx, t):
            if code == "occupe" and re.search(r"inoccup|libre d['’]occupation", t):
                continue
            out.append({"code": code, "label": label, "severity": sev})
    return out


def sale_status(text):
    t = text.lower()
    if re.search(r"vente (?:annul[ée]e|retir[ée]e|report[ée]e|radi[ée]e)|\bretir[ée]e? de la vente|\bannul[ée]e\b", t):
        return "retirée"
    if re.search(r"\badjug[ée]e?\b|prix d['’]adjudication", t):
        return "adjugée"
    return None


DATE_RX = re.compile(
    r"(?:(?:lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche)\s+)?(\d{1,2})(?:er)?\s+"
    r"(janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[uû]t|septembre|octobre|novembre|d[ée]cembre)\s+(\d{4})"
    r"(?:\s*(?:à|a)\s*(\d{1,2})\s*h\s*(\d{2})?)?", re.I)


def parse_fr_date(s):
    m = DATE_RX.search(s or "")
    if not m:
        m2 = re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", s or "")
        if m2:
            try:
                return date(int(m2.group(3)), int(m2.group(2)), int(m2.group(1))).isoformat(), None
            except ValueError:
                return None, None
        return None, None
    mois = MOIS.get(m.group(2).lower().replace("é", "e").replace("û", "u"), MOIS.get(m.group(2).lower()))
    try:
        d = date(int(m.group(3)), mois, int(m.group(1))).isoformat()
    except (ValueError, TypeError):
        return None, None
    hh = f"{int(m.group(4)):02d}:{m.group(5) or '00'}" if m.group(4) else None
    return d, hh


def sale_date_from_text(text):
    """Date de vente : cherche d'abord un contexte « vente / audience / adjudication »."""
    for m in re.finditer(r"(?:vente|audience|adjudication|enchères?|ench[eè]res)[^\n]{0,80}", text, re.I):
        d, h = parse_fr_date(m.group(0))
        if d:
            return d, h
    return None, None


def cleanup(s):
    return " ".join(htmlmod.unescape(s or "").split())


def analyse_text(text):
    """Applique toutes les extractions à un texte d'annonce."""
    sfc, sfc_src = surface(text)
    mp = mises_a_prix(text)
    sd, st = sale_date_from_text(text)
    return {
        "type": property_type(text),
        "rooms": rooms(text),
        "surface": sfc,
        "surface_kind": sfc_src,
        "floor": floor(text),
        "mise_a_prix": mp[0] if mp else None,
        "lots_count": len(mp) if len(mp) > 1 else None,
        "adjudication_price": adjudication_price(text),
        "sale_date": sd,
        "sale_time": st,
        "occupation": occupation(text),
        "features": features(text),
        "works_level": works_level(text),
        "risks": risks(text),
        "status_hint": sale_status(text),
    }
