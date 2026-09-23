"""Base SQLite locale : ventes DVF, loyers, annonces et analyses."""
from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB = Path("data/immo.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS dvf_ventes (
    id_mutation      TEXT PRIMARY KEY,
    date_mutation    TEXT NOT NULL,
    annee            INTEGER NOT NULL,
    valeur           REAL NOT NULL,
    code_postal      TEXT,
    code_commune     TEXT NOT NULL,
    nom_commune      TEXT,
    code_departement TEXT NOT NULL,
    type_local       TEXT NOT NULL,          -- appartement | maison
    surface          REAL NOT NULL,
    pieces           INTEGER,
    surface_terrain  REAL,
    neuf             INTEGER DEFAULT 0,      -- VEFA
    lat              REAL,
    lon              REAL,
    prix_m2          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dvf_commune ON dvf_ventes(code_commune, type_local, date_mutation);
CREATE INDEX IF NOT EXISTS idx_dvf_geo ON dvf_ventes(type_local, lat, lon);
CREATE INDEX IF NOT EXISTS idx_dvf_cp ON dvf_ventes(code_postal);
CREATE INDEX IF NOT EXISTS idx_dvf_dep ON dvf_ventes(code_departement, type_local);

CREATE TABLE IF NOT EXISTS communes (
    code_commune     TEXT NOT NULL,
    code_postal      TEXT NOT NULL,
    nom_commune      TEXT,
    nb               INTEGER,
    PRIMARY KEY (code_commune, code_postal)
);
CREATE INDEX IF NOT EXISTS idx_communes_cp ON communes(code_postal);

CREATE TABLE IF NOT EXISTS loyers (
    code_commune  TEXT NOT NULL,
    type_local    TEXT NOT NULL,
    loyer_m2      REAL NOT NULL,
    loyer_m2_bas  REAL,
    loyer_m2_haut REAL,
    nb_obs        INTEGER,
    PRIMARY KEY (code_commune, type_local)
);

CREATE TABLE IF NOT EXISTS annonces (
    id                TEXT PRIMARY KEY,
    source            TEXT NOT NULL,
    url               TEXT,
    titre             TEXT,
    type_local        TEXT,
    prix              REAL,
    prix_initial      REAL,
    surface           REAL,
    pieces            INTEGER,
    surface_terrain   REAL,
    code_postal       TEXT,
    code_commune      TEXT,
    ville             TEXT,
    departement       TEXT,
    lat               REAL,
    lon               REAL,
    dpe               TEXT,
    description       TEXT,
    loyer_actuel      REAL,
    charges_annuelles REAL,
    taxe_fonciere     REAL,
    neuf              INTEGER DEFAULT 0,
    date_publication  TEXT,
    mode_vente        TEXT,
    date_vente        TEXT,
    premiere_vue      TEXT,
    derniere_vue      TEXT,
    active            INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS alertes_envoyees (
    annonce_id  TEXT NOT NULL,
    evenement   TEXT NOT NULL,         -- nouveau | baisse
    prix        REAL,
    envoye_le   TEXT,
    PRIMARY KEY (annonce_id, evenement, prix)
);

CREATE TABLE IF NOT EXISTS analyses (
    annonce_id        TEXT PRIMARY KEY REFERENCES annonces(id) ON DELETE CASCADE,
    calcule_le        TEXT,
    score             REAL,
    verdict           TEXT,
    valeur_estimee    REAL,
    decote_pct        REAL,
    rendement_brut    REAL,
    rendement_net     REAL,
    rendement_net_net REAL,
    cashflow_mensuel  REAL,
    tri               REAL,
    plus_value_horizon REAL,
    tendance_annuelle REAL,
    fiabilite         TEXT,
    details           TEXT                   -- JSON complet
);
"""


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    _migrer(conn)
    return conn


def _migrer(conn) -> None:
    """Ajoute les colonnes apparues dans les versions récentes aux bases existantes."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(annonces)")}
    for col in ("mode_vente", "date_vente", "departement"):
        if col not in cols:
            conn.execute(f"ALTER TABLE annonces ADD COLUMN {col} TEXT")
    conn.commit()
