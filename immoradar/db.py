"""Stockage.

- Données de référence volumineuses et re-téléchargeables (DVF, loyers, cache de géocodage) :
  toujours dans un SQLite local (data/reference.db).
- Vos données (annonces, favoris, corrections, réglages, historique des recherches) :
  SQLite local (data/immoradar.db) par défaut, ou PostgreSQL si DATABASE_URL est défini
  (utile pour un hébergement gratuit dont le disque n'est pas conservé). Le pilote pg8000
  n'est nécessaire que dans ce cas.
"""
import json
import os
import re
import sqlite3
import threading
import urllib.parse
from datetime import datetime, timezone

DATA_DIR = os.environ.get("IMMORADAR_DATA", os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"))
DB_PATH = os.path.join(DATA_DIR, "immoradar.db")
REF_PATH = os.path.join(DATA_DIR, "reference.db")
DATABASE_URL = os.environ.get("DATABASE_URL", "")
_lock = threading.RLock()

USER_TABLES = re.compile(r"\b(listings|listing_sources|runs|settings)\b")

USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id {pk},
    dedup_key TEXT UNIQUE,
    data TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_run_id INTEGER,
    first_run_id INTEGER,
    favorite INTEGER DEFAULT 0,
    hidden INTEGER DEFAULT 0,
    overrides TEXT DEFAULT '{{}}',
    analysis TEXT
);
CREATE TABLE IF NOT EXISTS listing_sources (
    listing_id INTEGER, source TEXT, source_id TEXT, url TEXT, collected_at TEXT, raw TEXT,
    PRIMARY KEY (source, source_id)
);
CREATE TABLE IF NOT EXISTS runs (
    id {pk}, started_at TEXT, finished_at TEXT, status TEXT, summary TEXT, sources TEXT, log TEXT
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

REF_SCHEMA = """
CREATE TABLE IF NOT EXISTS dvf (
    id_mutation TEXT, date TEXT, prix REAL, type TEXT, surface REAL, pieces INTEGER,
    numero TEXT, voie TEXT, code_voie TEXT, code_postal TEXT, code_commune TEXT, commune TEXT,
    dep TEXT, lat REAL, lon REAL, lots INTEGER, carrez REAL
);
CREATE INDEX IF NOT EXISTS dvf_dep ON dvf(dep, type, date);
CREATE TABLE IF NOT EXISTS dvf_files (dep TEXT, year TEXT, rows INTEGER, loaded_at TEXT, url TEXT, PRIMARY KEY(dep, year));
CREATE TABLE IF NOT EXISTS loyers (
    code_commune TEXT, kind TEXT, loyer_m2 REAL, bas REAL, haut REAL, nb_obs INTEGER, commune TEXT,
    PRIMARY KEY (code_commune, kind)
);
CREATE TABLE IF NOT EXISTS geocache (q TEXT PRIMARY KEY, result TEXT, at TEXT);
"""


def now_iso():
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _sqlite(path, schema):
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.executescript(schema)
    return c


class _Pg:
    """Petit adaptateur PostgreSQL (pg8000) : paramètres ?, lignes en dict, reconnexion automatique."""

    def __init__(self, url):
        self.url = url
        self.c = None

    def _connect(self):
        import ssl
        import pg8000.dbapi
        u = urllib.parse.urlparse(self.url)
        qs = urllib.parse.parse_qs(u.query)
        ssl_ctx = None if (qs.get("sslmode", ["require"])[0] == "disable") else ssl.create_default_context()
        self.c = pg8000.dbapi.connect(user=urllib.parse.unquote(u.username or ""), password=urllib.parse.unquote(u.password or ""),
                                      host=u.hostname, port=u.port or 5432, database=u.path.lstrip("/"), ssl_context=ssl_ctx, timeout=30)
        self.c.autocommit = True
        cur = self.c.cursor()
        for stmt in USER_SCHEMA.format(pk="SERIAL PRIMARY KEY").split(";"):
            if stmt.strip():
                cur.execute(stmt)

    def run(self, sql, args=(), many=False, returning=False):
        sql = sql.replace("?", "%s")
        if returning:
            sql += " RETURNING id"
        for attempt in (1, 2):
            try:
                if self.c is None:
                    self._connect()
                cur = self.c.cursor()
                if many:
                    cur.executemany(sql, args)
                    return None
                cur.execute(sql, tuple(args))
                if cur.description is None:
                    return None
                cols = [d[0] for d in cur.description]
                return [dict(zip(cols, r)) for r in cur.fetchall()]
            except Exception as e:
                name = type(e).__name__
                if attempt == 1 and name in ("InterfaceError", "OperationalError", "ConnectionResetError", "BrokenPipeError", "OSError"):
                    self.c = None  # connexion coupée (base en veille) : on se reconnecte une fois
                    continue
                raise


_ref = None
_user = None


def _conns():
    global _ref, _user
    with _lock:
        if _ref is None:
            _ref = _sqlite(REF_PATH, REF_SCHEMA)
        if _user is None:
            _user = _Pg(DATABASE_URL) if DATABASE_URL else _sqlite(DB_PATH, USER_SCHEMA.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT"))
        return _ref, _user


def conn():
    _conns()
    if isinstance(_user, _Pg):
        _user.run("SELECT 1")
    return _ref


def backend():
    return "PostgreSQL (DATABASE_URL)" if DATABASE_URL else "SQLite local"


def _target(sql):
    ref, user = _conns()
    return user if USER_TABLES.search(sql) else ref


def q(sql, args=()):
    with _lock:
        t = _target(sql)
        if isinstance(t, _Pg):
            return t.run(sql, args) or []
        return t.execute(sql, args).fetchall()


def x(sql, args=()):
    """Exécute une écriture ; pour un INSERT renvoie l'id créé."""
    with _lock:
        t = _target(sql)
        if isinstance(t, _Pg):
            ret = sql.lstrip().upper().startswith("INSERT INTO LISTINGS") or sql.lstrip().upper().startswith("INSERT INTO RUNS")
            r = t.run(sql, args, returning=ret)
            return r[0]["id"] if ret and r else None
        return t.execute(sql, args).lastrowid


def many(sql, rows):
    with _lock:
        t = _target(sql)
        if isinstance(t, _Pg):
            t.run(sql, rows, many=True)
            return
        t.execute("BEGIN")
        t.executemany(sql, rows)
        t.execute("COMMIT")


def get_setting(key, default=None):
    r = q("SELECT value FROM settings WHERE key=?", (key,))
    return json.loads(r[0]["value"]) if r else default


def set_setting(key, value):
    x("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
      (key, json.dumps(value, ensure_ascii=False)))
