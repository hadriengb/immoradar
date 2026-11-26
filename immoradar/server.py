"""Serveur HTTP minimal (bibliothèque standard uniquement) : API JSON + fichiers statiques."""
import base64
import hmac
import json
import mimetypes
import os
import re
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import automation, contacts, config, db, documents, opendata, pipeline
from .collectors import ALL as COLLECTORS
from .geo_fr import DEPARTEMENTS, ZONES

PASSWORD = os.environ.get("IMMORADAR_PASSWORD", "")
USER = os.environ.get("IMMORADAR_USER", "immoradar")
STATIC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")


def dashboard():
    lr = db.q("SELECT * FROM runs WHERE status!='en cours' ORDER BY id DESC LIMIT 1")
    last = dict(lr[0]) if lr else None
    cards = pipeline.list_cards()
    seuil = config.load()["scoring"]["seuil_opportunite"]
    total = db.q("SELECT count(*) n FROM listings")[0]["n"]
    return {
        "running": pipeline.STATE["running"], "step": pipeline.STATE["step"], "log": pipeline.STATE["log"][-40:],
        "last_run": {"id": last["id"], "started_at": last["started_at"], "finished_at": last["finished_at"],
                     "status": last["status"], "summary": json.loads(last["summary"] or "{}"),
                     "sources": json.loads(last["sources"] or "[]")} if last else None,
        "total_stored": total,
        "matching": len(cards),
        "new": sum(1 for c in cards if c["status"] == "nouvelle"),
        "new_cards": [c for c in cards if c["status"] == "nouvelle"][:12],
        "automation": {**automation.settings(), **automation.launch_agent_status(), "due": automation.search_due(),
                       "last_trigger": (json.loads(last["summary"] or "{}").get("declencheur") if last else None)},
        "opportunities": sum(1 for c in cards if max(c["score_revente"] or 0, c["score_locatif"] or 0) >= seuil),
        "top": cards[:5],
        "seuil": seuil,
        "dvf": opendata.dvf_status(),
        "storage": db.backend(),
        "doc_reader": documents.reader_label(),
        "loyers": [dict(r) for r in db.q("SELECT kind, count(*) n FROM loyers GROUP BY kind")],
        "sources_def": [{"name": n, "label": m.LABEL, "mode": m.MODE, "legal": m.LEGAL, "home": m.HOME}
                        for n, m in COLLECTORS.items()],
    }


def detail(lid):
    rows = db.q("SELECT * FROM listings WHERE id=?", (lid,))
    if not rows:
        return None
    row = rows[0]
    item, data, ov = pipeline.effective(row)
    c = pipeline.card(row, pipeline.last_run_id())
    srcs = [dict(r) for r in db.q("SELECT source, source_id, url, collected_at FROM listing_sources WHERE listing_id=?", (lid,))]
    return {**c, "item": item, "overrides": ov, "analysis": json.loads(row["analysis"] or "{}"), "raw_sources": srcs,
            "params": config.load()["calculs"], "demande": contacts.request_mail(item, config.load().get("signature") or ""),
            "doc_tools": documents.tools(), "doc_reader": documents.reader_label()}


