import functools
import http.server
import random
import threading
from datetime import date

import pytest

from immo_scanner import db, demo, surveillance
from immo_scanner.config import Config
from immo_scanner.sources import dvf, sites, texte

PAGE_ENCHERE = """<html><head><title>Vente aux enchères - Appartement - Bordeaux</title>
<meta property="og:title" content="Un appartement de 3 pièces à Bordeaux"></head><body>
<nav>Accueil | Mes favoris</nav>
<h1>Un appartement de 3 pièces</h1>
<div class="adresse">12 rue des Lilas<br>33000 Bordeaux</div>
<p>Surface habitable : 62,40 m² (loi Carrez). Classe énergie : E.</p>
<p>Mise à prix : 45 000 €</p>
<p>Audience d'adjudication : jeudi 15 octobre 2026 à 14h, Tribunal judiciaire de Bordeaux</p>
<script>var prix = "999 999 €";</script>
</body></html>"""

PAGE_LD = """<html><script type="application/ld+json">
{"@type":"RealEstateListing","name":"Maison 4 pièces","offers":{"price":150000},
 "about":{"@type":"House","floorSize":{"value":95},"address":{"postalCode":"33000","addressLocality":"Bordeaux"}}}
</script></html>"""

INDEX = """<html><body>
<a href="/annonce/1/vente-appartement.html">Appartement</a>
<a href="/annonce/2/vente-maison.html">Maison</a>
<a href="/annonce/3/vente-lyon.html">Lyon</a>
<a href="/ventes-aux-encheres-immobilieres/page-2.html">Suivante</a>
<a href="/mentions-legales.html">Mentions</a></body></html>"""

PAGE_LYON = PAGE_ENCHERE.replace("33000 Bordeaux", "69003 Lyon").replace("Bordeaux", "Lyon")


def test_extraction_texte_enchere():
    a = texte.extraire(PAGE_ENCHERE, "https://x/annonce/1", "licitor", "enchere").normalized()
    assert a.prix == 45000 and a.surface == 62.4
    assert (a.code_postal, a.ville) == ("33000", "Bordeaux")
    assert a.type_local == "appartement" and a.dpe == "E" and a.pieces == 3
    assert a.date_vente == "15 octobre 2026" and a.mode_vente == "enchere"


def test_extraction_texte_sans_prix_renvoie_none():
    assert texte.extraire("<html><p>Contactez-nous</p></html>", "u", "s") is None


@pytest.fixture
def serveur(tmp_path, monkeypatch):
    (tmp_path / "robots.txt").write_text("User-agent: *\nDisallow: /prive/\n")
    (tmp_path / "index.html").write_text(INDEX)
    (tmp_path / "annonce" / "1").mkdir(parents=True)
    (tmp_path / "annonce" / "1" / "vente-appartement.html").write_text(PAGE_ENCHERE)
    (tmp_path / "annonce" / "2").mkdir()
    (tmp_path / "annonce" / "2" / "vente-maison.html").write_text(PAGE_LD)
    (tmp_path / "annonce" / "3").mkdir()
    (tmp_path / "annonce" / "3" / "vente-lyon.html").write_text(PAGE_LYON)
    (tmp_path / "ventes-aux-encheres-immobilieres").mkdir()
    (tmp_path / "ventes-aux-encheres-immobilieres" / "page-2.html").write_text("<html></html>")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def conn():
    c = db.connect(":memory:")
    ventes = demo.generer_dvf(random.Random(3), date(2026, 6, 30))
    for v in ventes:     # transpose la commune démo 99001 à Bordeaux pour le test
        if v["code_commune"] == "99001":
            v.update(code_commune="33063", code_postal="33000", nom_commune="Bordeaux", code_departement="33")
    c.executemany(f"INSERT INTO dvf_ventes ({','.join(dvf.COLUMNS)}) VALUES ({','.join('?' * len(dvf.COLUMNS))})",
                  [[v[k] for k in dvf.COLUMNS] for v in ventes])
    c.executemany("INSERT INTO loyers VALUES (?,?,?,?,?,?)",
                  [("33063", "appartement", 14.5, None, None, 100), ("33063", "maison", 11.0, None, None, 50)])
    dvf.rebuild_communes(c)
    return c


