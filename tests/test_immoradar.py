"""Tests ImmoRadar (python3 -m unittest discover tests).

IMPORTANT : les pages HTML et les ventes DVF ci-dessous sont des JEUX DE TEST construits pour vérifier
la logique (parsing, déduplication, calculs). Ce ne sont pas des annonces ni des ventes réelles, et ils
ne sont jamais écrits dans la base de l'application (les tests utilisent un dossier temporaire).
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

TMP = tempfile.mkdtemp(prefix="immoradar-test-")
os.environ["IMMORADAR_DATA"] = TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from immoradar import config, db, estimate, extract, finance, net, opendata, pipeline, scoring  # noqa: E402
from immoradar.collectors import licitor  # noqa: E402

FUTURE = date.today() + timedelta(days=40)
MOIS_FR = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
FUT_FR = f"jeudi {FUTURE.day} {MOIS_FR[FUTURE.month - 1]} {FUTURE.year}"

FIXTURE_ANNONCE = f"""<html><head><meta property="og:title" content="Un appartement, Paris 11ème, Paris">
<meta property="og:image" content="/data/pub/media/annonce/test.jpg"></head><body>
<p>Annonce publiée le 20 novembre 2026</p><h1>Annonce n°999001</h1>
<p>Tribunal Judiciaire de Paris (Paris)</p><p>Vente aux enchères publiques</p><p>{FUT_FR} à 14h</p>
<h2>Un appartement</h2><p>de 42,50 m², au 3ème étage avec ascenseur, comprenant : entrée, séjour, chambre, cuisine, salle d'eau. Cave.
Travaux à prévoir. Libre de toute occupation.</p>
<h3>Mise à prix : 180 000 €</h3>
<p>Paris 11ème</p><p>12 rue de la Roquette</p>
<a href="https://maps.google.fr/maps?q=48.8550,2.3730&z=13">carte</a>
<p>Visite sur place lundi 4 janvier 2027 à 10h</p>
<h3>Maître Jeanne Test, Avocat</h3>
<a href="/docs/ccv.pdf">Cahier des conditions de vente</a>
<p>Annonce non-officielle - Contenu non certifié</p></body></html>"""

ANNONCE_PATH = "/annonce/99/90/01/vente-aux-encheres/un-appartement/paris-11eme/paris/999001.html"
ANNONCE_PATH_LYON = "/annonce/99/90/02/vente-aux-encheres/une-maison/villeurbanne/rhone/999002.html"
ANNONCE_PATH_OTHER = "/annonce/99/90/03/vente-aux-encheres/une-maison/annecy/haute-savoie/999003.html"


class FakeSite(BaseHTTPRequestHandler):
    robots = "User-agent: *\nDisallow: /data/pub/\n"
    captcha = False

    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        if p == "/robots.txt":
            return self._send(self.robots, "text/plain")
        if self.captcha:
            return self._send("<html><title>Just a moment...</title><div class='cf-chl-widget'></div></html>", code=403)
        if p.startswith("/ventes-aux-encheres-immobilieres/paris-et-ile-de-france/prochaines-ventes.html"):
            if "p=2" in p:
                return self._send(f'<a href="{ANNONCE_PATH}">dup</a>')  # page 2 : rien de neuf -> arrêt
            return self._send(f'<a href="{ANNONCE_PATH}">A</a><a href="{ANNONCE_PATH_OTHER}">B</a>')
        if p.startswith("/ventes-aux-encheres-immobilieres/rhone-alpes.html"):
            return self._send(f'<a href="{ANNONCE_PATH_LYON}">C</a>')
        if p == ANNONCE_PATH:
            return self._send(FIXTURE_ANNONCE)
        if p == ANNONCE_PATH_LYON:
            return self._send(f"<p>Tribunal Judiciaire de Lyon (Rhône)</p><p>{FUT_FR} à 13h30</p><h2>Une maison</h2><p>de 95 m² 4 pièces, jardin. Occupée par le propriétaire saisi.</p><h3>Mise à prix : 150 000 €</h3>")
        return self._send("not found", code=404)

    def _send(self, body, ctype="text/html", code=200):
        b = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def start_fake():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeSite)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def seed_dvf_paris_11():
    """Ventes de test autour de (48.8550, 2.3730), ~10 000 €/m²."""
    rows = []
    base = date.today() - timedelta(days=200)
    for i in range(14):
        sfc = 35 + i * 1.5
        ppm = 9500 + (i % 5) * 250
        rows.append((f"T{i}", (base + timedelta(days=i * 7)).isoformat(), round(sfc * ppm), "Appartement", sfc, 2,
                     str(10 + i), "RUE DE LA ROQUETTE", "8270", "75011", "75111", "Paris 11e Arrondissement", "75",
                     48.8550 + i * 0.0003, 2.3730 + i * 0.0002, 1, None))
    rows.append(("TOUT", base.isoformat(), 2_000_000, "Appartement", 40, 2, "5", "RUE DE LA ROQUETTE", "8270", "75011", "75111",
                 "Paris 11e", "75", 48.8551, 2.3731, 1, None))  # valeur aberrante
    db.many("INSERT INTO dvf VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.x("INSERT OR REPLACE INTO dvf_files VALUES ('75', ?, 15, ?, 'test')", (str(base.year), db.now_iso()))
    for y in opendata.dvf_years():
        db.x("INSERT OR REPLACE INTO dvf_files VALUES ('75', ?, 0, ?, 'test')", (y, db.now_iso()))
    db.x("INSERT OR REPLACE INTO loyers VALUES ('75056','app12',30.0,26.0,35.0,5000,'Paris')")


class T01Extract(unittest.TestCase):
    def test_fields(self):
        t = "Appartement de 3 pièces de 64,20 m² (loi Carrez 63,10 m²) au 2e étage, balcon, sans ascenseur. Mise à prix : 210 000 €. Bien occupé. A rénover."
        d = extract.analyse_text(t)
        self.assertEqual(d["type"], "appartement")
        self.assertEqual(d["rooms"], 3)
        self.assertAlmostEqual(d["surface"], 63.10)
        self.assertEqual(d["surface_kind"], "Carrez")
        self.assertEqual(d["floor"], 2)
        self.assertEqual(d["mise_a_prix"], 210000)
        self.assertEqual(d["occupation"], "occupé")
        self.assertEqual(d["works_level"], "moyen")
        self.assertTrue(d["features"]["balcon"])
        self.assertFalse(d["features"]["ascenseur"])

    def test_unknowns_stay_none(self):
        d = extract.analyse_text("Bien à vendre aux enchères, renseignements auprès de l'avocat.")
        self.assertIsNone(d["surface"])
        self.assertIsNone(d["mise_a_prix"])
        self.assertIsNone(d["occupation"])
        self.assertIsNone(d["sale_date"])

    def test_dates_and_risks(self):
        self.assertEqual(extract.parse_fr_date("vendredi 8 janvier 2027 à 14h")[0], "2027-01-08")
        self.assertEqual(extract.parse_fr_date("mardi 1er décembre 2026 à 9h30"), ("2026-12-01", "09:30"))
        codes = {r["code"] for r in extract.risks("Bien occupé sans droit ni titre. Servitude de passage.")}
        self.assertIn("squat", codes)
        self.assertIn("servitude", codes)

    def test_terrain_surface_ignored(self):
        s, _ = extract.surface("Maison sur terrain de 800 m², surface habitable 110 m²")
        self.assertEqual(s, 110)


class T02Licitor(unittest.TestCase):
    def test_parse_detail(self):
        d = licitor.parse_detail(FIXTURE_ANNONCE, "https://www.licitor.com" + ANNONCE_PATH)
        self.assertEqual(d["source_id"], "999001")
        self.assertEqual(d["dep"], "75")
        self.assertEqual(d["type"], "appartement")
        self.assertEqual(d["surface"], 42.5)
        self.assertEqual(d["floor"], 3)
        self.assertEqual(d["mise_a_prix"], 180000)
        self.assertEqual(d["sale_date"], FUTURE.isoformat())
        self.assertEqual(d["sale_time"], "14:00")
        self.assertEqual(d["sale_kind"], "judiciaire")
        self.assertIn("Tribunal Judiciaire de Paris", d["sale_place"])
        self.assertEqual(d["occupation"], "libre")
        self.assertEqual(d["lat"], 48.855)
        self.assertEqual(d["address"], "12 rue de la Roquette")
        self.assertTrue(d["documents"][0]["url"].endswith("/docs/ccv.pdf"))
        self.assertTrue(d["photos"][0].endswith("test.jpg"))


class T03CollecteEtRecherche(unittest.TestCase):
    """Bouton de recherche de bout en bout contre un faux site local (robots.txt, pagination, filtre départements)."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = start_fake()
        cls.old_home = licitor.HOME
        licitor.HOME = cls.url
        cls.old_mods = dict(pipeline.COLLECTORS)
        pipeline.COLLECTORS.pop("avoventes")  # pas de réseau ici
        cls.old_delay = net.PoliteClient.__init__.__defaults__
        net.PoliteClient.__init__.__defaults__ = (0.0, 80)
        # pas de réseau : géocodage, DVF et loyers neutralisés pour ce test
        cls._geo, cls._rev = opendata.geocode, opendata.reverse
        opendata.geocode = lambda *a, **k: None
        opendata.reverse = lambda *a, **k: {"citycode": "75111", "postcode": "75011"}
        cls._dvf = opendata.load_dvf_department
        opendata.load_dvf_department = lambda d, **k: {"dep": d, "loaded": [], "errors": []}
        cls._loy = opendata.load_loyers
        opendata.load_loyers = lambda **k: {}
        seed_dvf_paris_11()

    @classmethod
    def tearDownClass(cls):
        licitor.HOME = cls.old_home
        pipeline.COLLECTORS.clear()
        pipeline.COLLECTORS.update(cls.old_mods)
        net.PoliteClient.__init__.__defaults__ = cls.old_delay
        opendata.geocode, opendata.reverse = cls._geo, cls._rev
        opendata.load_dvf_department, opendata.load_loyers = cls._dvf, cls._loy
        cls.srv.shutdown()

    def wait(self):
        time.sleep(0.2)
        while pipeline.STATE["running"]:
            time.sleep(0.1)

    def test_a_search_collect_dedup_estimate_rank(self):
        rid = pipeline.run_search()
        self.assertIsNotNone(rid)
        self.wait()
        run = db.q("SELECT * FROM runs WHERE id=?", (rid,))[0]
        self.assertEqual(run["status"], "terminée")
        srcs = {s["name"]: s for s in json.loads(run["sources"])}
        self.assertEqual(srcs["licitor"]["status"], "ok")
        self.assertEqual(srcs["licitor"]["found"], 2)      # Paris + Villeurbanne ; Annecy (74) écarté
        self.assertEqual(srcs["notaires"]["status"], "manuel")
        self.assertEqual(srcs["domaine"]["status"], "manuel")
        cards = pipeline.list_cards()
        self.assertEqual(len(cards), 2)
        paris = [c for c in cards if c["dep"] == "75"][0]
        self.assertEqual(paris["status"], "nouvelle")
        self.assertEqual(paris["estimation"]["status"], "ok")
        self.assertIsNotNone(paris["score_revente"])
        self.assertIsNotNone(paris["score_locatif"])
        # tri par intérêt : Paris (estimable) avant Villeurbanne (pas de DVF chargé => non notée)
        self.assertEqual(cards[0]["id"], paris["id"])
        an = json.loads(db.q("SELECT analysis FROM listings WHERE id=?", (paris["id"],))[0]["analysis"])
        self.assertEqual(an["estimation"]["n_outliers_removed"], 1)
        lyon = [c for c in cards if c["dep"] == "69"][0]
        self.assertEqual(lyon["estimation"]["status"], "insuffisant")
        self.assertIsNone(lyon["score_revente"])

        # 2e recherche : aucune nouvelle annonce, pas de doublon, statut "à venir"
        pipeline.run_search()
        self.wait()
        self.assertEqual(db.q("SELECT count(*) n FROM listings")[0]["n"], 2)
        paris2 = [c for c in pipeline.list_cards() if c["dep"] == "75"][0]
        self.assertEqual(paris2["status"], "à venir")

    def test_b_cross_source_dedup(self):
        n0 = db.q("SELECT count(*) n FROM listings")[0]["n"]
        lid = pipeline.manual_import({"source": "notaires", "url": "https://exemple.test/annonce-x", "city": "Paris 11ème",
                                      "postcode": "75011", "mise_a_prix": "180000", "sale_date": FUTURE.isoformat(),
                                      "texte": "Appartement 2 pièces de 42,5 m². Balcon. Mise à prix : 180 000 €"})
        self.assertEqual(db.q("SELECT count(*) n FROM listings")[0]["n"], n0)  # fusionné avec l'annonce Licitor
        data = json.loads(db.q("SELECT data FROM listings WHERE id=?", (lid,))[0]["data"])
        self.assertEqual({s["source"] for s in data["sources"]}, {"licitor", "manuel:notaires"})
        self.assertTrue(data["features"]["balcon"])            # complété par la 2e source
        self.assertEqual(data["prov"]["surface"], "licitor")    # la 1re source n'est pas écrasée

    def test_c_robots_disallow(self):
        FakeSite.robots = "User-agent: *\nDisallow: /\n"
        try:
            rid = pipeline.run_search()
            self.wait()
            srcs = {s["name"]: s for s in json.loads(db.q("SELECT sources FROM runs WHERE id=?", (rid,))[0]["sources"])}
            self.assertEqual(srcs["licitor"]["status"], "bloquée")
            self.assertIn("robots.txt", srcs["licitor"]["message"])
        finally:
            FakeSite.robots = "User-agent: *\nDisallow: /data/pub/\n"

    def test_d_captcha_detected_not_bypassed(self):
        FakeSite.captcha = True
        try:
            rid = pipeline.run_search()
            self.wait()
            srcs = {s["name"]: s for s in json.loads(db.q("SELECT sources FROM runs WHERE id=?", (rid,))[0]["sources"])}
            self.assertEqual(srcs["licitor"]["status"], "bloquée")
            self.assertIn("anti-robot", srcs["licitor"]["message"])
            # les annonces déjà collectées restent consultables
            self.assertGreaterEqual(len(pipeline.list_cards()), 2)
        finally:
            FakeSite.captcha = False

    def test_e_filters(self):
        crit = dict(config.load()["criteres"])
        self.assertEqual(len(pipeline.list_cards(crit)), 2)
        self.assertEqual(len(pipeline.list_cards({**crit, "zones": ["lyon"]})), 1)
        self.assertEqual(len(pipeline.list_cards({**crit, "types": ["maison"]})), 1)
        self.assertEqual(len(pipeline.list_cards({**crit, "surface_min": 50})), 1)
        self.assertEqual(len(pipeline.list_cards({**crit, "mise_a_prix_max": 160000})), 1)
        self.assertEqual(len(pipeline.list_cards({**crit, "occupation": "libre"})), 1)
        self.assertEqual(len(pipeline.list_cards({**crit, "exterieurs": ["jardin"]})), 1)
        self.assertEqual(len(pipeline.list_cards({**crit, "villes": ["Villeurbanne"]})), 1)
        self.assertEqual(len(pipeline.list_cards({**crit, "types_vente": ["domaniale"]})), 0)
        self.assertEqual(len(pipeline.list_cards({**crit, "date_max": (FUTURE - timedelta(days=1)).isoformat(), "inclure_inconnus": False})), 0)


