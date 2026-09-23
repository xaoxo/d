"""Rapport HTML autonome (un seul fichier, sans dépendance externe) :
tableau triable/filtrable des opportunités + fiche détaillée de chaque bien."""
from __future__ import annotations

import html
import json
from datetime import datetime

TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Opportunités immobilières</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e3de;--accent:#1f6f5c;
--good:#1f7a3f;--warn:#a15c00;--bad:#b3261e;--chip:#eef3f1}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--card:#1e1e1c;--ink:#ecebe6;--muted:#a3a29b;
--line:#33332f;--accent:#5cc2a6;--good:#6ccf8e;--warn:#e0a24a;--bad:#ef8a80;--chip:#23302c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{padding:24px 16px 8px;max-width:1400px;margin:auto}
h1{font-size:22px;margin:0 0 4px}.sub{color:var(--muted)}
main{max-width:1400px;margin:auto;padding:0 16px 48px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin:16px 0}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.kpi b{display:block;font-size:22px}.kpi span{color:var(--muted);font-size:12px}
.filters{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 12px}
.filters input,.filters select{background:var(--card);color:var(--ink);border:1px solid var(--line);
border-radius:8px;padding:7px 9px;font:inherit}
.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;min-width:1000px}
th,td{padding:8px 10px;border-bottom:1px solid var(--line);text-align:right;white-space:nowrap}
th{position:sticky;top:0;background:var(--card);cursor:pointer;font-weight:600;font-size:12px;color:var(--muted)}
th:first-child,td:first-child,th.l,td.l{text-align:left}
tr.row{cursor:pointer}tr.row:hover{background:var(--chip)}
.score{display:inline-block;min-width:42px;text-align:center;border-radius:6px;padding:2px 6px;font-weight:700;color:#fff}
.pos{color:var(--good)}.neg{color:var(--bad)}
.detail td{background:var(--bg);white-space:normal;text-align:left}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:16px;padding:6px 2px}
.grid h4{margin:0 0 6px;font-size:13px}.grid dl{display:grid;grid-template-columns:1fr auto;gap:2px 12px;margin:0}
.grid dt{color:var(--muted)}.grid dd{margin:0;text-align:right}
ul{margin:0;padding-left:18px}.sig li{color:var(--good)}.al li{color:var(--warn)}
a{color:var(--accent)}.note{color:var(--muted);font-size:12px;margin-top:16px}
</style>
</head>
<body>
<header><h1>Opportunités immobilières</h1>
<div class="sub">Généré le __DATE__ · __N__ biens analysés · hypothèses : __HYP__</div></header>
<main>
<div class="kpis" id="kpis"></div>
<div class="filters">
<input id="q" placeholder="Rechercher ville, titre…">
<select id="type"><option value="">Tous types</option><option>appartement</option><option>maison</option></select>
<input id="pmax" type="number" placeholder="Prix max €">
<input id="smin" type="number" placeholder="Score min">
<select id="verdict"><option value="">Tous verdicts</option></select>
</div>
<div class="wrap"><table><thead><tr>
<th data-k="score">Score</th><th class="l" data-k="verdict">Verdict</th><th class="l" data-k="ville">Ville</th>
<th class="l" data-k="type_local">Type</th><th data-k="surface">m²</th><th data-k="prix">Prix</th>
<th data-k="valeur_estimee">Valeur estimée</th><th data-k="decote_pct">Décote</th>
<th data-k="rendement_brut">Rdt brut</th><th data-k="rendement_net">Rdt net</th>
<th data-k="cashflow_mensuel">Cash-flow/mois</th><th data-k="tri">TRI</th>
<th data-k="plus_value_horizon">PV nette</th><th data-k="tendance_annuelle">Tendance</th><th class="l" data-k="dpe">DPE</th>
</tr></thead><tbody id="tb"></tbody></table></div>
<p class="note">Estimations indicatives calculées à partir des ventes réelles DVF (données publiques) et des
indicateurs de loyers. Elles ne remplacent ni une visite, ni un avis professionnel. Décote = écart entre la valeur
de marché estimée (travaux déduits le cas échéant) et le prix affiché.</p>
</main>
<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const eur = x => x==null ? "—" : Math.round(x).toLocaleString("fr-FR")+" €";
const pct = (x,d=1) => x==null ? "—" : (x*100).toFixed(d).replace(".",",")+" %";
const cls = x => x==null ? "" : (x>0 ? "pos" : (x<0 ? "neg" : ""));
const color = s => s>=75?"#1f7a3f":s>=62?"#3d8b4f":s>=50?"#8a8f2a":s>=38?"#a15c00":"#b3261e";
let sortKey="score", sortDir=-1, open=new Set();
[...new Set(DATA.map(r=>r.verdict))].sort().forEach(v=>{const o=document.createElement("option");o.textContent=v;$("#verdict").appendChild(o)});
function esc(s){return String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function filtered(){
  const q=$("#q").value.toLowerCase(), t=$("#type").value, pm=+$("#pmax").value||Infinity,
        sm=+$("#smin").value||-Infinity, v=$("#verdict").value;
  return DATA.filter(r=>(!q||(r.ville+" "+r.titre+" "+r.code_postal).toLowerCase().includes(q))
    &&(!t||r.type_local===t)&&r.prix<=pm&&r.score>=sm&&(!v||r.verdict===v))
   .sort((a,b)=>{const x=a[sortKey],y=b[sortKey];if(x==null)return 1;if(y==null)return -1;
     return (x>y?1:x<y?-1:0)*sortDir});
}
function detail(r){
  const d=r.details||{}, b=d.bilan||{}, e=d.estimation||{}, ch=b.charges_annuelles||{};
  const comps=(e.comparables||[]).map(c=>`<li>${esc(c.date)} · ${esc(c.commune)} · ${c.surface} m² · ${eur(c.prix)} (${eur(c.prix_m2)}/m²)</li>`).join("");
  return `<div class="grid">
  <div><h4>Marché</h4><dl>
   <dt>Prix m² annonce</dt><dd>${eur(r.prix/r.surface)}</dd>
   <dt>Prix m² marché (médian)</dt><dd>${eur(e.prix_m2)}</dd>
   <dt>Fourchette (Q1–Q3)</dt><dd>${eur(e.prix_m2_bas)} – ${eur(e.prix_m2_haut)}</dd>
   <dt>Comparables</dt><dd>${e.nb_comparables??0} (${esc(e.methode)})</dd>
   <dt>Fiabilité</dt><dd>${esc(r.fiabilite)}</dd>
   <dt>Ventes/an commune</dt><dd>${(e.ventes_par_an_commune||0).toFixed(0)}</dd>
   <dt>Tendance prix</dt><dd class="${cls(r.tendance_annuelle)}">${pct(r.tendance_annuelle)}/an</dd>
   <dt>Travaux estimés</dt><dd>${eur(d.travaux_estimes)}</dd>
   <dt>Valeur après travaux</dt><dd>${eur(d.valeur_apres_travaux)}</dd></dl></div>
  <div><h4>Location &amp; financement</h4><dl>
   <dt>Loyer mensuel (${esc(d.source_loyer||"—")})</dt><dd>${eur(b.loyer_mensuel)}</dd>
   <dt>Coût total (notaire, travaux)</dt><dd>${eur(b.cout_total)}</dd>
   <dt>Apport / emprunt</dt><dd>${eur(b.apport)} / ${eur(b.emprunt)}</dd>
   <dt>Mensualité (assurance incl.)</dt><dd>${eur(b.mensualite_credit)}</dd>
   <dt>Taxe foncière</dt><dd>${eur(ch.taxe_fonciere)}</dd>
   <dt>Copro non récupérable</dt><dd>${eur(ch.copro_non_recuperable)}</dd>
   <dt>Impôt annuel</dt><dd>${eur(b.impot_annuel)}</dd>
   <dt>Rendement net-net</dt><dd>${pct(b.rendement_net_net)}</dd>
   <dt>Cash-flow avant impôt</dt><dd class="${cls(b.cashflow_mensuel)}">${eur(b.cashflow_mensuel)}</dd></dl></div>
  <div><h4>Revente à ${b.horizon??"—"} ans</h4><dl>
   <dt>Valeur de revente</dt><dd>${eur(b.valeur_revente)}</dd>
   <dt>Plus-value brute</dt><dd class="${cls(b.plus_value_brute)}">${eur(b.plus_value_brute)}</dd>
   <dt>Impôt plus-value</dt><dd>${eur(b.impot_plus_value)}</dd>
   <dt>Plus-value nette</dt><dd class="${cls(b.plus_value_nette)}">${eur(b.plus_value_nette)}</dd>
   <dt>Enrichissement total</dt><dd class="${cls(b.enrichissement)}">${eur(b.enrichissement)}</dd>
   <dt>TRI (sur apport)</dt><dd>${pct(b.tri)}</dd></dl></div>
  <div><h4>Signaux</h4><ul class="sig">${(d.signaux||[]).map(s=>`<li>${esc(s)}</li>`).join("")||"<li>—</li>"}</ul>
   <h4 style="margin-top:10px">Points de vigilance</h4><ul class="al">${(d.alertes||[]).map(s=>`<li>${esc(s)}</li>`).join("")||"<li>—</li>"}</ul>
   ${r.url?`<p><a href="${esc(r.url)}" target="_blank" rel="noopener">Voir l'annonce ↗</a></p>`:""}</div>
  <div><h4>Dernières ventes comparables</h4><ul>${comps||"<li>—</li>"}</ul></div></div>`;
}
function render(){
  const rows=filtered();
  const top=rows.filter(r=>r.score>=62).length, dec=rows.filter(r=>(r.decote_pct||0)>=0.1).length;
  const rdt=rows.map(r=>r.rendement_brut).filter(x=>x!=null).sort((a,b)=>a-b);
  const cf=rows.filter(r=>(r.cashflow_mensuel||-1)>=0).length;
  $("#kpis").innerHTML=[["Biens",rows.length],["Très intéressants ou mieux",top],["Sous-évalués ≥ 10 %",dec],
    ["Rendement brut médian",rdt.length?pct(rdt[Math.floor(rdt.length/2)]):"—"],["Cash-flow positif",cf]]
    .map(([l,v])=>`<div class="kpi"><b>${v}</b><span>${l}</span></div>`).join("");
  $("#tb").innerHTML=rows.map(r=>`<tr class="row" data-id="${esc(r.id)}">
   <td><span class="score" style="background:${color(r.score)}">${r.score.toFixed(0)}</span></td>
   <td class="l">${esc(r.verdict)}</td><td class="l">${esc(r.ville||r.code_commune)} <small>${esc(r.code_postal)}</small></td>
   <td class="l">${esc(r.type_local)}</td><td>${Math.round(r.surface)}</td><td>${eur(r.prix)}</td>
   <td>${eur(r.valeur_estimee)}</td><td class="${cls(r.decote_pct)}">${pct(r.decote_pct,0)}</td>
   <td>${pct(r.rendement_brut)}</td><td>${pct(r.rendement_net)}</td>
   <td class="${cls(r.cashflow_mensuel)}">${eur(r.cashflow_mensuel)}</td><td>${pct(r.tri)}</td>
   <td class="${cls(r.plus_value_horizon)}">${eur(r.plus_value_horizon)}</td>
   <td class="${cls(r.tendance_annuelle)}">${pct(r.tendance_annuelle)}</td><td class="l">${esc(r.dpe||"—")}</td></tr>`
   +(open.has(r.id)?`<tr class="detail"><td colspan="15"><b>${esc(r.titre||"")}</b>${detail(r)}</td></tr>`:"")).join("");
}
$("#tb").addEventListener("click",e=>{const tr=e.target.closest("tr.row");if(!tr||e.target.closest("a"))return;
  const id=tr.dataset.id;open.has(id)?open.delete(id):open.add(id);render()});
document.querySelectorAll("th").forEach(th=>th.addEventListener("click",()=>{const k=th.dataset.k;
  sortDir=(k===sortKey)?-sortDir:-1;sortKey=k;render()}));
["#q","#type","#pmax","#smin","#verdict"].forEach(s=>$(s).addEventListener("input",render));
render();
</script>
</body>
</html>
"""


def render(rows: list[dict], hypotheses: str) -> str:
    data = json.dumps(rows, ensure_ascii=False, default=str).replace("</", "<\\/")
    return (TEMPLATE.replace("__DATA__", data)
            .replace("__DATE__", datetime.now().strftime("%d/%m/%Y %H:%M"))
            .replace("__N__", str(len(rows)))
            .replace("__HYP__", html.escape(hypotheses)))


def hypotheses(cfg) -> str:
    f = cfg.financement
    return (f"apport {f.apport_pct:.0%}, crédit {f.taux_credit:.2%} sur {f.duree_annees} ans, "
            f"fiscalité {cfg.fiscalite.regime} (TMI {cfg.fiscalite.tmi:.0%}), "
            f"horizon de revente {cfg.projection.horizon_annees} ans")