def site_test(url):
    return sites.Site("test", "Site de test", [url + "/index.html"], liens_annonce=r"/annonce/\d+/",
                      liens_liste=r"/ventes-aux-encheres-immobilieres/", mode_vente="enchere")


def test_passage_complet_alertes_et_doublons(serveur, conn, tmp_path):
    cfg = Config()
    cfg.alertes.windows = False
    logs = []
    res = surveillance.passage(conn, cfg, [site_test(serveur)], ["33"], tmp_path, str(tmp_path / "r.html"),
                               delai=0, log=logs.append)
    assert res["lues"] == 2 and res["nouveaux"] == 2          # Lyon écartée (hors département)
    enchere = conn.execute("SELECT a.*, an.details FROM annonces a JOIN analyses an ON an.annonce_id=a.id "
                           "WHERE a.mode_vente='enchere' AND a.prix=45000").fetchone()
    assert enchere["date_vente"] == "15 octobre 2026"
    import json
    d = json.loads(enchere["details"])
    assert d["offre_max"] > 45000                               # mise à prix très basse -> marge
    assert any("offre max conseillée" in s for s in d["signaux"])
    assert not any("Vente atypique" in s for s in d["alertes"])
    assert res["alertes"] >= 1 and any("[ALERTE]" in l for l in logs)
    assert "Offres" in (tmp_path / "r.html").read_text() or "enchère" in (tmp_path / "r.html").read_text()

    # second passage : rien de nouveau, aucune alerte en double
    res2 = surveillance.passage(conn, cfg, [site_test(serveur)], ["33"], tmp_path, None, delai=0, log=logs.append)
    assert res2["nouveaux"] == 0 and res2["alertes"] == 0


def test_baisse_de_prix_alerte(serveur, conn, tmp_path):
    cfg = Config()
    cfg.alertes.windows = False
    surveillance.passage(conn, cfg, [site_test(serveur)], ["33"], tmp_path, None, delai=0, log=lambda *_: None)
    page = tmp_path / "annonce" / "2" / "vente-maison.html"
    page.write_text(PAGE_LD.replace("150000", "130000"))
    conn.execute("UPDATE annonces SET derniere_vue='2000-01-01'")   # force la relecture des fiches
    logs = []
    res = surveillance.passage(conn, cfg, [site_test(serveur)], ["33"], tmp_path, None, delai=0, log=logs.append)
    assert res["baisses"] == 1 and any("Baisse de prix -13%" in l for l in logs)


def test_robots_respecte(serveur):
    s = site_test(serveur)
    s.depart = [serveur + "/prive/index.html"]
    from immo_scanner.sources.web import Crawler
    rep = sites.collecter(s, Crawler(delay=0), log=lambda *_: None)
    assert rep.bloques_robots and not rep.annonces


def test_charger_sites_toml(tmp_path):
    p = tmp_path / "sites.toml"
    p.write_text('[sites.licitor]\nactif = false\n\n[sites.mon-agence]\ndepart = ["https://agence.fr/biens"]\n'
                 'liens_annonce = "/bien/\\\\d+"\n')
    s = sites.charger_sites(p)
    assert s["licitor"].actif is False and s["mon-agence"].liens_annonce == r"/bien/\d+"


def test_robots_lu_avec_notre_identite_et_explique(serveur):
    from immo_scanner.sources.web import Crawler
    c = Crawler(delay=0)
    assert c.allowed(serveur + "/index.html")
    assert not c.allowed(serveur + "/prive/x.html")
    exp = c.explication_robots(serveur + "/prive/x.html")
    assert "statut 200" in exp and "Disallow: /prive/" in exp


