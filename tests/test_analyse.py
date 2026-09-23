import random
from datetime import date

import pytest

from immo_scanner import db, demo
from immo_scanner.analysis.analyzer import analyser
from immo_scanner.analysis.communes import classer_communes
from immo_scanner.analysis.market import MarketModel
from immo_scanner.cli import main
from immo_scanner.config import Config, load_config
from immo_scanner.sources import dvf


@pytest.fixture(scope="module")
def conn():
    c = db.connect(":memory:")
    ventes = demo.generer_dvf(random.Random(1), date(2026, 6, 30))
    c.executemany(f"INSERT INTO dvf_ventes ({','.join(dvf.COLUMNS)}) VALUES ({','.join('?' * len(dvf.COLUMNS))})",
                  [[v[k] for k in dvf.COLUMNS] for v in ventes])
    c.executemany("INSERT INTO loyers VALUES (?,?,?,?,?,?)", demo.generer_loyers())
    dvf.rebuild_communes(c)
    return c


def annonce(**kw):
    a = {"id": "t:1", "type_local": "appartement", "surface": 50, "prix": 160_000, "prix_initial": None,
         "code_commune": "99001", "lat": 45.0, "lon": 3.0, "dpe": "D", "titre": "", "description": "",
         "loyer_actuel": None, "charges_annuelles": None, "taxe_fonciere": None, "neuf": 0,
         "surface_terrain": None}
    a.update(kw)
    return a


def test_estimation_proche_du_marche(conn):
    est = MarketModel(conn, Config().marche).estimer("appartement", 50, "99001", 45.0, 3.0)
    assert 2800 < est.prix_m2 < 3700          # prix de base de la commune démo : 3 200 €/m²
    assert est.nb_comparables >= 20


def test_tendance_detectee(conn):
    t = MarketModel(conn, Config().marche).tendance("99002", "appartement")
    assert t.niveau == "commune" and 0.01 < t.taux_annuel < 0.08


def test_bien_sous_evalue_mieux_note(conn):
    cfg, m = Config(), MarketModel(conn, Config().marche)
    bon = analyser(conn, cfg, m, annonce(prix=115_000))
    cher = analyser(conn, cfg, m, annonce(prix=210_000))
    assert bon["decote_pct"] > 0.1 > cher["decote_pct"]
    assert bon["score"] > cher["score"]
    assert bon["rendement_brut"] > cher["rendement_brut"]


def test_signaux_et_alertes(conn):
    cfg, m = Config(), MarketModel(conn, Config().marche)
    r = analyser(conn, cfg, m, annonce(dpe="G", prix_initial=180_000,
                                       description="Vente urgente, appartement à rénover"))
    import json
    d = json.loads(r["details"])
    assert d["travaux_estimes"] == pytest.approx(50 * (900 + 500))
    assert any("Baisse de prix" in s for s in d["signaux"])
    assert any("pressé" in s for s in d["signaux"])
    assert any("DPE G" in s for s in d["alertes"])
    viager = analyser(conn, cfg, m, annonce(prix=40_000, description="Viager occupé, bouquet"))
    assert viager["verdict"].startswith("À vérifier")


def test_classement_communes(conn):
    rows = classer_communes(conn, Config(), ["99"], "appartement", today=date(2026, 6, 30))
    assert {r["code_commune"] for r in rows} == {"99001", "99002", "99003", "99004"}
    assert all(r["rendement_brut_theorique"] for r in rows)


def test_config_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[financement]\ntaux_credit = 0.04\n[fiscalite]\nregime = "lmnp_micro"\n'
                 '[scoring.poids]\ndecote = 0.5\n')
    cfg = load_config(p)
    assert cfg.financement.taux_credit == 0.04 and cfg.fiscalite.regime == "lmnp_micro"
    assert cfg.scoring.poids["decote"] == 0.5 and cfg.scoring.poids["rendement"] == 0.25


def test_cli_demo_bout_en_bout(tmp_path, capsys):
    base = str(tmp_path / "t.db")
    main(["--db", base, "demo", "--sortie", str(tmp_path / "r.html")])
    html = (tmp_path / "r.html").read_text()
    assert "Opportunités immobilières" in html and "Démoville" in html
    main(["--db", base, "top", "--limite", "5", "--csv", str(tmp_path / "e.csv")])
    main(["--db", base, "marches", "--departements", "99"])
    main(["--db", base, "estimer", "--prix", "120000", "--surface", "45", "--cp", "99100",
          "--ville", "Démoville", "--dpe", "E"])
    out = capsys.readouterr().out
    assert "Score :" in out and "Rendement brut" in out and "Démoville" in out
