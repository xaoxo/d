import io

from immo_scanner.config import Marche
from immo_scanner.sources import dvf

HEADER = ("id_mutation,date_mutation,numero_disposition,nature_mutation,valeur_fonciere,adresse_numero,"
          "adresse_suffixe,adresse_nom_voie,adresse_code_voie,code_postal,code_commune,nom_commune,"
          "code_departement,ancien_code_commune,ancien_nom_commune,id_parcelle,ancien_id_parcelle,"
          "numero_volume,lot1_numero,lot1_surface_carrez,lot2_numero,lot2_surface_carrez,lot3_numero,"
          "lot3_surface_carrez,lot4_numero,lot4_surface_carrez,lot5_numero,lot5_surface_carrez,nombre_lots,"
          "code_type_local,type_local,surface_reelle_bati,nombre_pieces_principales,code_nature_culture,"
          "nature_culture,code_nature_culture_speciale,nature_culture_speciale,surface_terrain,longitude,latitude")


def row(mid, nature, valeur, type_local, surface, pieces, lot="", parcelle="33063000AB0001", terrain=""):
    code = {"Appartement": "2", "Maison": "1", "Dépendance": "3", "Local industriel. commercial ou assimilé": "4"}
    return (f"{mid},2024-03-15,1,{nature},{valeur},12,,RUE TEST,0001,33000,33063,Bordeaux,33,,,{parcelle},,,"
            f"{lot},,,,,,,,,,1,{code.get(type_local, '')},{type_local},{surface},{pieces},,,,,{terrain},-0.57,44.84")


def parse(lines):
    return {s["id_mutation"]: s for s in dvf.parse(io.StringIO("\n".join([HEADER] + lines)), Marche())}


def test_vente_simple_appartement_avec_cave():
    sales = parse([
        row("M1", "Vente", 200000, "Appartement", 50, 2, lot="12"),
        row("M1", "Vente", 200000, "Dépendance", "", "", lot="40"),
    ])
    s = sales["M1"]
    assert s["type_local"] == "appartement"
    assert s["prix_m2"] == 4000
    assert s["code_postal"] == "33000" and s["neuf"] == 0


def test_logement_repete_sur_plusieurs_parcelles_compte_une_fois():
    sales = parse([
        row("M2", "Vente", 300000, "Maison", 100, 4, parcelle="P1", terrain="400"),
        row("M2", "Vente", 300000, "Maison", 100, 4, parcelle="P2", terrain="200"),
    ])
    assert sales["M2"]["surface_terrain"] == 600
    assert sales["M2"]["prix_m2"] == 3000


def test_exclusions():
    sales = parse([
        row("BLOC", "Vente", 500000, "Appartement", 40, 2, lot="1"),
        row("BLOC", "Vente", 500000, "Appartement", 60, 3, lot="2"),
        row("MIXTE", "Vente", 400000, "Appartement", 60, 3, lot="1"),
        row("MIXTE", "Vente", 400000, "Local industriel. commercial ou assimilé", 80, 0, lot="2"),
        row("ECHANGE", "Echange", 100000, "Appartement", 30, 1),
        row("ABERRANT", "Vente", 1, "Appartement", 30, 1),
    ])
    assert sales == {}


def test_vefa_marquee_neuf():
    sales = parse([row("N1", "Vente en l'état futur d'achèvement", 250000, "Appartement", 50, 2)])
    assert sales["N1"]["neuf"] == 1
