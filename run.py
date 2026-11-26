#!/usr/bin/env python3
"""Lancement : python3 run.py  (ou double-clic sur « Lancer ImmoRadar.command » sur Mac)
Options : --port 8765  --host 127.0.0.1  --no-browser  --search (recherche sans interface puis quitte)"""
import argparse
import glob
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
import webbrowser

if sys.version_info < (3, 9):
    sys.exit("Python 3.9 ou plus récent est requis.")

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def _count_listings(path):
    try:
        import sqlite3
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        n = c.execute("SELECT COUNT(*) FROM listings").fetchone()[0]
        c.close()
        return n
    except Exception:
        return 0


def migrate_old_data(data_dir):
    """Reprend automatiquement les annonces d'une version précédente (dossier data/ d'un ancien zip).
    Se déclenche aussi si la base actuelle existe mais est vide (premier lancement raté, par exemple)."""
    if os.environ.get("IMMORADAR_DATA"):
        return
    target = os.path.join(data_dir, "immoradar.db")
    if os.path.exists(target) and _count_listings(target) > 0:
        return
    home = os.path.expanduser("~")
    cands = [os.path.join(HERE, "data", "immoradar.db")]
    for base in ("Downloads", "Desktop", "Documents", "Téléchargements", "Bureau"):
        for depth in ("*", "*/*", "*/*/*"):
            cands += glob.glob(os.path.join(home, base, depth, "data", "immoradar.db"))
    cands = [c for c in set(cands) if os.path.isfile(c) and _count_listings(c) > 0]
    if not cands:
        return
    src = max(cands, key=os.path.getmtime)
    os.makedirs(data_dir, exist_ok=True)
    for name in ("immoradar.db", "reference.db"):
        p = os.path.join(os.path.dirname(src), name)
        if os.path.isfile(p):
            for ext in ("-wal", "-shm"):  # fichiers WAL de l'ancienne base : à recopier avec elle, ou à effacer côté cible
                old_side, new_side = p + ext, os.path.join(data_dir, name + ext)
                if os.path.exists(new_side):
                    os.remove(new_side)
                if os.path.isfile(old_side):
                    shutil.copy2(old_side, new_side)
            shutil.copy2(p, os.path.join(data_dir, name))
    print(f"Données de la version précédente reprises depuis : {os.path.dirname(src)} ({_count_listings(target)} annonces)")


def port_free(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def other_immoradar(port):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=2)
        return True
    except Exception:
        return False


ap = argparse.ArgumentParser()
ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
ap.add_argument("--search", action="store_true", help="lancer une recherche sans interface puis quitter")
ap.add_argument("--no-browser", action="store_true")
ap.add_argument("--if-due", action="store_true", help="avec --search : seulement si la recherche du matin n'a pas encore été faite")
ap.add_argument("--test-pdf", metavar="FICHIER", help="tester la lecture d'un PDF puis quitter")
ap.add_argument("--diagnostic", metavar="SOURCE", nargs="?", const="avoventes",
                help="interroger une source (avoventes par défaut) et enregistrer les pages reçues dans un zip à m'envoyer")
a = ap.parse_args()

from immoradar import db  # noqa: E402

migrate_old_data(db.DATA_DIR)

from immoradar import documents, pipeline, server  # noqa: E402

if a.test_pdf:
    print("Lecteur :", documents.reader_label())
    with open(a.test_pdf, "rb") as fh:
        rec = documents.analyse_bytes(fh.read(), os.path.basename(a.test_pdf), a.test_pdf)
    print("Statut :", rec["status"], "|", rec.get("method") or "", "|", rec.get("message") or "")
    for f in rec.get("facts", []):
        print(f"  - {f['label']} : {f['value']}  (p.{f['page']})")
    for r in rec.get("risks", []):
        print(f"  ! {r['label']}  (p.{r['page']})")
    sys.exit(0)

if a.diagnostic:
    from immoradar import automation, config
    res = automation.run_diagnostic(a.diagnostic, pipeline.criteria_deps(config.load()["criteres"]))
    print(f"\nFichier à envoyer : {res['zip']}")
    if sys.platform == "darwin":
        os.system(f'open -R "{res["zip"]}"')
    sys.exit(0)

if a.search:
    if a.if_due:
        from immoradar import automation
        if not automation.search_due():
            print(time.strftime("%Y-%m-%d %H:%M"), "recherche du jour déjà faite (ou désactivée) : rien à faire")
            sys.exit(0)
    print(time.strftime("%Y-%m-%d %H:%M"), "recherche automatique")
    pipeline.run_search(trigger="automatique" if a.if_due else "manuelle")
    time.sleep(0.5)
    while pipeline.STATE["running"]:
        time.sleep(1)
    sys.exit(0)

port = a.port
if not port_free(a.host, port):
    if other_immoradar(port):
        print(f"\n⚠️  Une autre fenêtre ImmoRadar (peut-être l'ancienne version) tourne déjà sur le port {port}.")
        print("    Fermez-la (Ctrl+C dans son Terminal) pour éviter la confusion. Cette version démarre sur un autre port.\n")
    for p in range(port + 1, port + 30):
        if port_free(a.host, p):
            port = p
            break
    else:
        sys.exit(f"Aucun port libre entre {a.port} et {a.port + 29}.")

print(f"Données : {db.DATA_DIR}")
print(f"Lecture des documents : {documents.reader_label()}")
local = a.host in ("127.0.0.1", "localhost")
if local and not a.no_browser:
    threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{port}/")).start()
server.serve(a.host, port)
