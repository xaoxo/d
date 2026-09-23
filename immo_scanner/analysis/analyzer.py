"""Analyse complète d'une annonce : valeur de marché, décote, loyer, rentabilité,
travaux, DPE, plus-value, TRI, signaux et score global."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict
from datetime import datetime, timezone

from ..config import Config
from ..util import clamp, normalize, parent_commune
from .finance import bilan
from .market import MarketModel

# Calendrier loi Climat & Résilience : interdiction de mise en location.
INTERDICTION_LOCATION = {"G": 2025, "F": 2028, "E": 2034}

MOTS_TRAVAUX = [
    ("renovation_lourde", r"\b(a rehabiliter|a restaurer|gros travaux|entierement a renover|tout a refaire|plateau a amenager|ruine|grange a renover)\b"),
    ("renovation", r"\b(a renover|travaux a prevoir|renovation a prevoir|a moderniser|necessite des travaux|prevoir travaux)\b"),
    ("rafraichissement", r"\b(a rafraichir|rafraichissement|quelques travaux|petits travaux|travaux de decoration|a remettre au gout du jour)\b"),
]
MOTS_VENDEUR_PRESSE = r"\b(urgente?|succession|mutation|divorce|a saisir|prix negociable|baisse de prix|prix en baisse|vente rapide|cause depart|exclusivite prix)\b"
MOTS_EXCLUSION = {
    "viager": r"\b(viager|bouquet|rente viagere)\b",
    "nue-propriété": r"\b(nue propriete|usufruit)\b",
    "enchères": r"\b(encheres|mise a prix|adjudication)\b",
    "parts de SCI / droits": r"\b(parts de sci|quote part|droits indivis)\b",
}
MOTS_POTENTIEL = {
    "division / terrain constructible": r"\b(divisible|terrain constructible|possibilite de division|detachable)\b",
    "extension / combles": r"\b(combles amenageables|possibilite d extension|surelevation|grenier amenageable)\b",
    "locataire en place": r"\b(loue|locataire en place|vendu loue|bail en cours)\b",
    "immeuble de rapport": r"\b(immeuble de rapport|plusieurs lots|immeuble entier|\d+ appartements)\b",
    "local à transformer": r"\b(changement de destination|local commercial a transformer)\b",
}


def estimer_loyer(conn, cfg: Config, code_commune: str | None, type_local: str, surface: float):
    if not code_commune:
        return None, None
    for code, niveau in ((code_commune, "commune"), (parent_commune(code_commune), "ville")):
        if not code:
            continue
        r = conn.execute("SELECT * FROM loyers WHERE code_commune=? AND type_local=?",
                         (code, type_local)).fetchone()
        if r:
            break
    else:
        dep = code_commune[:3] if code_commune.startswith("97") else code_commune[:2]
        r = conn.execute("SELECT AVG(loyer_m2) AS loyer_m2 FROM loyers WHERE code_commune LIKE ? "
                         "AND type_local=?", (dep + "%", type_local)).fetchone()
        niveau = "departement"
        if not r or r["loyer_m2"] is None:
            return None, None
    m = cfg.marche
    ref = m.surface_ref_appartement if type_local == "appartement" else m.surface_ref_maison
    loyer_m2 = r["loyer_m2"] * (surface / ref) ** m.elasticite_loyer_surface
    return loyer_m2 * surface, niveau


def detecter_travaux(cfg: Config, texte: str, dpe: str | None, surface: float) -> tuple[float, list]:
    t = cfg.travaux
    niveau = next((n for n, rx in MOTS_TRAVAUX if re.search(rx, texte)), None)
    cout = {"renovation_lourde": t.renovation_lourde_m2, "renovation": t.renovation_m2,
            "rafraichissement": t.rafraichissement_m2}.get(niveau, 0.0) * surface
    notes = [f"Travaux détectés dans l'annonce : {niveau.replace('_', ' ')}"] if niveau else []
    if dpe in t.renovation_energetique_m2 and niveau not in ("renovation_lourde",):
        cout += t.renovation_energetique_m2[dpe] * surface
        notes.append(f"Rénovation énergétique estimée (DPE {dpe})")
    return cout, notes


def _composantes(cfg: Config, decote, rendement, tri, tendance, cashflow, liquidite):
    comp = {}
    if decote is not None:
        comp["decote"] = clamp((decote + 0.15) / 0.45)          # -15 % -> 0 ; +30 % -> 1
    if rendement is not None:
        comp["rendement"] = clamp((rendement - 0.02) / 0.06)    # 2 % -> 0 ; 8 % -> 1
    if tri is not None:
        comp["tri"] = clamp((tri + 0.02) / 0.17)                # -2 % -> 0 ; 15 % -> 1
    if tendance is not None:
        comp["tendance"] = clamp((tendance + 0.03) / 0.08)      # -3 %/an -> 0 ; +5 %/an -> 1
    if cashflow is not None:
        comp["cashflow"] = clamp((cashflow + 300) / 600)        # -300 € -> 0 ; +300 € -> 1
    if liquidite is not None:
        comp["liquidite"] = clamp(math.log1p(liquidite) / math.log1p(300))
    poids = cfg.scoring.poids
    total = sum(poids.get(k, 0) for k in comp)
    score = 100 * sum(v * poids.get(k, 0) for k, v in comp.items()) / total if total else 0
    return score, comp


def verdict(score: float, exclu: bool, sans_marche: bool = False) -> str:
    if sans_marche:
        return "Données insuffisantes"
    if exclu:
        return "À vérifier (vente atypique)"
    if score >= 75:
        return "Pépite"
    if score >= 62:
        return "Très intéressant"
    if score >= 50:
        return "Intéressant"
    if score >= 38:
        return "Dans le marché"
    return "Peu intéressant"


def analyser(conn, cfg: Config, market: MarketModel, a) -> dict:
    """``a`` : ligne de la table annonces (sqlite3.Row ou dict)."""
    a = dict(a)
    type_local = a["type_local"] or "appartement"
    surface, prix = a["surface"], a["prix"]
    texte = normalize(f"{a.get('titre') or ''} {a.get('description') or ''}")
    dpe = a.get("dpe")
    signaux, alertes = [], []
    mode = a.get("mode_vente") or "vente"
    enchere, interactif = mode == "enchere", mode in ("enchere", "offre")
    taux_frais = cfg.encheres.frais_pct if enchere else None

    est = market.estimer(type_local, surface, a.get("code_commune"), a.get("lat"), a.get("lon"))
    tend = market.tendance(a["code_commune"], type_local) if a.get("code_commune") else None
    taux_tendance = tend.taux_annuel if tend else None

    # --- valeur de marché (en l'état) ---
    travaux, notes_travaux = detecter_travaux(cfg, texte, dpe, surface)
    valeur_renovee = valeur_etat = decote = None
    if est.prix_m2:
        valeur_etat = est.prix_m2 * surface * (1 + cfg.marche.ajustement_dpe.get(dpe, 0.0))
        if travaux:
            # Valeur une fois rénové (DPE ramené à C, prime « refait à neuf ») ; la décote
            # devient la marge « marchand de biens » :
            # (valeur après travaux - prix - travaux) / valeur après travaux.
            valeur_renovee = est.prix_m2 * surface * (1 + cfg.marche.ajustement_dpe.get("C", 0.0)
                                                      + cfg.travaux.prime_bien_renove)
            decote = (valeur_renovee - travaux - prix) / valeur_renovee
        else:
            valeur_renovee = valeur_etat
            decote = (valeur_etat - prix) / valeur_etat

    # --- enchères / ventes à offres : offre maximale conseillée ---
    offre_max = None
    if interactif and valeur_renovee:
        frais = cfg.encheres.frais_pct if enchere else cfg.frais.notaire_ancien
        offre_max = max(0.0, (valeur_renovee * (1 - cfg.encheres.marge_cible) - travaux) / (1 + frais))

    # --- loyer ---
    loyer, niveau_loyer = (a["loyer_actuel"], "annonce") if a.get("loyer_actuel") else \
        estimer_loyer(conn, cfg, a.get("code_commune"), type_local, surface)

    b = None
    if loyer:
        b = bilan(cfg, prix, travaux, loyer, bool(a.get("neuf")),
                  valeur_renovee or valeur_etat or prix, taux_tendance or 0.0, surface, type_local,
                  a.get("charges_annuelles"), a.get("taxe_fonciere"), taux_frais)

    # --- signaux ---
    if interactif:
        quoi = "Enchère judiciaire" if enchere else "Vente à offres"
        depart = "mise à prix" if enchere else "prix de départ"
        if offre_max is not None:
            txt = (f"{quoi} : {depart} {prix:,.0f} €, offre max conseillée {offre_max:,.0f} € "
                   f"(marge {cfg.encheres.marge_cible:.0%}, frais et travaux inclus)").replace(",", " ")
            (signaux if offre_max > prix else alertes).append(txt)
        if a.get("date_vente"):
            signaux.append(f"Date de vente / fin des offres : {a['date_vente']}")
        alertes.append(f"Décote calculée sur la {depart} : le prix final sera plus élevé")
    if decote is not None:
        if travaux:
            if decote >= 0.10:
                signaux.append(f"Marge après travaux de {decote:.0%} (prix + travaux sous la valeur rénovée)")
            elif decote <= -0.10:
                alertes.append(f"Prix + travaux dépassent de {-decote:.0%} la valeur après rénovation")
        elif decote >= 0.10:
            signaux.append(f"Prix {decote:.0%} sous la valeur de marché estimée")
        elif decote <= -0.10:
            alertes.append(f"Prix {-decote:.0%} au-dessus du marché")
    if a.get("prix_initial") and a["prix_initial"] > prix:
        baisse = 1 - prix / a["prix_initial"]
        signaux.append(f"Baisse de prix de {baisse:.0%} depuis la première détection")
    if re.search(MOTS_VENDEUR_PRESSE, texte):
        signaux.append("Vendeur potentiellement pressé : " + ", ".join(sorted(set(re.findall(MOTS_VENDEUR_PRESSE, texte)))))
    for label, rx in MOTS_POTENTIEL.items():
        if re.search(rx, texte):
            signaux.append(f"Potentiel : {label}")
    if a.get("surface_terrain") and type_local == "maison" and a["surface_terrain"] >= 1000:
        signaux.append(f"Grand terrain ({a['surface_terrain']:.0f} m²) : division parcellaire à étudier")
    if b and b.rendement_brut >= 0.08:
        signaux.append(f"Rendement brut élevé ({b.rendement_brut:.1%})")
    if b and b.cashflow_mensuel_apres_impot >= 0:
        signaux.append("Autofinancé (cash-flow positif après impôt)")
    if taux_tendance is not None:
        if taux_tendance >= 0.03:
            signaux.append(f"Marché local en hausse ({taux_tendance:+.1%}/an)")
        elif taux_tendance <= -0.02:
            alertes.append(f"Marché local en baisse ({taux_tendance:+.1%}/an)")
    signaux.extend(notes_travaux)

    exclu = False
    for label, rx in MOTS_EXCLUSION.items():
        if interactif and label == "enchères":
            continue
        if re.search(rx, texte):
            alertes.append(f"Vente atypique ({label}) : le prix affiché n'est pas comparable au marché")
            exclu = True
    if dpe in INTERDICTION_LOCATION:
        an = INTERDICTION_LOCATION[dpe]
        alertes.append(f"DPE {dpe} : location interdite {'depuis' if an <= datetime.now().year else 'à partir de'} "
                       f"{an} sans rénovation énergétique")
    if est.prix_m2 is None:
        alertes.append("Aucune vente DVF comparable : importez les ventes du département (immo dvf)")
    elif est.fiabilite == "faible":
        alertes.append(f"Estimation peu fiable ({est.nb_comparables} ventes comparables, niveau {est.methode})")
    if not loyer:
        alertes.append("Loyer non estimé : importez les indicateurs de loyers (immo loyers)")
    if surface and prix and prix / surface < 200:
        alertes.append("Prix au m² anormalement bas : vérifier l'annonce (terrain, viager, erreur ?)")

    score, comp = _composantes(
        cfg, decote, b.rendement_net if b else None, b.tri if b else None, taux_tendance,
        b.cashflow_mensuel_apres_impot if b else None, est.ventes_par_an_commune or None)
    if dpe in ("F", "G"):
        score -= 5
    if est.fiabilite == "faible":
        score -= 5
    score = max(0.0, min(100.0, score))

    details = {
        "estimation": asdict(est),
        "tendance": asdict(tend) if tend else None,
        "valeur_etat": valeur_etat,
        "valeur_apres_travaux": valeur_renovee,
        "travaux_estimes": travaux,
        "loyer_mensuel_estime": loyer,
        "source_loyer": niveau_loyer,
        "bilan": asdict(b) if b else None,
        "composantes_score": comp,
        "mode_vente": mode,
        "offre_max": offre_max,
        "signaux": signaux,
        "alertes": alertes,
    }
    return {
        "annonce_id": a["id"],
        "calcule_le": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "score": round(score, 1),
        "verdict": verdict(score, exclu, sans_marche=est.prix_m2 is None),
        "valeur_estimee": valeur_renovee,
        "decote_pct": decote,
        "rendement_brut": b.rendement_brut if b else None,
        "rendement_net": b.rendement_net if b else None,
        "rendement_net_net": b.rendement_net_net if b else None,
        "cashflow_mensuel": b.cashflow_mensuel_apres_impot if b else None,
        "tri": b.tri if b else None,
        "plus_value_horizon": b.plus_value_nette if b else None,
        "tendance_annuelle": taux_tendance,
        "fiabilite": est.fiabilite,
        "details": json.dumps(details, ensure_ascii=False, default=str),
    }


COLS = ["annonce_id", "calcule_le", "score", "verdict", "valeur_estimee", "decote_pct",
        "rendement_brut", "rendement_net", "rendement_net_net", "cashflow_mensuel", "tri",
        "plus_value_horizon", "tendance_annuelle", "fiabilite", "details"]


def analyser_tout(conn, cfg: Config, where: str = "active=1", params=()) -> int:
    market = MarketModel(conn, cfg.marche)
    rows = conn.execute(f"SELECT * FROM annonces WHERE {where}", params).fetchall()
    for r in rows:
        res = analyser(conn, cfg, market, r)
        conn.execute(f"INSERT OR REPLACE INTO analyses ({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})",
                     [res[c] for c in COLS])
    conn.commit()
    return len(rows)
