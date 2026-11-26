#!/usr/bin/env python3
"""Lancement : python3 run.py  puis ouvrir http://127.0.0.1:8765
Options : --port 8765  --host 127.0.0.1  --search (lance une recherche en ligne de commande puis quitte)"""
import argparse
import os
import sys
import time

if sys.version_info < (3, 9):
    sys.exit("Python 3.9 ou plus récent est requis.")

from immoradar import pipeline, server  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
ap.add_argument("--search", action="store_true", help="lancer une recherche sans interface puis quitter")
a = ap.parse_args()

if a.search:
    pipeline.run_search()
    time.sleep(0.5)
    while pipeline.STATE["running"]:
        time.sleep(1)
    sys.exit(0)
server.serve(a.host, a.port)
