"""Extraction d'une annonce depuis le TEXTE d'une page, sans connaître sa structure HTML.

Sert de filet de sécurité quand un site ne publie pas de données schema.org :
on lit le titre (balises og:title / <h1> / <title>), puis on cherche dans le texte
visible le prix (ou la mise à prix), la surface, le code postal + ville, les pièces,
le terrain, le DPE et la date d'audience / de fin des offres.
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

from ..geo import departement_depuis_nom
from ..util import to_float
from .base import Annonce

_IGNORES = {"script", "style", "noscript", "svg", "template", "head"}
_VIDES = {"br", "img", "input", "link", "hr", "meta", "source", "wbr", "area", "col", "embed"}
_BLOCS = {"p", "div", "br", "li", "tr", "td", "th", "h1", "h2", "h3", "h4", "section", "article", "dd", "dt"}


class _Texte(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.meta: dict[str, str] = {}
        self.titre = ""
        self.h1 = ""
        self._pile: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            cle = (a.get("property") or a.get("name") or "").lower()
            if cle and a.get("content"):
                self.meta[cle] = a["content"]
            return
        if tag in _BLOCS:
            self.parts.append("\n")
        if tag not in _VIDES:
            self._pile.append(tag)

    def handle_endtag(self, tag):
        if tag in self._pile:
            while self._pile and self._pile.pop() != tag:
                pass
        if tag in _BLOCS:
            self.parts.append("\n")

    def handle_data(self, data):
        if "title" in self._pile:
            self.titre += data
        elif not any(t in _IGNORES for t in self._pile):
            if "h1" in self._pile:
                self.h1 += data
            self.parts.append(data)


NB = r"(\d{1,3}(?:[  \xa0.]\d{3})+|\d{4,9})(?:[,.]\d{1,2})?"
RX_MISE_A_PRIX = re.compile(r"mise\s+[àa]\s+prix\s*(?:de|:)?\s*:?\s*" + NB + r"\s*(?:€|eur|euros?)", re.I)
RX_OFFRE = re.compile(r"(?:premi[èe]re\s+offre(?:\s+possible)?|offre\s+de\s+d[ée]part|prix\s+de\s+d[ée]part)"
                      r"\s*:?\s*" + NB + r"\s*(?:€|eur|euros?)", re.I)
RX_PRIX = re.compile(r"prix(?:\s+de\s+vente)?(?:\s+(?:net\s+vendeur|fai|hai|honoraires\s+inclus))?\s*:?\s*"
                     + NB + r"\s*(?:€|eur|euros?)", re.I)
RX_EUROS = re.compile(NB + r"\s*(?:€|euros?)", re.I)
RX_SURFACE_CTX = re.compile(r"(?:surface(?:\s+habitable)?|loi\s+carrez|superficie|habitable)\s*(?:de|:)?\s*:?\s*"
                            r"(\d{1,4}(?:[,.]\d{1,2})?)\s*m(?:²|2|\b)", re.I)
RX_SURFACE = re.compile(r"(\d{1,4}(?:[,.]\d{1,2})?)\s*m(?:²|2\b)", re.I)
RX_TERRAIN = re.compile(r"terrain\s*(?:de|d'une\s+surface\s+de|:)?\s*:?\s*(\d{1,3}(?:[  \xa0.]\d{3})*|\d+)"
                        r"(?:[,.]\d+)?\s*m", re.I)
RX_PIECES = re.compile(r"(\d{1,2})\s*pi[èe]ces?|\b[TF](\d)\b", re.I)
RX_CP_VILLE = re.compile(r"\b((?:0[1-9]|[1-8]\d|9[0-5])\d{3}|97[1-6]\d{2}|20[0-2]\d{2})\s*[-,]?\s*"
                         r"([A-ZÉÈÊÎÔÂÛÇ][A-Za-zÀ-ÿ'’\- ]{1,40}?)(?=\s*(?:\n|\(|,|;|\.|\||$| - ))")
RX_VILLE_CP = re.compile(r"([A-ZÉÈÊÎÔÂÛÇ][A-Za-zÀ-ÿ'’\- ]{1,40}?)\s*\(\s*((?:0[1-9]|[1-8]\d|9[0-5])\d{3}|"
                         r"97[1-6]\d{2}|20[0-2]\d{2}|\d{2}|2[AB])\s*\)")
# « … une villa à Ceyreste (Bouches-du-Rhône) », « appartement à Paris 15ème (Paris) »
RX_TITRE_LIEU = re.compile(r"\b(?:à|a|sur la commune d[e'’]|commune d[e'’])\s*"
                           r"([A-ZÉÈÊÎÔÂÛÇ][A-Za-zÀ-ÿ0-9'’\- ]{1,50}?)\s*\(([^)]{2,40})\)")
RX_TITRE_VILLE = re.compile(r"\b(?:à|a)\s+([A-ZÉÈÊÎÔÂÛÇ][A-Za-zÀ-ÿ0-9'’\- ]{1,50}?)\s*$")
MOIS = "janvier|f[ée]vrier|mars|avril|mai|juin|juillet|ao[ûu]t|septembre|octobre|novembre|d[ée]cembre"
RX_DATE_VENTE = re.compile(r"(?:audience|adjudication|vente\s+(?:le|du)|date\s+de\s+(?:la\s+)?vente|fin\s+des\s+offres|"
                           r"cl[ôo]ture|jusqu'au)\D{0,40}?(\d{1,2}(?:er)?\s+(?:" + MOIS + r")\s+20\d\d|\d{2}/\d{2}/20\d\d)",
                           re.I)


def _nombre(txt: str) -> float | None:
    return to_float(re.sub(r"[  \xa0.](?=\d{3}\b)", "", txt))


def _plausible_prix(v):
    return v is not None and 1_000 <= v <= 50_000_000


def texte_page(html: str) -> tuple[str, str, dict]:
    p = _Texte()
    p.feed(html)
    texte = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    texte = re.sub(r"\n\s*\n+", "\n", texte).strip()
    titre = (p.meta.get("og:title") or p.h1 or p.titre or "").strip()
    return titre, texte, p.meta


def _cp_pres_de(ville: str, corps: str) -> str | None:
    """Code postal écrit juste à côté du nom de la ville (« 13600 Ceyreste » / « Ceyreste (13600) »)."""
    v = re.escape(ville.strip())
    cp = r"((?:0[1-9]|[1-8]\d|9[0-5])\d{3}|97[1-6]\d{2}|20[0-2]\d{2})"
    m = re.search(cp + r"\s*[-,]?\s*" + v + r"\b", corps, re.I) or \
        re.search(r"\b" + v + r"\s*[-,(]?\s*" + cp + r"\b", corps, re.I)
    return m.group(1) if m else None


def localiser(titre: str, corps: str, url: str = "") -> tuple[str | None, str | None, str | None]:
    """(code postal, ville, département) du BIEN.

    Le premier code postal d'une page est souvent celui du tribunal, de l'avocat ou de
    l'agence : on privilégie donc la ville citée dans le titre (« … à Ceyreste (Bouches-du-Rhône) »)
    et on ne retient un code postal que s'il est écrit à côté de cette ville."""
    m = RX_TITRE_LIEU.search(titre) or RX_TITRE_VILLE.search(titre)
    if m:
        ville = m.group(1).strip(" -")
        departement = departement_depuis_nom(m.group(2)) if m.lastindex and m.lastindex >= 2 else None
        cp = _cp_pres_de(ville, corps)
        if cp and departement and not cp.startswith(departement[:2]):
            cp = None
        return cp, ville, departement
    m = RX_CP_VILLE.search(corps)
    if m:
        return m.group(1), m.group(2).strip(" -"), None
    m = RX_VILLE_CP.search(corps)
    if m and len(m.group(2)) == 5:
        return m.group(2), m.group(1).strip(" -"), None
    return None, None, None


