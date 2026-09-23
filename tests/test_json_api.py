import json

from immo_scanner.sources import json_api, sites
from immo_scanner.sources.web import Crawler

from .test_surveillance import serveur  # noqa: F401 - fixture

DONNEES = {
    "nbTotal": 2,
    "annonceResumeDto": [
        {"id": 123, "typeTransaction": "VNI", "typeBien": "APPARTEMENT", "prixAffiche": 185000,
         "prixM2": 3700, "surface": 50, "surfaceTerrain": None, "nbPieces": 2,
         "localisation": {"codePostal": "33000", "libelleCommune": "Bordeaux", "codeInsee": "33063"},
         "urlDetailAnnonceFr": "/fr/annonce-immo-vente-appartement-bordeaux-33-123",
         "dateFinOffres": "2026-10-20"},
        {"id": 124, "typeTransaction": "VENTE", "typeBien": "MAISON", "prixAffiche": 320000,
         "surface": 110, "surfaceTerrain": 600,
         "localisation": {"codePostal": "33600", "libelleCommune": "Pessac"},
         "urlDetailAnnonceFr": "/fr/annonce-immo-vente-maison-pessac-33-124"},
        {"id": 125, "typeTransaction": "LOCATION", "prixAffiche": 900},
    ],
}


def test_reconnaissance_generique():
    a, b = json_api.extraire(DONNEES, "notaires", "https://www.immobilier.notaires.fr")
    assert (a.prix, a.surface, a.code_postal, a.ville, a.code_commune) == (185000, 50, "33000", "Bordeaux", "33063")
    assert a.mode_vente == "offre" and a.date_vente == "2026-10-20"
    assert a.url == "https://www.immobilier.notaires.fr/fr/annonce-immo-vente-appartement-bordeaux-33-123"
    assert a.normalized().type_local == "appartement"
    assert (b.mode_vente, b.surface_terrain, b.normalized().type_local) == ("vente", 600, "maison")


def test_cles_exemple_pour_diagnostic():
    cles = json_api.cles_exemple({"resultats": [{"a": 1, "loc": {"cp": "1"}}, {"a": 2}]})
    assert cles == ["a", "loc.cp"]


def test_collecte_par_api(serveur, tmp_path):  # noqa: F811
    # le serveur de test sert le même dossier temporaire : on y dépose 2 pages d'API
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "p1.json").write_text(json.dumps(DONNEES))
    (tmp_path / "api" / "p2.json").write_text(json.dumps({"annonceResumeDto": []}))
    site = sites.Site("notaires-test", "test", [], liens_annonce=r"$^", api=serveur + "/api/p{page}.json")
    rep = sites.collecter(site, Crawler(delay=0), ["33"], log=lambda *_: None)
    assert len(rep.annonces) == 2 and rep.api_statut.startswith("OK")
