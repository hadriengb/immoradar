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
        if p.startswith("/ventes-aux-encheres-par-departement-Essonne.html"):
            if "p=2" in p:
                return self._send('<a href="/vente-167305-une-maison-evry.html">m</a>')
            return self._send(VENCH_LIST)
        if p == "/vente-167303-un-appartement-de-2158-m-grigny.html":
            return self._send(VENCH_DETAIL)
        if p == "/vente-167305-une-maison-evry.html":
            return self._send(VENCH_DETAIL.replace("13 000.00", "90 000.00").replace("Un APPARTEMENT de 21,58 m²", "Une MAISON de 80 m²"))
        if p == "/docs/pv-descriptif.pdf":
            return self._send(make_pdf(PV_PAGES), "application/pdf")
        if p == ANNONCE_PATH_LYON:
            return self._send(f"<p>Tribunal Judiciaire de Lyon (Rhône)</p><p>{FUT_FR} à 13h30</p><h2>Une maison</h2><p>de 95 m² 4 pièces, jardin. Occupée par le propriétaire saisi.</p><h3>Mise à prix : 150 000 €</h3>")
        return self._send("not found", code=404)

    def _send(self, body, ctype="text/html", code=200):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def make_pdf(pages):
    """PDF texte minimal (une page par élément), construit à la main : aucune dépendance."""
    def esc(t):
        return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"]
    kids = []
    for txt in pages:
        lines = []
        for para in (txt or " ").split("\n"):
            while len(para) > 95:
                cut = para.rfind(" ", 0, 95)
                cut = cut if cut > 0 else 95
                lines.append(para[:cut])
                para = para[cut:].lstrip()
            lines.append(para)
        ops = "BT /F1 9 Tf 11 TL 40 800 Td " + " ".join(f"({esc(line)}) Tj T*" for line in lines) + " ET"
        stream = ops.encode("cp1252", "replace")
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 3 0 R >> >> /Contents {len(objs)} 0 R >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offs = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n".encode() + (o if isinstance(o, bytes) else o.encode()) + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offs)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


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
        pipeline.COLLECTORS.pop("vench")
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


class T12LinkedDocuments(unittest.TestCase):
    """Chemin complet du bouton « Analyser les documents liés » : robots.txt, téléchargement du PDF, lecture, avis."""

    @classmethod
    def setUpClass(cls):
        cls.srv, cls.url = start_fake()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_download_and_analyse(self):
        from immoradar import documents
        if not documents.tools()["pdftotext"] and not documents.tools()["macos"]:
            self.skipTest("aucun lecteur PDF sur cette machine")
        data = {"documents": [{"label": "PV descriptif", "url": self.url + "/docs/pv-descriptif.pdf"},
                              {"label": "Cahier des conditions de vente", "url": self.url + "/data/pub/ccv.pdf"}]}
        n = documents.analyse_listing_documents(data, only_new=False)
        self.assertEqual(n, 2)
        recs = {d["label"]: d for d in data["doc_analyses"]}
        pv = recs["PV descriptif"]
        self.assertEqual(pv["status"], "analysé", pv.get("message"))
        f = {x["field"]: x for x in pv["facts"]}
        self.assertEqual(f["occupation"]["value"], "libre")
        self.assertEqual(f["occupation"]["page"], 4)
        self.assertEqual(f["prix_m2_document"]["value"], 6440)
        self.assertIn("residence_sociale", {r["code"] for r in pv["risks"]})
        ccv = recs["Cahier des conditions de vente"]                  # interdit par robots.txt : refus propre, pas une erreur
        self.assertEqual(ccv["status"], "non téléchargé")
        self.assertIn("robots.txt", ccv["message"])

    def test_button_through_api(self):
        """Même chose via l'API du serveur, comme le bouton de la fiche, jusqu'à l'avis."""
        from immoradar import documents, server
        if not documents.tools()["pdftotext"] and not documents.tools()["macos"]:
            self.skipTest("aucun lecteur PDF sur cette machine")
        lid = pipeline.manual_import({"title": "Test documents liés", "type": "appartement", "surface": "16.18", "city": "Paris 13e",
                                      "mise_a_prix": "10000", "url": self.url + "/annonce-test"})
        row = db.q("SELECT data FROM listings WHERE id=?", (lid,))[0]
        data = json.loads(row["data"])
        data["documents"] = [{"label": "PV descriptif", "url": self.url + "/docs/pv-descriptif.pdf"}]
        db.x("UPDATE listings SET data=? WHERE id=?", (json.dumps(data), lid))
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{httpd.server_address[1]}"
            req = urllib.request.Request(f"{base}/api/listing/{lid}/documents", data=b'{"action": "analyse"}',
                                         headers={"Content-Type": "application/json"}, method="POST")
            d = json.load(urllib.request.urlopen(req, timeout=60))
        finally:
            httpd.shutdown()
        recs = d["item"]["doc_analyses"]
        self.assertEqual(recs[0]["status"], "analysé", recs[0].get("message"))
        self.assertEqual(d["item"]["occupation"], "libre")
        avis = d["analysis"]["avis"]
        self.assertEqual(avis["valeur_prudente"], 51520)
        self.assertTrue(any(p["ref"] and "p." in p["ref"] for p in avis["sections"]["nature"]))


