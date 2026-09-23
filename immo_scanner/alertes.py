"""Envoi des alertes : console, notification Windows, Telegram, e-mail."""
from __future__ import annotations

import json
import os
import smtplib
import subprocess
import urllib.parse
import urllib.request
from email.mime.text import MIMEText

from .config import Alertes


def _eur(x):
    return "—" if x is None else f"{x:,.0f} €".replace(",", " ")


def formater(r: dict, evenement: str, ancien_prix: float | None = None) -> tuple[str, str]:
    """Titre court + message détaillé pour une annonce analysée (ligne annonces + analyses)."""
    d = r.get("details") or {}
    lieu = r.get("ville") or r.get("code_postal") or "?"
    if evenement == "baisse" and ancien_prix:
        titre = f"Baisse de prix -{1 - r['prix'] / ancien_prix:.0%} : {lieu}"
    else:
        genre = {"enchere": "Enchère", "offre": "Vente à offres"}.get(r.get("mode_vente"), "Nouvelle annonce")
        titre = f"{genre} {r.get('score', 0):.0f}/100 : {lieu}"
    lignes = [
        f"{r.get('titre') or r.get('type_local') or 'Bien'} — {r.get('surface') or '?'} m²",
        f"Prix : {_eur(r.get('prix'))}" + (f" (avant : {_eur(ancien_prix)})" if ancien_prix else ""),
        f"Valeur estimée : {_eur(r.get('valeur_estimee'))}"
        + (f" · décote {r['decote_pct']:.0%}" if r.get("decote_pct") is not None else ""),
    ]
    if d.get("offre_max") is not None:
        lignes.append(f"Offre max conseillée : {_eur(d['offre_max'])}")
    if r.get("date_vente"):
        lignes.append(f"Date de vente : {r['date_vente']}")
    if r.get("rendement_brut") is not None:
        lignes.append(f"Rendement brut {r['rendement_brut']:.1%} · cash-flow {_eur(r.get('cashflow_mensuel'))}/mois")
    lignes.append(f"Verdict : {r.get('verdict')}")
    if r.get("url"):
        lignes.append(r["url"])
    return titre, "\n".join(lignes)


def windows(titre: str, message: str) -> None:
    if os.name != "nt":
        return
    esc = lambda s: s.replace("'", "''").replace("\n", " · ")[:250]  # noqa: E731
    script = (
        "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; $n.Icon = [System.Drawing.SystemIcons]::Information; "
        f"$n.Visible = $true; $n.ShowBalloonTip(15000, '{esc(titre)}', '{esc(message)}', "
        "[System.Windows.Forms.ToolTipIcon]::Info); Start-Sleep -Seconds 16; $n.Dispose()"
    )
    subprocess.Popen(["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def telegram(cfg: Alertes, titre: str, message: str) -> None:
    data = urllib.parse.urlencode({"chat_id": cfg.telegram_chat_id, "text": f"{titre}\n\n{message}",
                                   "disable_web_page_preview": "true"}).encode()
    url = f"https://api.telegram.org/bot{cfg.telegram_token}/sendMessage"
    with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=30) as resp:
        if not json.load(resp).get("ok"):
            raise RuntimeError("Telegram a refusé le message")


def email(cfg: Alertes, titre: str, message: str) -> None:
    msg = MIMEText(message, _charset="utf-8")
    msg["Subject"], msg["From"], msg["To"] = titre, cfg.email_utilisateur, cfg.email_destinataire
    with smtplib.SMTP_SSL(cfg.email_smtp, cfg.email_port, timeout=30) as s:
        s.login(cfg.email_utilisateur, cfg.email_mot_de_passe)
        s.send_message(msg)


def envoyer(cfg: Alertes, titre: str, message: str, log=print) -> None:
    log(f"\n[ALERTE] {titre}\n{message}")
    canaux = []
    if cfg.windows:
        canaux.append(("Windows", lambda: windows(titre, message)))
    if cfg.telegram_token and cfg.telegram_chat_id:
        canaux.append(("Telegram", lambda: telegram(cfg, titre, message)))
    if cfg.email_smtp and cfg.email_destinataire:
        canaux.append(("e-mail", lambda: email(cfg, titre, message)))
    for nom, envoi in canaux:
        try:
            envoi()
        except Exception as exc:  # noqa: BLE001 - une alerte ratée ne doit pas arrêter la surveillance
            log(f"  (échec de l'alerte {nom} : {exc})")
