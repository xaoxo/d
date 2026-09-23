"""Import des Demandes de Valeurs Foncières géolocalisées (Etalab).

Source officielle : toutes les ventes immobilières de France (hors Alsace-Moselle
et Mayotte), 5 dernières années, publiées sur files.data.gouv.fr/geo-dvf.

Nettoyage appliqué :
- uniquement les ventes (et VEFA, marquées « neuf ») ;
- mutations portant sur exactement UN logement (maison ou appartement),
  dépendances (caves, parkings) tolérées ; ventes en bloc et ventes mixtes
  avec locaux commerciaux écartées car leur prix au m² n'a pas de sens ;
- prix au m² filtré dans une plage plausible (configurable).
"""
from __future__ import annotations

import csv
import gzip
import io
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from ..config import Marche
from ..util import download, to_float, to_int

BASE_URL = "https://files.data.gouv.fr/geo-dvf/latest/csv/{annee}/departements/{dep}.csv.gz"

# Départements couverts par DVF (57, 67, 68 : livre foncier ; 976 absent).
DEPARTEMENTS = (
    [f"{i:02d}" for i in range(1, 20)] + ["2A", "2B"] + [f"{i:02d}" for i in range(21, 96)]
    + ["971", "972", "973", "974"]
)
DEPARTEMENTS = [d for d in DEPARTEMENTS if d not in ("57", "67", "68")]

TYPES = {"Appartement": "appartement", "Maison": "maison"}
NATURES = {"Vente": False, "Vente en l'état futur d'achèvement": True}


def departement_depuis_cp(cp: str | None) -> str | None:
    """Département à partir du code postal (Corse : 200xx-201xx = 2A, sinon 2B)."""
    if not cp or len(cp) < 2:
        return None
    if cp.startswith("97"):
        return cp[:3]
    if cp.startswith("20"):
        return "2A" if cp[:3] in ("200", "201") else "2B"
    return cp[:2]


def departement_annonce(a) -> str | None:
    return (a.departement or departement_depuis_cp(a.code_postal)
            or (a.code_commune[:3] if a.code_commune and a.code_commune.startswith("97") else
                a.code_commune[:2] if a.code_commune else None))


def annees_par_defaut() -> list[int]:
    from datetime import date
    return list(range(date.today().year - 5, date.today().year))


def assurer_departement(conn, dep: str, cache_dir: Path, cfg: Marche, log=print) -> int:
    """Télécharge les ventes DVF d'un département si elles ne sont pas déjà en base."""
    deja = conn.execute("SELECT COUNT(*) FROM dvf_ventes WHERE code_departement=?", (dep,)).fetchone()[0]
    if deja:
        return 0
    if dep not in DEPARTEMENTS:
        log(f"Département {dep} non couvert par DVF (Alsace-Moselle / Mayotte).")
        return 0
    total = 0
    for annee in annees_par_defaut():
        try:
            n = load_file(conn, fetch(annee, dep, cache_dir), cfg)
            log(f"  ventes réelles {annee}, dép. {dep} : {n}")
            total += n
        except Exception as exc:  # noqa: BLE001 - une année manquante n'est pas bloquante
            log(f"  ventes {annee}, dép. {dep} : indisponible ({exc})")
    rebuild_communes(conn)
    return total


def dvf_url(annee: int, dep: str) -> str:
    return BASE_URL.format(annee=annee, dep=dep)


def fetch(annee: int, dep: str, cache_dir: Path) -> Path:
    dest = cache_dir / "dvf" / str(annee) / f"{dep}.csv.gz"
    if not dest.exists() or dest.stat().st_size == 0:
        download(dvf_url(annee, dep), dest)
    return dest


def open_csv(path: Path) -> io.TextIOBase:
    if str(path).endswith(".gz"):
        return io.TextIOWrapper(gzip.open(path, "rb"), encoding="utf-8", newline="")
    return open(path, encoding="utf-8", newline="")


