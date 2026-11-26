"""Accès réseau poli : robots.txt respecté, délai entre requêtes, détection des protections.

Aucune tentative de contournement : si une page renvoie un CAPTCHA, un défi anti-robot
ou une authentification, on lève ProtectionDetected et la source est marquée bloquée.
"""
import gzip
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser

USER_AGENT = "ImmoRadar/0.1 (outil personnel de veille immobiliere; collecte lente)"
TIMEOUT = 25


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


class ProtectionDetected(FetchError):
    pass


_CHALLENGE_MARKERS = (
    "cf-chl-", "<title>just a moment", "captcha-delivery", "attention required! | cloudflare",
    "please enable js and disable any ad blocker",
)
# Indices plus faibles : présents aussi sur des pages normales (script passif, formulaire de contact avec reCAPTCHA).
# Ils ne comptent comme protection que si la page est en erreur ou quasi vide.
_WEAK_MARKERS = ("challenge-platform", "g-recaptcha", "hcaptcha", "datadome")
DIAG_DIR = None  # si défini (run.py --diagnostic), chaque page reçue est enregistrée ici pour analyse


def raw_get(url, timeout=TIMEOUT, accept="text/html,application/json;q=0.9,*/*;q=0.8"):
    """GET sans contrôle robots (réservé aux API ouvertes : DVF, BAN, etc.). Renvoie (status, bytes, headers)."""
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": accept,
        "Accept-Language": "fr-FR,fr;q=0.9",
        "Accept-Encoding": "gzip",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
            if r.headers.get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return r.status, data, r.headers
    except urllib.error.HTTPError as e:
        body = b""
        try:
            body = e.read()
            if e.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
        except Exception:
            pass
        return e.code, body, e.headers
    except Exception as e:  # DNS, timeout, proxy, TLS...
        raise FetchError(f"{type(e).__name__}: {e}") from e


def _save_diag(url, status, text):
    import os
    import re as _re
    os.makedirs(DIAG_DIR, exist_ok=True)
    n = len(os.listdir(DIAG_DIR)) + 1
    name = f"{n:03d}-{status}-" + _re.sub(r"[^\w.-]+", "_", url.split("://", 1)[-1])[:120] + ".html"
    with open(os.path.join(DIAG_DIR, name), "w", encoding="utf-8") as fh:
        fh.write(f"<!-- {url} HTTP {status} -->\n" + text)


class PoliteClient:
    """Client par source : lit robots.txt une fois, applique un délai minimal entre requêtes."""

    def __init__(self, base_url, min_delay=3.0, max_requests=80):
        self.base_url = base_url.rstrip("/")
        self.min_delay = min_delay
        self.max_requests = max_requests
        self.requests_done = 0
        self._last = 0.0
        self._robots = None
        self.robots_status = "non vérifié"

    def _load_robots(self):
        if self._robots is not None:
            return
        rp = urllib.robotparser.RobotFileParser()
        status, body, headers = raw_get(self.base_url + "/robots.txt", accept="text/plain,*/*")
        ctype = (headers.get("Content-Type") or "") if headers else ""
        if status == 200 and "html" not in ctype.lower():
            rp.parse(body.decode("utf-8", "replace").splitlines())
            self.robots_status = "robots.txt lu"
            delay = rp.crawl_delay(USER_AGENT) or rp.crawl_delay("*")
            if delay:
                self.min_delay = max(self.min_delay, float(delay))
        elif status in (401, 403):
            # RFC 9309 : accès robots.txt refusé => tout est interdit
            rp.disallow_all = True
            self.robots_status = f"robots.txt refusé (HTTP {status}) : collecte interdite"
        else:
            # 404 ou contenu non texte : pas de règle publiée => autorisé
            rp.allow_all = True
            self.robots_status = f"pas de robots.txt exploitable (HTTP {status})"
        self._robots = rp

    def get(self, url):
        if not url.startswith("http"):
            url = urllib.parse.urljoin(self.base_url + "/", url)
        self._load_robots()
        if not self._robots.can_fetch(USER_AGENT, url):
            raise RobotsDisallowed(f"robots.txt interdit {url}")
        if self.requests_done >= self.max_requests:
            raise FetchError(f"limite de {self.max_requests} requêtes atteinte pour cette recherche")
        wait = self.min_delay - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()
        self.requests_done += 1
        status, body, headers = raw_get(url)
        text = body.decode("utf-8", "replace")
        low = text[:30000].lower()
        if DIAG_DIR:
            _save_diag(url, status, text)
        challenged = any(m in low for m in _CHALLENGE_MARKERS) or \
            ((status >= 400 or len(text) < 5000) and any(m in low for m in _WEAK_MARKERS))
        if challenged or status in (401, 429):
            raise ProtectionDetected(f"protection anti-robot, CAPTCHA ou authentification (HTTP {status}) sur {url}")
        if status >= 400:
            raise FetchError(f"HTTP {status} sur {url}")
        return text
