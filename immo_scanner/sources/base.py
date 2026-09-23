"""Modèle d'annonce commun à toutes les sources et enregistrement en base."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone

from ..util import normalize, to_float, to_int


@dataclass
class Annonce:
    source: str
    source_id: str | None = None
    url: str | None = None
    titre: str | None = None
    type_local: str | None = None     # appartement | maison
    prix: float | None = None
    surface: float | None = None
    pieces: int | None = None
    surface_terrain: float | None = None
    code_postal: str | None = None
    code_commune: str | None = None
    ville: str | None = None
    departement: str | None = None          # utile quand le code postal est inconnu
    lat: float | None = None
    lon: float | None = None
    dpe: str | None = None
    description: str | None = None
    loyer_actuel: float | None = None       # si vendu loué
    charges_annuelles: float | None = None  # charges de copropriété
    taxe_fonciere: float | None = None
    neuf: bool = False
    date_publication: str | None = None
    mode_vente: str | None = None           # vente | enchere | offre (vente interactive / État)
    date_vente: str | None = None           # date d'audience / de fin des offres

    @property
    def id(self) -> str:
        key = self.source_id or self.url or f"{self.titre}|{self.prix}|{self.surface}|{self.code_postal}"
        return f"{self.source}:{hashlib.sha1(str(key).encode()).hexdigest()[:16]}"

    def normalized(self) -> "Annonce":
        self.prix = to_float(self.prix)
        self.surface = to_float(self.surface)
        self.pieces = to_int(self.pieces)
        self.surface_terrain = to_float(self.surface_terrain)
        self.lat, self.lon = to_float(self.lat), to_float(self.lon)
        for f in ("loyer_actuel", "charges_annuelles", "taxe_fonciere"):
            setattr(self, f, to_float(getattr(self, f)))
        if self.code_postal:
            self.code_postal = re.sub(r"\D", "", str(self.code_postal))[:5].zfill(5)
        if self.code_commune:
            self.code_commune = str(self.code_commune).strip().zfill(5)
        self.type_local = guess_type(self.type_local, self.titre, self.description)
        if self.dpe:
            m = re.search(r"\b([A-G])\b", str(self.dpe).upper())
            self.dpe = m.group(1) if m else None
        else:
            self.dpe = guess_dpe(self.description)
        text = normalize(f"{self.titre} {self.description}")
        if not self.neuf and re.search(r"\b(vefa|programme neuf|livraison 20\d\d)\b", text):
            self.neuf = True
        return self


def guess_type(value, titre, description) -> str | None:
    for text in (value, titre, description):
        t = normalize(text)
        if re.search(r"\b(appartement|appart|studio|duplex|loft|t\d|f\d)\b", t):
            return "appartement"
        if re.search(r"\b(maison|villa|pavillon|longere|mas|chalet|fermette)\b", t):
            return "maison"
    return None


def guess_dpe(description) -> str | None:
    t = normalize(description)
    m = re.search(r"\b(?:dpe|classe energie|classe energetique|diagnostic de performance energetique)\s*(?:classe\s*)?:?\s*([a-g])\b", t)
    return m.group(1).upper() if m else None


def resolve_commune(conn, a: Annonce) -> None:
    """Complète le code INSEE (et le code postal) à partir du code postal + ville,
    ou à défaut de la ville + département, via la table des communes issue de DVF."""
    from ..geo import code_arrondissement
    if a.code_commune:
        return
    arr = code_arrondissement(a.ville)
    if arr:
        a.code_commune = arr
        if not a.code_postal:
            r = conn.execute("SELECT code_postal FROM communes WHERE code_commune=? ORDER BY nb DESC",
                             (arr,)).fetchone()
            a.code_postal = r["code_postal"] if r else None
        return
    ville = normalize(a.ville)
    if a.code_postal:
        rows = conn.execute("SELECT code_commune, code_postal, nom_commune, nb FROM communes "
                            "WHERE code_postal=? ORDER BY nb DESC", (a.code_postal,)).fetchall()
    elif ville and a.departement:
        rows = conn.execute("SELECT code_commune, code_postal, nom_commune, nb FROM communes "
                            "WHERE code_commune LIKE ? ORDER BY nb DESC", (a.departement + "%",)).fetchall()
        rows = [r for r in rows if normalize(r["nom_commune"]) == ville]
    else:
        return
    if not rows:
        return
    choix = next((r for r in rows if ville and normalize(r["nom_commune"]) == ville), None) or \
        next((r for r in rows if ville and (ville in normalize(r["nom_commune"])
                                            or normalize(r["nom_commune"]) in ville)), None) or rows[0]
    a.code_commune = choix["code_commune"]
    a.code_postal = a.code_postal or choix["code_postal"]
    a.ville = a.ville or choix["nom_commune"]


def save(conn, annonces, evenements: list | None = None) -> tuple[int, int]:
    """Insère ou met à jour les annonces. Conserve le premier prix vu pour détecter les baisses.

    Si ``evenements`` est une liste, y ajoute ("nouveau", id) et ("baisse", id, ancien_prix)."""
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new = updated = 0
    cols = [f.name for f in fields(Annonce) if f.name not in ("source_id",)]
    for a in annonces:
        a.normalized()
        resolve_commune(conn, a)
        if not a.prix or not a.surface:
            continue
        data = asdict(a)
        data.pop("source_id")
        data["neuf"] = int(bool(a.neuf))
        existing = conn.execute("SELECT prix FROM annonces WHERE id=?", (a.id,)).fetchone()
        if existing:
            if evenements is not None and existing["prix"] and a.prix < existing["prix"]:
                evenements.append(("baisse", a.id, existing["prix"]))
            sets = ",".join(f"{c}=?" for c in cols)
            conn.execute(f"UPDATE annonces SET {sets}, derniere_vue=?, active=1 WHERE id=?",
                         [data[c] for c in cols] + [now, a.id])
            updated += 1
        else:
            conn.execute(
                f"INSERT INTO annonces (id, {','.join(cols)}, prix_initial, premiere_vue, derniere_vue) "
                f"VALUES (?, {','.join('?' * len(cols))}, ?, ?, ?)",
                [a.id] + [data[c] for c in cols] + [a.prix, now, now])
            new += 1
            if evenements is not None:
                evenements.append(("nouveau", a.id))
    conn.commit()
    return new, updated