def test_robots_absent_tout_permis(tmp_path, monkeypatch):
    import functools, http.server, threading
    from immo_scanner.sources.web import Crawler
    monkeypatch.setenv("no_proxy", "127.0.0.1")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert Crawler(delay=0).allowed(f"http://127.0.0.1:{srv.server_address[1]}/page.html")
    finally:
        srv.shutdown()


PAGE_LICITOR = """<html><head><title>Vente aux enchères : une villa à Ceyreste (Bouches-du-Rhône)</title></head>
<body><h1>Une villa à Ceyreste (Bouches-du-Rhône)</h1>
<p>Tribunal Judiciaire de Marseille, 6 rue Joseph Autran, 13006 Marseille</p>
<p>Maître Dupont, avocat, 13006 Marseille</p>
<p>Une villa de 156,45 m², terrain de 1 200 m². Mise à prix : 200 000 €</p>
<p>Visite sur place : 12 chemin des Pins, Ceyreste</p></body></html>"""


def test_localisation_par_titre_et_pas_par_le_tribunal():
    a = texte.extraire(PAGE_LICITOR, "https://www.licitor.com/annonce/1", "licitor", "enchere")
    assert (a.ville, a.departement, a.code_postal) == ("Ceyreste", "13", None)
    assert a.prix == 200000 and a.surface == 156.45 and a.surface_terrain == 1200


def test_cp_retenu_seulement_a_cote_de_la_ville():
    page = PAGE_LICITOR.replace("12 chemin des Pins, Ceyreste", "12 chemin des Pins, 13600 Ceyreste")
    a = texte.extraire(page, "u", "licitor", "enchere")
    assert a.code_postal == "13600"


def test_resolution_commune_par_nom_et_departement():
    from immo_scanner.sources.base import Annonce, resolve_commune
    c = db.connect(":memory:")
    c.executemany("INSERT INTO communes VALUES (?,?,?,?)", [
        ("13023", "13600", "Ceyreste", 40), ("13055", "13001", "Marseille", 900),
        ("75115", "75015", "Paris 15e Arrondissement", 500)])
    a = Annonce(source="t", ville="Ceyreste", departement="13")
    resolve_commune(c, a)
    assert (a.code_commune, a.code_postal) == ("13023", "13600")
    b = Annonce(source="t", ville="Paris 15ème", departement="75")
    resolve_commune(c, b)
    assert (b.code_commune, b.code_postal) == ("75115", "75015")


def test_geo():
    from immo_scanner import geo
    assert geo.departement_depuis_nom("Bouches-du-Rhône") == "13"
    assert geo.departement_depuis_nom("val-d-oise") == "95"
    assert geo.departement_depuis_nom("Corse-du-Sud") == "2A"
    assert geo.code_arrondissement("Lyon 3e") == "69383"
    assert geo.code_arrondissement("Marseille 8ème arrondissement") == "13208"
    assert geo.code_arrondissement("Parisot") is None


def test_liens_caches_dans_le_code_et_mode_par_url(serveur, tmp_path):
    (tmp_path / "recherche.html").write_text(
        '<html><div id="app"></div><script>window.__DATA__ = {"ventes": ['
        '{"lien": "\\/enchere\\/un-appartement-a-bordeaux"}, {"lien": "/vente-amiable/une-maison-a-pessac"},'
        '{"lien": "' + serveur.replace("127.0.0.1", "localhost") + '/enchere/un-appartement-a-bordeaux"}]}'
        '</script></html>')
    (tmp_path / "enchere").mkdir()
    (tmp_path / "enchere" / "un-appartement-a-bordeaux").write_text(PAGE_ENCHERE)
    (tmp_path / "vente-amiable").mkdir()
    (tmp_path / "vente-amiable" / "une-maison-a-pessac").write_text(
        PAGE_ENCHERE.replace("Mise à prix : 45 000 €", "Prix : 245 000 €").replace("Bordeaux", "Pessac")
        .replace("33000", "33600"))
    from immo_scanner.sources.web import Crawler
    s = sites.Site("avo", "test", [serveur + "/recherche.html"],
                   liens_annonce=r"/(?:enchere|vente-amiable)/[a-z0-9][^?#\"'\s]*",
                   mode_vente="enchere", modes_url={"amiable": "vente"})
    rep = sites.collecter(s, Crawler(delay=0), log=lambda *_: None)
    modes = sorted((a.mode_vente, a.prix) for a in rep.annonces)
    assert modes == [("enchere", 45000), ("vente", 245000)]