class T04Finance(unittest.TestCase):
    def test_emoluments(self):
        # 6500*7.256% + 10500*2.993% + 43000*1.995% + 40000*1.497% = 2242.56 HT -> x1.2
        self.assertAlmostEqual(finance.emoluments_vente_judiciaire(100000), 2242.56 * 1.2, places=0)

    def test_simulation_and_maxbid(self):
        c = config.DEFAULTS["calculs"]
        item = {"surface": 40, "mise_a_prix": 200000, "sale_kind": "judiciaire", "works_level": "léger", "type": "appartement"}
        est = {"status": "ok", "valeur": {"bas": 360000, "central": 400000, "haut": 440000}}
        rent = {"loyer_m2": 30, "bas": 26, "haut": 35, "nb_obs": 100}
        s = finance.simulate(item, est, rent, c)
        ar, lo = s["achat_revente"], s["locatif"]
        self.assertGreater(ar["benefice_net"], 0)
        # au prix max conseillé, la rentabilité vaut la cible (à l'arrondi près)
        r = finance._resale(ar["prix_max_conseille"], item, 400000, c)
        self.assertAlmostEqual(r["net"] / r["cout"] * 100, c["marge_cible_pct"], delta=0.1)
        self.assertEqual(lo["loyer_mensuel"], round(30 * 40 * 0.9))
        self.assertAlmostEqual(lo["rendement_brut_sur_prix_pct"], 1080 * 12 / 200000 * 100, places=1)
        self.assertEqual(lo["cashflow_mensuel"], round(finance._rental(200000, item, 1080, c)["net"] / 12))  # apport 100 %
        # chaque ligne a une nature explicite
        self.assertTrue(all(l["nature"] for l in ar["lines"] + lo["lines"]))

    def test_no_price_no_simulation(self):
        s = finance.simulate({"surface": 40}, None, None, config.DEFAULTS["calculs"])
        self.assertIsNone(s["achat_revente"])
        self.assertIn("Mise à prix inconnue", s["warnings"][0])

    def test_no_rent_no_rental(self):
        s = finance.simulate({"surface": 40, "mise_a_prix": 100000}, None, None, config.DEFAULTS["calculs"])
        self.assertIsNone(s["locatif"])


