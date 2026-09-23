"""Import des indicateurs de loyers par commune.

Source recommandée : « Carte des loyers » (ministère de la Transition écologique /
ANIL), jeu de données data.gouv.fr « Indicateurs de loyers d'annonce par commune ».
Fichiers CSV séparés par « ; », décimale « , », un fichier par type de bien
(appartements, maisons). Colonnes utilisées : INSEE_C, loypredm2, lwr.IPm2,
upr.IPm2, nbobs_com.

Un CSV simple est aussi accepté : code_commune;type_local;loyer_m2
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from ..util import download, to_float, to_int

ALIASES = {
    "code_commune": ["insee_c", "insee", "code_commune", "codgeo", "code_insee"],
    "loyer_m2": ["loypredm2", "loyer_m2", "loyer_moyen_m2", "loyer"],
    "bas": ["lwr.ipm2", "lwr_ipm2", "loyer_m2_bas"],
    "haut": ["upr.ipm2", "upr_ipm2", "loyer_m2_haut"],
    "nb": ["nbobs_com", "nb_obs", "nbobs"],
    "type_local": ["type_local", "type"],
}


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Encodage illisible : {path}")


def parse(text: str, type_local: str | None) -> list[tuple]:
    sample = text[:4096]
    delim = ";" if sample.count(";") >= sample.count(",") else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delim)
    cols = {c.lower().strip().strip('"'): c for c in reader.fieldnames or []}

    def col(key):
        return next((cols[a] for a in ALIASES[key] if a in cols), None)

    c_code, c_loyer = col("code_commune"), col("loyer_m2")
    if not c_code or not c_loyer:
        raise ValueError(f"Colonnes non reconnues : {list(cols)}")
    c_bas, c_haut, c_nb, c_type = col("bas"), col("haut"), col("nb"), col("type_local")
    out = []
    for row in reader:
        code = (row.get(c_code) or "").strip().zfill(5)
        loyer = to_float(row.get(c_loyer))
        t = (row.get(c_type) or "").strip().lower() if c_type else type_local
        if t and t.startswith("appart"):
            t = "appartement"
        elif t and t.startswith("maison"):
            t = "maison"
        if not code.strip("0") or not loyer or t not in ("appartement", "maison"):
            continue
        out.append((code, t, loyer,
                    to_float(row.get(c_bas)) if c_bas else None,
                    to_float(row.get(c_haut)) if c_haut else None,
                    to_int(row.get(c_nb)) if c_nb else None))
    return out


def load(conn, source: str, type_local: str | None, cache_dir: Path) -> int:
    if source.startswith(("http://", "https://")):
        path = download(source, cache_dir / "loyers" / (source.rstrip("/").rsplit("/", 1)[-1] or "loyers.csv"))
    else:
        path = Path(source)
    rows = parse(_read_text(path), type_local)
    conn.executemany("INSERT OR REPLACE INTO loyers VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)
