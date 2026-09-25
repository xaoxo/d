"""Interface en ligne de commande : ``immo <commande>``."""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import date
from pathlib import Path

from . import db
from .analysis.analyzer import analyser, analyser_tout
from .analysis.communes import classer_communes
from .analysis.market import MarketModel
from .config import load_config
from .report import export, html
from .sources import base, dvf, fichier, loyers, web


def _annees(spec: str) -> list[int]:
    if "-" in spec:
        a, b = spec.split("-")
        return list(range(int(a), int(b) + 1))
    return [int(x) for x in spec.split(",")]


def cmd_dvf(args, conn, cfg):
    if args.fichier:
        for f in args.fichier:
            n = dvf.load_file(conn, Path(f), cfg.marche)
            print(f"{f} : {n} ventes importées")
    else:
        deps = dvf.DEPARTEMENTS if args.departements == ["all"] else args.departements
        annees = _annees(args.annees)
        for annee in annees:
            for dep in deps:
                try:
                    path = dvf.fetch(annee, dep, Path(args.cache))
                    n = dvf.load_file(conn, path, cfg.marche)
                    print(f"DVF {annee} dép. {dep} : {n} ventes")
                except Exception as exc:  # noqa: BLE001
                    print(f"DVF {annee} dép. {dep} : échec ({exc})", file=sys.stderr)
    dvf.rebuild_communes(conn)
    total = conn.execute("SELECT COUNT(*) FROM dvf_ventes").fetchone()[0]
    print(f"Base DVF : {total} ventes exploitables")


def _preparer_loyers(conn, cache):
    if conn.execute("SELECT COUNT(*) FROM loyers").fetchone()[0]:
        return
    print("Téléchargement des loyers de marché (Carte des loyers, data.gouv.fr)…")
    try:
        for t, n in loyers.charger_auto(conn, Path(cache)).items():
            print(f"  loyers {t}s : {n} communes")
    except Exception as exc:  # noqa: BLE001
        print(f"  Échec du téléchargement automatique des loyers ({exc}).\n"
              "  Téléchargez le fichier « Carte des loyers » sur data.gouv.fr puis : immo loyers FICHIER --type appartement")


def cmd_preparer(args, conn, cfg):
    deps = dvf.DEPARTEMENTS if args.departements == ["all"] else args.departements
    for dep in deps:
        print(f"Département {dep} : téléchargement des ventes réelles (DVF)…")
        dvf.assurer_departement(conn, dep.upper(), Path(args.cache), cfg.marche)
    _preparer_loyers(conn, args.cache)
    print()
    cmd_stats(args, conn, cfg)
    if conn.execute("SELECT COUNT(*) FROM annonces").fetchone()[0]:
        print(f"{analyser_tout(conn, cfg)} annonce(s) ré-analysée(s)")


def cmd_loyers(args, conn, cfg):
    for src in args.source:
        n = loyers.load(conn, src, args.type, Path(args.cache))
        print(f"{src} : {n} indicateurs de loyer importés")


def _enregistrer(conn, cfg, annonces, analyse=True):
    new, upd = base.save(conn, annonces)
    print(f"{new} nouvelle(s) annonce(s), {upd} mise(s) à jour")
    if analyse:
        n = analyser_tout(conn, cfg)
        print(f"{n} annonce(s) analysée(s)")


def cmd_import(args, conn, cfg):
    annonces = []
    for f in args.fichiers:
        lot = fichier.read(f, args.source)
        print(f"{f} : {len(lot)} ligne(s) lue(s)")
        annonces += lot
    deps = {dvf.departement_depuis_cp(a.normalized().code_postal) for a in annonces} - {None}
    for dep in sorted(deps):
        dvf.assurer_departement(conn, dep, Path(args.cache), cfg.marche)
    if deps:
        _preparer_loyers(conn, args.cache)
    if args.remplacer:
        sources = {a.source for a in annonces}
        conn.executemany("UPDATE annonces SET active=0 WHERE source=?", [(s,) for s in sources])
    _enregistrer(conn, cfg, annonces)


