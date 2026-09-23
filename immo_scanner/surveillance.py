"""Surveillance en continu des sites : collecte, analyse, alertes, rapport."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from . import alertes
from .analysis.analyzer import analyser_tout
from .config import Config
from .report import export, html
from .sources import base, dvf, sites as sites_mod
from .sources.web import Crawler


def _ligne(conn, annonce_id: str) -> dict | None:
    r = conn.execute(
        "SELECT a.*, an.score, an.verdict, an.valeur_estimee, an.decote_pct, an.rendement_brut, "
        "an.cashflow_mensuel, an.details FROM annonces a JOIN analyses an ON an.annonce_id = a.id "
        "WHERE a.id = ?", (annonce_id,)).fetchone()
    if not r:
        return None
    r = dict(r)
    r["details"] = json.loads(r["details"]) if r.get("details") else {}
    return r


def a_alerter(cfg: Config, r: dict, evenement: str, ancien_prix: float | None) -> bool:
    c = cfg.alertes
    if evenement == "baisse":
        return bool(ancien_prix) and r["prix"] <= ancien_prix * (1 - c.baisse_min)
    if r.get("verdict") == "Données insuffisantes":
        return False
    offre_max = (r.get("details") or {}).get("offre_max")
    return ((r.get("score") or 0) >= c.score_min
            or (r.get("decote_pct") is not None and r["decote_pct"] >= c.decote_min)
            or (offre_max is not None and offre_max > r["prix"]))


def traiter_evenements(conn, cfg: Config, evenements, log=print) -> int:
    envoyees = 0
    for ev in evenements:
        evenement, annonce_id = ev[0], ev[1]
        ancien = ev[2] if len(ev) > 2 else None
        r = _ligne(conn, annonce_id)
        if not r or not a_alerter(cfg, r, evenement, ancien):
            continue
        cle = (annonce_id, evenement, r["prix"])
        if conn.execute("SELECT 1 FROM alertes_envoyees WHERE annonce_id=? AND evenement=? AND prix=?",
                        cle).fetchone():
            continue
        titre, message = alertes.formater(r, evenement, ancien)
        alertes.envoyer(cfg.alertes, titre, message, log)
        conn.execute("INSERT INTO alertes_envoyees VALUES (?,?,?,?)",
                     cle + (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
        envoyees += 1
    conn.commit()
    return envoyees


def passage(conn, cfg: Config, sites: list, departements, cache: Path, rapport: str | None,
            delai: float = 2.0, log=print) -> dict:
    """Un passage complet sur tous les sites. Renvoie un résumé."""
    crawler = Crawler(delay=delai)
    deja = sites_mod.urls_recentes(conn)
    evenements, total, deps_faits = [], 0, set()
    for site in sites:
        rep = sites_mod.collecter(site, crawler, departements, deja, log)
        for a in rep.annonces:
            dep = dvf.departement_depuis_cp(a.normalized().code_postal)
            if dep and dep not in deps_faits:
                deps_faits.add(dep)
                dvf.assurer_departement(conn, dep, cache, cfg.marche, log)
        base.save(conn, rep.annonces, evenements)
        total += len(rep.annonces)
        if rep.erreurs:
            log(f"  {site.nom} : {len(rep.erreurs)} erreur(s), ex. {rep.erreurs[0]}")
    if evenements:
        ids = [e[1] for e in evenements]
        analyser_tout(conn, cfg, f"id IN ({','.join('?' * len(ids))})", ids)
    n_alertes = traiter_evenements(conn, cfg, evenements, log)
    if rapport:
        rows = export.resultats(conn, limite=2000)
        Path(rapport).write_text(html.render(rows, html.hypotheses(cfg)), encoding="utf-8")
    nouveaux = sum(1 for e in evenements if e[0] == "nouveau")
    baisses = sum(1 for e in evenements if e[0] == "baisse")
    log(f"[{datetime.now():%H:%M}] {total} annonce(s) lue(s), {nouveaux} nouvelle(s), "
        f"{baisses} baisse(s) de prix, {n_alertes} alerte(s)")
    return {"lues": total, "nouveaux": nouveaux, "baisses": baisses, "alertes": n_alertes}


def surveiller(conn, cfg: Config, sites: list, departements, cache: Path, rapport: str | None,
               intervalle_min: float, une_fois: bool = False, delai: float = 2.0, log=print) -> None:
    log(f"Surveillance de {len(sites)} site(s) : {', '.join(s.nom for s in sites)}"
        + (f" — départements {' '.join(departements)}" if departements else " — toute la France"))
    while True:
        try:
            passage(conn, cfg, sites, departements, cache, rapport, delai, log)
        except KeyboardInterrupt:
            raise
        except Exception as exc:  # noqa: BLE001 - la surveillance continue au prochain passage
            log(f"Erreur pendant le passage : {exc}")
        if une_fois:
            return
        log(f"Prochain passage dans {intervalle_min:g} min (Ctrl+C pour arrêter)")
        time.sleep(intervalle_min * 60)
