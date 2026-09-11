"""Genera el sitio estatico: tickets del dia e historial honesto del libro.

Pensado para GitHub Pages, que solo sirve ficheros: aqui se produce el HTML ya
hecho y Pages lo publica. Toda la generacion ocurre antes, en el workflow.

**Lo que decide el diseno.** Una pagina de pronosticos tiende sola a ensenar las
cuotas altas y esconder lo demas. Aqui la probabilidad real va junto a la cuota y
del mismo tamano, el ROI acumulado aparece aunque sea negativo, y si hay menos de
100 tickets liquidados se dice que la cifra todavia no significa nada en lugar de
presentarla como un resultado.

    python generar_sitio.py --salida ../_sitio
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import unicodedata
from typing import Any, Dict, List, Optional

try:
    import pandas as pd
except ImportError:
    pd = None


def limpio(texto: Any) -> str:
    t = unicodedata.normalize("NFKC", str(texto or "")).replace("�", "")
    return html.escape(t.strip())


def etiqueta_seleccion(p: Dict[str, Any]) -> str:
    """El nombre del equipo, no "1": un boleto ilegible no informa."""
    sel = str(p.get("seleccion") or "")
    equipos = [e.strip() for e in str(p.get("partido") or "").split(" - ")]
    lado = p.get("lado")
    if lado == "local" and equipos:
        sel = equipos[0]
    elif lado == "visitante" and len(equipos) > 1:
        sel = equipos[1]
    elif sel == "1" and equipos:
        sel = equipos[0]
    elif sel == "2" and len(equipos) > 1:
        sel = equipos[1]
    elif sel.lower() == "x":
        sel = "Empate"
    if p.get("linea") is not None:
        es_handicap = "ndicap" in str(p.get("mercado", ""))
        sel += f" {p['linea']:+g}" if es_handicap else f" {p['linea']:g}"
    return limpio(sel)


CSS = """
:root{--fondo:#11151c;--tarjeta:#1a2029;--linea:#2c3542;--texto:#e8ecf1;
--tenue:#8a95a5;--acento:#4ea3ff;--verde:#3ddc84;--ambar:#ffb454;--rojo:#ff6b6b}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--texto);
font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
.envoltorio{max-width:880px;margin:0 auto;padding:28px 18px 60px}
h1{font-size:1.5rem;margin:0 0 4px}
h2{font-size:1.05rem;margin:34px 0 12px;color:var(--tenue);
text-transform:uppercase;letter-spacing:.08em}
.sub{color:var(--tenue);font-size:.9rem;margin:0 0 24px}
.ticket{background:var(--tarjeta);border:1px solid var(--linea);border-radius:14px;
padding:18px 20px;margin-bottom:16px}
.cab{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.cuota{font-size:2rem;font-weight:700;letter-spacing:-.02em}
.prob{color:var(--ambar);font-weight:700}
.dato{color:var(--tenue);font-size:.85rem}
.patas{margin:14px 0 0;border-top:1px solid var(--linea);padding-top:12px}
.pata{display:flex;justify-content:space-between;gap:12px;padding:7px 0;
border-bottom:1px solid rgba(44,53,66,.5)}
.pata:last-child{border-bottom:0}
.pata .txt{min-width:0}
.pata .eq{font-weight:600}
.pata .mk{color:var(--tenue);font-size:.85rem}
.pata .cd{font-weight:700;font-size:1.05rem;white-space:nowrap}
.pie{display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;
margin-top:12px;padding-top:10px;border-top:1px solid var(--linea);font-size:.85rem}
.ev{color:var(--verde);font-weight:700}
table{width:100%;border-collapse:collapse;font-size:.9rem}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--linea)}
th{color:var(--tenue);font-weight:600;font-size:.8rem;text-transform:uppercase;
letter-spacing:.05em}
td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.cajas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}
.caja{background:var(--tarjeta);border:1px solid var(--linea);border-radius:12px;padding:14px}
.caja .v{font-size:1.5rem;font-weight:700}
.caja .e{color:var(--tenue);font-size:.8rem}
.aviso{background:rgba(255,180,84,.08);border:1px solid rgba(255,180,84,.35);
border-radius:12px;padding:14px 16px;color:var(--ambar);font-size:.88rem;margin:22px 0}
.legal{color:var(--tenue);font-size:.78rem;margin-top:34px;
border-top:1px solid var(--linea);padding-top:16px}
.neg{color:var(--rojo)}
@media(max-width:560px){.cuota{font-size:1.6rem}.pata{flex-direction:column;gap:2px}}
"""


def caja(valor: str, etiqueta: str, clase: str = "") -> str:
    return (f'<div class="caja"><div class="v {clase}">{valor}</div>'
            f'<div class="e">{limpio(etiqueta)}</div></div>')


def html_ticket(t: Dict[str, Any], i: int) -> str:
    patas = "".join(
        f'<div class="pata"><div class="txt">'
        f'<div class="eq">{limpio(p["partido"])}</div>'
        f'<div class="mk">{limpio(p["mercado"])}: {etiqueta_seleccion(p)}'
        f'{" · 1ª parte" if p.get("periodo") == "primera" else ""} · {limpio(p["liga"])}</div>'
        f'</div><div class="cd">{p["cuota"]:.2f}</div></div>'
        for p in t["patas"])
    return f"""<article class="ticket">