class H(BaseHTTPRequestHandler):
    server_version = "ImmoRadar/0.1"

    def log_message(self, fmt, *args):
        pass

    def _authorized(self):
        """Accès protégé par mot de passe (HTTP Basic) dès que IMMORADAR_PASSWORD est défini."""
        if not PASSWORD or self.path == "/healthz":
            return True
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(h[6:]).decode().partition(":")
            except Exception:
                return False
            ok_user = hmac.compare_digest(user.encode(), USER.encode())
            ok_pwd = hmac.compare_digest(pwd.encode(), PASSWORD.encode())
            if ok_user and ok_pwd:
                return True
        time.sleep(0.5)
        body = "Authentification requise".encode()
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="ImmoRadar", charset="UTF-8"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return False

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        if not self._authorized():
            return
        if self.path == "/healthz":
            return self._json({"ok": True})
        u = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
        p = u.path
        try:
            if p == "/api/state":
                return self._json(dashboard())
            if p == "/api/listings":
                crit = json.loads(qs["criteria"]) if qs.get("criteria") else None
                return self._json(pipeline.list_cards(crit, qs.get("sort", "interet"), qs.get("fav") == "1", qs.get("hidden") == "1"))
            m = re.fullmatch(r"/api/listing/(\d+)", p)
            if m:
                d = detail(int(m.group(1)))
                return self._json(d) if d else self._json({"error": "introuvable"}, 404)
            if p == "/api/compare":
                ids = [int(i) for i in qs.get("ids", "").split(",") if i.isdigit()]
                return self._json([detail(i) for i in ids if detail(i)])
            if p == "/api/params":
                return self._json({"params": config.load(), "defaults": config.DEFAULTS, "zones": ZONES, "departements": DEPARTEMENTS})
            if p.startswith("/api/diagnostic/"):
                f = automation.diagnostic_file(urllib.parse.unquote(p.rsplit("/", 1)[1]))
                if not f:
                    return self._json({"error": "introuvable"}, 404)
                with open(f, "rb") as fh:
                    data = fh.read()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition", f'attachment; filename="{os.path.basename(f)}"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if p == "/api/runs":
                return self._json([dict(r) for r in db.q("SELECT id, started_at, finished_at, status, summary, sources FROM runs ORDER BY id DESC LIMIT 20")])
            return self._static(p)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        if not self._authorized():
            return
        p = urllib.parse.urlparse(self.path).path
        try:
            if p == "/api/search":
                rid = pipeline.run_search()
                return self._json({"run_id": rid, "already_running": rid is None})
            m = re.fullmatch(r"/api/listing/(\d+)/documents", p)
            if m:
                return self._json(self._documents(int(m.group(1))))
            m = re.fullmatch(r"/api/listing/(\d+)", p)
            if m:
                lid = int(m.group(1))
                body = json.loads(self._body() or b"{}")
                if "favorite" in body:
                    db.x("UPDATE listings SET favorite=? WHERE id=?", (1 if body["favorite"] else 0, lid))
                if "hidden" in body:
                    db.x("UPDATE listings SET hidden=? WHERE id=?", (1 if body["hidden"] else 0, lid))
                if "overrides" in body:
                    ov = {k: v for k, v in body["overrides"].items() if k in pipeline.OVERRIDABLE and v not in ("", None)}
                    for k in ("surface", "mise_a_prix", "adjudication_price", "prix_envisage_manuel", "loyer_manuel", "travaux_manuel", "valeur_marche_manuel"):
                        if k in ov:
                            ov[k] = float(ov[k])
                    for k in ("rooms", "floor"):
                        if k in ov:
                            ov[k] = int(ov[k])
                    db.x("UPDATE listings SET overrides=? WHERE id=?", (json.dumps(ov, ensure_ascii=False), lid))
                    if "address" in ov or "city" in ov:
                        pipeline.geolocate(lid)
                pipeline.analyse(lid)
                return self._json(detail(lid))
            if p == "/api/params":
                body = json.loads(self._body() or b"{}")
                config.save(body)
                pipeline.analyse_all()
                return self._json({"ok": True, "params": config.load()})
            if p == "/api/params/reset":
                db.set_setting("params", {})
                pipeline.analyse_all()
                return self._json({"ok": True})
            if p == "/api/seen":
                db.set_setting("seen_run_id", pipeline.last_run_id())
                pipeline._seen_cache["t"] = 0
                return self._json({"ok": True})
            if p == "/api/automation":
                body = json.loads(self._body() or b"{}")
                if "heure" in body or "active" in body:
                    prm = config.load()
                    ra = automation.settings(prm)
                    ra.update({k: body[k] for k in ("heure", "active") if k in body})
                    ra["heure"] = max(0, min(23, int(ra["heure"])))
                    prm["recherche_auto"] = ra
                    config.save(prm)
                    automation.refresh_launch_agent()
                if body.get("action") == "install":
                    automation.install_launch_agent()
                elif body.get("action") == "uninstall":
                    automation.uninstall_launch_agent()
                return self._json({**automation.settings(), **automation.launch_agent_status()})
            if p == "/api/diagnostic":
                if pipeline.STATE["running"]:
                    raise ValueError("Une recherche est en cours : relancez le diagnostic quand elle sera finie.")
                body = json.loads(self._body() or b"{}")
                res = automation.run_diagnostic(body.get("source", "avoventes"), pipeline.criteria_deps(config.load()["criteres"]))
                return self._json({k: v for k, v in res.items() if k != "zip"})
            if p == "/api/import":
                lid = pipeline.manual_import(json.loads(self._body() or b"{}"))
                return self._json(detail(lid))
            if p == "/api/dvf/load":
                body = json.loads(self._body() or b"{}")
                res = [opendata.load_dvf_department(d) for d in body.get("deps", [])]
                loy = opendata.load_loyers()
                pipeline.analyse_all()
                return self._json({"dvf": res, "loyers": loy})
            if p == "/api/dvf/upload":
                name = self.headers.get("X-Filename", "dvf.csv")
                with tempfile.NamedTemporaryFile(suffix=".gz" if name.endswith(".gz") else ".csv", delete=False) as fh:
                    fh.write(self._body())
                n = opendata.import_dvf_file(fh.name)
                os.unlink(fh.name)
                pipeline.analyse_all()
                return self._json({"rows": n})
            return self._json({"error": "route inconnue"}, 404)
        except ValueError as e:
            return self._json({"error": str(e)}, 400)
        except Exception as e:
            return self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _documents(self, lid):
        """POST /api/listing/<id>/documents
        - JSON {"action": "analyse"} : télécharge (si robots.txt l'autorise) et analyse les documents liés ;
        - JSON {"text": "...", "label": "..."} : analyse un texte collé ;
        - corps binaire (PDF) avec en-tête X-Filename : analyse un fichier déposé."""
        row = db.q("SELECT data FROM listings WHERE id=?", (lid,))
        if not row:
            raise ValueError("annonce introuvable")
        data = json.loads(row[0]["data"])
        ctype = self.headers.get("Content-Type", "")
        raw = self._body()
        if "json" in ctype:
            body = json.loads(raw or b"{}")
            if body.get("action") == "analyse":
                documents.analyse_listing_documents(data, only_new=False)
            elif body.get("text"):
                label = body.get("label") or "Texte collé"
                rec = documents.analyse_bytes(body["text"].encode("utf-8"), label, "collé : " + label)
                data["doc_analyses"] = [d for d in data.get("doc_analyses", []) if d.get("origin") != rec["origin"]] + [rec]
            elif body.get("remove"):
                data["doc_analyses"] = [d for d in data.get("doc_analyses", []) if d.get("origin") != body["remove"]]
        else:
            name = urllib.parse.unquote(self.headers.get("X-Filename", "document.pdf"))
            rec = documents.analyse_bytes(raw, name, "déposé : " + name)
            data["doc_analyses"] = [d for d in data.get("doc_analyses", []) if d.get("origin") != rec["origin"]] + [rec]
        db.x("UPDATE listings SET data=? WHERE id=?", (json.dumps(data, ensure_ascii=False), lid))
        pipeline.analyse(lid)
        return detail(lid)

    def _static(self, p):
        if p in ("", "/"):
            p = "/index.html"
        path = os.path.normpath(os.path.join(STATIC, p.lstrip("/")))
        if not path.startswith(STATIC) or not os.path.isfile(path):
            path = os.path.join(STATIC, "index.html")
        with open(path, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", (mimetypes.guess_type(path)[0] or "application/octet-stream") + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


SCHED_PERIOD = 300


def scheduler():
    """Actualisation périodique optionnelle (Paramètres > actualisation automatique)."""
    time.sleep(20)  # au démarrage : si la recherche du matin n'a pas été faite (Mac éteint), elle part tout de suite
    while True:
        try:
            if not pipeline.STATE["running"] and automation.search_due():
                pipeline.run_search(trigger="automatique")
        except Exception as e:
            print("planificateur :", e)
        time.sleep(SCHED_PERIOD)


def serve(host="127.0.0.1", port=8765):
    if host not in ("127.0.0.1", "localhost", "::1") and not PASSWORD:
        raise SystemExit("Refus de démarrer : écoute réseau publique sans mot de passe. Définissez IMMORADAR_PASSWORD.")
    db.conn()
    automation.refresh_launch_agent()
    threading.Thread(target=scheduler, daemon=True).start()
    httpd = ThreadingHTTPServer((host, port), H)
    print(f"ImmoRadar prêt : http://{host}:{port}  (Ctrl+C pour arrêter)")
    httpd.serve_forever()
