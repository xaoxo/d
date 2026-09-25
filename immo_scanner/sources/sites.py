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

import json
import re
import tomllib
import urllib.parse
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import json_api
from ..util import normalize
from .base import Annonce
from .dvf import departement_annonce
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
    modes_url: dict = field(default_factory=dict)   # {motif d'URL: mode de vente}
    api: str = ""                       # API JSON paginée, avec {page} (et éventuellement {dep})
    api_premiere_page: int = 1


SITES_DEFAUT = [
    Site("licitor", "Licitor — ventes aux enchères judiciaires",
         ["https://www.licitor.com/"],
         liens_annonce=r"licitor\.com/annonce/",
         liens_liste=r"licitor\.com/ventes-(?:aux-encheres|judiciaires)-immobilieres/[^?#]*",
         mode_vente="enchere"),
    Site("avoventes", "Avoventes — enchères judiciaires et ventes amiables d'avocats",
         ["https://www.avoventes.fr/recherche/toutes?sort=date&order=desc&display=liste"],
         liens_annonce=r"avoventes\.fr/(?:enchere|encheres|vente-amiable|amiable|vente)/[a-z0-9][^?#\"'\s]*",
         liens_liste=r"avoventes\.fr/(?:recherche/toutes\?[^#]*page=\d+|ventes-aux-encheres\?[^#]*page=\d+|"
                     r"ventes-amiables\?[^#]*page=\d+)",
         mode_vente="enchere",
         modes_url={r"amiable": "vente", r"/encheres?/": "enchere"}),
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
         # VENTE = vente classique, VNI = vente notariale interactive (36h-immo), VAE = enchères
         api="https://www.immobilier.notaires.fr/pub-services/inotr-www-annonces/v1/annonces"
             "?page={page}&parPage=48&typeTransactions=VENTE,VNI,VAE",
         notes="Annonces chargées en JavaScript : lecture via l'API JSON du site (adresse à confirmer)."),
    Site("36h-immo", "36h-immo — ventes notariales interactives",
         ["https://www.36h-immo.com/fr/annonces/ventes-interactives-immobilieres-en-ligne.html"],
         liens_annonce=r"36h-immo\.com/fr/(?:annonce|vente|bien|immobilier)[^?#\"'\s]*?\d{3,}[^?#\"'\s]*",
         liens_liste=r"36h-immo\.com/fr/annonces/[^#]*(?:page|p)=\d+",
         mode_vente="offre",
         notes="Les ventes 36h-immo sont aussi publiées sur le site des notaires (type VNI)."),
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


HORS_CIBLE = re.compile(r"\b(terrains?|parcelles?|bureaux?|local commercial|locaux|entrepots?|hangars?|"
                        r"parkings?|garages?|box|forets?|bois|terres?|pres?|friche|chapelle|eglise)\b")


