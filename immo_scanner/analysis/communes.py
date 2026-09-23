"""Classement des marchés (communes) : où acheter ?

Ne nécessite aucune annonce : uniquement DVF (+ loyers si importés). Pour chaque
commune et type de bien : prix médian au m², volume de ventes, tendance annuelle,
loyer au m² et rendement brut théorique.
"""
from __future__ import annotations

from datetime import date, timedelta

from ..config import Config
from ..util import clamp, median, parent_commune
from .market import MarketModel


def classer_communes(conn, cfg: Config, departements=None, type_local="appartement",
                     min_ventes: int = 30, today: date | None = None) -> list[dict]:
    market = MarketModel(conn, cfg.marche, today)
    since = (market.today - timedelta(days=int(365.25 * cfg.marche.annees_comparables))).isoformat()
    where, params = "type_local=? AND date_mutation>=? AND neuf=0", [type_local, since]
    if departements:
        where += f" AND code_departement IN ({','.join('?' * len(departements))})"
        params += list(departements)
    groups: dict[str, dict] = {}
    for r in conn.execute(f"SELECT code_commune, nom_commune, prix_m2 FROM dvf_ventes WHERE {where}", params):
        g = groups.setdefault(r[0], {"nom": r[1], "prix": []})
        g["prix"].append(r[2])

    loyers = {(r[0], r[1]): r[2] for r in conn.execute("SELECT code_commune, type_local, loyer_m2 FROM loyers")}
    out = []
    for code, g in groups.items():
        if len(g["prix"]) < min_ventes:
            continue
        pm2 = median(g["prix"])
        tend = market.tendance(code, type_local)
        loyer_m2 = loyers.get((code, type_local)) or loyers.get((parent_commune(code), type_local))
        rdt = loyer_m2 * 12 / pm2 if loyer_m2 else None
        t = tend.taux_annuel if tend.niveau in ("commune", "ville") else None
        comp = [clamp((rdt - 0.03) / 0.07) * 0.5 if rdt else 0.0,
                clamp(((t or 0) + 0.03) / 0.08) * 0.35,
                clamp(len(g["prix"]) / cfg.marche.annees_comparables / 300) * 0.15]
        poids = (0.5 if rdt else 0) + 0.35 + 0.15
        out.append({
            "code_commune": code, "commune": g["nom"], "type_local": type_local,
            "prix_m2_median": round(pm2), "ventes_par_an": round(len(g["prix"]) / cfg.marche.annees_comparables, 1),
            "tendance_annuelle": t, "loyer_m2": loyer_m2, "rendement_brut_theorique": rdt,
            "score": round(100 * sum(comp) / poids, 1),
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    return out
