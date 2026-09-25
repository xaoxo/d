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
from ..util import normalize, to_float
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
SURF = r"((?:\d{1,3}(?:[ \u202f\xa0]\d{3})+|\d{1,5})(?:[,.]\d{1,2})?)"
RX_SURFACE_CTX = re.compile(r"(?:surface(?:\s+habitable)?|loi\s+carrez|superficie|habitable)\s*(?:de|:)?\s*:?\s*"
                            + SURF + r"\s*m(?:²|2|\b)", re.I)
RX_SURFACE = re.compile(SURF + r"\s*m(?:²|2\b)", re.I)
RX_TITRE_PRIX = re.compile(r"^\s*" + NB + r"\s*(?:€|eur\b|euros?\b)", re.I)
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
    import unicodedata

    def plier(t):            # sans accents ni majuscules : « MERIGNAC » = « Mérignac »
        return "".join(c for c in unicodedata.normalize("NFD", t) if not unicodedata.combining(c)).lower()
    corps, v = plier(corps), re.escape(plier(ville.strip()))
    cp = r"((?:0[1-9]|[1-8]\d|9[0-5])\d{3}|97[1-6]\d{2}|20[0-2]\d{2})"
    m = re.search(cp + r"\s*[-,]?\s*" + v + r"\b", corps, re.I) or \
        re.search(r"\b" + v + r"\s*[-,(]?\s*" + cp + r"\b", corps, re.I)
    return m.group(1) if m else None


_PREPOSITION = re.compile(r"\s(?:à|À|a|A|sur la commune de|commune de)\s")
_PAS_UN_LIEU = re.compile(r"^(vendre|renover|rafraichir|saisir|prevoir|usage|proximite|partir|deux pas|quelques)\b")
_CONTEXTE_TIERS = re.compile(r"(avocat|ma[iî]tre|\bme\b|scp|selarl|selas|cabinet|tribunal|greffe|barreau|notaire|"
                             r"[ée]tude|agence|si[èe]ge|t[ée]l|fax|cedex|bo[iî]te postale|\bbp\b)", re.I)
_PAS_UNE_VILLE = re.compile(r"^(num[ée]ro|n°|r[ée]f[ée]rence|r[ée]f|dossier|t[ée]l|fax|cedex|lot|parcelle)", re.I)


def _lieu_du_titre(titre: str):
    """(ville, département, code postal) cités dans le titre, ou None."""
    m = RX_TITRE_LIEU.search(titre)
    if m:
        ville, paren = m.group(1).strip(" -"), m.group(2).strip()
        if re.fullmatch(r"\d{5}", paren):
            return ville, departement_depuis_nom(paren[:3] if paren.startswith("97") else paren[:2]), paren
        return ville, departement_depuis_nom(paren), None
    morceaux = _PREPOSITION.split(titre)
    if len(morceaux) < 2:
        return None
    lieu = morceaux[-1].strip(" -.,")
    cp = dep = None
    fin = re.search(r"\s+(\d{5}|\d{2,3}|2[AB])$", lieu)
    if fin:
        code, lieu = fin.group(1), lieu[:fin.start()].strip(" -,")
        cp = code if len(code) == 5 else None
        dep = departement_depuis_nom(code[:3] if code.startswith("97") else code[:2]) if cp else \
            departement_depuis_nom(code)
    if (not lieu or re.search(r"\d", lieu) or len(lieu.split()) > 6
            or _PAS_UN_LIEU.search(normalize(lieu)) or not lieu[0].isupper()):
        return None
    return lieu, dep, cp


def localiser(titre: str, corps: str, url: str = "") -> tuple[str | None, str | None, str | None]:
    """(code postal, ville, département) du BIEN.

    Le premier code postal d'une page est souvent celui du tribunal, de l'avocat ou de
    l'agence : on privilégie donc la ville citée dans le titre (« … à Ceyreste (Bouches-du-Rhône) »)
    et on ne retient un code postal que s'il est écrit à côté de cette ville. À défaut, on écarte
    les adresses de tiers (avocat, tribunal, CEDEX…) et les numéros de dossier."""
    lieu = _lieu_du_titre(titre)
    if lieu:
        ville, departement, cp = lieu
        cp = cp or _cp_pres_de(ville, corps)
        if cp and departement and not cp.startswith(departement[:2]):
            cp = None
        return cp, ville, departement or (departement_depuis_nom(cp[:2]) if cp else None)

    titre_n = normalize(titre)
    candidats = []
    for m in RX_CP_VILLE.finditer(corps):
        ville = m.group(2).strip(" -")
        avant = corps[max(0, m.start() - 120):m.start()]
        apres = corps[m.end():m.end() + 12]
        if (_PAS_UNE_VILLE.search(ville) or re.search(r"cedex", apres, re.I)
                or re.search(r"(n°|n\s*o|r[ée]f\.?|dossier)\s*:?\s*$", avant, re.I)
                or _CONTEXTE_TIERS.search("\n".join(avant.split("\n")[-2:]))):   # 2 dernières lignes
            continue
        candidats.append((m.group(1), ville))
    for cp, ville in candidats:           # une ville citée aussi dans le titre l'emporte
        if len(normalize(ville)) > 2 and normalize(ville) in titre_n:
            return cp, ville, None
    if candidats:
        return candidats[0][0], candidats[0][1], None
    m = RX_VILLE_CP.search(corps)
    if m and len(m.group(2)) == 5:
        return m.group(2), m.group(1).strip(" -"), None
    return None, None, None


def extraire(html: str, url: str, source: str, mode_vente: str = "vente") -> Annonce | None:
    titre, texte, meta = texte_page(html)
    corps = f"{titre}\n{meta.get('og:description', '')}\n{texte}"

    prix = None
    m = RX_TITRE_PRIX.search(titre)          # « 120 000 euros - Appartement T3… » : prix affiché par le site
    if m and _plausible_prix(_nombre(m.group(1))):
        prix = _nombre(m.group(1))
    ordre = (RX_MISE_A_PRIX, RX_OFFRE, RX_PRIX, RX_EUROS) if mode_vente != "vente" else \
        (RX_PRIX, RX_MISE_A_PRIX, RX_OFFRE, RX_EUROS)
    for rx in ordre if not prix else ():
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
            v = to_float(re.sub(r"[ \u202f\xa0](?=\d{3})", "", m.group(1)))
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
