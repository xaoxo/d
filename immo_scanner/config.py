"""Hypothèses de calcul (financement, charges, fiscalité, scoring).

Toutes les valeurs peuvent être surchargées par un fichier TOML
(voir ``config.example.toml``) passé avec ``immo --config``.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path


@dataclass
class Financement:
    apport_pct: float = 0.10          # apport en % du coût total
    taux_credit: float = 0.035        # taux nominal annuel
    duree_annees: int = 20
    taux_assurance: float = 0.0030    # assurance emprunteur, % du capital emprunté / an


@dataclass
class Frais:
    notaire_ancien: float = 0.078
    notaire_neuf: float = 0.025
    vacance_mois: float = 1.0                 # mois sans loyer par an
    gestion_pct: float = 0.0                  # frais d'agence de gestion (0 = autogestion)
    assurance_pno: float = 150.0              # €/an
    provision_entretien_pct: float = 0.05     # % des loyers
    taxe_fonciere_mois_loyer: float = 1.0     # estimation si inconnue
    charges_copro_m2_an: float = 25.0         # estimation si inconnues (appartement)
    part_charges_non_recuperables: float = 0.4
    prime_meuble_pct: float = 0.10            # surloyer d'un meublé


@dataclass
class Fiscalite:
    regime: str = "micro_foncier"   # micro_foncier | reel | lmnp_micro | lmnp_reel
    tmi: float = 0.30
    prelevements_sociaux: float = 0.172   # à vérifier selon la loi de finances en vigueur
    mobilier: float = 5000.0              # pour LMNP


@dataclass
class Marche:
    annees_comparables: float = 3.0       # profondeur des ventes DVF utilisées
    rayons_m: tuple = (300, 600, 1000, 2000)
    min_comparables: int = 8              # en dessous : niveau géographique supérieur
    cible_comparables: int = 20           # on élargit le rayon jusqu'à atteindre ce nombre
    prix_m2_min: float = 300.0            # filtres anti-valeurs aberrantes DVF
    prix_m2_max: float = 30000.0
    surface_min: float = 9.0
    ajustement_dpe: dict = field(default_factory=lambda: {
        "A": 0.05, "B": 0.03, "C": 0.01, "D": 0.0, "E": -0.03, "F": -0.07, "G": -0.12,
    })
    # Loyer : élasticité à la surface (les petites surfaces se louent plus cher au m²)
    surface_ref_appartement: float = 52.0
    surface_ref_maison: float = 92.0
    elasticite_loyer_surface: float = -0.2


@dataclass
class Travaux:
    prime_bien_renove: float = 0.10       # un bien rénové se vend ~10 % au-dessus de la médiane
    rafraichissement_m2: float = 300.0
    renovation_m2: float = 900.0
    renovation_lourde_m2: float = 1500.0
    renovation_energetique_m2: dict = field(default_factory=lambda: {"E": 150.0, "F": 350.0, "G": 500.0})


@dataclass
class Projection:
    horizon_annees: int = 10
    tendance_min: float = -0.05   # bornes de la tendance projetée par an
    tendance_max: float = 0.06


@dataclass
class Scoring:
    poids: dict = field(default_factory=lambda: {
        "decote": 0.30, "rendement": 0.25, "tri": 0.15,
        "tendance": 0.15, "cashflow": 0.05, "liquidite": 0.10,
    })


@dataclass
class Config:
    financement: Financement = field(default_factory=Financement)
    frais: Frais = field(default_factory=Frais)
    fiscalite: Fiscalite = field(default_factory=Fiscalite)
    marche: Marche = field(default_factory=Marche)
    travaux: Travaux = field(default_factory=Travaux)
    projection: Projection = field(default_factory=Projection)
    scoring: Scoring = field(default_factory=Scoring)


def _apply(obj, values: dict) -> None:
    known = {f.name: f for f in fields(obj)}
    for key, value in values.items():
        if key not in known:
            raise ValueError(f"Paramètre de configuration inconnu : {key}")
        current = getattr(obj, key)
        if is_dataclass(current):
            _apply(current, value)
        elif isinstance(current, dict):
            merged = dict(current)
            merged.update(value)
            setattr(obj, key, merged)
        elif isinstance(current, tuple):
            setattr(obj, key, tuple(value))
        else:
            setattr(obj, key, type(current)(value) if current is not None else value)


def load_config(path: str | Path | None = None) -> Config:
    cfg = Config()
    if path:
        with open(path, "rb") as fh:
            _apply(cfg, tomllib.load(fh))
    return cfg
