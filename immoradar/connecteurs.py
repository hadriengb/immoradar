"""Réglages des connecteurs (alertes email, Stream Estate) : stockés à part des paramètres de calcul,
jamais renvoyés en clair à l'interface (mot de passe et clé masqués)."""
from . import db, pipeline
from .collectors import alertes_email, streamestate

MASK = "••••••••"
SECRETS = {"alertes_email": "mot_de_passe", "streamestate": "cle_api"}


def _stored():
    return db.get_setting("connecteurs", {}) or {}


def public():
    st = _stored()
    out = {}
    for name, mod in (("alertes_email", alertes_email), ("streamestate", streamestate)):
        s = mod.settings(st)
        sec = SECRETS[name]
        out[name] = {**s, sec: MASK if s.get(sec) else "", "configure": bool(s.get(sec))}
    return out


def save_and_test(body):
    st = _stored()
    for name in ("alertes_email", "streamestate"):
        if name in body:
            new = dict(body[name])
            sec = SECRETS[name]
            if new.get(sec) in (MASK, None):
                new[sec] = (st.get(name) or {}).get(sec, "")      # champ masqué non modifié : on garde l'ancien
            if name == "streamestate" and isinstance(new.get("communes_insee"), str):
                new["communes_insee"] = [c.strip() for c in new["communes_insee"].split(",") if c.strip()]
            st[name] = {**(st.get(name) or {}), **new}
    db.set_setting("connecteurs", st)
    res = {"ok": True, **public()}
    test = body.get("test")
    try:
        if test == "alertes_email":
            msgs = alertes_email.fetch_imap(alertes_email.settings(st), log=lambda m: None)
            found = sum(len(alertes_email.parse_message(m)) for m in msgs)
            res["test"] = f"Connexion réussie : {len(msgs)} email(s) d'alerte, {found} annonce(s) reconnue(s). Elles seront importées à la prochaine recherche."
        elif test == "streamestate":
            items = streamestate.search({**streamestate.settings(st), "max_annonces": 5}, None, log=lambda m: None)
            res["test"] = f"Clé acceptée : {len(items)} annonce(s) reçue(s) en test" + (f", ex. {items[0]['title']} — {items[0].get('city') or 'ville inconnue'}" if items else "") + "."
    except Exception as e:
        txt = str(e)
        if "AUTHENTICATIONFAILED" in txt or "Invalid credentials" in txt or "LOGIN failed" in txt:
            txt = ("identifiants refusés. Avec Gmail, iCloud, Outlook ou Yahoo, il faut un « mot de passe d'application » "
                   "(créé dans les réglages de sécurité du compte), pas votre mot de passe habituel.")
        res["test_error"] = txt
    return res


def import_eml(raw, name):
    items = alertes_email.parse_message(raw)
    ids = [pipeline._finish_import(d) for d in items]
    return {"fichier": name, "annonces": len(ids), "ids": ids}
