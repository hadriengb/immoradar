"""Collecteurs indépendants, un par source.

Chaque collecteur expose :
  NAME, LABEL, HOME, MODE ("auto" | "manuel"), LEGAL (constat robots.txt / CGU)
  collect(criteria_deps, known, log) -> list[dict]   (uniquement pour MODE == "auto")
  manual_links(deps) -> list[{label, url}]            (liens de recherche à ouvrir soi-même)
"""
from . import alertes_email, avoventes, classiques, domaine, infoencheres, licitor, notaires, streamestate, vench

ALL = {m.NAME: m for m in (licitor, vench, alertes_email, streamestate, avoventes, notaires, domaine, infoencheres, classiques)}
