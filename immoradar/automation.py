"""Recherche automatique du matin, lancement automatique sur Mac (LaunchAgent) et diagnostic d'une source.

- Dans l'application ouverte : le planificateur lance la recherche une fois par jour, à partir de l'heure choisie.
- Application fermée (Mac) : un LaunchAgent, installé d'un clic dans Paramètres, lance « run.py --search --if-due »
  chaque matin à l'heure choisie (et au réveil du Mac s'il dormait à cette heure-là). L'application est copiée
  dans le dossier de données, car macOS empêche les tâches de fond de lire le dossier Téléchargements.
- Une seule recherche par jour au maximum, quel que soit le déclencheur : la table « runs » est partagée.
"""
import os
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime

from . import config, db, net

LABEL = "fr.immoradar.recherche-du-matin"
APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def settings(params=None):
    p = params or config.load()
    ra = p.get("recherche_auto") or {}
    return {"active": bool(ra.get("active", True)), "heure": int(ra.get("heure", 7))}


def search_due(params=None, now=None):
    """Vrai si la recherche du jour n'a pas encore été lancée et que l'heure choisie est passée."""
    s = settings(params)
    if not s["active"]:
        return False
    now = now or datetime.now().astimezone()
    if now.hour < s["heure"]:
        return False
    since = now.replace(hour=s["heure"], minute=0, second=0, microsecond=0)
    last = db.q("SELECT max(started_at) s FROM runs")[0]["s"]
    return not last or datetime.fromisoformat(last) < since


# ------------------------------------------------------------------ LaunchAgent (macOS)

def _plist_path():
    return os.path.expanduser(f"~/Library/LaunchAgents/{LABEL}.plist")


def _auto_copy_dir():
    return os.path.join(db.DATA_DIR, "app-recherche-auto")


def mac_available():
    return sys.platform == "darwin" and not os.environ.get("DATABASE_URL")


def launch_agent_status():
    if not mac_available():
        return {"available": False, "installed": False}
    return {"available": True, "installed": os.path.exists(_plist_path()), "log": os.path.join(db.DATA_DIR, "recherche-auto.log")}


def _copy_app():
    dest = _auto_copy_dir()
    if os.path.isdir(dest):
        shutil.rmtree(dest)
    shutil.copytree(APP_DIR, dest, ignore=shutil.ignore_patterns("__pycache__", "data", "tests", ".git", "*.zip"))
    return dest


def install_launch_agent():
    if not mac_available():
        raise RuntimeError("Le lancement automatique avec l'application fermée n'existe que sur Mac.")
    s = settings()
    app = _copy_app()
    log = os.path.join(db.DATA_DIR, "recherche-auto.log")
    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, os.path.join(app, "run.py"), "--search", "--if-due"],
        "WorkingDirectory": app,
        "StartCalendarInterval": {"Hour": s["heure"], "Minute": 0},
        "EnvironmentVariables": {"IMMORADAR_DATA": db.DATA_DIR},
        "StandardOutPath": log, "StandardErrorPath": log,
        "ProcessType": "Background",
    }
    path = _plist_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    subprocess.run(["launchctl", "unload", path], capture_output=True)
    with open(path, "wb") as fh:
        plistlib.dump(plist, fh)
    r = subprocess.run(["launchctl", "load", "-w", path], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"launchctl : {r.stderr.strip() or r.stdout.strip()}")
    return launch_agent_status()


def uninstall_launch_agent():
    path = _plist_path()
    if os.path.exists(path):
        subprocess.run(["launchctl", "unload", "-w", path], capture_output=True)
        os.remove(path)
    if os.path.isdir(_auto_copy_dir()):
        shutil.rmtree(_auto_copy_dir())
    return launch_agent_status()


def refresh_launch_agent():
    """Au démarrage : si le lancement automatique est installé, on le remet à jour (nouvelle version, nouvelle heure)."""
    if launch_agent_status().get("installed") and os.path.abspath(APP_DIR) != os.path.abspath(_auto_copy_dir()):
        try:
            install_launch_agent()
        except Exception as e:
            print("Lancement automatique : mise à jour impossible :", e)


# ------------------------------------------------------------------ diagnostic d'une source

def run_diagnostic(source, deps, log=print):
    """Interroge une source automatique en lisant peu de pages, enregistre les pages reçues et produit un zip."""
    from .collectors import ALL
    mod = ALL.get(source)
    if not mod or mod.MODE != "auto":
        raise ValueError(f"Source inconnue ou non automatique : {source}")
    stamp = time.strftime("%Y%m%d-%H%M%S")
    folder = os.path.join(db.DATA_DIR, "diagnostics", f"diagnostic-{source}-{stamp}")
    old_dir, old_max = net.DIAG_DIR, getattr(mod, "MAX_DETAILS", None)
    net.DIAG_DIR = folder
    if old_max:
        mod.MAX_DETAILS = min(old_max, 5)
    lines, n = [], None
    out = lambda m: (lines.append(str(m)), log(m))  # noqa: E731
    try:
        items = mod.collect(deps, {}, out)
        n = len(items)
        out(f"Annonces reconnues : {n}")
    except Exception as e:
        out(f"Arrêt : {type(e).__name__}: {e}")
    finally:
        net.DIAG_DIR = old_dir
        if old_max:
            mod.MAX_DETAILS = old_max
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "journal.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    pages = len([f for f in os.listdir(folder) if f.endswith(".html")])
    zpath = shutil.make_archive(folder, "zip", folder)
    shutil.rmtree(folder, ignore_errors=True)
    return {"zip": zpath, "name": os.path.basename(zpath), "pages": pages, "items": n, "log": lines}


def diagnostic_file(name):
    base = os.path.join(db.DATA_DIR, "diagnostics")
    path = os.path.join(base, os.path.basename(name))
    return path if name.endswith(".zip") and os.path.isfile(path) else None