# Bloc « avocats / greffe » recopié de la capture d'une annonce Licitor (Épinay-sur-Seine) fournie par l'utilisateur.
FIXTURE_CONTACTS = f"""<html><head><meta property="og:title" content="Un appartement, Épinay-sur-Seine, Seine Saint Denis"></head><body>
<p>Tribunal Judiciaire de Bobigny (Seine Saint Denis)</p><p>{FUT_FR} à 13h30</p>
<h2>UN APPARTEMENT</h2><p>de 32,18 m², au 5ème et dernier étage, comprenant : entrée, séjour, chambre, cuisine, salle d'eau avec wc, placards</p>
<h3>Une cave</h3><p><b>Libre</b> au 30/03/2026 (date du PVD)</p><h3>Mise à prix : 27 000 €</h3>
<p>Épinay-sur-Seine</p><p>76, av. Galliéni</p><p>Visite sur place lundi 30 novembre 2026 de 9h à 10h</p>
<h3>Maître Jérôme Hocquard, de la SELARL Eloca, Avocat</h3><p>79, bd du Montparnasse - 75006 Paris<br>Tél.: 01 43 26 82 98 (de 10h à 12h)<br>
<a href="http://www.dgassocies-eloca.fr">www.dgassocies-eloca.fr</a></p>
<h3>Maître Sylvie Langlais, membre de la SCP Langlais - Chopin, Avocat</h3><p>7 bis, rue de Vincennes - 93100 Montreuil-sous-Bois</p>
<p><i>Le cahier des conditions de vente, référence Greffe n°26/05931, peut être consulté au Greffe du Juge de l'Exécution du Tribunal Judiciaire de Bobigny et au Cabinet du ou (des) Avocat(s)</i></p>
</body></html>"""


