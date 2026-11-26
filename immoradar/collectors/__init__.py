"""Collecteurs indépendants, un par source.

Chaque collecteur expose :
  NAME, LABEL, HOME, MODE ("auto" | "manuel"), LEGAL (constat robots.txt / CGU)
  collect(criteria_deps, known, log) -> list[dict]   (uniquement pour MODE == "auto")
  manual_links(deps) -> list[{label, url}]            (liens de recherche à ouvrir soi-même)
"""
from . import avoventes, domaine, licitor, notaires

ALL = {m.NAME: m for m in (licitor, avoventes, notaires, domaine)}
