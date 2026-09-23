"""Profils des sites surveillés (enchères, notaires, État, agences) et collecte.

Chaque profil indique : les pages de départ, un motif (regex) pour reconnaître les
liens d'annonces, un motif pour les pages de liste à parcourir (pagination, régions),
et le mode de vente (vente classique, enchère, offre interactive).

Sur chaque page d'annonce, on lit d'abord les données schema.org (JSON-LD) puis,
à défaut, le texte de la page (voir texte.py). Les profils par défaut sont un point
de départ : ``immo sites-tester`` montre ce que chaque site renvoie et
``sites.toml`` permet d'ajuster les motifs ou d'ajouter ses propres sites.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .base import Annonce
from .dvf import departement_depuis_cp
from .texte import extraire
from .web import Crawler, parse_html


@dataclass
class Site:
    nom: str
    libelle: str
    depart: list[str]
    liens_annonce: str                  # regex des URL de fiches d'annonce
    liens_liste: str = ""               # regex des pages de liste à parcourir
    mode_vente: str = "vente"           # vente | enchere | offre
    max_pages: int = 150
    actif: bool = True
    notes: str = ""
    depart_par_departement: list[str] = field(default_factory=list)   # URL avec {dep}


SITES_DEFAUT = [
    Site("licitor", "Licitor — ventes aux enchères judiciaires",
         ["https://www.licitor.com/"],
         liens_annonce=r"licitor\.com/annonce/",
         liens_liste=r"licitor\.com/ventes-(?:aux-encheres|judiciaires)-immobilieres/[^?#]*",
         mode_vente="enchere"),
    Site("avoventes", "Avoventes — enchères judiciaires (Conseil national des barreaux)",
         ["https://www.avoventes.fr/"],
         liens_annonce=r"avoventes\.fr/(?:vente|ventes|annonce|lot|bien)s?/[^?#]+",
         liens_liste=r"avoventes\.fr/(?:ventes|recherche|encheres|annonces)(?:[/?][^#]*)?$",
         mode_vente="enchere"),
    Site("etat", "Cessions immobilières de l'État",
         ["https://cessions.immobilier-etat.gouv.fr/"],
         liens_annonce=r"cessions\.immobilier-etat\.gouv\.fr/(?:bien|biens|annonce|annonces)/[^?#]+",
         liens_liste=r"cessions\.immobilier-etat\.gouv\.fr/(?:recherche|biens|annonces)(?:[/?][^#]*)?$",
         mode_vente="offre"),
    Site("notaires", "Immobilier des notaires",
         ["https://www.immobilier.notaires.fr/fr/annonces-immobilieres-liste?typeTransaction=VENTE"],
         liens_annonce=r"immobilier\.notaires\.fr/fr/annonce-immo-",
         liens_liste=r"immobilier\.notaires\.fr/fr/annonces-immobilieres-liste\?[^#]*",
         mode_vente="vente",
         depart_par_departement=["https://www.immobilier.notaires.fr/fr/annonces-immobilieres-liste"
                                 "?typeTransaction=VENTE&departement={dep}"],
         notes="Site très dynamique (JavaScript) : si le test ne trouve rien, il faudra passer par leur API."),
    Site("36h-immo", "36h-immo — ventes notariales interactives",
         ["https://www.36h-immo.com/"],
         liens_annonce=r"36h-immo\.com/(?:bien|biens|annonce|annonces|vente|ventes)/[^?#]+",
         liens_liste=r"36h-immo\.com/(?:recherche|biens|ventes|annonces)(?:[/?][^#]*)?$",
         mode_vente="offre"),
]


def charger_sites(path: str | Path | None = None) -> dict[str, Site]:
    """Profils par défaut, surchargés/complétés par un fichier TOML ([sites.<nom>])."""
    sites = {s.nom: s for s in SITES_DEFAUT}
    if path and Path(path).exists():
        with open(path, "rb") as fh:
            data = tomllib.load(fh).get("sites", {})
        noms = {f.name for f in fields(Site)}
        for nom, valeurs in data.items():
            inconnus = set(valeurs) - noms
            if inconnus:
                raise ValueError(f"sites.toml [{nom}] : paramètres inconnus {sorted(inconnus)}")
            if nom in sites:
                for k, v in valeurs.items():
                    setattr(sites[nom], k, v)
            else:
                valeurs.setdefault("libelle", nom)
                sites[nom] = Site(nom=nom, **valeurs)
    return sites


@dataclass
class Rapport:
    site: str
    pages_liste: int = 0
    pages_annonce: int = 0
    liens_annonce_trouves: int = 0
    annonces: list = field(default_factory=list)
    hors_departement: int = 0
    illisibles: list = field(default_factory=list)
    bloques_robots: list = field(default_factory=list)
    erreurs: list = field(default_factory=list)


def collecter(site: Site, crawler: Crawler, departements=None, deja_vues: set | None = None,
              log=print) -> Rapport:
    """Parcourt les pages de liste du site, puis lit chaque fiche d'annonce.

    ``deja_vues`` : URL de fiches récemment lues, ignorées pour alléger la surveillance."""
    rep = Rapport(site.nom)
    rx_annonce = re.compile(site.liens_annonce)
    rx_liste = re.compile(site.liens_liste) if site.liens_liste else None
    departs = list(site.depart)
    if departements and site.depart_par_departement:
        departs = [u.format(dep=d) for u in site.depart_par_departement for d in departements]
    listes, fiches = list(departs), []
    vues_liste, vues_fiche = set(), set()
    budget_listes = max(5, site.max_pages // 3)     # le reste du budget va aux fiches
    budget = site.max_pages
    deps = {d.upper() for d in departements} if departements else None

    def garder(a) -> bool:
        if a.mode_vente in (None, "vente"):
            a.mode_vente = site.mode_vente
        if deps:
            dep = departement_depuis_cp(a.normalized().code_postal or "")
            if dep and dep not in deps:
                rep.hors_departement += 1
                return False
        return True

    def lire(url):
        if not crawler.allowed(url):
            rep.bloques_robots.append(url)
            return None
        try:
            return crawler.get(url)
        except Exception as exc:  # noqa: BLE001 - on continue avec les autres pages
            rep.erreurs.append(f"{url} : {exc}")
            return None

    while listes and budget_listes > 0:
        url = listes.pop(0)
        if url in vues_liste:
            continue
        vues_liste.add(url)
        html = lire(url)
        budget -= 1
        budget_listes -= 1
        if html is None:
            continue
        rep.pages_liste += 1
        annonces_ld, liens = parse_html(html, url, site.nom)
        for a in annonces_ld:       # certaines pages de liste portent déjà les données complètes
            if garder(a):
                rep.annonces.append(a)
        for lien in liens:
            lien = lien.split("#")[0]
            if rx_annonce.search(lien):
                if lien not in vues_fiche:
                    vues_fiche.add(lien)
                    fiches.append(lien)
            elif rx_liste and rx_liste.search(lien) and lien not in vues_liste:
                listes.append(lien)
    rep.liens_annonce_trouves = len(fiches)

    for url in fiches:
        if budget <= 0:
            break
        if deja_vues and url in deja_vues:
            continue
        html = lire(url)
        budget -= 1
        if html is None:
            continue
        rep.pages_annonce += 1
        ld, _ = parse_html(html, url, site.nom)
        a = ld[0] if ld else extraire(html, url, site.nom, site.mode_vente)
        if a is None:
            rep.illisibles.append(url)
            continue
        a.url = a.url or url
        if garder(a):
            rep.annonces.append(a)
    log(f"  {site.nom} : {rep.pages_liste} page(s) de liste, {rep.liens_annonce_trouves} lien(s) d'annonce, "
        f"{len(rep.annonces)} annonce(s) lue(s)")
    return rep


def urls_recentes(conn, heures: float = 20) -> set[str]:
    """Fiches lues il y a moins de ``heures`` : inutile de les relire à chaque passage."""
    limite = (datetime.now(timezone.utc) - timedelta(hours=heures)).isoformat(timespec="seconds")
    return {r[0] for r in conn.execute("SELECT url FROM annonces WHERE url IS NOT NULL AND derniere_vue >= ?",
                                       (limite,))}
