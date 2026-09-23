"""Fonctions utilitaires partagées."""
from __future__ import annotations

import math
import re
import unicodedata
import urllib.request
from pathlib import Path

USER_AGENT = "immo-scanner/0.1 (+analyse immobiliere open data)"

# Paris, Lyon, Marseille : DVF utilise les codes d'arrondissement.
PLM = {"751": "75056", "693": "69123", "132": "13055"}


def normalize(text: str | None) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", " ", text.lower())
    text = re.sub(r"\bst\b", "saint", text)
    text = re.sub(r"\bste\b", "sainte", text)
    return text.strip()


def to_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if isinstance(value, float) and math.isnan(value) else float(value)
    s = str(value).strip().replace(" ", "").replace("\xa0", "").replace(" ", "")
    s = re.sub(r"[€m²]|EUR", "", s, flags=re.I)
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def to_int(value) -> int | None:
    f = to_float(value)
    return None if f is None else int(round(f))


def parent_commune(code: str | None) -> str | None:
    """Code INSEE de la commune pour un arrondissement PLM (75111 -> 75056)."""
    if code and code[:3] in PLM and code != PLM[code[:3]]:
        return PLM[code[:3]]
    return None


def haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def median(values):
    s = sorted(values)
    n = len(s)
    if not n:
        return None
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def quantile(values, q):
    s = sorted(values)
    if not s:
        return None
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def download(url: str, dest: Path, timeout: int = 120) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp, open(dest, "wb") as out:
        while chunk := resp.read(1 << 16):
            out.write(chunk)
    return dest


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))