<div class="cab"><span class="cuota">&times;{t['cuota_combinada']:.2f}</span>
<span class="prob">{t['probabilidad_real_pct']:.2f}% de probabilidad real</span>
<span class="dato">se da 1 de cada {t['una_de_cada']} veces · {t['n_patas']} patas,
partidos distintos</span></div>
<div class="patas">{patas}</div>
<div class="pie"><span class="dato">La casa le da
{t['probabilidad_implicita_casa_pct']:.2f}% · el modelo
{t['probabilidad_real_pct']:.2f}%</span>
<span class="ev">ventaja estimada {t['ev_pct']:+.0f}%</span></div>
</article>"""


def html_resumen(r: Dict[str, Any]) -> str:
    if not r or not r.get("liquidados"):
        return ('<div class="cajas">'
                + caja(str(r.get("tickets_registrados", 0)), "tickets emitidos")
                + caja(str(r.get("pendientes", 0)), "pendientes de resolver")
                + caja("—", "ROI real")
                + '</div><p class="dato">Todavía no hay tickets liquidados. '
                  'El ROI real solo existe cuando se juegan los partidos.</p>')
    roi = r["roi_real_pct"]
    clase = "neg" if roi < 0 else ""
    ic = r.get("ic95_roi_pct")
    return ('<div class="cajas">'
            + caja(f"{roi:+.1f}%", "ROI real", clase)
            + caja(f"{r['ev_prometido_medio_pct']:+.0f}%", "EV que prometía el modelo")
            + caja(str(r["liquidados"]), "tickets liquidados")
            + caja(f"{r['tasa_de_acierto_pct']:.0f}%", "tickets acertados")
            + "</div>"
            + (f'<p class="dato">Intervalo de confianza al 95%: '
               f'{ic[0]:+.1f}% a {ic[1]:+.1f}%.</p>' if ic else "")
            + f'<p class="dato">{limpio(r.get("lectura", ""))}</p>')


def html_historial(df, limite: int = 40) -> str:
    if df is None or df.empty:
        return ""
    liq = df[df["estado"] == "liquidado"].sort_values("emitido_en", ascending=False)
    if liq.empty:
        return ""
    filas = "".join(
        f'<tr><td>{limpio(str(f["emitido_en"])[:10])}</td>'
        f'<td class="num">{f["cuota"]:.2f}</td>'
        f'<td class="num">{f["prob_modelo"]*100:.2f}%</td>'
        f'<td>{"acertado" if f["pago"] > 1 else "fallado"}</td>'
        f'<td class="num {"neg" if f["retorno"] < 0 else ""}">{f["retorno"]:+.2f}</td></tr>'
        for _, f in liq.head(limite).iterrows())
    return f"""<h2>Historial</h2><table>
<thead><tr><th>Emitido</th><th class="num">Cuota</th><th class="num">Prob.</th>
<th>Resultado</th><th class="num">Retorno</th></tr></thead>
<tbody>{filas}</tbody></table>"""


def generar(tickets_json: str, libro: str, salida: str) -> str:
    with open(tickets_json, "r", encoding="utf-8") as fh:
        informe = json.load(fh)
    tickets = informe.get("tickets", [])

    resumen: Dict[str, Any] = {}
    df = None
    if pd is not None and os.path.exists(libro):
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "..", "papel"))
        from registro import resumen as calcular_resumen
        resumen = calcular_resumen(libro)
        df = pd.read_parquet(libro)

    ahora = dt.datetime.now(dt.timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    cuerpo = "".join(html_ticket(t, i) for i, t in enumerate(tickets, 1))

    pagina = f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Crisbet · tickets del día</title>
<style>{CSS}</style></head><body><div class="envoltorio">
<h1>Tickets del día</h1>
<p class="sub">Generado el {ahora} · {len(tickets)} tickets · simulación en papel</p>

<div class="aviso"><strong>Esto no es un consejo de apuesta.</strong>
Son estimaciones de un modelo estadístico que <strong>no ha demostrado batir al
mercado</strong>. La ventaja que muestra cada ticket es una hipótesis sin validar:
lo único que la confirmará o la desmentirá es el ROI real del historial, y hacen
falta cientos de tickets para que esa cifra signifique algo. Puede perderse todo
lo apostado.</div>

<h2>Resultados acumulados</h2>
{html_resumen(resumen)}

<h2>Tickets de hoy</h2>
{cuerpo or '<p class="dato">Hoy no se emitieron tickets.</p>'}

{html_historial(df)}

<div class="legal">
Solo para mayores de 18 años. El juego puede causar adicción.
Línea de atención en Colombia: 018000 113 113 · juegocolombia.com<br>
Este sitio publica pronósticos con fines informativos. No gestiona, recibe ni
custodia dinero de terceros, ni está asociado a ningún operador de juego.
</div></div></body></html>"""

    os.makedirs(salida, exist_ok=True)
    ruta = os.path.join(salida, "index.html")
    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write(pagina)
    return ruta


def main() -> None:
    p = argparse.ArgumentParser(description="Genera el sitio estatico")
    p.add_argument("--tickets", default="../model/reports/tickets.json")
    p.add_argument("--libro", default="../papel/libro/tickets.parquet")
    p.add_argument("--salida", default="../_sitio")
    args = p.parse_args()
    ruta = generar(args.tickets, args.libro, args.salida)
    print(f"sitio generado: {ruta} ({os.path.getsize(ruta)} bytes)")


if __name__ == "__main__":
    main()
