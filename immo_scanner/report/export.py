"""Exports CSV / JSON et affichage terminal des résultats."""
from __future__ import annotations

import csv
import json

QUERY = """
SELECT a.*, an.score, an.verdict, an.valeur_estimee, an.decote_pct, an.rendement_brut,
       an.rendement_net, an.rendement_net_net, an.cashflow_mensuel, an.tri,
       an.plus_value_horizon, an.tendance_annuelle, an.fiabilite, an.details
FROM analyses an JOIN annonces a ON a.id = an.annonce_id
WHERE a.active = 1 {filtre}
ORDER BY {ordre} DESC NULLS LAST
LIMIT ?
"""

ORDRES = {"score": "an.score", "decote": "an.decote_pct", "rendement": "an.rendement_net",
          "cashflow": "an.cashflow_mensuel", "tri": "an.tri", "plus_value": "an.plus_value_horizon"}


def resultats(conn, ordre="score", limite=100, departement=None, type_local=None,
              prix_max=None, score_min=None) -> list[dict]:
    filtre, params = "", []
    if departement:
        filtre += " AND substr(a.code_commune,1,?) = ?"
        params += [len(departement), departement]
    if type_local:
        filtre += " AND a.type_local = ?"
        params.append(type_local)
    if prix_max:
        filtre += " AND a.prix <= ?"
        params.append(prix_max)
    if score_min is not None:
        filtre += " AND an.score >= ?"
        params.append(score_min)
    sql = QUERY.format(filtre=filtre, ordre=ORDRES.get(ordre, "an.score"))
    rows = [dict(r) for r in conn.execute(sql, params + [limite])]
    for r in rows:
        r["details"] = json.loads(r["details"]) if r.get("details") else {}
    return rows


def pct(x, d=1):
    return "—" if x is None else f"{x * 100:.{d}f}%"


def eur(x):
    return "—" if x is None else f"{x:,.0f} €".replace(",", " ")


def table_terminal(rows) -> str:
    head = f"{'Score':>5}  {'Verdict':<18} {'Ville':<20} {'Type':<5} {'m²':>5} {'Prix':>11} " \
           f"{'Valeur est.':>11} {'Décote':>7} {'Rdt brut':>8} {'Rdt net':>7} {'CF/mois':>8} {'TRI':>6}"
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append(
            f"{r['score']:>5.1f}  {r['verdict'][:18]:<18} {(r['ville'] or r['code_commune'] or '?')[:20]:<20} "
            f"{(r['type_local'] or '?')[:5]:<5} {r['surface']:>5.0f} {eur(r['prix']):>11} "
            f"{eur(r['valeur_estimee']):>11} {pct(r['decote_pct'], 0):>7} {pct(r['rendement_brut']):>8} "
            f"{pct(r['rendement_net']):>7} {eur(r['cashflow_mensuel']):>8} {pct(r['tri']):>6}")
    return "\n".join(lines)


def to_csv(rows, path) -> None:
    cols = ["score", "verdict", "titre", "url", "ville", "code_postal", "code_commune", "type_local",
            "surface", "pieces", "prix", "prix_initial", "dpe", "valeur_estimee", "decote_pct",
            "rendement_brut", "rendement_net", "rendement_net_net", "cashflow_mensuel", "tri",
            "plus_value_horizon", "tendance_annuelle", "fiabilite"]
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh, delimiter=";")
        w.writerow(cols + ["loyer_estime", "travaux_estimes", "signaux", "alertes"])
        for r in rows:
            d = r["details"]
            w.writerow([r.get(c) for c in cols] + [
                d.get("loyer_mensuel_estime"), d.get("travaux_estimes"),
                " | ".join(d.get("signaux", [])), " | ".join(d.get("alertes", []))])
