"""Collecteur web générique basé sur les données structurées schema.org (JSON-LD).

Beaucoup de sites d'agences et de réseaux publient leurs annonces avec des
balises schema.org (RealEstateListing, Apartment, House, Offer…). Ce collecteur :
- respecte robots.txt et un délai entre requêtes ;
- extrait les annonces des pages fournies ;
- peut suivre les liens d'une page de résultats correspondant à un motif regex.

Il ne contourne aucune protection anti-robot : pour les grands portails, passez
par leur API / un flux partenaire / un agrégateur sous licence, puis importez
l'export avec ``immo import``.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from html.parser import HTMLParser

from ..util import USER_AGENT
from .base import Annonce

LISTING_TYPES = {"realestatelisting", "apartment", "house", "singlefamilyresidence", "residence",
                 "accommodation", "offer", "product", "place"}


class _Extractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.jsonld: list[str] = []
        self.links: list[str] = []
        self._in_ld = False
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and (a.get("type") or "").lower() == "application/ld+json":
            self._in_ld, self._buf = True, []
        elif tag == "a" and a.get("href"):
            self.links.append(a["href"])

    def handle_data(self, data):
        if self._in_ld:
            self._buf.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._in_ld:
            self._in_ld = False
            self.jsonld.append("".join(self._buf))


def _walk(node):
    if isinstance(node, list):
        for n in node:
            yield from _walk(n)
    elif isinstance(node, dict):
        yield node
        for key in ("@graph", "itemListElement", "item", "mainEntity", "about", "itemOffered"):
            if key in node:
                yield from _walk(node[key])


def _types(node) -> set[str]:
    t = node.get("@type", [])
    return {str(x).lower() for x in (t if isinstance(t, list) else [t])}


def _val(node, *path):
    for p in path:
        if isinstance(node, list):
            node = node[0] if node else None
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    if isinstance(node, dict) and "value" in node:
        return node["value"]
    return node


def _node_to_annonce(node: dict, page_url: str, source: str) -> Annonce | None:
    item = node.get("itemOffered") or node.get("about") or node.get("mainEntity") or node
    if isinstance(item, list):
        item = item[0] if item else node
    if not isinstance(item, dict):
        item = node
    offers = node.get("offers") or item.get("offers") or (node if "price" in node else {})
    prix = _val(offers, "price") or _val(offers, "priceSpecification", "price")
    surface = _val(item, "floorSize") or _val(node, "floorSize")
    if not prix or not surface:
        return None
    address = item.get("address") or node.get("address") or {}
    geo = item.get("geo") or node.get("geo") or {}
    url = node.get("url") or item.get("url") or page_url
    return Annonce(
        source=source,
        source_id=str(node.get("identifier") or node.get("sku") or url),
        url=urllib.parse.urljoin(page_url, url),
        titre=node.get("name") or item.get("name"),
        type_local=" ".join(_types(item)),
        prix=prix,
        surface=surface,
        pieces=_val(item, "numberOfRooms"),
        code_postal=_val(address, "postalCode"),
        ville=_val(address, "addressLocality"),
        lat=_val(geo, "latitude"),
        lon=_val(geo, "longitude"),
        description=node.get("description") or item.get("description"),
        date_publication=node.get("datePosted") or node.get("datePublished"),
    )


def parse_html(html: str, page_url: str, source: str) -> tuple[list[Annonce], list[str]]:
    ex = _Extractor()
    ex.feed(html)
    annonces = []
    for block in ex.jsonld:
        try:
            data = json.loads(block.strip())
        except json.JSONDecodeError:
            continue
        for node in _walk(data):
            if _types(node) & LISTING_TYPES:
                a = _node_to_annonce(node, page_url, source)
                if a:
                    annonces.append(a)
    links = [urllib.parse.urljoin(page_url, h) for h in ex.links]
    return annonces, links


class Crawler:
    def __init__(self, delay: float = 2.0, max_pages: int = 200, timeout: int = 30):
        self.delay, self.max_pages, self.timeout = delay, max_pages, timeout
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.robots_info: dict[str, tuple[str, str]] = {}
        self._last = 0.0

    def _lire_robots(self, base: str):
        """Lit robots.txt avec NOTRE identité (celle de Python est souvent refusée d'office).

        Renvoie (parser ou None, statut lisible). Conformément à la RFC 9309 : fichier absent
        (404…) = tout est permis ; accès refusé (401/403) = on s'abstient par prudence."""
        req = urllib.request.Request(base + "/robots.txt", headers={"User-Agent": USER_AGENT})
        rp = urllib.robotparser.RobotFileParser(base + "/robots.txt")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                texte = resp.read().decode("utf-8", errors="replace")
            rp.parse(texte.splitlines())
            return rp, "200", texte
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                rp.disallow_all = True
                return rp, f"{exc.code} (le site refuse de montrer son robots.txt aux robots)", ""
            return None, f"{exc.code} (pas de robots.txt : tout est permis)", ""
        except Exception as exc:  # noqa: BLE001 - site injoignable : l'erreur remontera au get()
            return None, f"injoignable ({exc})", ""

    def allowed(self, url: str) -> bool:
        parts = urllib.parse.urlsplit(url)
        base = f"{parts.scheme}://{parts.netloc}"
        if base not in self._robots:
            rp, statut, texte = self._lire_robots(base)
            self._robots[base] = rp
            self.robots_info[base] = (statut, texte)
        rp = self._robots[base]
        return rp is None or rp.can_fetch(USER_AGENT, url)

    def explication_robots(self, url: str) -> str:
        """Pourquoi robots.txt bloque cette URL (statut + règles qui nous concernent)."""
        parts = urllib.parse.urlsplit(url)
        statut, texte = self.robots_info.get(f"{parts.scheme}://{parts.netloc}", ("?", ""))
        regles, groupe = [], False
        for ligne in texte.splitlines():
            l = ligne.split("#")[0].strip()
            if l.lower().startswith("user-agent:"):
                agent = l.split(":", 1)[1].strip()
                groupe = agent == "*" or agent.lower() in USER_AGENT.lower()
                if groupe:
                    regles.append(l)
            elif groupe and l.lower().startswith(("disallow:", "allow:")):
                regles.append(l)
        return f"robots.txt : statut {statut}" + ("\n       " + "\n       ".join(regles[:12]) if regles else "")

    def get(self, url: str, accept: str | None = None) -> str:
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        entetes = {"User-Agent": USER_AGENT, "Accept-Language": "fr-FR"}
        if accept:
            entetes["Accept"] = accept
        req = urllib.request.Request(url, headers=entetes)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                charset = resp.headers.get_content_charset() or "utf-8"
                return resp.read().decode(charset, errors="replace")
        finally:
            self._last = time.monotonic()

    def crawl(self, start_urls, follow: str | None = None, source: str = "web", log=print):
        pattern = re.compile(follow) if follow else None
        queue, seen, results = list(start_urls), set(), {}
        while queue and len(seen) < self.max_pages:
            url = queue.pop(0)
            if url in seen:
                continue
            seen.add(url)
            if not self.allowed(url):
                log(f"  robots.txt interdit : {url}")
                continue
            try:
                html = self.get(url)
            except Exception as exc:  # noqa: BLE001 - on continue le crawl
                log(f"  erreur {url} : {exc}")
                continue
            annonces, links = parse_html(html, url, source)
            for a in annonces:
                results[a.id] = a
            log(f"  {url} -> {len(annonces)} annonce(s)")
            if pattern:
                queue.extend(l for l in links if pattern.search(l) and l not in seen)
        return list(results.values())