class T13Contacts(unittest.TestCase):
    def test_extract_from_licitor(self):
        d = licitor.parse_detail(FIXTURE_CONTACTS, "https://www.licitor.com/annonce/1/2/3/vente-aux-encheres/un-appartement/epinay-sur-seine/seine-saint-denis/123.html")
        c = d["contacts"]
        self.assertEqual([x["name"] for x in c], ["Maître Jérôme Hocquard", "Maître Sylvie Langlais"])
        self.assertEqual(c[0]["firm"], "SELARL Eloca")
        self.assertEqual(c[0]["phones"], ["01 43 26 82 98"])
        self.assertEqual(c[0]["hours"], "de 10h à 12h")
        self.assertEqual(c[0]["websites"], ["www.dgassocies-eloca.fr"])
        self.assertEqual(c[0]["emails"], [])                     # aucun email dans l'annonce : rien n'est inventé
        self.assertEqual(c[1]["phones"], [])
        self.assertIn("93100", c[1]["address"])
        self.assertEqual(d["greffe"]["reference"], "26/05931")
        self.assertIn("Bobigny", d["greffe"]["lieu"])
        self.assertIn("Hocquard", d["lawyer"])

    def test_request_mail(self):
        from immoradar import contacts
        d = licitor.parse_detail(FIXTURE_CONTACTS, "https://www.licitor.com/annonce/x.html")
        d["doc_analyses"] = []
        m = contacts.request_mail(d, "Jean Test\n06 00 00 00 00")
        self.assertEqual(m["to"], [])
        self.assertTrue(m["mailto"].startswith("mailto:?subject="))
        self.assertIn("26/05931", m["subject"])
        asked = " ".join(x["demande"] for x in m["demandes"])
        for w in ("procès-verbal descriptif", "cahier des conditions de vente", "diagnostics", "charges de copropriété", "taxe foncière"):
            self.assertIn(w, asked)
        self.assertNotIn("occupation actuelle", asked)          # « Libre » est dans l'annonce
        self.assertNotIn("dates de visite", asked)
        self.assertIn("27 000 €", m["body"])
        self.assertTrue(m["body"].startswith("Maître,"))
        self.assertIn("Jean Test", m["body"])

    def test_email_from_listing_and_document(self):
        from immoradar import contacts, documents
        html = FIXTURE_CONTACTS.replace("Tél.: 01 43 26 82 98", "Tél.: 01 43 26 82 98 - contact@cabinet-test.fr")
        d = licitor.parse_detail(html, "https://www.licitor.com/annonce/x.html")
        rec = documents.analyse_bytes("\f".join(PV_PAGES + ["Commissaire de justice : etude@commissaire-test.fr"]).encode(), "PV descriptif", "collé")
        d["doc_analyses"] = [rec]
        m = contacts.request_mail(d)
        self.assertEqual(m["to"], ["contact@cabinet-test.fr"])
        self.assertEqual(m["doc_emails"][0]["email"], "etude@commissaire-test.fr")   # proposé, jamais mis en destinataire
        self.assertNotIn("procès-verbal descriptif", " ".join(x["demande"] for x in m["demandes"]))


# Pages Vench de test, construites d'après la structure constatée le 26/11/2026 (fiche 167303, Grigny) ; contenu fictif.
VENCH_LIST = """<html><body><p>2 ventes aux enchères trouvées</p>
<a href="https://www.vench.fr/vente-167303-un-appartement-de-2158-m-grigny.html"><h3>Un appartement de 21,58 m² Grigny</h3></a>
<ul><li>Mise à prix : 13 000.00 €</li><li>Date de la vente : 13/01/27 J - 48</li></ul>
<a href="https://www.vench.fr/prochaines-ventes-aux-encheres.html?p=2">2</a>
<a href="/ventes-aux-encheres-par-departement-Essonne.html?p=2">Suivant</a></body></html>"""
VENCH_DETAIL = """<html><head><meta property="og:title" content="Un APPARTEMENT de 21,58 m² • Grigny">
<meta property="og:image" content="/vignettes/v75205.jpg"></head><body>
<h1>Un APPARTEMENT de 21,58 m² • Grigny</h1><p>Vente référence : 167303</p>
<p>Vente aux enchères publiques - Tribunal judiciaire de EVRY-COURCOURONNES</p>
<h4>Adresse</h4><p>3 Square Surcouf</p><p>91350 Grigny</p>
<h4>Date de l'audience</h4><p>13/01/2027 à 10:00</p>
<h4>Date(s) de visite</h4><p>29/12/2026 à 08:30</p>
<h4>Type</h4><p>Appartement</p>
<h4>Description</h4><p>Au 10ème étage du bâtiment C2, un appartement de 21,58 m² comprenant : couloir, salle d'eau avec WC, une pièce, coin cuisine.</p>
<h4>Occupation</h4><p>Bien occupé</p>
<h4>Documents</h4><a href="https://document.vench.fr/docs/75206.pdf">Avis Greffe.pdf</a> <a href="https://document.vench.fr/docs/75253.pdf">PVD - CCV - DIAGNOSTICS</a>
<h4>Mise à prix</h4><p>13 000.00 €</p>
<p>Consulter le cahier des conditions de vente (RG: 25/00206), s'adresser au Greffe du Juge de l'Exécution du Tribunal Judiciaire de EVRY-COURCOURONNES</p>
<h4>Avocat poursuivant</h4><p>SELAS AVOCATS ASSOCIES MIORINI</p><p>Maître Priscillia MIORINI Avocat</p>
<p>Résidence Le Feray - 4 rue Feray, 91100 Corbeil-Essonnes</p><p>01.60.90.13.13</p>
<p><a href="/cdn-cgi/l/email-protection#abc">[email protected]</a></p>
<script src="/cdn-cgi/challenge-platform/scripts/jsd/main.js"></script>
<p>Informations PLU réservées aux abonnés</p>""" + "<p>" + "texte de remplissage " * 300 + "</p></body></html>"


