"""Calculs financiers : crédit, rendements, fiscalité locative, plus-value, TRI."""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Config


def mensualite(capital: float, taux_annuel: float, annees: int) -> float:
    n = annees * 12
    if capital <= 0 or n <= 0:
        return 0.0
    t = taux_annuel / 12
    return capital / n if t == 0 else capital * t / (1 - (1 + t) ** -n)


def capital_restant(capital: float, taux_annuel: float, annees: int, apres_mois: int) -> float:
    if capital <= 0:
        return 0.0
    m = mensualite(capital, taux_annuel, annees)
    t = taux_annuel / 12
    k = min(apres_mois, annees * 12)
    if t == 0:
        return max(capital - m * k, 0.0)
    return max(capital * (1 + t) ** k - m * ((1 + t) ** k - 1) / t, 0.0)


def interets_annee(capital: float, taux_annuel: float, annees: int, annee: int) -> float:
    """Intérêts payés pendant l'année ``annee`` (1 = première année)."""
    debut = capital_restant(capital, taux_annuel, annees, (annee - 1) * 12)
    fin = capital_restant(capital, taux_annuel, annees, annee * 12)
    paye = mensualite(capital, taux_annuel, annees) * 12 if annee <= annees else 0.0
    return max(paye - (debut - fin), 0.0)


def impot_plus_value(prix_achat: float, prix_vente: float, annees: float,
                     travaux: float = 0.0, frais_acquisition: float | None = None) -> float:
    """Impôt sur la plus-value immobilière des particuliers (résidence secondaire /
    locatif) : 19 % IR + 17,2 % prélèvements sociaux, avec abattements pour durée
    de détention, forfaits frais (7,5 %) et travaux (15 % après 5 ans).
    La surtaxe sur les plus-values > 50 000 € n'est pas modélisée."""
    frais = frais_acquisition if frais_acquisition is not None else 0.075 * prix_achat
    frais = max(frais, 0.075 * prix_achat)
    tr = max(travaux, 0.15 * prix_achat) if annees > 5 else travaux
    pv = prix_vente - (prix_achat + frais + tr)
    if pv <= 0:
        return 0.0
    y = int(annees)
    ab_ir = min(1.0, 0.06 * max(0, min(y, 21) - 5) + (0.04 if y >= 22 else 0))
    ab_ps = 0.0165 * max(0, min(y, 21) - 5) + (0.016 if y >= 22 else 0) + 0.09 * max(0, min(y, 30) - 22)
    ab_ps = min(1.0, ab_ps)
    return pv * (1 - ab_ir) * 0.19 + pv * (1 - ab_ps) * 0.172


def tri(flux: list[float]) -> float | None:
    """Taux de rendement interne annuel (bissection)."""
    if not flux or all(f >= 0 for f in flux) or all(f <= 0 for f in flux):
        return None

    def van(r):
        return sum(f / (1 + r) ** i for i, f in enumerate(flux))

    lo, hi = -0.99, 10.0
    if van(lo) * van(hi) > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if van(lo) * van(mid) <= 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


@dataclass
class Bilan:
    cout_total: float
    frais_notaire: float
    emprunt: float
    apport: float
    mensualite_credit: float
    loyer_mensuel: float
    loyers_annuels_nets_vacance: float
    charges_annuelles: dict
    rendement_brut: float
    rendement_net: float
    impot_annuel: float
    rendement_net_net: float
    cashflow_mensuel: float
    cashflow_mensuel_apres_impot: float
    valeur_revente: float
    plus_value_brute: float
    impot_plus_value: float
    plus_value_nette: float
    enrichissement: float
    tri: float | None
    horizon: int


def bilan(cfg: Config, prix: float, travaux: float, loyer_mensuel: float, neuf: bool,
          valeur_actuelle: float, tendance: float, surface: float, type_local: str,
          charges_copro: float | None = None, taxe_fonciere: float | None = None) -> Bilan:
    f, fin, fis = cfg.frais, cfg.financement, cfg.fiscalite
    notaire = prix * (f.notaire_neuf if neuf else f.notaire_ancien)
    cout = prix + notaire + travaux
    apport = cout * fin.apport_pct
    emprunt = cout - apport
    mens = mensualite(emprunt, fin.taux_credit, fin.duree_annees)
    assurance = emprunt * fin.taux_assurance / 12

    meuble = fis.regime.startswith("lmnp")
    loyer = loyer_mensuel * (1 + f.prime_meuble_pct if meuble else 1)
    brut_annuel = loyer * 12
    encaisse = loyer * (12 - f.vacance_mois)

    if charges_copro is None:
        charges_copro = f.charges_copro_m2_an * surface if type_local == "appartement" else 0.0
    tf = taxe_fonciere if taxe_fonciere is not None else f.taxe_fonciere_mois_loyer * loyer_mensuel
    charges = {
        "copro_non_recuperable": charges_copro * f.part_charges_non_recuperables,
        "taxe_fonciere": tf,
        "assurance_pno": f.assurance_pno,
        "gestion": encaisse * f.gestion_pct,
        "entretien": encaisse * f.provision_entretien_pct,
    }
    total_charges = sum(charges.values())
    net_annuel = encaisse - total_charges

    # --- impôt (année 1) ---
    ps = fis.prelevements_sociaux
    interets = interets_annee(emprunt, fin.taux_credit, fin.duree_annees, 1) + assurance * 12
    if fis.regime == "micro_foncier":
        base = encaisse * 0.70
    elif fis.regime == "reel":
        base = encaisse - total_charges - interets
    elif fis.regime == "lmnp_micro":
        base = encaisse * 0.50
    elif fis.regime == "lmnp_reel":
        amort = prix * 0.85 / 30 + travaux / 15 + fis.mobilier / 7
        base = encaisse - total_charges - interets - amort
    else:
        raise ValueError(f"Régime fiscal inconnu : {fis.regime}")
    impot = max(base, 0.0) * (fis.tmi + ps)

    cash = (net_annuel - (mens + assurance) * 12) / 12
    cash_ai = cash - impot / 12

    # --- revente à l'horizon ---
    h = cfg.projection.horizon_annees
    t = max(cfg.projection.tendance_min, min(cfg.projection.tendance_max, tendance or 0.0))
    revente = valeur_actuelle * (1 + t) ** h
    pv_brute = revente - cout
    ipv = impot_plus_value(prix, revente, h, travaux=travaux, frais_acquisition=notaire)
    crd = capital_restant(emprunt, fin.taux_credit, fin.duree_annees, h * 12)
    flux = [-apport] + [cash_ai * 12] * h
    flux[-1] += revente - crd - ipv
    enrichissement = sum(flux)

    return Bilan(
        cout_total=cout, frais_notaire=notaire, emprunt=emprunt, apport=apport,
        mensualite_credit=mens + assurance, loyer_mensuel=loyer,
        loyers_annuels_nets_vacance=encaisse, charges_annuelles=charges,
        rendement_brut=brut_annuel / prix if prix else 0.0,
        rendement_net=net_annuel / cout if cout else 0.0,
        impot_annuel=impot,
        rendement_net_net=(net_annuel - impot) / cout if cout else 0.0,
        cashflow_mensuel=cash, cashflow_mensuel_apres_impot=cash_ai,
        valeur_revente=revente, plus_value_brute=pv_brute, impot_plus_value=ipv,
        plus_value_nette=pv_brute - ipv, enrichissement=enrichissement,
        tri=tri(flux), horizon=h,
    )