def extraire(html: str, url: str, source: str, mode_vente: str = "vente") -> Annonce | None:
    titre, texte, meta = texte_page(html)
    corps = f"{titre}\n{meta.get('og:description', '')}\n{texte}"

    prix = None
    ordre = (RX_MISE_A_PRIX, RX_OFFRE, RX_PRIX, RX_EUROS) if mode_vente != "vente" else \
        (RX_PRIX, RX_MISE_A_PRIX, RX_OFFRE, RX_EUROS)
    for rx in ordre:
        for m in rx.finditer(corps):
            v = _nombre(m.group(1))
            if _plausible_prix(v):
                prix = v
                break
        if prix:
            break

    surface = None
    for rx in (RX_SURFACE_CTX, RX_SURFACE):
        for m in rx.finditer(corps):
            v = to_float(m.group(1))
            if v and 9 <= v <= 2000:
                surface = v
                break
        if surface:
            break
    if not prix or not surface:
        return None

    cp, ville, departement = localiser(titre, corps, url)

    pieces = None
    m = RX_PIECES.search(corps)
    if m:
        pieces = int(m.group(1) or m.group(2))
    terrain = None
    m = RX_TERRAIN.search(corps)
    if m:
        terrain = _nombre(m.group(1))
    m = RX_DATE_VENTE.search(corps)
    date_vente = m.group(1) if m else None

    return Annonce(
        source=source, source_id=url, url=url, titre=titre[:200] or None,
        prix=prix, surface=surface, pieces=pieces, surface_terrain=terrain,
        code_postal=cp, ville=ville, departement=departement,
        description=(meta.get("og:description", "") + "\n" + texte)[:3000],
        mode_vente=mode_vente, date_vente=date_vente,
    )
