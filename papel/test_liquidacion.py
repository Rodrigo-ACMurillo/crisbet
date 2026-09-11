"""Pruebas de la liquidacion. Sin red, exhaustivas a proposito.

Aqui un error no se nota: liquidar mal una pata no lanza nada, solo mueve el
ROI medido. Y si el ROI medido esta mal, el paper trading deja de servir para
lo unico que sirve — saber si el sistema gana o pierde.

    python test_liquidacion.py
"""
from __future__ import annotations

import json
import sys
from typing import List

from liquidacion import (DEVUELVE, GANA, GANA_MEDIA, PIERDE, PIERDE_MEDIA,
                         Marcador, liquidar_pata, liquidar_ticket, pago_de_pata)

fallos: List[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"[{'PASS' if condicion else 'FAIL'}] {nombre}" +
          (f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


M = Marcador(2, 1, ht_local=1, ht_visitante=0)      # 2-1, al descanso 1-0


def test_1x2() -> None:
    check("gana el local", liquidar_pata("Resultado Final", "1", None, M) == GANA)
    check("pierde el empate", liquidar_pata("Resultado Final", "X", None, M) == PIERDE)
    check("pierde el visitante", liquidar_pata("Resultado Final", "2", None, M) == PIERDE)
    empate = Marcador(1, 1)
    check("con empate gana la X", liquidar_pata("Resultado Final", "X", None, empate) == GANA)


def test_doble_oportunidad_y_sin_empate() -> None:
    check("1X gana con victoria local",
          liquidar_pata("Doble Oportunidad", "1X", None, M) == GANA)
    check("X2 pierde con victoria local",
          liquidar_pata("Doble Oportunidad", "X2", None, M) == PIERDE)
    check("12 gana con victoria local",
          liquidar_pata("Doble Oportunidad", "12", None, M) == GANA)

    check("sin empate: gana el local", liquidar_pata("Apuesta sin empate", "1", None, M) == GANA)
    check("sin empate: el empate DEVUELVE",
          liquidar_pata("Apuesta sin empate", "1", None, Marcador(1, 1)) == DEVUELVE)


def test_totales_y_devolucion() -> None:
    check("mas de 2.5 gana con 3 goles",
          liquidar_pata("Total de goles", "Mas de", 2.5, M) == GANA)
    check("menos de 2.5 pierde con 3 goles",
          liquidar_pata("Total de goles", "Menos de", 2.5, M) == PIERDE)
    check("linea entera clavada DEVUELVE",
          liquidar_pata("Total de goles", "Mas de", 3.0, M) == DEVUELVE)
    check("mas de 3.5 pierde con 3 goles",
          liquidar_pata("Total de goles", "Mas de", 3.5, M) == PIERDE)


def test_lineas_de_cuarto() -> None:
    """Con 3 goles y linea 2.75: media apuesta en 2.5 (gana) y media en 3.0 (devuelve)."""
    r = liquidar_pata("Total asiatico", "Mas de", 2.75, M)
    check("2.75 con 3 goles da media ganancia", r == GANA_MEDIA, str(r))
    check("el pago es cuota/2 + 1/2",
          abs(pago_de_pata(r, 2.0) - (0.5 * 2.0 + 0.5)) < 1e-9)

    r2 = liquidar_pata("Total asiatico", "Mas de", 3.25, M)
    check("3.25 con 3 goles da media perdida", r2 == PIERDE_MEDIA, str(r2))
    check("el pago de media perdida es 0.5", abs(pago_de_pata(r2, 2.0) - 0.5) < 1e-9)


def test_totales_por_equipo() -> None:
    check("el local marco 2: mas de 1.5 gana",
          liquidar_pata("Total de goles de Local", "Mas de", 1.5, M, lado="local") == GANA)
    check("el visitante marco 1: mas de 1.5 pierde",
          liquidar_pata("Total de goles de Visitante", "Mas de", 1.5, M, lado="visitante") == PIERDE)
    check("sin saber el lado no se liquida",
          liquidar_pata("Total de goles de X", "Mas de", 1.5, M) is None)


def test_handicap_asiatico() -> None:
    # 2-1: el local gana por 1.
    check("local -0.5 gana",
          liquidar_pata("Handicap Asiatico", "1", -0.5, M, lado="local") == GANA)
    check("local -1.0 DEVUELVE (gana por exactamente 1)",
          liquidar_pata("Handicap Asiatico", "1", -1.0, M, lado="local") == DEVUELVE)
    check("local -1.5 pierde",
          liquidar_pata("Handicap Asiatico", "1", -1.5, M, lado="local") == PIERDE)
    check("visitante +1.5 gana",
          liquidar_pata("Handicap Asiatico", "2", 1.5, M, lado="visitante") == GANA)
    check("visitante +1.0 DEVUELVE",
          liquidar_pata("Handicap Asiatico", "2", 1.0, M, lado="visitante") == DEVUELVE)
    check("visitante +0.5 pierde",
          liquidar_pata("Handicap Asiatico", "2", 0.5, M, lado="visitante") == PIERDE)

    check("local -0.75 con victoria por 1 da media ganancia",
          liquidar_pata("Handicap Asiatico", "1", -0.75, M, lado="local") == GANA_MEDIA)
    check("local -1.25 con victoria por 1 da media perdida",
          liquidar_pata("Handicap Asiatico", "1", -1.25, M, lado="local") == PIERDE_MEDIA)


def test_handicap_3way_no_devuelve() -> None:
    r = liquidar_pata("Handicap 3-Way", "X", -1.0, M)
    check("el empate con handicap es un RESULTADO, no devolucion", r == GANA, str(r))
    check("sin devolucion posible",
          liquidar_pata("Handicap 3-Way", "1", -1.0, M) == PIERDE)


def test_primera_parte() -> None:
    check("al descanso 1-0: gana el local",
          liquidar_pata("Resultado Final", "1", None, M, periodo="primera") == GANA)
    check("al descanso 1 gol: menos de 1.5 gana",
          liquidar_pata("Total de goles", "Menos de", 1.5, M, periodo="primera") == GANA)
    check("y con el partido completo perderia",
          liquidar_pata("Total de goles", "Menos de", 1.5, M) == PIERDE)

    sin_descanso = Marcador(2, 1)
    check("sin marcador al descanso no se liquida la primera parte",
          liquidar_pata("Resultado Final", "1", None, sin_descanso, periodo="primera") is None)
    check("la segunda parte nunca se liquida",
          liquidar_pata("Resultado Final", "1", None, M, periodo="segunda") is None)


def test_ambos_marcan_y_resultado_correcto() -> None:
    check("2-1: ambos marcan, si", liquidar_pata("Ambos Equipos Marcaran", "Si", None, M) == GANA)
    check("2-0: ambos marcan, no",
          liquidar_pata("Ambos Equipos Marcaran", "No", None, Marcador(2, 0)) == GANA)
    check("marcador exacto acertado", liquidar_pata("Resultado Correcto", "2-1", None, M) == GANA)
    check("marcador exacto fallado", liquidar_pata("Resultado Correcto", "1-1", None, M) == PIERDE)


def test_mercado_desconocido() -> None:
    check("un mercado que no se sabe liquidar devuelve None",
          liquidar_pata("Numero de saques de banda", "Mas de", 20.5, M) is None)


def test_ticket_completo() -> None:
    patas = [
        {"event_id": 1, "mercado": "Resultado Final", "seleccion": "1", "linea": None, "cuota": 2.0},
        {"event_id": 2, "mercado": "Total de goles", "seleccion": "Mas de", "linea": 2.5, "cuota": 1.8},
    ]
    marcadores = {1: Marcador(2, 1), 2: Marcador(3, 1)}
    r = liquidar_ticket(patas, marcadores)
    check("las dos patas ganan", r["acertado"])
    check("el pago es el producto", abs(r["pago"] - 3.6) < 1e-9, str(r["pago"]))
    check("el retorno descuenta el importe", abs(r["retorno"] - 2.6) < 1e-9)

    perdedor = liquidar_ticket(patas, {1: Marcador(0, 1), 2: Marcador(3, 1)})
    check("una pata fallada tumba el ticket", perdedor["pago"] == 0.0)
    check("y el retorno es -1", perdedor["retorno"] == -1.0)

    pendiente = liquidar_ticket(patas, {1: Marcador(2, 1)})
    check("si falta un resultado, el ticket queda pendiente", pendiente is None)


def test_devolucion_en_combinado() -> None:
    """Una pata devuelta no tumba el ticket: su cuota pasa a valer 1."""
    patas = [
        {"event_id": 1, "mercado": "Handicap Asiatico", "seleccion": "1", "linea": -1.0,
         "cuota": 2.0, "lado": "local"},
        {"event_id": 2, "mercado": "Resultado Final", "seleccion": "1", "linea": None, "cuota": 3.0},
    ]
    r = liquidar_ticket(patas, {1: Marcador(2, 1), 2: Marcador(1, 0)})
    check("la pata devuelta no anula el ticket", r["acertado"])
    check("el ticket paga solo la cuota de la pata ganadora",
          abs(r["pago"] - 3.0) < 1e-9, str(r["pago"]))


if __name__ == "__main__":
    for prueba in (test_1x2, test_doble_oportunidad_y_sin_empate, test_totales_y_devolucion,
                   test_lineas_de_cuarto, test_totales_por_equipo, test_handicap_asiatico,
                   test_handicap_3way_no_devuelve, test_primera_parte,
                   test_ambos_marcan_y_resultado_correcto, test_mercado_desconocido,
                   test_ticket_completo, test_devolucion_en_combinado):
        print("\n== " + prueba.__name__ + " ==")
        prueba()
    print("\n" + ("TODO OK" if not fallos else "FALLOS: " + json.dumps(fallos)))
    sys.exit(1 if fallos else 0)