class T14Vench(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from immoradar.collectors import vench
        cls.vench = vench
        cls.srv, cls.url = start_fake()
        cls.old = vench.HOME
        vench.HOME = cls.url
        cls.old_delay = net.PoliteClient.__init__.__defaults__
        net.PoliteClient.__init__.__defaults__ = (0.0, 80)

    @classmethod
    def tearDownClass(cls):
        cls.vench.HOME = cls.old
        net.PoliteClient.__init__.__defaults__ = cls.old_delay
        cls.srv.shutdown()

    def test_parse_detail(self):
        d = self.vench.parse_detail(VENCH_DETAIL, "https://www.vench.fr/vente-167303-un-appartement-de-2158-m-grigny.html")
        self.assertEqual(d["source_id"], "167303")
        self.assertEqual(d["mise_a_prix"], 13000)
        self.assertEqual(d["surface"], 21.58)
        self.assertEqual((d["postcode"], d["city"], d["dep"]), ("91350", "Grigny", "91"))
        self.assertEqual(d["address"], "3 Square Surcouf")
        self.assertEqual((d["sale_date"], d["sale_time"]), ("2027-01-13", "10:00"))
        self.assertIn("Evry-Courcouronnes", d["sale_place"])
        self.assertEqual(d["occupation"], "occupé")
        self.assertEqual(len(d["documents"]), 2)
        self.assertEqual(d["greffe"]["reference"], "25/00206")
        self.assertEqual(d["contacts"][0]["phones"], ["01 60 90 13 13"])
        self.assertEqual(d["contacts"][0]["emails"], [])          # email masqué par le site : non décodé
        self.assertIn("abonnés", d["note_source"])
        self.assertTrue(d["photos"][0].endswith("/vignettes/v75205.jpg"))

    def test_collect_with_pagination(self):
        logs = []
        items = self.vench.collect(["91"], {}, logs.append)    # page normale avec script Cloudflare passif : pas bloquée
        self.assertEqual(sorted(i["source_id"] for i in items), ["167303", "167305"])
        self.assertTrue(any("Essonne : 2" in l for l in logs))

    def test_dedup_with_licitor_city_names(self):
        self.assertEqual(pipeline.city_key("Paris 13ème"), pipeline.city_key("Paris"))
        a = pipeline.dedup_key({"source": "licitor", "source_id": 1, "dep": "75", "city": "Paris 13eme", "sale_date": "2026-12-03", "mise_a_prix": 10000})
        b = pipeline.dedup_key({"source": "vench", "source_id": 2, "dep": "75", "city": "Paris", "sale_date": "2026-12-03", "mise_a_prix": 10000.0})
        self.assertEqual(a, b)

    def test_import_by_url_rules(self):
        with self.assertRaises(ValueError) as cm:
            pipeline.import_url("https://www.immobilier.notaires.fr/fr/annonce-immo/123")
        self.assertIn("Cmd+A", str(cm.exception))
        self.assertEqual(pipeline.source_for_url("https://www.licitor.com/annonce/1.html").NAME, "licitor")
        self.assertIsNone(pipeline.source_for_url("https://exemple.org/x"))


class T15Protection(unittest.TestCase):
    def test_weak_markers(self):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                body = {"/ok": VENCH_DETAIL, "/challenge": "<html><script src='/cdn-cgi/challenge-platform/x.js'></script></html>"}.get(self.path, "")
                b = body.encode()
                self.send_response(200 if self.path == "/ok" else 403 if self.path == "/challenge" else 404)
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            c = net.PoliteClient(base, min_delay=0)
            self.assertIn("Grigny", c.get(base + "/ok"))
            with self.assertRaises(net.ProtectionDetected):
                c.get(base + "/challenge")
        finally:
            srv.shutdown()


class T16Robustesse(unittest.TestCase):
    def test_retry_after_connection_reset(self):
        calls = []
        real = net.raw_get

        def flaky(url, **kw):
            calls.append(url)
            if url.endswith("/page") and len([c for c in calls if c.endswith("/page")]) == 1:
                raise net.FetchError("ConnectionResetError: [Errno 54] Connection reset by peer")
            return 200, b"<html>" + b"ok " * 3000 + b"</html>", {}
        old_waits, net.RETRY_WAITS, net.raw_get = net.RETRY_WAITS, (0, 0), flaky
        try:
            c = net.PoliteClient("http://exemple.test", min_delay=1.0)
            self.assertIn("ok", c.get("http://exemple.test/page"))
            self.assertEqual(c.retries, 1)
            self.assertGreaterEqual(c.min_delay, 6.0)        # plus lent après une coupure
        finally:
            net.RETRY_WAITS, net.raw_get = old_waits, real

    def test_no_retry_on_rate_limit(self):
        real = net.raw_get
        net.raw_get = lambda url, **kw: (429, b"Too many requests", {}) if not url.endswith("robots.txt") else (404, b"", {})
        try:
            with self.assertRaises(net.ProtectionDetected):
                net.PoliteClient("http://exemple.test", min_delay=0).get("http://exemple.test/x")
        finally:
            net.raw_get = real

    def test_licitor_keeps_partial_results(self):
        srv, url = start_fake()
        old_home, old_waits = licitor.HOME, net.RETRY_WAITS
        licitor.HOME, net.RETRY_WAITS = url, (0, 0)
        real = net.raw_get

        def cut_detail(u, **kw):
            if "/annonce/" in u and "999001" not in u:
                raise net.FetchError("ConnectionResetError: [Errno 54] Connection reset by peer")
            return real(u, **kw)
        net.raw_get = cut_detail
        old_delay = net.PoliteClient.__init__.__defaults__
        net.PoliteClient.__init__.__defaults__ = (0.0, 80)
        try:
            items = licitor.collect(["75", "69"], {}, lambda m: None)
            self.assertEqual([i["source_id"] for i in items], ["999001"])
            self.assertIn("Les annonces déjà enregistrées restent affichées", licitor.LAST_WARNING)
        finally:
            net.raw_get, licitor.HOME, net.RETRY_WAITS = real, old_home, old_waits
            net.PoliteClient.__init__.__defaults__ = old_delay
            srv.shutdown()


class T17Automatique(unittest.TestCase):
    def test_search_due(self):
        from datetime import datetime
        from immoradar import automation
        params = {"recherche_auto": {"active": True, "heure": 7}}
        tz = datetime.now().astimezone().tzinfo
        db.x("INSERT INTO runs(started_at,status) VALUES (?,?)", ("2030-01-10T06:30:00+00:00", "terminée"))
        t = lambda h: datetime(2030, 1, 10, h, 5, tzinfo=tz)  # noqa: E731
        self.assertFalse(automation.search_due(params, t(6)))                # avant l'heure
        self.assertTrue(automation.search_due(params, datetime(2030, 1, 10, 9, 0, tzinfo=datetime.fromisoformat("2030-01-10T06:30:00+00:00").tzinfo)))
        db.x("INSERT INTO runs(started_at,status) VALUES (?,?)", ("2030-01-10T07:10:00+00:00", "terminée"))
        self.assertFalse(automation.search_due(params, datetime(2030, 1, 10, 9, 0, tzinfo=datetime.fromisoformat("2030-01-10T07:10:00+00:00").tzinfo)))
        self.assertFalse(automation.search_due({"recherche_auto": {"active": False, "heure": 7}}))
        db.x("DELETE FROM runs WHERE started_at LIKE '2030-%'")

    def test_diagnostic_zip(self):
        import zipfile
        from immoradar import automation
        from immoradar.collectors import vench
        srv, url = start_fake()
        old = vench.HOME
        vench.HOME = url
        old_delay = net.PoliteClient.__init__.__defaults__
        net.PoliteClient.__init__.__defaults__ = (0.0, 80)
        try:
            res = automation.run_diagnostic("vench", ["91"], log=lambda m: None)
        finally:
            vench.HOME = old
            net.PoliteClient.__init__.__defaults__ = old_delay
            srv.shutdown()
        self.assertEqual(res["items"], 2)
        self.assertGreaterEqual(res["pages"], 3)
        names = zipfile.ZipFile(res["zip"]).namelist()
        self.assertIn("journal.txt", names)
        self.assertTrue(any("vente-167303" in n for n in names))
        self.assertEqual(automation.diagnostic_file(res["name"]), res["zip"])
        self.assertIsNone(automation.diagnostic_file("../immoradar.db"))
        self.assertEqual(vench.MAX_DETAILS, 60)                          # limite remise après le diagnostic


class T18ClassementEtSeuil(unittest.TestCase):
    def test_seuil_80_et_migration(self):
        old = db.get_setting("params", {})
        try:
            self.assertEqual(config.DEFAULTS["scoring"]["seuil_opportunite"], 80)
            db.set_setting("params", {"scoring": {"seuil_opportunite": 60}})        # ancienne valeur par défaut enregistrée
            self.assertEqual(config.load()["scoring"]["seuil_opportunite"], 80)
            db.set_setting("params", {"scoring": {"seuil_opportunite": 70}, "_version": 2})  # choix explicite conservé
            self.assertEqual(config.load()["scoring"]["seuil_opportunite"], 70)
        finally:
            db.set_setting("params", old)

    def test_completude_et_classement(self):
        from immoradar import documents
        rec = documents.analyse_bytes("\f".join(PV_PAGES).encode(), "PV descriptif", "collé")
        vide = {"surface": 30}
        riche = {"surface": 30, "occupation": "libre", "works_level": "léger", "sale_date": "2027-01-01",
                 "contacts": [{"name": "Maître X"}], "doc_analyses": [rec]}
        n0, m0 = pipeline.completude(vide, False)
        n1, m1 = pipeline.completude(riche, True)
        self.assertLess(n0, 20)
        self.assertIn("PV descriptif", m0)
        self.assertNotIn("PV descriptif", m1)
        self.assertGreater(n1, 60)
        a = {"score_revente": 82, "score_locatif": None, "completude": 20}   # bonne note, dossier vide
        b = {"score_revente": 78, "score_locatif": None, "completude": 90}   # note un peu moins bonne, dossier complet
        self.assertLess(pipeline.SORTS["interet"]({**b, "classement": 0}), pipeline.SORTS["interet"]({**a, "classement": 0}))
        self.assertLess(pipeline.SORTS["note"](a), pipeline.SORTS["note"](b))
