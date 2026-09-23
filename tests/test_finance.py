import pytest

from immo_scanner.analysis import finance
from immo_scanner.config import Config


def test_mensualite_et_capital_restant():
    m = finance.mensualite(100_000, 0.035, 20)
    assert m == pytest.approx(579.96, abs=0.01)
    assert finance.capital_restant(100_000, 0.035, 20, 240) == pytest.approx(0, abs=1e-6)
    assert finance.capital_restant(100_000, 0.0, 10, 60) == pytest.approx(50_000)


def test_interets_premiere_annee():
    # environ 3,4 k€ d'intérêts la première année sur 100 k€ à 3,5 %
    assert 3300 < finance.interets_annee(100_000, 0.035, 20, 1) < 3500


def test_tri():
    assert finance.tri([-100, 110]) == pytest.approx(0.10, abs=1e-6)
    assert finance.tri([100, 50]) is None


def test_impot_plus_value_abattements():
    assert finance.impot_plus_value(100_000, 90_000, 5) == 0
    court = finance.impot_plus_value(100_000, 200_000, 3)
    assert court == pytest.approx((200_000 - 107_500) * 0.362)
    # 22 ans : exonéré d'IR, PS abattus de 28 % ; forfaits 7,5 % frais + 15 % travaux
    assert finance.impot_plus_value(100_000, 200_000, 22) == pytest.approx(77_500 * 0.72 * 0.172)
    assert finance.impot_plus_value(100_000, 200_000, 30) == pytest.approx(0)


def test_bilan_coherent():
    cfg = Config()
    b = finance.bilan(cfg, prix=100_000, travaux=0, loyer_mensuel=600, neuf=False,
                      valeur_actuelle=110_000, tendance=0.02, surface=40, type_local="appartement")
    assert b.rendement_brut == pytest.approx(0.072)
    assert b.rendement_net < b.rendement_brut
    assert b.rendement_net_net < b.rendement_net
    assert b.cout_total == pytest.approx(107_800)
    assert b.tri is not None and b.enrichissement > 0


def test_regimes_fiscaux():
    for regime in ("micro_foncier", "reel", "lmnp_micro", "lmnp_reel"):
        cfg = Config()
        cfg.fiscalite.regime = regime
        b = finance.bilan(cfg, 150_000, 10_000, 750, False, 160_000, 0.01, 45, "appartement")
        assert b.impot_annuel >= 0
    cfg.fiscalite.regime = "inconnu"
    with pytest.raises(ValueError):
        finance.bilan(cfg, 150_000, 0, 750, False, 160_000, 0.01, 45, "appartement")