@dataclass
class Rapport:
    site: str
    pages_liste: int = 0
    pages_annonce: int = 0
    liens_annonce_trouves: int = 0
    annonces: list = field(default_factory=list)
    hors_departement: int = 0
    hors_cible: int = 0                                     # terrains, bureaux, parkings…
    illisibles: list = field(default_factory=list)          # (url, ce qui manque)
    exemples_liens: list = field(default_factory=list)      # pour ajuster les motifs
    indices_api: list = field(default_factory=list)         # URL d'API repérées dans le code des pages
    api_statut: str = ""
    api_cles: list = field(default_factory=list)
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
        mode_url = next((m for rx, m in site.modes_url.items() if a.url and re.search(rx, a.url)), None)
        if mode_url:
            a.mode_vente = mode_url
        elif a.mode_vente in (None, "vente"):
            a.mode_vente = site.mode_vente
        if not a.normalized().type_local and HORS_CIBLE.search(normalize(a.titre or "")):
            rep.hors_cible += 1
            return False
        if deps:
            dep = departement_annonce(a.normalized())
            if dep and dep not in deps:
                rep.hors_departement += 1
                return False
        return True

    def lire_json(url):
        if not crawler.allowed(url):
            rep.bloques_robots.append(url)
            return None
        try:
            return json.loads(crawler.get(url, accept="application/json"))
        except Exception as exc:  # noqa: BLE001
            rep.api_statut = f"échec ({exc})"
            return None

    def lire(url):
        if not crawler.allowed(url):
            rep.bloques_robots.append(url)
            return None
        try:
            return crawler.get(url)
        except Exception as exc:  # noqa: BLE001 - on continue avec les autres pages
            rep.erreurs.append(f"{url} : {exc}")
            return None

    if site.api:
        _collecter_api(site, rep, lire_json, departements, garder, budget_listes)

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
        hote = re.sub(r"^https?://", "", url).split("/")[0]
        for indice in re.findall(r"""["'](https?://[^"'\s]*?(?:api|services|graphql|json|search)[^"'\s]*)["']""",
                                 html, re.I)[:20]:
            if indice not in rep.indices_api:
                rep.indices_api.append(indice)
        # liens présents dans le code de la page sans balise <a> (cartes générées en JavaScript…)
        for brut in re.findall(r"""(?:https?:)?//[^"'\s<>]+|(?<=["'])/[^"'\s<>]+""", html):
            complet = urllib.parse.urljoin(url, brut.replace("\\/", "/"))
            if rx_annonce.search(complet):
                liens.append(complet)
        for lien in liens:
            lien = lien.split("#")[0]
            if hote in lien and lien not in rep.exemples_liens and len(rep.exemples_liens) < 60:
                rep.exemples_liens.append(lien)
            if rx_annonce.search(lien):
                cle = re.sub(r"://www\.", "://", lien).rstrip("/")     # www.x.fr et x.fr = même fiche
                if cle not in vues_fiche:
                    vues_fiche.add(cle)
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
            rep.illisibles.append((url, _manque(html)))
            continue
        a.url = a.url or url
        if garder(a):
            rep.annonces.append(a)
    log(f"  {site.nom} : {rep.pages_liste} page(s) de liste, {rep.liens_annonce_trouves} lien(s) d'annonce, "
        f"{len(rep.annonces)} annonce(s) lue(s)")
    return rep


def _manque(html: str) -> str:
    from .texte import RX_MISE_A_PRIX, RX_OFFRE, RX_PRIX, RX_SURFACE, RX_SURFACE_CTX, texte_page
    titre, corps, _ = texte_page(html)
    prix = any(rx.search(corps) for rx in (RX_MISE_A_PRIX, RX_OFFRE, RX_PRIX))
    surface = any(rx.search(corps) for rx in (RX_SURFACE_CTX, RX_SURFACE))
    manque = [n for n, ok in (("prix", prix), ("surface", surface)) if not ok]
    return ("pas de " + " ni de ".join(manque) if manque else "format inattendu") + f" — « {titre[:60]} »"


def _collecter_api(site: Site, rep: Rapport, lire_json, departements, garder, pages_max: int) -> None:
    base = re.match(r"https?://[^/]+", site.api).group(0)
    deps = departements if (departements and "{dep}" in site.api) else [None]
    for dep in deps:
        for page in range(site.api_premiere_page, site.api_premiere_page + pages_max):
            data = lire_json(site.api.format(page=page, dep=dep or ""))
            if data is None:
                return
            trouvees = json_api.extraire(data, site.nom, base, site.mode_vente)
            if page == site.api_premiere_page:
                rep.api_statut = rep.api_statut or f"OK, {len(trouvees)} annonce(s) en page 1"
                if not trouvees:
                    rep.api_cles = json_api.cles_exemple(data)
            if not trouvees:
                break
            for a in trouvees:
                if garder(a):
                    rep.annonces.append(a)


def urls_recentes(conn, heures: float = 20) -> set[str]:
    """Fiches lues il y a moins de ``heures`` : inutile de les relire à chaque passage."""
    limite = (datetime.now(timezone.utc) - timedelta(hours=heures)).isoformat(timespec="seconds")
    return {r[0] for r in conn.execute("SELECT url FROM annonces WHERE url IS NOT NULL AND derniere_vue >= ?",
                                       (limite,))}