def test_robots_403_ne_bloque_pas(monkeypatch):
    import urllib.error
    from immo_scanner.sources import web

    def refuse(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)

    monkeypatch.setattr(web.urllib.request, "urlopen", refuse)
    c = web.Crawler(delay=0)
    assert c.allowed("https://exemple.gouv.fr/page")
    assert "403" in c.explication_robots("https://exemple.gouv.fr/page")


def page(titre, corps):
    return f"<html><head><title>{titre}</title></head><body><h1>{titre}</h1>{corps}</body></html>"


def test_avoventes_prix_du_titre_et_ville_hors_avocat():
    html = page("120 000 euros - A VENDRE APPARTEMENT T3 A MERIGNAC",
                "<p>Maître Durand, avocat au barreau de Bordeaux<br>10 cours de Verdun<br>33000 BORDEAUX</p>"
                "<p>Appartement T3 de 50,54 m² situé 5 avenue X, 33700 Mérignac.</p>"
                "<p>Prix de vente frais inclus : 129 600 €</p>")
    a = texte.extraire(html, "https://avoventes.fr/enchere/x", "avoventes", "enchere")
    assert a.prix == 120000 and a.surface == 50.54
    assert (a.ville, a.code_postal, a.departement) == ("MERIGNAC", "33700", "33")


def test_cedex_et_numero_de_dossier_ecartes():
    html = page("Maison LACANAU OCÉAN",
                "<p>SCP Martin, 33701 MERIGNAC CEDEX</p><p>Numéro de dossier : 34003 Numéro de dossier</p>"
                "<p>Maison de 157 m² à 33680 Lacanau. Mise à prix : 675 000 €</p>")
    a = texte.extraire(html, "u", "avoventes", "enchere")
    assert (a.code_postal, a.ville) == ("33680", "Lacanau")


def test_surface_avec_separateur_de_milliers_et_hors_cible(serveur, tmp_path):
    html = page("Terrain Constructible De 1 178 M2 - Agde", "<p>Surface : 1 178 m². Prix : 252 000 €</p>")
    a = texte.extraire(html, "u", "etat", "offre")
    assert a.surface == 1178
    (tmp_path / "liste.html").write_text('<a href="/biens/terrain-agde">t</a><a href="/biens/maison-agde">m</a>')
    (tmp_path / "biens").mkdir()
    (tmp_path / "biens" / "terrain-agde").write_text(html)
    (tmp_path / "biens" / "maison-agde").write_text(
        page("Maison de ville à Agde (Hérault)", "<p>Maison de 95 m². Prix : 210 000 €</p>"))
    from immo_scanner.sources.web import Crawler
    s = sites.Site("etat", "t", [serveur + "/liste.html"], liens_annonce=r"/biens/", mode_vente="offre")
    rep = sites.collecter(s, Crawler(delay=0), log=lambda *_: None)
    assert rep.hors_cible == 1 and [a.ville for a in rep.annonces] == ["Agde"]
    assert rep.annonces[0].departement == "34"


def test_titre_avec_code_postal():
    a = texte.extraire(page("Maison en vente interactive à Freneuse (78840)", "<p>335 m². Prix : 755 000 €</p>"),
                       "u", "36h", "offre")
    assert (a.ville, a.code_postal, a.departement) == ("Freneuse", "78840", "78")
    b = texte.extraire(page("Charmante Maison Des Années 30 à Bourg-en-bresse 01", "<p>142 m². Prix : 264 000 €</p>"),
                       "u", "etat", "offre")
    assert (b.ville, b.departement) == ("Bourg-en-bresse", "01")