class T05Scoring(unittest.TestCase):
    def test_legal_risk_caps_score(self):
        item = {"surface": 40, "occupation": "libre", "works_level": "aucun", "type": "appartement", "sale_date": "2030-01-01",
                "rooms": 2, "geo_precision": "adresse", "risks": []}
        est = {"status": "ok", "confidence": 90, "valeur": {"central": 400000}}
        sim = {"achat_revente": {"prix_envisage": 150000, "marge_nette_pct": 40, "prix_max_conseille": 300000}, "locatif": None}
        good = scoring.score(item, est, sim)["revente"]["score"]
        self.assertGreater(good, 70)
        item["risks"] = [{"code": "usufruit", "label": "Démembrement", "severity": 3}]
        bad = scoring.score(item, est, sim)["revente"]
        self.assertLessEqual(bad["score"], 40)
        self.assertIsNotNone(bad["cap"])


class T06Estimate(unittest.TestCase):
    def test_insufficient(self):
        p = config.DEFAULTS
        self.assertEqual(estimate.estimate({"type": "appartement", "dep": "75"}, p)["status"], "insuffisant")
        r = estimate.estimate({"type": "appartement", "surface": 40, "dep": "13", "code_commune": "13055"}, p)
        self.assertEqual(r["status"], "insuffisant")
        self.assertIn("DVF", r["reasons"][0])

    def test_dvf_csv_parsing(self):
        hdr = "id_mutation,date_mutation,numero_disposition,nature_mutation,valeur_fonciere,adresse_numero,adresse_suffixe,adresse_nom_voie,adresse_code_voie,code_postal,code_commune,nom_commune,code_departement,ancien_code_commune,ancien_nom_commune,id_parcelle,ancien_id_parcelle,numero_volume,lot1_numero,lot1_surface_carrez,nombre_lots,code_type_local,type_local,surface_reelle_bati,nombre_pieces_principales,code_nature_culture,nature_culture,code_nature_culture_speciale,nature_culture_speciale,surface_terrain,longitude,latitude"
        rows = [
            "M1,2025-03-01,1,Vente,300000,10,,RUE X,1,75011,75111,Paris 11,75,,,p,,,1,30,1,2,Appartement,31,2,,,,,,2.37,48.85",
            "M1,2025-03-01,1,Vente,300000,10,,RUE X,1,75011,75111,Paris 11,75,,,p,,,2,,1,3,Dépendance,,0,,,,,,2.37,48.85",
            "M2,2025-03-02,1,Vente,600000,12,,RUE X,1,75011,75111,Paris 11,75,,,p,,,1,30,2,2,Appartement,30,2,,,,,,2.37,48.85",
            "M2,2025-03-02,1,Vente,600000,12,,RUE X,1,75011,75111,Paris 11,75,,,p,,,1,30,2,2,Appartement,32,2,,,,,,2.37,48.85",
            "M3,2025-03-03,1,Echange,300000,14,,RUE X,1,75011,75111,Paris 11,75,,,p,,,1,30,1,2,Appartement,31,2,,,,,,2.37,48.85",
        ]
        out = opendata.parse_dvf_csv(hdr + "\n" + "\n".join(rows), "75")
        self.assertEqual([r[0] for r in out], ["M1"])  # M2 multi-locaux et M3 échange écartés
        self.assertEqual(out[0][16], 30.0)            # surface Carrez lue

    def test_loyers_csv_parsing(self):
        text = 'id_zone;INSEE_C;LIBGEO;EPCI;DEP;REG;loypredm2;lwr.IPm2;upr.IPm2;TYPPRED;nbobs_com;nbobs_mail;R2_adj\n1;75056;Paris;x;75;11;30,5;25,1;36,2;commune;5000;6000;0,8\n'
        rows = opendata.parse_loyers_csv(text, "app")
        self.assertEqual(rows[0][:4], ("75056", "app", 30.5, 25.1))