def cmd_web(args, conn, cfg):
    urls = list(args.urls)
    if args.fichier_urls:
        urls += [l.strip() for l in Path(args.fichier_urls).read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
    crawler = web.Crawler(delay=args.delai, max_pages=args.max_pages)
    annonces = crawler.crawl(urls, follow=args.suivre, source=args.source)
    print(f"{len(annonces)} annonce(s) trouvée(s)")
    _enregistrer(conn, cfg, annonces)


def cmd_analyser(args, conn, cfg):
    print(f"{analyser_tout(conn, cfg)} annonce(s) analysée(s)")


def _filtres(args):
    return dict(ordre=args.ordre, limite=args.limite, departement=args.departement,
                type_local=args.type, prix_max=args.prix_max, score_min=args.score_min)


def cmd_top(args, conn, cfg):
    rows = export.resultats(conn, **_filtres(args))
    print(export.table_terminal(rows))
    if args.csv:
        export.to_csv(rows, args.csv)
        print(f"\nExport CSV : {args.csv}")


def cmd_rapport(args, conn, cfg):
    rows = export.resultats(conn, **_filtres(args))
    Path(args.sortie).write_text(html.render(rows, html.hypotheses(cfg)), encoding="utf-8")
    print(f"Rapport : {args.sortie} ({len(rows)} biens)")


def cmd_marches(args, conn, cfg):
    rows = classer_communes(conn, cfg, args.departements, args.type, args.min_ventes)[: args.limite]
    print(f"{'Score':>5}  {'Commune':<28} {'Prix m²':>8} {'Ventes/an':>9} {'Tendance':>9} "
          f"{'Loyer m²':>8} {'Rdt brut':>8}")
    for r in rows:
        loyer = "—" if r["loyer_m2"] is None else f"{r['loyer_m2']:.1f}"
        print(f"{r['score']:>5.1f}  {r['commune'][:28]:<28} {r['prix_m2_median']:>8} {r['ventes_par_an']:>9} "
              f"{export.pct(r['tendance_annuelle']):>9} {loyer:>8} "
              f"{export.pct(r['rendement_brut_theorique']):>8}")
    if args.csv:
        import csv
        with open(args.csv, "w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]) if rows else ["code_commune"], delimiter=";")
            w.writeheader()
            w.writerows(rows)
        print(f"Export CSV : {args.csv}")


def cmd_estimer(args, conn, cfg):
    a = base.Annonce(source="manuel", titre=args.description, type_local=args.type, prix=args.prix,
                     surface=args.surface, code_postal=args.cp, code_commune=args.commune, ville=args.ville,
                     lat=args.lat, lon=args.lon, dpe=args.dpe, description=args.description,
                     loyer_actuel=args.loyer, charges_annuelles=args.charges, taxe_fonciere=args.taxe_fonciere,
                     neuf=args.neuf).normalized()
    dep = dvf.departement_depuis_cp(a.code_postal) or (a.code_commune[:2] if a.code_commune else None)
    if dep:
        if dvf.assurer_departement(conn, dep, Path(args.cache), cfg.marche):
            print()
        _preparer_loyers(conn, args.cache)
    base.resolve_commune(conn, a)
    row = {**a.__dict__, "id": a.id, "prix_initial": a.prix}
    res = analyser(conn, cfg, MarketModel(conn, cfg.marche), row)
    d = json.loads(res["details"])
    b, e = d.get("bilan") or {}, d["estimation"]
    pct, eur = export.pct, export.eur
    print(f"Score : {res['score']} / 100  →  {res['verdict']}")
    print(f"Commune (INSEE) : {a.code_commune or 'inconnue'}")
    print(f"Prix marché : {eur(e['prix_m2'])}/m² ({e['nb_comparables']} comparables, {e['methode']}, "
          f"fiabilité {e['fiabilite']})")
    print(f"Valeur estimée : {eur(res['valeur_estimee'])}   Décote : {pct(res['decote_pct'])}")
    print(f"Travaux estimés : {eur(d['travaux_estimes'])}   Tendance : {pct(res['tendance_annuelle'])}/an")
    if b:
        print(f"Loyer : {eur(b['loyer_mensuel'])}/mois ({d['source_loyer']})")
        print(f"Rendement brut {pct(b['rendement_brut'])} · net {pct(b['rendement_net'])} · "
              f"net-net {pct(b['rendement_net_net'])}")
        print(f"Mensualité {eur(b['mensualite_credit'])} · cash-flow {eur(b['cashflow_mensuel'])} avant impôt, "
              f"{eur(b['cashflow_mensuel_apres_impot'])} après")
        print(f"À {b['horizon']} ans : revente {eur(b['valeur_revente'])}, plus-value nette "
              f"{eur(b['plus_value_nette'])}, enrichissement {eur(b['enrichissement'])}, TRI {pct(b['tri'])}")
    for s in d["signaux"]:
        print(f"  + {s}")
    for s in d["alertes"]:
        print(f"  ! {s}")


def _choisir_sites(args):
    from .sources import sites as sites_mod
    tous = sites_mod.charger_sites(args.sites_config)
    if not args.sites or args.sites == ["all"]:
        return [s for s in tous.values() if s.actif]
    inconnus = [n for n in args.sites if n not in tous]
    if inconnus:
        raise SystemExit(f"Site(s) inconnu(s) : {', '.join(inconnus)}. Disponibles : {', '.join(tous)}")
    return [tous[n] for n in args.sites]


def cmd_sites(args, conn, cfg):
    from .sources import sites as sites_mod
    for s in sites_mod.charger_sites(args.sites_config).values():
        print(f"{'[actif]  ' if s.actif else '[inactif]'} {s.nom:<12} {s.libelle}  ({s.mode_vente})")
        if s.notes:
            print(f"              {s.notes}")


def cmd_sites_tester(args, conn, cfg):
    """Diagnostic : ce que chaque site renvoie, sans rien enregistrer."""
    from .sources import sites as sites_mod
    from .sources.web import Crawler
    for site in _choisir_sites(args):
        print(f"\n=== {site.libelle} ===")
        s = sites_mod.Site(**{**site.__dict__, "max_pages": args.max_pages})
        crawler = Crawler(delay=args.delai)
        rep = sites_mod.collecter(s, crawler, args.departements, log=lambda *_: None)
        print(f"Pages de liste lues     : {rep.pages_liste}")
        print(f"Liens d'annonce trouvés : {rep.liens_annonce_trouves}")
        print(f"Fiches lues             : {rep.pages_annonce}")
        print(f"Annonces extraites      : {len(rep.annonces)}  (hors département : {rep.hors_departement}, "
              f"hors logement : {rep.hors_cible})")
        for a in rep.annonces[:5]:
            a.normalized()
            print(f"   - {a.prix:,.0f} € · {a.surface} m² · {a.code_postal or '?'} {a.ville or ''} · "
                  f"{(a.titre or '')[:50]}  {a.url}".replace(",", " "))
        for u, raison in rep.illisibles[:3]:
            print(f"   ? fiche non comprise ({raison}) : {u}")
        if s.api:
            print(f"API JSON                : {rep.api_statut or 'non lue'}")
            if rep.api_cles:
                print("   clés trouvées : " + ", ".join(rep.api_cles))
        for u in rep.bloques_robots[:3]:
            print(f"   ! interdit par robots.txt : {u}")
        if rep.bloques_robots:
            print("     " + crawler.explication_robots(rep.bloques_robots[0]))
        for e in rep.erreurs[:3]:
            print(f"   ! erreur : {e}")
        if rep.annonces:
            print("=> OK" + (f" ({len(rep.illisibles)} fiche(s) sans prix ou surface ignorée(s))"
                             if rep.illisibles else ""))
        elif rep.bloques_robots and not rep.pages_liste:
            print("=> Le site interdit l'accès aux robots : il est ignoré. Copiez ce diagnostic à l'assistant.")
        elif not rep.pages_liste:
            print("=> Site injoignable. Copiez ce diagnostic à l'assistant pour corriger le profil.")
        elif not rep.liens_annonce_trouves and not rep.annonces:
            print("=> Aucun lien d'annonce reconnu. Liens vus sur le site (pour l'assistant) :")
            for lien in rep.exemples_liens[:40]:
                print(f"     {lien}")
            for indice in rep.indices_api[:10]:
                print(f"     [api] {indice}")
        else:
            print("=> Liens trouvés mais fiches non comprises : le site ne publie ni données "
                  "structurées ni prix/surface lisibles.")


def cmd_surveiller(args, conn, cfg):
    from . import surveillance
    surveillance.surveiller(conn, cfg, _choisir_sites(args), args.departements, Path(args.cache),
                            args.rapport, args.intervalle, args.une_fois, args.delai)


def cmd_stats(args, conn, cfg):
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    print(f"Ventes DVF        : {q('SELECT COUNT(*) FROM dvf_ventes')}")
    print(f"  période         : {q('SELECT MIN(date_mutation) FROM dvf_ventes')} → "
          f"{q('SELECT MAX(date_mutation) FROM dvf_ventes')}")
    print(f"  départements    : {q('SELECT COUNT(DISTINCT code_departement) FROM dvf_ventes')}")
    print(f"Indicateurs loyer : {q('SELECT COUNT(*) FROM loyers')}")
    print(f"Annonces actives  : {q('SELECT COUNT(*) FROM annonces WHERE active=1')}")
    print(f"Analyses          : {q('SELECT COUNT(*) FROM analyses')}")


def cmd_demo(args, conn, cfg):
    from . import demo
    rng = random.Random(42)
    today = date.today()
    ventes = demo.generer_dvf(rng, today)
    conn.executemany(f"INSERT OR REPLACE INTO dvf_ventes ({','.join(dvf.COLUMNS)}) "
                     f"VALUES ({','.join('?' * len(dvf.COLUMNS))})", [[v[c] for c in dvf.COLUMNS] for v in ventes])
    conn.executemany("INSERT OR REPLACE INTO loyers VALUES (?,?,?,?,?,?)", demo.generer_loyers())
    conn.commit()
    dvf.rebuild_communes(conn)
    annonces = demo.generer_annonces(rng)
    base.save(conn, annonces)
    for a in annonces[::7]:        # simule des baisses de prix
        a.prix = round(a.prix * rng.uniform(0.88, 0.96), -3)
    base.save(conn, annonces[::7])
    print(f"DÉMO (données fictives) : {len(ventes)} ventes, {len(annonces)} annonces")
    print(f"{analyser_tout(conn, cfg)} annonce(s) analysée(s)")
    rows = export.resultats(conn, limite=500)
    Path(args.sortie).write_text(html.render(rows, html.hypotheses(cfg) + " — DONNÉES FICTIVES"),
                                 encoding="utf-8")
    print(export.table_terminal(rows[:15]))
    print(f"\nRapport : {args.sortie}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="immo", description="Scanner d'opportunités immobilières (France)")
    p.add_argument("--db", default=str(db.DEFAULT_DB), help="base SQLite (défaut : data/immo.db)")
    p.add_argument("--config", help="fichier TOML d'hypothèses (défaut : config.toml s'il existe ; voir config.example.toml)")
    p.add_argument("--cache", default="data/cache", help="dossier de téléchargement")
    p.add_argument("--sites-config", default="sites.toml", help="profils de sites personnalisés")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("dvf", help="importer les ventes DVF (data.gouv.fr)")
    s.add_argument("--departements", nargs="+", default=["all"], help="ex : 75 92 93, ou all")
    s.add_argument("--annees", default=f"{date.today().year - 5}-{date.today().year - 1}",
                   help="ex : 2021-2025 ou 2023,2024")
    s.add_argument("--fichier", nargs="+", help="fichier(s) DVF géolocalisés locaux (.csv / .csv.gz)")
    s.set_defaults(func=cmd_dvf)

    s = sub.add_parser("preparer", help="télécharger automatiquement ventes réelles + loyers d'un ou plusieurs départements")
    s.add_argument("departements", nargs="+", help="ex : 33 40, ou all")
    s.set_defaults(func=cmd_preparer)

    s = sub.add_parser("loyers", help="importer des indicateurs de loyers (fichier ou URL)")
    s.add_argument("source", nargs="+")
    s.add_argument("--type", choices=["appartement", "maison"], help="type de bien du fichier")
    s.set_defaults(func=cmd_loyers)

    s = sub.add_parser("import", help="importer des annonces (CSV / JSON / JSONL)")
    s.add_argument("fichiers", nargs="+")
    s.add_argument("--source", help="nom de la source (défaut : nom du fichier)")
    s.add_argument("--remplacer", action="store_true",
                   help="désactive les annonces de la source absentes de ce nouvel import")
    s.set_defaults(func=cmd_import)

    s = sub.add_parser("web", help="collecter des annonces schema.org (JSON-LD) sur des pages web")
    s.add_argument("urls", nargs="*")
    s.add_argument("--fichier-urls")
    s.add_argument("--suivre", help="regex des liens d'annonces à suivre depuis les pages de résultats")
    s.add_argument("--max-pages", type=int, default=200)
    s.add_argument("--delai", type=float, default=2.0, help="secondes entre deux requêtes")
    s.add_argument("--source", default="web")
    s.set_defaults(func=cmd_web)

    s = sub.add_parser("analyser", help="(re)calculer toutes les analyses")
    s.set_defaults(func=cmd_analyser)

    for name, helptext in (("top", "afficher les meilleures opportunités"),
                           ("rapport", "générer le rapport HTML")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--ordre", choices=list(export.ORDRES), default="score")
        s.add_argument("--limite", type=int, default=30 if name == "top" else 1000)
        s.add_argument("--departement")
        s.add_argument("--type", choices=["appartement", "maison"])
        s.add_argument("--prix-max", type=float)
        s.add_argument("--score-min", type=float)
        if name == "top":
            s.add_argument("--csv", help="exporter en CSV")
            s.set_defaults(func=cmd_top)
        else:
            s.add_argument("--sortie", default="rapport.html")
            s.set_defaults(func=cmd_rapport)

    s = sub.add_parser("marches", help="classer les communes (prix, tendance, rendement)")
    s.add_argument("--departements", nargs="+")
    s.add_argument("--type", choices=["appartement", "maison"], default="appartement")
    s.add_argument("--min-ventes", type=int, default=30)
    s.add_argument("--limite", type=int, default=30)
    s.add_argument("--csv")
    s.set_defaults(func=cmd_marches)

    s = sub.add_parser("estimer", help="analyser un bien précis sans l'enregistrer")
    s.add_argument("--prix", type=float, required=True)
    s.add_argument("--surface", type=float, required=True)
    s.add_argument("--type", choices=["appartement", "maison"], default="appartement")
    s.add_argument("--cp", help="code postal")
    s.add_argument("--commune", help="code INSEE")
    s.add_argument("--ville")
    s.add_argument("--lat", type=float)
    s.add_argument("--lon", type=float)
    s.add_argument("--dpe")
    s.add_argument("--loyer", type=float, help="loyer mensuel actuel si loué")
    s.add_argument("--charges", type=float, help="charges de copropriété annuelles")
    s.add_argument("--taxe-fonciere", type=float)
    s.add_argument("--neuf", action="store_true")
    s.add_argument("--description", default="")
    s.set_defaults(func=cmd_estimer)

    s = sub.add_parser("sites", help="lister les sites surveillables")
    s.set_defaults(func=cmd_sites)

    for name, helptext in (("sites-tester", "diagnostiquer ce que chaque site renvoie (n'enregistre rien)"),
                           ("surveiller", "surveiller les sites en continu et envoyer des alertes")):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--sites", nargs="+", help="noms des sites (défaut : tous les sites actifs)")
        s.add_argument("--departements", nargs="+", help="ne garder que ces départements")
        s.add_argument("--delai", type=float, default=2.0, help="secondes entre deux requêtes")
        if name == "sites-tester":
            s.add_argument("--max-pages", type=int, default=15)
            s.set_defaults(func=cmd_sites_tester)
        else:
            s.add_argument("--intervalle", type=float, default=30, help="minutes entre deux passages")
            s.add_argument("--une-fois", action="store_true", help="un seul passage (planificateur de tâches)")
            s.add_argument("--rapport", default="rapport.html", help="rapport HTML mis à jour à chaque passage")
            s.set_defaults(func=cmd_surveiller)

    s = sub.add_parser("stats", help="état de la base")
    s.set_defaults(func=cmd_stats)

    s = sub.add_parser("demo", help="charger un jeu de données FICTIF et produire un rapport")
    s.add_argument("--sortie", default="rapport_demo.html")
    s.set_defaults(func=cmd_demo)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    # config.toml à côté du programme est chargé automatiquement (alertes Telegram, hypothèses…)
    cfg = load_config(args.config or ("config.toml" if Path("config.toml").exists() else None))
    conn = db.connect(args.db)
    try:
        args.func(args, conn, cfg)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