def _mutation_to_sale(rows: list[dict], cfg: Marche) -> dict | None:
    first = rows[0]
    neuf = NATURES.get(first.get("nature_mutation", ""))
    if neuf is None:
        return None
    valeur = to_float(first.get("valeur_fonciere"))
    if not valeur or valeur <= 0:
        return None

    logements, other_local = {}, False
    parcelles = {}
    for r in rows:
        t = r.get("type_local") or ""
        if t in TYPES:
            key = (t, r.get("surface_reelle_bati"), r.get("nombre_pieces_principales"),
                   r.get("lot1_numero"), r.get("id_parcelle"))
            logements[key] = r
        elif t and t != "Dépendance":
            other_local = True
        if r.get("id_parcelle"):
            parcelles[r["id_parcelle"]] = max(parcelles.get(r["id_parcelle"], 0.0),
                                              to_float(r.get("surface_terrain")) or 0.0)
    # Un même logement peut apparaître sur plusieurs parcelles : on déduplique
    # sur (type, surface, pièces, lot) sans tenir compte de la parcelle.
    distinct = {k[:4] for k in logements}
    if other_local or len(distinct) != 1:
        return None
    lg = next(iter(logements.values()))
    surface = to_float(lg.get("surface_reelle_bati"))
    if not surface or surface < cfg.surface_min:
        return None
    prix_m2 = valeur / surface
    if not (cfg.prix_m2_min <= prix_m2 <= cfg.prix_m2_max):
        return None
    date = first.get("date_mutation", "")
    return {
        "id_mutation": first["id_mutation"],
        "date_mutation": date,
        "annee": int(date[:4]),
        "valeur": valeur,
        "code_postal": first["code_postal"].zfill(5) if first.get("code_postal") else None,
        "code_commune": first.get("code_commune"),
        "nom_commune": first.get("nom_commune"),
        "code_departement": first.get("code_departement"),
        "type_local": TYPES[lg["type_local"]],
        "surface": surface,
        "pieces": to_int(lg.get("nombre_pieces_principales")),
        "surface_terrain": sum(parcelles.values()) or None,
        "neuf": int(neuf),
        "lat": to_float(lg.get("latitude") or first.get("latitude")),
        "lon": to_float(lg.get("longitude") or first.get("longitude")),
        "prix_m2": prix_m2,
    }


def parse(fh: Iterable[str], cfg: Marche) -> Iterator[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for row in csv.DictReader(fh):
        mid = row.get("id_mutation")
        if mid:
            groups[mid].append(row)
    for rows in groups.values():
        sale = _mutation_to_sale(rows, cfg)
        if sale:
            yield sale


COLUMNS = ["id_mutation", "date_mutation", "annee", "valeur", "code_postal", "code_commune",
           "nom_commune", "code_departement", "type_local", "surface", "pieces",
           "surface_terrain", "neuf", "lat", "lon", "prix_m2"]


def load_file(conn, path: Path, cfg: Marche) -> int:
    with open_csv(path) as fh:
        sales = list(parse(fh, cfg))
    conn.executemany(
        f"INSERT OR REPLACE INTO dvf_ventes ({','.join(COLUMNS)}) "
        f"VALUES ({','.join('?' * len(COLUMNS))})",
        [[s[c] for c in COLUMNS] for s in sales],
    )
    conn.commit()
    return len(sales)


def rebuild_communes(conn) -> None:
    """Table de correspondance code postal <-> code INSEE à partir des ventes DVF."""
    counts: Counter = Counter()
    names = {}
    for r in conn.execute("SELECT code_commune, code_postal, nom_commune FROM dvf_ventes "
                          "WHERE code_postal IS NOT NULL"):
        counts[(r[0], r[1])] += 1
        names[r[0]] = r[2]
    conn.execute("DELETE FROM communes")
    conn.executemany("INSERT INTO communes VALUES (?,?,?,?)",
                     [(cc, cp, names.get(cc), n) for (cc, cp), n in counts.items()])
    conn.commit()
