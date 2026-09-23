"""Jeu de données FICTIF pour tester l'outil sans téléchargement.

Les communes de démonstration utilisent le département « 99 » (inexistant en
France métropolitaine) afin de ne jamais être confondues avec de vraies données.
"""
from __future__ import annotations

import math
import random
from datetime import date, timedelta

from .sources.base import Annonce

COMMUNES = [
    # code, nom, cp, lat, lon, prix m² appart, prix m² maison, tendance/an, loyer m² appart, loyer m² maison
    ("99001", "Démoville", "99100", 45.00, 3.00, 3200, 2700, 0.035, 14.5, 11.0),
    ("99002", "Fictif-sur-Mer", "99200", 45.30, 3.40, 4800, 4200, 0.045, 17.0, 13.5),
    ("99003", "Exemple-les-Bains", "99300", 44.70, 2.60, 1500, 1350, -0.01, 10.5, 8.5),
    ("99004", "Testbourg", "99400", 45.10, 2.80, 2200, 1900, 0.015, 12.5, 9.5),
]


def generer_dvf(rng: random.Random, today: date) -> list[dict]:
    ventes = []
    for code, nom, cp, lat, lon, pa, pm, tend, _, _ in COMMUNES:
        for type_local, base in (("appartement", pa), ("maison", pm)):
            for i in range(rng.randint(350, 600) if type_local == "appartement" else rng.randint(150, 300)):
                d = today - timedelta(days=rng.randint(0, 5 * 365))
                age = (today - d).days / 365.25
                surface = max(12, rng.gauss(55 if type_local == "appartement" else 105, 22))
                pm2 = base / (1 + tend) ** age * math.exp(rng.gauss(0, 0.16))
                pm2 *= (surface / 55) ** -0.08
                ventes.append({
                    "id_mutation": f"DEMO-{code}-{type_local[0]}-{i}", "date_mutation": d.isoformat(),
                    "annee": d.year, "valeur": round(pm2 * surface, -2), "code_postal": cp,
                    "code_commune": code, "nom_commune": nom, "code_departement": "99",
                    "type_local": type_local, "surface": round(surface), "pieces": max(1, round(surface / 22)),
                    "surface_terrain": round(rng.uniform(200, 1500)) if type_local == "maison" else None,
                    "neuf": 0, "lat": lat + rng.gauss(0, 0.012), "lon": lon + rng.gauss(0, 0.012),
                    "prix_m2": pm2 * 1.0,
                })
                ventes[-1]["prix_m2"] = ventes[-1]["valeur"] / ventes[-1]["surface"]
    return ventes


DESCRIPTIONS = [
    "Bel appartement lumineux, proche commerces et transports.",
    "Appartement à rénover, gros potentiel, prix négociable.",
    "Maison familiale avec jardin, quelques travaux de décoration à prévoir.",
    "Vendu loué, locataire en place depuis 3 ans, idéal investisseur.",
    "Vente urgente pour cause de mutation. Appartement à rafraîchir.",
    "Maison à réhabiliter entièrement, terrain constructible divisible.",
    "Studio étudiant proche université, forte demande locative.",
    "Viager occupé, bouquet intéressant.",
    "Succession : maison de ville avec combles aménageables.",
    "Appartement refait à neuf, aucun travaux.",
]


def generer_annonces(rng: random.Random, n: int = 60) -> list[Annonce]:
    out = []
    for i in range(n):
        code, nom, cp, lat, lon, pa, pm, _, la, lm = rng.choice(COMMUNES)
        type_local = rng.choice(["appartement"] * 3 + ["maison"])
        surface = round(max(15, rng.gauss(50 if type_local == "appartement" else 100, 25)))
        base = pa if type_local == "appartement" else pm
        desc = rng.choice([d for d in DESCRIPTIONS if not (type_local == "maison" and "Studio" in d)])
        if type_local == "maison":
            desc = desc.replace("Appartement", "Maison").replace("appartement", "maison")
        else:
            desc = desc.replace("Maison familiale", "Appartement familial").replace("Maison", "Appartement")
        prix = round(base * surface * rng.uniform(0.72, 1.25), -3)
        dpe = rng.choice("BCCDDDEEFG")
        a = Annonce(
            source="demo", source_id=f"demo-{i}", url=None,
            titre=f"{type_local.capitalize()} {max(1, round(surface / 22))} pièces {surface} m² — {nom}",
            type_local=type_local, prix=prix, surface=surface, pieces=max(1, round(surface / 22)),
            code_postal=cp, ville=nom, lat=lat + rng.gauss(0, 0.01), lon=lon + rng.gauss(0, 0.01),
            dpe=dpe, description=desc + f" DPE : {dpe}.",
            loyer_actuel=round(la * surface * 0.95) if "loué" in desc else None,
            surface_terrain=round(rng.uniform(150, 2500)) if type_local == "maison" else None,
        )
        out.append(a)
    return out


def generer_loyers() -> list[tuple]:
    rows = []
    for code, _, _, _, _, _, _, _, la, lm in COMMUNES:
        rows.append((code, "appartement", la, la * 0.8, la * 1.2, 500))
        rows.append((code, "maison", lm, lm * 0.8, lm * 1.2, 200))
    return rows
