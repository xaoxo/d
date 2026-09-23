"""Lecture d'annonces depuis une API JSON dont on ne connaît pas le format exact.

Beaucoup de sites modernes (notaires, ventes interactives…) affichent leurs annonces en
JavaScript à partir d'une API JSON. Plutôt que de coder chaque format, on parcourt le JSON
et on reconnaît les objets « annonce » à leurs clés : un prix et une surface, plus si
possible code postal, ville, type de bien, lien et type de transaction.
"""
from __future__ import annotations

import re
import urllib.parse

from ..util import normalize, to_float
from .base import Annonce

_CLES = {
    "prix": re.compile(r"(prix|price|montant|miseaprix|premiereoffre)(?!.*(m2|metre|carre|honoraire|frais))"),
    "surface": re.compile(r"(surface|superficie|area|habitable)(?!.*(terrain|land|jardin|sejour))"),
    "terrain": re.compile(r"(surfaceterrain|terrain|landsurface)"),
    "cp": re.compile(r"(codepostal|postalcode|zipcode|^cp$|codepostalbien)"),
    "ville": re.compile(r"(commune|ville|localite|city|libellecommune)(?!.*(code|insee|id))"),
    "insee": re.compile(r"(codeinsee|insee|codecommune)"),
    "type": re.compile(r"(typebien|typedebien|propertytype|nature|typelogement)"),
    "pieces": re.compile(r"(nbpieces|nombrepieces|pieces|rooms)"),
    "dpe": re.compile(r"(dpe|classeenergie|energyclass)"),
    "url": re.compile(r"(url|lien|permalink|href)"),
    "id": re.compile(r"^(id|idannonce|reference|identifiant|uuid)$"),
    "titre": re.compile(r"(titre|title|intitule|libelle)$"),
    "description": re.compile(r"(description|descriptif|texte)"),
    "transaction": re.compile(r"(typetransaction|transaction|modevente|typevente)"),
    "date": re.compile(r"(datefin|datevente|dateaudience|dateadjudication|findesoffres)"),
    "lat": re.compile(r"^(lat|latitude)$"),
    "lon": re.compile(r"^(lng|lon|longitude)$"),
}


def _aplatir(d: dict, prefixe: str = "", profondeur: int = 0) -> dict:
    out = {}
    for k, v in d.items():
        cle = f"{prefixe}{k}"
        if isinstance(v, dict) and profondeur < 2:
            out.update(_aplatir(v, cle + ".", profondeur + 1))
        elif isinstance(v, list) and v and all(isinstance(x, (str, int, float)) for x in v):
            out[cle] = v[0]
        elif not isinstance(v, (dict, list)):
            out[cle] = v
    return out


def _trouver(plat: dict, quoi: str, test=None):
    rx = _CLES[quoi]
    for cle, v in plat.items():
        nom = normalize(cle.rsplit(".", 1)[-1]).replace(" ", "")
        if v not in (None, "") and rx.search(nom) and (test is None or test(v)):
            return v
    return None


def _mode(transaction, defaut: str) -> str:
    t = normalize(str(transaction or ""))
    if re.search(r"\b(vae|enchere|encheres|adjudication)\b", t):
        return "enchere"
    if re.search(r"\b(vni|interactive|36h|offre)\b", t):
        return "offre"
    return defaut


def objet_vers_annonce(obj: dict, source: str, base_url: str, mode_defaut: str) -> Annonce | None:
    plat = _aplatir(obj)
    prix = to_float(_trouver(plat, "prix", lambda v: (to_float(v) or 0) >= 1000))
    surface = to_float(_trouver(plat, "surface", lambda v: 9 <= (to_float(v) or 0) <= 5000))
    if not prix or not surface:
        return None
    url = _trouver(plat, "url", lambda v: isinstance(v, str) and ("/" in v))
    ident = _trouver(plat, "id")
    return Annonce(
        source=source, source_id=str(ident or url or ""),
        url=urllib.parse.urljoin(base_url, url) if url else None,
        titre=_trouver(plat, "titre", lambda v: isinstance(v, str)),
        type_local=str(_trouver(plat, "type") or ""),
        prix=prix, surface=surface,
        pieces=_trouver(plat, "pieces", lambda v: to_float(v) is not None),
        surface_terrain=to_float(_trouver(plat, "terrain", lambda v: to_float(v) is not None)),
        code_postal=_trouver(plat, "cp", lambda v: re.fullmatch(r"\d{4,5}", str(v)) is not None),
        code_commune=_trouver(plat, "insee", lambda v: re.fullmatch(r"\d[\dAB]\d{3}", str(v)) is not None),
        ville=_trouver(plat, "ville", lambda v: isinstance(v, str) and not v.isdigit()),
        lat=_trouver(plat, "lat"), lon=_trouver(plat, "lon"),
        dpe=_trouver(plat, "dpe", lambda v: isinstance(v, str) and len(v) <= 3),
        description=_trouver(plat, "description", lambda v: isinstance(v, str)),
        mode_vente=_mode(_trouver(plat, "transaction"), mode_defaut),
        date_vente=_trouver(plat, "date", lambda v: isinstance(v, str)),
    )


def extraire(data, source: str, base_url: str, mode_defaut: str = "vente") -> list[Annonce]:
    """Toutes les annonces reconnues dans un document JSON, quelle que soit sa forme."""
    out, pile = [], [data]
    while pile:
        n = pile.pop()
        if isinstance(n, list):
            pile.extend(reversed(n))
        elif isinstance(n, dict):
            a = objet_vers_annonce(n, source, base_url, mode_defaut)
            if a:
                out.append(a)
            else:
                pile.extend(v for v in n.values() if isinstance(v, (dict, list)))
    return out


def cles_exemple(data, limite: int = 40) -> list[str]:
    """Pour le diagnostic : les clés du premier objet de la plus grande liste du JSON."""
    meilleur, pile = [], [data]
    while pile:
        n = pile.pop()
        if isinstance(n, list):
            if len(n) > len(meilleur) and n and isinstance(n[0], dict):
                meilleur = n
            pile.extend(n)
        elif isinstance(n, dict):
            pile.extend(v for v in n.values() if isinstance(v, (dict, list)))
    return list(_aplatir(meilleur[0]))[:limite] if meilleur else []