class T07Http(unittest.TestCase):
    def test_api(self):
        from immoradar import server
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            st = json.loads(urllib.request.urlopen(base + "/api/state").read())
            self.assertIn("matching", st)
            html = urllib.request.urlopen(base + "/").read().decode()
            self.assertIn("ImmoRadar", html)
            lst = json.loads(urllib.request.urlopen(base + "/api/listings?sort=prix").read())
            if lst:
                d = json.loads(urllib.request.urlopen(base + f"/api/listing/{lst[0]['id']}").read())
                self.assertIn("analysis", d)
                req = urllib.request.Request(base + f"/api/listing/{lst[0]['id']}", data=json.dumps({"overrides": {"loyer_manuel": "1500"}}).encode(), method="POST")
                d2 = json.loads(urllib.request.urlopen(req).read())
                if d2["analysis"]["simulation"].get("locatif"):
                    self.assertEqual(d2["analysis"]["simulation"]["locatif"]["loyer_mensuel"], 1500)
        finally:
            srv.shutdown()


def tearDownModule():
    shutil.rmtree(TMP, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class T08Auth(unittest.TestCase):
    def test_password(self):
        import base64
        from immoradar import server
        old = server.PASSWORD
        server.PASSWORD = "s3cret"
        srv = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(base + "/api/state")
            self.assertEqual(ctx.exception.code, 401)
            req = urllib.request.Request(base + "/api/state", headers={"Authorization": "Basic " + base64.b64encode(b"immoradar:s3cret").decode()})
            self.assertEqual(urllib.request.urlopen(req).status, 200)
            self.assertEqual(urllib.request.urlopen(base + "/healthz").status, 200)
        finally:
            server.PASSWORD = old
            srv.shutdown()


# Extraits du PV de description du 02/07/2026 (70 bd Vincent Auriol, Paris 13e) transmis par l'utilisateur,
# découpés par page comme dans le document (pages 3, 4, 5, 9, 13, 23, 24, 27).
PV_PAGES = [""] * 2 + [
    "1. DESIGNATION DU BIEN SAISI\nLe lot numéro SIX (6) de l'état descriptif de division :\nUne CHAMBRE et une salle de bains avec water-closet au premier étage, "
    "comprenant un droit à la jouissance exclusive d'un balcon.\nConstituant le lot d'enchères sur la mise à prix : DIX MILLE EUROS (10.000,00€).",
    "Que les acquéreurs ont engagé une action en nullité de la vente et que par jugement du 2 mai 2002 ... a prononcé la nullité de la vente.\n"
    "5. CONDITIONS D'OCCUPATION\nLe lot n°6 comprenant la chambre et la salle de bains avec water-closet est vide et libre de toute occupation.\n"
    "6. DESCRIPTION DU LOT N°6\nIl s'agit d'une chambre d'environ 8m2, avec une salle de bain et un water-closet, située dans une résidence sociale.",
    "De manière générale, l'appartement est en état d'usage.\nL'entrée est éclairée par un spot lumineux non fonctionnel au moment des constatations.",
    "", "", "",
    "Le sol est recouvert d'un carrelage à l'état d'usage, dont un carreau à l'entrée est fissuré.\nIl est d'aspect moderne mais vétuste. La bonde est absente.",
    "Les trappes de visite carrelées sont cassées et fissurées.", "", "",
    "La chambre dispose d'un balcon filant partagé avec deux autres chambres.\nAu sol, la chape en béton comporte plusieurs fissurations.",
] + [""] * 9 + [
    "LIQUIDATION JUDICIAIRE\nQue selon le site internet « Meilleurs Agents » le prix moyen du mètre carré retenu est de 6.440 € ; qu'il ressort d'annonces "
    "immobilières qu'une pièce de 8m² (chambre avec salle de bains) est proposée à la vente à 65.000 €.\n"
    "Que d'après la demande de renseignements certifié au 16 mars 2026, ces biens et droits immobiliers ne sont grevés d'aucune inscription d'hypothèque.",
    "Les adjudications réalisées en application des alinéas qui précèdent emportent purge des hypothèques.",
    "", "",
    "ORDONNONS la vente aux enchères publiques EN UN LOT D'ENCHERES\nSUR LA MISE A PRIX DE : DIX MILLE EUROS (10.000 €) AVEC FACULTE DE BAISSE D'UN TIERS PUIS DE MOITIE EN CAS DE CARENCE D'ENCHERES",
]


class T09Documents(unittest.TestCase):
    def test_pv_analysis(self):
        from immoradar import documents
        r = documents.analyse_pages(PV_PAGES, "PV descriptif")
        self.assertEqual(r["kind"], "PV descriptif")
        f = {x["field"]: x for x in r["facts"]}
        self.assertEqual(f["occupation"]["value"], "libre")
        self.assertEqual(f["occupation"]["page"], 4)
        self.assertEqual(f["works_level"]["value"], "léger")       # « état d'usage » + plusieurs défauts
        self.assertEqual(f["surface_document"]["value"], 8)
        self.assertFalse(f["surface_document"]["apply"])           # « environ » : affiché, n'écrase pas la surface
        self.assertIn("lots_count", f)                             # « en un lot d'enchères »
        self.assertIn("baisse", f["baisse_mise_a_prix"]["value"].lower())
        self.assertEqual(f["prix_m2_document"]["value"], 6440)
        self.assertEqual(f["comparable_document"]["value"], 65000)
        codes = {x["code"]: x for x in r["risks"]}
        for c in ("liquidation", "nullite", "residence_sociale", "chambre", "hypotheque_ok", "purge", "balcon_partage"):
            self.assertIn(c, codes)
        self.assertEqual(codes["hypotheque_ok"]["severity"], 0)

    def test_apply_and_conflicts(self):
        from immoradar import documents
        rec = documents.analyse_bytes("\f".join(PV_PAGES).encode(), "PV descriptif", "collé")
        self.assertEqual(rec["status"], "analysé")
        item = {"surface": 16.18, "occupation": None, "lots_count": 2, "mise_a_prix": 10000, "risks": [], "doc_analyses": [rec], "prov": {}}
        conflicts = documents.apply_to_item(item)
        self.assertEqual(item["occupation"], "libre")
        self.assertIn("PV descriptif p.4", item["prov"]["occupation"])
        self.assertIsNone(item["lots_count"])
        self.assertEqual(item["surface"], 16.18)
        self.assertTrue(any("Surface" in c for c in conflicts))

    def test_baisse_not_multilot(self):
        d = extract.analyse_text("Mise à prix : 10 000 € avec faculté de baisse d'un tiers puis de moitié. Mise à prix : 6 667 €")
        self.assertIsNone(d["lots_count"])
        self.assertIsNotNone(d["baisse_mise_a_prix"])
        d2 = extract.analyse_text("Lot 1 Mise à prix : 50 000 €. Lot 2 Mise à prix : 30 000 €")
        self.assertEqual(d2["lots_count"], 2)

    def test_scanned_pdf_without_text(self):
        from immoradar import documents
        if not documents.tools()["pdftotext"]:
            self.skipTest("pdftotext absent")
        # PDF minimal sans texte (une page vide) : doit être signalé comme illisible si pas d'OCR
        pdf = (b"%PDF-1.1\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj "
               b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF")
        rec = documents.analyse_bytes(pdf, "scan", "test")
        if not documents.tools()["tesseract"]:
            self.assertEqual(rec["status"], "illisible")

    def test_dpe_g_blocks_rental_only(self):
        from immoradar import documents
        r = documents.analyse_pages(["Diagnostic de performance énergétique\nClasse énergie : G"], "DPE")
        risk = [x for x in r["risks"] if x["code"] == "dpe_g"][0]
        item = {"risks": [risk], "occupation": "libre", "works_level": "aucun"}
        est = {"status": "ok", "confidence": 80, "valeur": {"central": 300000}}
        sim = {"achat_revente": {"prix_envisage": 100000, "marge_nette_pct": 40, "prix_max_conseille": 200000},
               "locatif": {"prix_envisage": 100000, "rendement_net_pct": 9, "cashflow_mensuel": 300, "prix_max_conseille": 200000}}
        s = scoring.score(item, est, sim)
        self.assertIsNone(s["revente"]["cap"])
        self.assertLessEqual(s["locatif"]["score"], 40)


class T10MacReader(unittest.TestCase):
    def test_parse_mac_output(self):
        from immoradar import documents
        out = ("<<<PAGE 1 texte>>>\nLigne A\nLigne B\n<<<PAGE 2 ocr>>>\n"
               "1200\t5000\tsuite de la ligne\n9000\t1000\tTitre en haut\n1210\t1000\tdébut de la ligne\n<<<ERREUR OCR x>>>")
        pages, n = documents.parse_mac_output(out)
        self.assertEqual(n, 1)
        self.assertEqual(pages[0], "Ligne A\nLigne B")
        self.assertEqual(pages[1].split("\n"), ["Titre en haut", "début de la ligne", "suite de la ligne"])


class T11Avis(unittest.TestCase):
    def _item(self):
        from immoradar import documents
        rec = documents.analyse_bytes("\f".join(PV_PAGES).encode(), "PV descriptif", "collé")
        item = {"type": "appartement", "surface": 16.18, "rooms": 1, "city": "Paris 13e", "mise_a_prix": 10000, "sale_kind": "judiciaire",
                "occupation": None, "risks": [], "doc_analyses": [rec], "prov": {}}
        item["doc_conflicts"] = documents.apply_to_item(item)
        return item

    def test_avis_pv(self):
        from immoradar import avis
        item = self._item()
        params = config.load()
        est = {"status": "ok", "valeur": {"bas": 130000, "central": 150000, "haut": 170000}, "n_comparables": 12,
               "level": "à moins de 300 m", "confidence_label": "moyenne"}
        sim = finance.simulate(item, est, None, params["calculs"])
        sc = scoring.score(item, est, sim)
        a = avis.build(item, est, sim, sc, params)
        txt = json.dumps(a, ensure_ascii=False)
        print("\n" + a["verdict"])
        self.assertEqual(a["valeur_prudente"], 51520)                     # 6 440 €/m² × 8 m² < comparable 65 000 €
        self.assertTrue(any(p["nature"] == "confirmé" and p["ref"] and "p." in p["ref"] for p in a["sections"]["nature"]))
        self.assertIn("résidence sociale", txt.lower())
        self.assertIn("Vente en un seul lot.", [p["text"] for p in a["sections"]["positifs"]])
        self.assertIn("Aucune hypothèque inscrite.", [p["text"] for p in a["sections"]["positifs"]])
        self.assertTrue(a["verdict"].startswith(("À éviter", "Bien piège")))
        self.assertTrue(a["plafond"] is None or a["plafond"] < 51520)

    def test_avis_without_docs(self):
        from immoradar import avis
        params = config.load()
        item = {"type": "appartement", "surface": 40, "city": "Lyon", "mise_a_prix": 50000, "risks": [], "prov": {}}
        a = avis.build(item, {"status": "insuffisant"}, {}, {}, params)
        self.assertEqual(a["plafond_txt"], "Prix plafond non calculable : valeur ou surface inconnue.")
        self.assertTrue(any("Aucun document" in p["text"] for p in a["sections"]["vigilance"]))
        self.assertTrue(any("Aucune valeur fiable" in p["text"] for p in a["sections"]["valeur"]))
