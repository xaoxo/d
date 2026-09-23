# immo-scanner — détecteur d'opportunités immobilières en France

Outil en ligne de commande qui croise **toutes les ventes immobilières réelles de France**
(DVF, données publiques), les **loyers de marché** par commune et les **annonces en vente**
pour faire ressortir, pour chaque bien :

| Indicateur | Comment il est calculé |
|---|---|
| **Valeur de marché estimée** | Médiane des ventes DVF comparables (même type, surface proche) dans un rayon croissant de 300 m à 2 km, puis commune, puis département. Prix ramenés à aujourd'hui selon la tendance locale, ajustés selon le DPE. |
| **Décote / bien sous-évalué** | Écart entre la valeur estimée et le prix affiché. Si des travaux sont détectés : marge « marchand de biens » = (valeur après travaux − prix − travaux) / valeur après travaux. |
| **Loyer estimé** | Indicateur de loyer de la commune (Carte des loyers), corrigé de l'effet surface, ou loyer réel si le bien est vendu loué. |
| **Rendement brut / net / net-net** | Avec frais de notaire, vacance, taxe foncière, charges non récupérables, PNO, entretien, gestion, puis impôt selon le régime (micro-foncier, réel, LMNP micro, LMNP réel). |
| **Cash-flow mensuel** | Loyers − charges − mensualité de crédit (assurance incluse), avant et après impôt. |
| **Potentiel de plus-value** | Tendance annuelle des prix de la commune (régression sur 5 ans de DVF), projetée à l'horizon choisi, impôt sur la plus-value (abattements de durée de détention) déduit. |
| **TRI** | Taux de rendement interne de l'opération sur l'apport (cash-flows + revente − capital restant dû − impôt PV). |
| **Travaux** | Détection dans le texte (« à rafraîchir », « à rénover », « à réhabiliter »…) + rénovation énergétique selon le DPE. |
| **Signaux** | Baisse de prix depuis la 1ʳᵉ détection, vendeur pressé (succession, mutation, urgent…), terrain divisible, combles, locataire en place, immeuble de rapport, marché en hausse… |
| **Alertes** | DPE G/F/E (interdiction de location 2025/2028/2034), viager, nue-propriété, enchères, estimation peu fiable, marché en baisse… |
| **Score /100 + verdict** | Pondération configurable : décote, rendement, TRI, tendance, cash-flow, liquidité du marché. |

Un classement des **communes** (prix, tendance, volume, rendement théorique) permet aussi de
repérer les marchés porteurs sur toute la France, sans aucune annonce.

## Installation

```bash
python3 -m pip install -e .        # Python 3.11+, aucune dépendance externe
immo --help
```

## Essai immédiat (données fictives)

```bash
immo --db data/demo.db demo        # génère rapport_demo.html
```

## Utilisation réelle

```bash
# 1. Ventes réelles DVF (5 dernières années) — un département, plusieurs, ou toute la France
immo dvf --departements 33 69 --annees 2021-2025
immo dvf --departements all --annees 2021-2025      # France entière : plusieurs Go, ~1 h

# 2. Loyers de marché (Carte des loyers, data.gouv.fr : « indicateurs de loyers par commune »)
immo loyers chemin/ou/url/pred-app.csv --type appartement
immo loyers chemin/ou/url/pred-mai.csv --type maison

# 3. Annonces à analyser
immo import mes_annonces.csv                         # CSV/JSON/JSONL, colonnes reconnues automatiquement
immo web https://www.agence.fr/nos-biens --suivre "/bien/\d+"   # sites publiant du schema.org

# 4. Résultats
immo top --limite 30 --ordre score                   # ou decote, rendement, cashflow, tri, plus_value
immo top --departement 33 --type appartement --prix-max 200000 --csv export.csv
immo rapport --sortie rapport.html                   # rapport HTML interactif (tri, filtres, fiches)
immo marches --departements 33 --type appartement    # meilleures communes

# Analyser un bien précis vu sur une annonce
immo estimer --prix 145000 --surface 42 --cp 33000 --ville Bordeaux --dpe D --charges 1200
```

Relancez `immo import` régulièrement : les baisses de prix sont suivies automatiquement, et
`--remplacer` désactive les annonces disparues de la source.

### Format d'import des annonces

Voir `exemples/annonces_exemple.csv`. Seuls `prix` et `surface` sont indispensables, plus
`code_postal` (ou `code_commune` INSEE, ou `lat`/`lon`) pour la localisation. Colonnes
optionnelles : `type`, `pieces`, `ville`, `dpe`, `description`, `loyer_actuel`,
`charges_annuelles`, `taxe_fonciere`, `surface_terrain`, `url`… Les noms anglais (`price`,
`area`, `zipcode`…) sont aussi reconnus.

## D'où viennent les annonces ?

Les grands portails (Leboncoin, SeLoger, etc.) interdisent la collecte automatisée dans leurs
CGU et sont protégés contre les robots ; l'outil ne contourne pas ces protections. Pour couvrir
« toutes » les annonces de France de manière fiable et légale, branchez un **agrégateur
d'annonces sous licence** (plusieurs API françaises agrègent l'ensemble des portails), exportez
en CSV/JSON et utilisez `immo import`. Le collecteur `immo web` couvre les sites d'agences et de
réseaux qui publient leurs biens au format schema.org, en respectant `robots.txt`.

Ajouter une source = écrire une fonction qui renvoie des objets `Annonce`
(`immo_scanner/sources/base.py`) puis appeler `base.save()`.

## Hypothèses

Toutes les hypothèses (apport, taux, durée, TMI, régime fiscal, vacance, coûts de travaux,
pondérations du score…) sont modifiables : copiez `config.example.toml` et passez
`--config ma_config.toml`.

## Limites

- DVF ne couvre pas l'Alsace-Moselle (57, 67, 68) ni Mayotte, et est publié avec ~6 mois de retard.
- La valeur estimée est statistique : elle ignore l'étage, la vue, l'état exact, l'exposition…
  Les biens au-dessus de la médiane pour ces raisons apparaîtront « chers ».
- Taux fiscaux (prélèvements sociaux, régimes LMNP) : vérifiez les règles de la loi de finances en vigueur.
- Ce sont des aides à la décision, pas des conseils en investissement.

## Tests

```bash
python3 -m pip install -e ".[dev]" && python3 -m pytest
```

## Structure

```
immo_scanner/
  sources/   dvf.py (ventes officielles) · loyers.py · fichier.py (CSV/JSON) · web.py (schema.org) · base.py
  analysis/  market.py (comparables, tendance) · finance.py (crédit, fiscalité, PV, TRI)
             analyzer.py (décote, travaux, signaux, score) · communes.py (classement des marchés)
  report/    export.py (terminal, CSV) · html.py (rapport interactif)
  cli.py · config.py · db.py (SQLite) · demo.py
```
