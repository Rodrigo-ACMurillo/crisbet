"""Pruebas de la cobertura de mercados y del modulo de jugadores. Sin red.

Lo que mas se comprueba aqui son las **lineas asiaticas**, porque son donde es
facil equivocarse sin que salte nada: una devolucion mal contada no lanza
ninguna excepcion, solo produce un EV optimista.

    python test_mercados.py
"""
from __future__ import annotations

import json
import sys
from typing import List

import numpy as np

from jugadores import (ModeloGoleador, TasaDeGol, combinar_temporadas,
                       diagnostico_de_cobertura)
from mercados import MercadoDeConteo, MercadosDerivados, Resultado, _partes_asiaticas

fallos: List[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    print(f"[{'PASS' if condicion else 'FAIL'}] {nombre}" +
          (f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def matriz_ejemplo() -> np.ndarray:
    """Poisson independiente con 1.5 y 1.1 goles esperados: suficiente para probar."""
    from scipy.stats import poisson
    a = poisson.pmf(np.arange(10), 1.5)
    b = poisson.pmf(np.arange(10), 1.1)
    m = np.outer(a, b)
    return m / m.sum()


M = MercadosDerivados(matriz_ejemplo())


def test_1x2_y_derivados() -> None:
    rf = M.resultado_final()
    check("1X2 suma 1", abs(sum(r.prob_gana for r in rf) - 1) < 1e-9)
    do = M.doble_oportunidad()
    check("las 3 dobles oportunidades suman 2", abs(sum(r.prob_gana for r in do) - 2) < 1e-9)
    p1 = next(r.prob_gana for r in rf if r.seleccion == "1")
    px = next(r.prob_gana for r in rf if r.seleccion == "X")
    p1x = next(r.prob_gana for r in do if r.seleccion == "1X")
    check("1X = P(1) + P(X)", abs(p1x - (p1 + px)) < 1e-9)


def test_sin_empate_devuelve() -> None:
    """En 'apuesta sin empate' el empate no se pierde: se devuelve."""
    sd = M.apuesta_sin_empate()
    r = sd[0]
    check("las tres piezas suman 1", abs(r.prob_gana + r.prob_pierde + r.prob_empate - 1) < 1e-9)
    check("la devolucion no es cero", r.prob_empate > 0)
    check("la probabilidad efectiva descuenta la devolucion",
          r.prob_efectiva > r.prob_gana,
          f"{r.prob_efectiva:.4f} vs {r.prob_gana:.4f}")


def test_totales() -> None:
    t = M.total_goles(2.5)
    check("over + under en linea .5 suman 1",
          abs(t[0].prob_gana + t[1].prob_gana - 1) < 1e-9)
    check("sin devolucion en linea .5", t[0].prob_empate == 0.0)

    entera = M.total_goles(3.0)
    check("linea entera tiene devolucion", entera[0].prob_empate > 0)
    check("gana+pierde+devuelve = 1",
          abs(entera[0].prob_gana + entera[0].prob_pierde + entera[0].prob_empate - 1) < 1e-9)

    check("monotonia: P(>2.5) >= P(>3.5)",
          M.total_goles(2.5)[0].prob_gana >= M.total_goles(3.5)[0].prob_gana)
    check("monotonia: P(<2.5) <= P(<3.5)",
          M.total_goles(2.5)[1].prob_gana <= M.total_goles(3.5)[1].prob_gana)


def test_lineas_de_cuarto() -> None:
    check("-0.25 se parte en 0 y -0.5", _partes_asiaticas(-0.25) == [-0.5, 0.0],
          str(_partes_asiaticas(-0.25)))
    check("+0.75 se parte en 0.5 y 1.0", _partes_asiaticas(0.75) == [0.5, 1.0],
          str(_partes_asiaticas(0.75)))
    check("-0.5 no se parte", _partes_asiaticas(-0.5) == [-0.5])
    check("0 no se parte", _partes_asiaticas(0.0) == [0.0])
    check("-1.0 no se parte", _partes_asiaticas(-1.0) == [-1.0])


def test_handicap_asiatico() -> None:
    for linea in (-1.0, -0.75, -0.5, -0.25, 0.0, 0.25, 0.5, 1.0):
        ha = M.handicap_asiatico(linea)
        for r in ha:
            suma = r.prob_gana + r.prob_pierde + r.prob_empate
            check(f"asiatico {linea:+.2f} {r.seleccion}: las piezas suman 1",
                  abs(suma - 1) < 1e-9, f"{suma}")

    con_devolucion = M.handicap_asiatico(0.0)[0]
    check("handicap 0 devuelve en caso de empate", con_devolucion.prob_empate > 0)
    cuarto = M.handicap_asiatico(-0.25)[0]
    check("linea de cuarto devuelve solo la mitad",
          0 < cuarto.prob_empate < M.handicap_asiatico(0.0)[0].prob_empate + 1e-12,
          f"{cuarto.prob_empate:.4f}")

    check("dar ventaja mejora la probabilidad del local",
          M.handicap_asiatico(1.0)[0].prob_efectiva >
          M.handicap_asiatico(-1.0)[0].prob_efectiva)


def test_handicap_3way_no_devuelve() -> None:
    """El handicap europeo no devuelve: el empate con handicap es un resultado."""
    h = M.handicap_3way(-1.0)
    check("las 3 opciones suman 1", abs(sum(r.prob_gana for r in h) - 1) < 1e-9)
    check("sin devolucion", all(r.prob_empate == 0.0 for r in h))


def test_coherencia_entre_mercados() -> None:
    """Todo sale de la misma matriz: no puede haber contradicciones."""
    btts = M.ambos_marcan()[0].prob_gana
    marca_local = M.equipo_marca("local", 1)[0].prob_gana
    marca_visit = M.equipo_marca("visitante", 1)[0].prob_gana
    check("BTTS <= P(marca el local)", btts <= marca_local + 1e-12)
    check("BTTS <= P(marca el visitante)", btts <= marca_visit + 1e-12)
    check("P(marca 2+) <= P(marca 1+)",
          M.equipo_marca("local", 2)[0].prob_gana <= marca_local + 1e-12)

    rc = M.resultado_correcto(max_goles=9, minimo=0.0)
    check("los marcadores exactos suman ~1", abs(sum(r.prob_gana for r in rc) - 1) < 0.02,
          str(sum(r.prob_gana for r in rc)))

    p1 = next(r.prob_gana for r in M.resultado_final() if r.seleccion == "1")
    suma_local = sum(r.prob_gana for r in rc
                     if int(r.seleccion.split("-")[0]) > int(r.seleccion.split("-")[1]))
    check("la suma de marcadores con victoria local = P(1)", abs(suma_local - p1) < 0.02,
          f"{suma_local:.4f} vs {p1:.4f}")


def test_ev_y_cuota_justa() -> None:
    r = Resultado("x", "y", prob_gana=0.5, prob_pierde=0.5)
    check("cuota justa de un 50% es 2.0", abs(r.cuota_justa - 2.0) < 1e-9)
    check("EV cero a la cuota justa", abs(r.ev(2.0)) < 1e-9)
    check("EV positivo por encima", r.ev(2.2) > 0)
    check("EV negativo por debajo", r.ev(1.8) < 0)

    con_push = Resultado("x", "y", prob_gana=0.4, prob_pierde=0.4, prob_empate=0.2)
    check("la devolucion no cuenta como perdida", abs(con_push.prob_efectiva - 0.5) < 1e-9)
    check("EV con devolucion", abs(con_push.ev(2.0) - (0.4 * 1.0 - 0.4)) < 1e-9)


def test_catalogo_completo() -> None:
    todos = M.todos()
    check("el catalogo tiene volumen", len(todos) > 60, str(len(todos)))
    check("varios mercados distintos", len({r.mercado for r in todos}) >= 10)
    check("ninguna probabilidad fuera de rango",
          all(0.0 <= r.prob_gana <= 1.0 and 0.0 <= r.prob_pierde <= 1.0 for r in todos))
    check("ninguna cuota justa por debajo de 1",
          all(r.cuota_justa >= 1.0 for r in todos if r.prob_efectiva > 0))
    check("todas las piezas suman 1",
          all(abs(r.prob_gana + r.prob_pierde + r.prob_empate - 1) < 1e-6 for r in todos))


def test_mercado_de_conteo() -> None:
    c = MercadoDeConteo(10.0)
    t = c.total(9.5)
    check("over + under suman 1", abs(t[0].prob_gana + t[1].prob_gana - 1) < 1e-6)
    check("con media 10, el over de 9.5 pasa del 50%", t[0].prob_gana > 0.5)
    entero = c.total(10.0)
    check("linea entera de corners devuelve", entero[0].prob_empate > 0)


def test_mezcla_de_temporadas() -> None:
    """El caso real de la jornada 3: 3 goles en 3 partidos no son 1.00 por partido.

    Sin mezcla, el filtro de fiabilidad deja el modelo sin ningun jugador hasta
    bien entrada la temporada; sin encogimiento, una racha de tres partidos se
    convierte en una tasa absurda. Las dos cosas son errores.
    """
    actual = [TasaDeGol("Estrella", "X", 3, 3, "L", "t"),
              TasaDeGol("Fugaz", "X", 2, 2, "L", "t")]
    anterior = [TasaDeGol("Estrella", "X", 27, 36, "L", "t"),
                TasaDeGol("Ausente", "X", 20, 34, "L", "t")]
    tasas = combinar_temporadas(actual, anterior)

    e = tasas["Estrella"]
    check("la tasa se encoge hacia el pasado", e.tasa < 1.0, f"{e.tasa:.3f}")
    check("pero queda por encima de la del ano pasado", e.tasa > 27 / 36,
          f"{e.tasa:.3f} vs {27/36:.3f}")
    check("la estrella es fiable", e.fiable)

    check("un goleador del ano pasado que aun no marco sigue estando",
          "Ausente" in tasas)
    a = tasas["Ausente"]
    check("y con la tasa del ano pasado", abs(a.tasa - 20 / 34) < 1e-9)
    check("marcado como procedente solo del historico", "anterior" in a.fuente)

    f = tasas["Fugaz"]
    check("un jugador sin historial y con 2 partidos NO es fiable", not f.fiable)


def test_jugadores_sin_dato() -> None:
    tasas = combinar_temporadas(
        [TasaDeGol("Fiable", "X", 5, 10, "L", "t"), TasaDeGol("Novato", "X", 2, 2, "L", "t")],
        [TasaDeGol("Fiable", "X", 15, 30, "L", "t")])
    m = ModeloGoleador(tasas, media_goles_por_equipo=1.4)

    check("un jugador con historial se valora", m.probabilidades("Fiable", 1.6) is not None)
    check("un jugador con 2 partidos y sin pasado se descarta",
          m.probabilidades("Novato", 1.6) is None)
    check("un jugador desconocido se descarta", m.probabilidades("Nadie", 1.6) is None)

    r = m.probabilidades("Fiable", 1.6)
    check("P(2+) <= P(marca)", r["p_marca_2+"] <= r["p_marca"])
    check("P(3+) <= P(2+)", r["p_marca_3+"] <= r["p_marca_2+"])
    check("el aviso de alineaciones viaja con el resultado", "alineaciones" in r["aviso"])

    flojo = m.probabilidades("Fiable", 0.8)
    fuerte = m.probabilidades("Fiable", 2.4)
    check("contra un rival flojo el jugador marca mas",
          fuerte["p_marca"] > r["p_marca"] > flojo["p_marca"],
          f"{flojo['p_marca']:.3f} < {r['p_marca']:.3f} < {fuerte['p_marca']:.3f}")
    # El informe viene redondeado a 4 decimales: la tolerancia va acorde.
    check("el factor del partido escala con los goles esperados del equipo",
          abs(fuerte["factor_del_partido"] - 2.4 / 1.4) < 1e-4,
          f"{fuerte['factor_del_partido']} vs {2.4/1.4}")


def test_diagnostico_de_cobertura() -> None:
    d = diagnostico_de_cobertura("Colombia", "liga_betplay_dimayor")
    check("Liga BetPlay no se puede valorar sin proveedor de pago",
          d["se_puede_valorar"] is False)
    check("se explica el motivo", bool(d["motivo"]), str(d))
    check("se dice que hacer", "DESCARTAR" in (d["consecuencia"] or ""))


def test_clave_de_liga_no_es_solo_la_clave() -> None:
    """El bug que hubo: en el feed de Betplay `liga_key` NO es unico.

    'premier_league' vale para Inglaterra, Rusia, Ucrania, Kazajistan y Jordania.
    Mapear solo por la clave aplicaria las tasas de gol de la Premier inglesa a
    jugadores jordanos, sin lanzar ningun error.
    """
    from jugadores import FootballDataOrg
    prov = FootballDataOrg(token="token-de-prueba")
    check("Premier inglesa: si", prov.disponible_para("Inglaterra", "premier_league"))
    check("Premier de Jordania: NO", not prov.disponible_para("Jordania", "premier_league"))
    check("Premier de Rusia: NO", not prov.disponible_para("Rusia", "premier_league"))
    check("Bundesliga alemana: si", prov.disponible_para("Alemania", "bundesliga"))
    check("Bundesliga austriaca: NO", not prov.disponible_para("Austria", "bundesliga"))
    check("el pais con tilde se normaliza", prov.disponible_para("España", "la_liga"))
    check("el motivo nombra el pais",
          "Jordania" in prov.motivo_no_disponible("Jordania", "premier_league"))


if __name__ == "__main__":
    for prueba in (test_1x2_y_derivados, test_sin_empate_devuelve, test_totales,
                   test_lineas_de_cuarto, test_handicap_asiatico,
                   test_handicap_3way_no_devuelve, test_coherencia_entre_mercados,
                   test_ev_y_cuota_justa, test_catalogo_completo, test_mercado_de_conteo,
                   test_mezcla_de_temporadas, test_jugadores_sin_dato,
                   test_diagnostico_de_cobertura,
                   test_clave_de_liga_no_es_solo_la_clave):
        print("\n== " + prueba.__name__ + " ==")
        prueba()
    print("\n" + ("TODO OK" if not fallos else "FALLOS: " + json.dumps(fallos)))
    sys.exit(1 if fallos else 0)
