"""Import d'annonces depuis un fichier CSV / JSON (export d'un portail, d'une API
d'agrégation d'annonces, d'un tableur, d'un autre scraper…).

Les noms de colonnes sont reconnus de manière souple (voir ALIASES).
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from ..util import normalize
from .base import Annonce

ALIASES = {
    "source_id": ["id", "source_id", "reference", "ref", "listing_id"],
    "url": ["url", "lien", "link"],
    "titre": ["titre", "title", "nom", "name"],
    "type_local": ["type_local", "type", "type_bien", "property_type"],
    "prix": ["prix", "price", "prix_vente", "montant"],
    "surface": ["surface", "surface_habitable", "surface_m2", "living_area", "area"],
    "pieces": ["pieces", "nb_pieces", "rooms", "nombre_pieces"],
    "surface_terrain": ["surface_terrain", "terrain", "land_area", "land_surface"],
    "code_postal": ["code_postal", "cp", "postal_code", "zipcode", "zip"],
    "code_commune": ["code_commune", "code_insee", "insee", "insee_code"],
    "ville": ["ville", "commune", "city", "localite"],
    "lat": ["lat", "latitude"],
    "lon": ["lon", "lng", "longitude"],
    "dpe": ["dpe", "classe_energie", "energy_class", "dpe_classe"],
    "description": ["description", "desc", "texte"],
    "loyer_actuel": ["loyer_actuel", "loyer", "rent", "loyer_mensuel"],
    "charges_annuelles": ["charges_annuelles", "charges_copro", "charges", "charges_copropriete"],
    "taxe_fonciere": ["taxe_fonciere", "property_tax"],
    "neuf": ["neuf", "new_build"],
    "date_publication": ["date_publication", "date", "published_at", "created_at"],
}
_LOOKUP = {normalize(a).replace(" ", "_"): field for field, al in ALIASES.items() for a in al}


def _to_annonce(record: dict, source: str) -> Annonce:
    a = Annonce(source=source)
    for key, value in record.items():
        field = _LOOKUP.get(normalize(str(key)).replace(" ", "_"))
        if field and value not in (None, "") and getattr(a, field) in (None, False):
            if field == "neuf":
                value = str(value).strip().lower() in ("1", "true", "oui", "yes", "vrai")
            setattr(a, field, value)
    return a


def read(path: str | Path, source: str | None = None) -> list[Annonce]:
    path = Path(path)
    source = source or path.stem
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".json", ".jsonl", ".ndjson"):
        if path.suffix.lower() == ".json":
            data = json.loads(text)
            if isinstance(data, dict):
                data = next((v for v in data.values() if isinstance(v, list)), [data])
        else:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        sample = text[:4096]
        delim = ";" if sample.count(";") > sample.count(",") else ","
        data = list(csv.DictReader(io.StringIO(text), delimiter=delim))
    return [_to_annonce(r, source) for r in data if isinstance(r, dict)]
