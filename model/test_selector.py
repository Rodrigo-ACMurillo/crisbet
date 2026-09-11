"""Pruebas del selector. Sin red.

Las tres primeras son regresiones de bugs que se encontraron en produccion, y
las tres compartian la misma propiedad: **no lanzaban ningun error**. Producian
tickets con EV del +180% que parecian oro y eran aritmetica rota.

    python test_selector.py
"""
from __future__ import annotations

import json
import sys
from typing import List

import numpy as np
import pandas as pd
from scipy.stats import poisson

from mercados import MercadosDerivados
from selector import (Pata, Ticket, _norm_mercado, construir_tickets,
                      periodo_del_mercado, quitar_periodo, valorar_linea)

fallos: List[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"[{'PASS' if condicion else 'FAIL'}] {nombre}" +
          (f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def catalogo(lam: float = 2.2, mu: float = 0.62) -> MercadosDerivados:
    a = poisson.pmf(np.arange(10), lam)
    b = poisson.pmf(np.arange(10), mu)
    m = np.outer(a, b)
    return MercadosDerivados(m / m.sum())


COMPLETO = catalogo()
PRIMERA = catalogo(0.95, 0.28)


def linea(mercado, etiqueta=None, valor=None, participante=None,
          local="Real Madrid", visitante="Rayo Vallecano"):
    return pd.Series({"mercado": mercado, "etiqueta": etiqueta, "linea": valor,
                      "participante": participante, "_local": local,
                      "_visitante": visitante})


# -- regresiones ------------------------------------------------------------

def test_periodo_se_detecta_con_ordinal_femenino() -> None:
    """'1.ª parte' normaliza a '1.a parte', no a '1. parte'.

    La deteccion fallaba y los mercados de media parte se valoraban con el
    modelo del partido completo: "mas de 0.5 goles" pasaba de ~0.50 a ~0.86.
    """
    casos = {
        "Hándicap asiático - 1.ª parte": "primera",
        "Total de goles de Arsenal - 1ª parte": "primera",
        "Total asiático - 1.ª parte": "primera",
        "Total de goles de Sunderland - 2ª parte": "segunda",
        "Ambos Equipos Marcarán - 2.ª parte": "segunda",
        "Descanso": "segunda",
        "Resultado Final": "completo",
    }
    for texto, esperado in casos.items():
        obtenido = periodo_del_mercado(_norm_mercado(texto))
        check(f"periodo de {texto!r}", obtenido == esperado, obtenido)

    check("el periodo se quita del nombre base",
          quitar_periodo(_norm_mercado("Hándicap asiático - 1.ª parte")) == "handicap asiatico",
          quitar_periodo(_norm_mercado("Hándicap asiático - 1.ª parte")))


def test_segunda_parte_no_se_valora() -> None:
    """No hay modelo de segunda parte: valorarla con otro seria inventar."""
    r = valorar_linea(linea("Total de goles de Real Madrid - 2ª parte", "Menos de", 0.5),
                      COMPLETO, PRIMERA)
    check("la segunda parte devuelve None", r is None)


def test_equipo_de_los_totales_ignora_el_sufijo_de_periodo() -> None:
    """El bug caro: el equipo se leia del nombre CRUDO.

    En "Total de goles de Real Madrid - 1ª parte" quedaba como
    "real madrid - 1a parte", que nunca coincide con el local, y la linea se
    asignaba al VISITANTE. Todos los mercados de media parte valoraban al
    equipo contrario, con EV inventados del +182%.
    """
    r_local = valorar_linea(linea("Total de goles de Real Madrid", "Menos de", 0.5),
                            COMPLETO, PRIMERA)
    r_local_ht = valorar_linea(linea("Total de goles de Real Madrid - 1ª parte",
                                     "Menos de", 0.5), COMPLETO, PRIMERA)
    r_visit_ht = valorar_linea(linea("Total de goles de Rayo Vallecano - 1ª parte",
                                     "Menos de", 0.5), COMPLETO, PRIMERA)
    check("el total del local se valora", r_local is not None)
    check("el de primera parte tambien", r_local_ht is not None)
    check("y NO son el mismo equipo que el visitante",
          r_local_ht is not None and r_visit_ht is not None
          and abs(r_local_ht.prob_gana - r_visit_ht.prob_gana) > 0.05,
          f"local_ht={r_local_ht.prob_gana:.4f} visit_ht={r_visit_ht.prob_gana:.4f}"
          if r_local_ht and r_visit_ht else "None")
    check("el local fuerte falla menos al marcar que el visitante debil",
          r_local_ht.prob_gana < r_visit_ht.prob_gana)

    desconocido = valorar_linea(linea("Total de goles de Otro Equipo", "Menos de", 0.5),
                                COMPLETO, PRIMERA)
    check("un equipo que no juega el partido no se valora", desconocido is None)


def test_handicap_invierte_el_signo_para_el_visitante() -> None:
    """Cada seleccion trae su propio signo, desde su perspectiva.

    Pasar la misma linea a los dos lados daba probabilidades del 96% para el
    visitante y EV del +260%.
    """
    local = valorar_linea(linea("Hándicap Asiático", "Real Madrid", -1.0,
                                participante="Real Madrid"), COMPLETO, None)
    visit = valorar_linea(linea("Hándicap Asiático", "Rayo Vallecano", 1.0,
                                participante="Rayo Vallecano"), COMPLETO, None)
    check("ambos lados se valoran", local is not None and visit is not None)
    # Es la misma apuesta vista desde los dos lados: las probabilidades deben
    # ser complementarias, no iguales.
    check("los dos lados suman 1 (misma linea, perspectivas opuestas)",
          abs(local.prob_gana + visit.prob_gana + local.prob_empate - 1) < 1e-6,
          f"{local.prob_gana:.4f} + {visit.prob_gana:.4f} + {local.prob_empate:.4f}")
    # Con lambda 2.2 frente a 0.62, el favorito sigue ganando mas aun dando un
    # gol de ventaja. Lo que importa es que los dos lados sean complementarios,
    # no que el handicap invierta el favoritismo.
    check("un favorito muy superior gana aun dando 1 gol",
          local.prob_gana > visit.prob_gana,
          f"{local.prob_gana:.4f} vs {visit.prob_gana:.4f}")
    check("la devolucion es ganar por exactamente 1",
          local.prob_empate > 0.1, f"{local.prob_empate:.4f}")

    # Dar 3 goles si le da la vuelta: ahi el handicap pesa mas que la diferencia.
    duro = valorar_linea(linea("Handicap Asiatico", "Real Madrid", -3.0,
                               participante="Real Madrid"), COMPLETO, None)
    check("dando 3 goles el favorito ya no es favorito",
          duro.prob_gana < 0.5, f"{duro.prob_gana:.4f}")


# -- construccion de tickets ------------------------------------------------

def _pata(ev_id, cuota, prob, ev, oid):
    return Pata(event_id=ev_id, partido=f"p{ev_id}", liga="L", inicio="2026-09-12T00:00:00Z",
                mercado="m", seleccion="s", linea=None, cuota=cuota,
                prob_modelo=prob, ev=ev, outcome_id=oid)


def test_ticket_multiplica_bien() -> None:
    t = Ticket([_pata(1, 2.0, 0.5, 0.0, 10), _pata(2, 3.0, 0.34, 0.02, 20)])
    check("la cuota es el producto", abs(t.cuota - 6.0) < 1e-9, str(t.cuota))
    check("la probabilidad es el producto", abs(t.probabilidad - 0.17) < 1e-9)
    check("el EV sale de ambos", abs(t.ev - (0.17 * 6.0 - 1)) < 1e-9)
    check("un ticket vacio no revienta", Ticket().cuota == 0.0)


def test_no_repite_partido_en_un_ticket() -> None:
    """Dos patas del mismo partido estan correlacionadas y Betplay las recorta."""
    patas = [_pata(1, 3.0, 0.35, 0.05, i) for i in range(4)] + \
            [_pata(2, 3.0, 0.35, 0.04, 10), _pata(3, 3.0, 0.35, 0.03, 11),
             _pata(4, 3.0, 0.35, 0.02, 12)]
    tickets = construir_tickets(patas, objetivo=27.0, n_tickets=1)
    check("se construye un ticket", len(tickets) == 1, str(len(tickets)))
    if tickets:
        ids = [p.event_id for p in tickets[0].patas]
        check("ningun partido se repite", len(ids) == len(set(ids)), str(ids))


def test_no_reutiliza_patas_entre_tickets() -> None:
    patas = [_pata(i, 3.5, 0.3, 0.05, i) for i in range(12)]
    tickets = construir_tickets(patas, objetivo=40.0, n_tickets=3)
    usadas = [p.outcome_id for t in tickets for p in t.patas]
    check("se generan varios tickets", len(tickets) >= 2, str(len(tickets)))
    check("ninguna pata se repite entre tickets", len(usadas) == len(set(usadas)))


def test_no_se_pasa_de_la_cuota_objetivo() -> None:
    patas = [_pata(i, 4.0, 0.28, 0.12, i) for i in range(10)]
    tickets = construir_tickets(patas, objetivo=50.0, n_tickets=1, tolerancia=0.35)
    check("hay ticket", len(tickets) == 1)
    if tickets:
        check("la cuota queda en la banda pedida",
              50 * 0.65 <= tickets[0].cuota <= 50 * 1.35, str(tickets[0].cuota))


if __name__ == "__main__":
    for prueba in (test_periodo_se_detecta_con_ordinal_femenino,
                   test_segunda_parte_no_se_valora,
                   test_equipo_de_los_totales_ignora_el_sufijo_de_periodo,
                   test_handicap_invierte_el_signo_para_el_visitante,
                   test_ticket_multiplica_bien, test_no_repite_partido_en_un_ticket,
                   test_no_reutiliza_patas_entre_tickets,
                   test_no_se_pasa_de_la_cuota_objetivo):
        print("\n== " + prueba.__name__ + " ==")
        prueba()
    print("\n" + ("TODO OK" if not fallos else "FALLOS: " + json.dumps(fallos)))
    sys.exit(1 if fallos else 0)
