import json

from immo_scanner import db
from immo_scanner.sources import base, fichier, loyers, web


def test_loyers_format_carte_des_loyers():
    text = ('id_zone;INSEE_C;LIBGEO;EPCI;DEP;REG;loypredm2;lwr.IPm2;upr.IPm2;TYPPRED;nbobs_com\n'
            '1;01001;L\'Abergement;200069193;01;84;"10,5";"8,2";"13,4";commune;12\n'
            '2;75111;Paris 11e;200054781;75;11;"31,2";"25,0";"38,0";commune;4000\n')
    rows = loyers.parse(text, "appartement")
    assert rows[0] == ("01001", "appartement", 10.5, 8.2, 13.4, 12)
    assert rows[1][2] == 31.2


def test_import_csv_colonnes_souples(tmp_path):
    p = tmp_path / "export.csv"
    p.write_text("Reference;Price;Surface habitable;CP;City;Type;DPE;Description\n"
                 "A1;185 000 €;42,5;33000;Bordeaux;Appartement;D;Bel appartement\n", encoding="utf-8")
    a = fichier.read(p)[0].normalized()
    assert (a.prix, a.surface, a.code_postal, a.type_local, a.dpe) == (185000, 42.5, "33000", "appartement", "D")


def test_import_json(tmp_path):
    p = tmp_path / "api.json"
    p.write_text(json.dumps({"results": [{"id": 1, "price": 99000, "area": 30, "zipcode": "69003",
                                          "title": "Studio lumineux"}]}))
    a = fichier.read(p, "api")[0].normalized()
    assert a.source == "api" and a.type_local == "appartement" and a.prix == 99000


def test_jsonld():
    html = """<html><script type="application/ld+json">
    {"@context":"https://schema.org","@type":"RealEstateListing","name":"Maison 5 pièces",
     "url":"/annonce/42","datePosted":"2026-09-01",
     "offers":{"@type":"Offer","price":"320000","priceCurrency":"EUR"},
     "about":{"@type":"House","floorSize":{"@type":"QuantitativeValue","value":110},
              "numberOfRooms":5,"address":{"postalCode":"44000","addressLocality":"Nantes"},
              "geo":{"latitude":47.21,"longitude":-1.55}}}
    </script><a href="/annonce/43">x</a></html>"""
    annonces, links = web.parse_html(html, "https://agence.example/liste", "agence")
    a = annonces[0].normalized()
    assert a.url == "https://agence.example/annonce/42"
    assert (a.prix, a.surface, a.type_local, a.code_postal) == (320000, 110, "maison", "44000")
    assert "https://agence.example/annonce/43" in links


def test_save_detecte_baisse_de_prix_et_resout_commune():
    conn = db.connect(":memory:")
    conn.execute("INSERT INTO communes VALUES ('33063','33000','Bordeaux',100)")
    a = base.Annonce(source="t", source_id="1", prix=200000, surface=50, code_postal="33000",
                     ville="Bordeaux", titre="Appartement T2")
    assert base.save(conn, [a]) == (1, 0)
    a.prix = 185000
    assert base.save(conn, [a]) == (0, 1)
    r = conn.execute("SELECT * FROM annonces").fetchone()
    assert (r["prix"], r["prix_initial"], r["code_commune"]) == (185000, 200000, "33063")


def test_detection_dpe_dans_texte():
    a = base.Annonce(source="t", description="Classe énergie : F, chauffage gaz").normalized()
    assert a.dpe == "F"
