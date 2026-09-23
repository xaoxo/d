"""Estimation de la valeur de marché et de la tendance des prix à partir de DVF."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache

from ..config import Marche
from ..util import haversine_m, median, parent_commune, quantile


@dataclass
class Tendance:
    taux_annuel: float | None          # croissance annuelle composée du prix au m²
    niveau: str                         # commune | departement | aucune
    medianes: dict = field(default_factory=dict)   # année -> prix m² médian
    volumes: dict = field(default_factory=dict)    # année -> nb ventes


@dataclass
class Estimation:
    prix_m2: float | None
    prix_m2_bas: float | None
    prix_m2_haut: float | None
    nb_comparables: int
    methode: str                  # rayon_300m | commune | departement …
    fiabilite: str                # haute | moyenne | faible | aucune
    ventes_par_an_commune: float = 0.0
    comparables: list = field(default_factory=list)


def _weighted_loglinear(points):
    """Régression pondérée log(prix) = a + b*année -> croissance annuelle exp(b)-1."""
    sw = sum(w for _, _, w in points)
    mx = sum(x * w for x, _, w in points) / sw
    my = sum(math.log(y) * w for _, y, w in points) / sw
    num = sum(w * (x - mx) * (math.log(y) - my) for x, y, w in points)
    den = sum(w * (x - mx) ** 2 for x, _, w in points)
    return math.exp(num / den) - 1 if den else None


class MarketModel:
    def __init__(self, conn, cfg: Marche, today: date | None = None):
        self.conn, self.cfg = conn, cfg
        self.today = today or self._latest_sale_date() or date.today()
        self._tendance = lru_cache(maxsize=None)(self._tendance_impl)

    def _latest_sale_date(self) -> date | None:
        row = self.conn.execute("SELECT MAX(date_mutation) FROM dvf_ventes").fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    # ---------------------------------------------------------------- tendance
    def _yearly(self, where: str, params) -> tuple[dict, dict]:
        rows = self.conn.execute(
            f"SELECT annee, prix_m2 FROM dvf_ventes WHERE {where} AND neuf=0", params).fetchall()
        by_year: dict[int, list] = {}
        for annee, p in rows:
            by_year.setdefault(annee, []).append(p)
        return ({y: median(v) for y, v in by_year.items()}, {y: len(v) for y, v in by_year.items()})

    def _tendance_impl(self, code_commune: str, type_local: str) -> Tendance:
        levels = [("commune", "code_commune=? AND type_local=?", (code_commune, type_local))]
        parent = parent_commune(code_commune)
        if parent:  # Paris/Lyon/Marseille : toute la ville si l'arrondissement est trop mince
            levels.append(("ville", "substr(code_commune,1,3)=? AND type_local=?",
                           (code_commune[:3], type_local)))
        dep = code_commune[:3] if code_commune.startswith("97") else code_commune[:2]
        levels.append(("departement", "code_departement=? AND type_local=?", (dep, type_local)))
        for niveau, where, params in levels:
            med, vol = self._yearly(where, params)
            pts = [(y, med[y], math.sqrt(vol[y])) for y in med if vol[y] >= 5]
            if len(pts) >= 3:
                return Tendance(_weighted_loglinear(pts), niveau, med, vol)
        return Tendance(None, "aucune")

    def tendance(self, code_commune: str, type_local: str) -> Tendance:
        return self._tendance(code_commune, type_local)

    # -------------------------------------------------------------- estimation
    def _since(self) -> str:
        return (self.today - timedelta(days=int(365.25 * self.cfg.annees_comparables))).isoformat()

    def _adjusted(self, rows, taux) -> list[float]:
        """Ramène chaque prix de vente à aujourd'hui selon la tendance locale."""
        out = []
        for r in rows:
            age = (self.today - date.fromisoformat(r["date_mutation"])).days / 365.25
            out.append(r["prix_m2"] * (1 + (taux or 0)) ** max(age, 0))
        return out

    def _filter_surface(self, rows, surface):
        if not surface:
            return rows
        close = [r for r in rows if 0.5 * surface <= r["surface"] <= 2 * surface]
        return close if len(close) >= self.cfg.min_comparables else rows

    def estimer(self, type_local: str, surface: float | None, code_commune: str | None,
                lat: float | None = None, lon: float | None = None) -> Estimation:
        since = self._since()
        taux = self.tendance(code_commune, type_local).taux_annuel if code_commune else None
        candidates = []

        if lat is not None and lon is not None:
            # Rayon croissant : on s'arrête dès qu'on a assez de ventes proches.
            best = None
            for rayon in self.cfg.rayons_m:
                dlat = rayon / 111_320
                dlon = rayon / (111_320 * max(math.cos(math.radians(lat)), 0.01))
                rows = self.conn.execute(
                    "SELECT * FROM dvf_ventes WHERE type_local=? AND lat BETWEEN ? AND ? "
                    "AND lon BETWEEN ? AND ? AND date_mutation>=? AND neuf=0",
                    (type_local, lat - dlat, lat + dlat, lon - dlon, lon + dlon, since)).fetchall()
                rows = [r for r in rows if haversine_m(lat, lon, r["lat"], r["lon"]) <= rayon]
                if len(rows) >= self.cfg.min_comparables:
                    best = (f"rayon_{rayon}m", rows)
                    if len(rows) >= self.cfg.cible_comparables:
                        break
            if best:
                candidates.append(best)

        if not candidates and code_commune:
            rows = self.conn.execute(
                "SELECT * FROM dvf_ventes WHERE code_commune=? AND type_local=? "
                "AND date_mutation>=? AND neuf=0", (code_commune, type_local, since)).fetchall()
            if len(rows) >= self.cfg.min_comparables:
                candidates.append(("commune", rows))
            else:
                dep = code_commune[:3] if code_commune.startswith("97") else code_commune[:2]
                drows = self.conn.execute(
                    "SELECT * FROM dvf_ventes WHERE code_departement=? AND type_local=? "
                    "AND date_mutation>=? AND neuf=0", (dep, type_local, since)).fetchall()
                if len(drows) >= self.cfg.min_comparables:
                    candidates.append(("departement", drows))

        vpa = self.ventes_par_an(code_commune) if code_commune else 0.0
        if not candidates:
            return Estimation(None, None, None, 0, "aucune", "aucune", vpa)

        methode, rows = candidates[0]
        rows = self._filter_surface(rows, surface)
        prix = self._adjusted(rows, taux)
        med, q1, q3 = median(prix), quantile(prix, 0.25), quantile(prix, 0.75)
        dispersion = (q3 - q1) / med if med else 1
        n = len(prix)
        if methode == "departement" or n < self.cfg.min_comparables:
            fiab = "faible"
        elif n >= 30 and dispersion < 0.35 and methode != "commune":
            fiab = "haute"
        elif n >= 15 and dispersion < 0.5:
            fiab = "moyenne"
        else:
            fiab = "faible"
        comps = sorted(rows, key=lambda r: r["date_mutation"], reverse=True)[:10]
        return Estimation(med, q1, q3, n, methode, fiab, vpa, [
            {"date": r["date_mutation"], "prix": r["valeur"], "surface": r["surface"],
             "prix_m2": round(r["prix_m2"]), "commune": r["nom_commune"]} for r in comps])

    def ventes_par_an(self, code_commune: str) -> float:
        n = self.conn.execute("SELECT COUNT(*) FROM dvf_ventes WHERE code_commune=? AND date_mutation>=?",
                              (code_commune, self._since())).fetchone()[0]
        return n / self.cfg.annees_comparables
