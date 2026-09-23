import gzip

from immo_scanner import db
from immo_scanner.config import Marche
from immo_scanner.sources import dvf, loyers

from .test_dvf import HEADER, row


def test_departement_depuis_cp():
    assert dvf.departement_depuis_cp("33000") == "33"
    assert dvf.departement_depuis_cp("97400") == "974"
    assert dvf.departement_depuis_cp("20000") == "2A"
    assert dvf.departement_depuis_cp("20200") == "2B"
    assert dvf.departement_depuis_cp(None) is None


def test_choisir_ressources_carte_des_loyers():
    datasets = [
        {"title": "Autre jeu", "resources": []},
        {"title": "Carte des loyers : indicateurs de loyers d'annonce par commune en 2023", "resources": [
            {"title": "pred-app-mef-dhup.csv", "format": "csv", "url": "https://x/2023-app.csv"}]},
        {"title": "Carte des loyers : indicateurs de loyers d'annonce par commune en 2024", "resources": [
            {"title": "pred-app12-mef-dhup.csv", "format": "csv", "url": "https://x/app12.csv"},
            {"title": "pred-app-mef-dhup.csv", "format": "csv", "url": "https://x/app.csv"},
            {"title": "pred-mai-mef-dhup.csv", "format": "csv", "url": "https://x/mai.csv"},
            {"title": "notice.pdf", "format": "pdf", "url": "https://x/notice.pdf"}]},
    ]
    assert loyers.choisir_ressources(datasets) == {"appartement": "https://x/app.csv",
                                                    "maison": "https://x/mai.csv"}


def test_assurer_departement_telecharge_une_seule_fois(tmp_path, monkeypatch):
    fichier = tmp_path / "33.csv.gz"
    with gzip.open(fichier, "wt", encoding="utf-8") as fh:
        fh.write("\n".join([HEADER, row("M1", "Vente", 200000, "Appartement", 50, 2, lot="1")]))
    appels = []

    def faux_fetch(annee, dep, cache):
        appels.append((annee, dep))
        return fichier

    monkeypatch.setattr(dvf, "fetch", faux_fetch)
    conn = db.connect(":memory:")
    assert dvf.assurer_departement(conn, "33", tmp_path, Marche(), log=lambda *_: None) >= 1
    assert len(appels) == 5
    assert dvf.assurer_departement(conn, "33", tmp_path, Marche(), log=lambda *_: None) == 0
    assert len(appels) == 5
    assert conn.execute("SELECT code_postal FROM communes").fetchone()[0] == "33000"
    assert dvf.assurer_departement(conn, "67", tmp_path, Marche(), log=lambda *_: None) == 0
