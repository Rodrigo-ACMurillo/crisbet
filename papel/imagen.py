"""Genera una imagen PNG por ticket, con formato de boleto.

El objetivo no es que quede bonito sino que la ficha sea **honesta de un
vistazo**. Por eso la probabilidad real y el "1 de cada N" van en el encabezado,
al mismo tamano que la cuota: un boleto que solo ensena "x49.4" invita a leer
una cuota alta como una oportunidad, cuando lo que describe es un suceso que
ocurre el 3,5% de las veces.

El aviso de riesgo y el enlace de juego responsable son obligatorios en el plan
del proyecto (Sprint 9) y van en cada imagen, no en un pie de pagina aparte.

    python imagen.py --tickets ../model/reports/tickets.json
"""
from __future__ import annotations

import argparse
import json
import os
import unicodedata
from typing import Any, Dict, List

import matplotlib
matplotlib.use("Agg")            # sin ventana: esto corre en un cron
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, Rectangle

# Paleta sobria. El verde se reserva para lo que el modelo aporta y el ambar
# para las advertencias: si todo fuese verde, nada destacaria.
FONDO = "#11151c"
TARJETA = "#1a2029"
LINEA = "#2c3542"
TEXTO = "#e8ecf1"
TENUE = "#8a95a5"
ACENTO = "#4ea3ff"
VERDE = "#3ddc84"
AMBAR = "#ffb454"


def _limpio(texto: str) -> str:
    """Los nombres llegan con codificacion irregular del feed; se normaliza."""
    t = unicodedata.normalize("NFKC", str(texto or ""))
    return t.replace("�", "").strip()


def _corta(texto: str, n: int) -> str:
    t = _limpio(texto)
    return t if len(t) <= n else t[: n - 1] + "…"


def etiqueta_de_seleccion(pata: Dict[str, Any]) -> str:
    """"1" no dice nada en un handicap. Se nombra el equipo al que se apuesta.

    Un boleto que pone "Handicap: 1 -1.5" obliga a recordar quien era el local
    para saber que se ha apostado. Si hay que descifrarlo, la ficha no cumple
    su unica funcion.
    """
    sel = _limpio(pata["seleccion"])
    partido = _limpio(pata["partido"])
    equipos = [e.strip() for e in partido.split(" - ")] if " - " in partido else []
    lado = pata.get("lado")
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
    if pata.get("linea") is not None:
        # El signo solo significa algo en un handicap ("-1.5" es dar gol y medio).
        # En un total, "Menos de +1.5" no quiere decir nada.
        es_handicap = "ndicap" in _limpio(pata.get("mercado", ""))
        sel += f" {pata['linea']:+g}" if es_handicap else f" {pata['linea']:g}"
    return sel


def dibujar_ticket(ticket: Dict[str, Any], indice: int, destino: str,
                   aviso: str = "") -> str:
    patas = ticket["patas"]
    # El alto se deriva del contenido en las MISMAS unidades en que se dibuja.
    # Calcularlo a ojo dejaba el aviso de riesgo fuera del lienzo: la ficha se
    # veia bien y le faltaba justo lo que no es opcional.
    cabecera, por_pata, pie = 21.0, 6.2, 20.5
    Y = cabecera + por_pata * len(patas) + pie
    alto = Y / 10.0
    fig = plt.figure(figsize=(8.6, alto), dpi=170)
    fig.patch.set_facecolor(FONDO)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, Y)
    ax.axis("off")

    ax.add_patch(FancyBboxPatch((2.2, 2.2), 95.6, Y - 4.4, boxstyle="round,pad=0.6",
                                facecolor=TARJETA, edgecolor=LINEA, linewidth=1.2))

    # --- encabezado ---
    y = Y - 7
    ax.text(6, y, f"TICKET {indice}", color=ACENTO, fontsize=11, fontweight="bold",
            family="DejaVu Sans")
    ax.text(94, y, "CRISBET · simulación", color=TENUE, fontsize=8, ha="right")

    y -= 6.5
    ax.text(6, y, f"×{ticket['cuota_combinada']:.2f}", color=TEXTO, fontsize=27,
            fontweight="bold", va="center")
    # La probabilidad real, al lado y sin letra pequena.
    ax.text(34, y + 1.6, f"{ticket['probabilidad_real_pct']:.2f}% de probabilidad real",
            color=AMBAR, fontsize=11.5, fontweight="bold", va="center")
    ax.text(34, y - 2.6, f"se da 1 de cada {ticket['una_de_cada']} veces  ·  "
                         f"{ticket['n_patas']} patas, partidos distintos",
            color=TENUE, fontsize=9, va="center")

    y -= 6.5
    ax.plot([6, 94], [y, y], color=LINEA, linewidth=1)

    # --- patas ---
    for p in patas:
        y -= 6.2
        ax.text(6, y + 1.1, _corta(p["partido"], 40), color=TEXTO, fontsize=10.2,
                fontweight="bold")
        periodo = "  ·  1ª parte" if p.get("periodo") == "primera" else ""
        ax.text(6, y - 2.4, f"{_corta(p['mercado'], 26)}: {etiqueta_de_seleccion(p)}{periodo}",
                color=TENUE, fontsize=9)
        ax.text(72, y - 0.4, _corta(p["liga"], 16), color=TENUE, fontsize=8.5,
                ha="right", va="center")
        ax.text(94, y - 0.4, f"{p['cuota']:.2f}", color=TEXTO, fontsize=12.5,
                fontweight="bold", ha="right", va="center")

    y -= 4.5
    ax.plot([6, 94], [y, y], color=LINEA, linewidth=1)

    # --- pie: lo que el modelo cree frente a lo que cobra la casa ---
    y -= 5.2
    casa = ticket["probabilidad_implicita_casa_pct"]
    ax.text(6, y, f"La casa le da {casa:.2f}%  ·  el modelo {ticket['probabilidad_real_pct']:.2f}%",
            color=TENUE, fontsize=9)
    ax.text(94, y, f"ventaja estimada {ticket['ev_pct']:+.0f}%", color=VERDE, fontsize=9.5,
            fontweight="bold", ha="right")

    y -= 4.6
    ax.text(6, y, "Estimación de un modelo que NO ha demostrado batir al mercado. "
                  "Puede perderse todo.", color=AMBAR, fontsize=8, style="italic")
    y -= 3.4
    ax.text(6, y, "Solo mayores de 18 años  ·  juegocolombia.com  ·  Línea 018000 113 113",
            color=TENUE, fontsize=7.5)

    os.makedirs(os.path.dirname(os.path.abspath(destino)) or ".", exist_ok=True)
    fig.savefig(destino, facecolor=FONDO)
    plt.close(fig)
    return destino


def main() -> None:
    p = argparse.ArgumentParser(description="Tickets en PNG")
    p.add_argument("--tickets", default="../model/reports/tickets.json")
    p.add_argument("--salida", default="tickets_png")
    args = p.parse_args()

    with open(args.tickets, "r", encoding="utf-8") as fh:
        informe = json.load(fh)
    tickets = informe.get("tickets", [])
    if not tickets:
        raise SystemExit("El informe no trae tickets")

    generados: List[str] = []
    for i, t in enumerate(tickets, 1):
        ruta = os.path.join(args.salida, f"ticket_{i}.png")
        generados.append(dibujar_ticket(t, i, ruta, informe.get("aviso", "")))
        print(f"  {ruta}  ×{t['cuota_combinada']:.2f}  "
              f"{t['probabilidad_real_pct']:.2f}%  {t['n_patas']} patas")
    print(f"\n{len(generados)} imagenes en {args.salida}/")


if __name__ == "__main__":
    main()
