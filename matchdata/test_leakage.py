"""Suite anti-fuga del feature store. Sin red.

Una fuga de datos no se ve: el modelo mejora, el backtest sonrie y el dinero
desaparece en produccion. Estas pruebas la convierten en algo observable.

La prueba central es la de **invariancia por truncamiento**: las features de los
primeros N partidos tienen que salir identicas se procese el dataset completo o
solo esos N. Si el futuro toca el pasado por cualquier via, esta prueba falla.

    python test_leakage.py
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
from typing import Dict, List

from canonical import Match, OddsCierre, match_id, slug_equipo
from features import FeatureStore, columnas_entrada, construir, ordenar_cronologico

fallos: List[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    estado = "PASS" if condicion else "FAIL"
    print(f"[{estado}] {nombre}" + (f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


def liga_sintetica(n_equipos: int = 8, jornadas: int = 12, semilla: int = 7) -> List[Match]:
    """Una liga inventada pero coherente: sirve para comprobar propiedades sin red."""
    rnd = random.Random(semilla)
    equipos = [f"equipo_{i:02d}" for i in range(n_equipos)]
    # Fuerza latente: hace que los resultados no sean ruido puro.
    fuerza = {e: rnd.uniform(0.8, 2.0) for e in equipos}
    partidos: List[Match] = []
    inicio = dt.datetime(2024, 8, 10, 15, 0, 0)

    for jornada in range(jornadas):
        rnd.shuffle(equipos)
        for i in range(0, n_equipos - 1, 2):
            local, visitante = equipos[i], equipos[i + 1]
            kickoff = (inicio + dt.timedelta(days=7 * jornada, hours=i)).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
            gl = min(6, int(rnd.gauss(fuerza[local] * 1.2, 1.0) + 0.5))
            gv = min(6, int(rnd.gauss(fuerza[visitante], 1.0) + 0.5))
            gl, gv = max(0, gl), max(0, gv)
            partidos.append(Match(
                match_id=match_id("TEST", kickoff, local, visitante),
                liga="TEST", temporada="2024-25", kickoff_utc=kickoff,
                equipo_local=slug_equipo(local), equipo_visitante=slug_equipo(visitante),
                nombre_local=local, nombre_visitante=visitante,
                goles_local=gl, goles_visitante=gv,
                resultado="H" if gl > gv else "A" if gl < gv else "D",
                tiros_puerta_local=max(0, int(rnd.gauss(5, 2))),
                tiros_puerta_visitante=max(0, int(rnd.gauss(4, 2))),
                fuente="sintetico",
            ))
    return partidos


# --------------------------------------------------------------------------
# La prueba que de verdad importa
# --------------------------------------------------------------------------

def test_invariancia_por_truncamiento() -> None:
    """Cortar el futuro no puede cambiar el pasado."""
    partidos = liga_sintetica()
    completo = construir(partidos)
    corte = len(completo) // 2
    parcial = construir(ordenar_cronologico(partidos)[:corte])

    check("mismo numero de filas al truncar", len(parcial) == corte, f"{len(parcial)} vs {corte}")
    diferencias = []
    for a, b in zip(completo[:corte], parcial):
        for clave in a:
            if clave.startswith("y_"):
                continue
            if a[clave] != b[clave]:
                diferencias.append((a["match_id"], clave, a[clave], b[clave]))
    check("features identicas con y sin partidos futuros", not diferencias,
          json.dumps(diferencias[:5], default=str))


def test_invariancia_de_orden() -> None:
    """El orden de las filas de entrada no puede alterar el resultado."""
    partidos = liga_sintetica()
    barajado = partidos[:]
    random.Random(99).shuffle(barajado)
    check("barajar la entrada no cambia las features",
          construir(partidos) == construir(barajado))


def test_partido_futuro_envenenado() -> None:
    """Un partido absurdo en 2030 no puede tocar las features de 2024."""
    partidos = liga_sintetica()
    base = construir(partidos)
    veneno = Match(
        match_id="veneno", liga="TEST", temporada="2029-30",
        kickoff_utc="2030-01-01T15:00:00Z",
        equipo_local=base[0]["equipo_local"], equipo_visitante=base[0]["equipo_visitante"],
        nombre_local="x", nombre_visitante="y",
        goles_local=99, goles_visitante=0, resultado="H", fuente="sintetico",
    )
    con_veneno = construir(partidos + [veneno])[:len(base)]
    check("un partido futuro extremo no altera el pasado", base == con_veneno)


# --------------------------------------------------------------------------
# Propiedades del estado
# --------------------------------------------------------------------------

def test_primer_partido_sin_historial() -> None:
    filas = construir(liga_sintetica())
    primera = filas[0]
    check("primer partido: 0 partidos previos",
          primera["local_partidos_previos"] == 0 and primera["visitante_partidos_previos"] == 0)
    check("primer partido: sin medias rodantes",
          primera["local_goles_favor_5"] is None and primera["local_forma_ponderada"] is None)
    check("primer partido: Elo inicial y diferencia nula", primera["elo_diff"] == 0.0)
    check("primer partido: marcado como no fiable", primera["fiable"] == 0)
    check("primer partido: sin descanso previo", primera["local_descanso_dias"] is None)


def test_elo_no_incluye_el_partido_actual() -> None:
    """El Elo de la fila i tiene que ser el que resulta de los i-1 anteriores."""
    partidos = ordenar_cronologico(liga_sintetica())
    filas = construir(partidos)
    store = FeatureStore()
    desajustes = []
    for i, partido in enumerate(partidos):
        # El estado paralelo solo ha visto los partidos 0..i-1.
        esperado = round(store._estado(partido.equipo_local).elo, 2)
        if filas[i]["local_elo"] != esperado:
            desajustes.append((i, filas[i]["local_elo"], esperado))
        store.actualizar(partido)
    check("Elo de la fila = Elo previo al partido", not desajustes, str(desajustes[:3]))


def test_media_de_liga_es_acumulada() -> None:
    """La media de goles de la liga no puede ser la de la temporada completa."""
    partidos = ordenar_cronologico(liga_sintetica())
    filas = construir(partidos)
    jugados = [p for p in partidos if p.jugado]
    media_final = sum(p.goles_local for p in jugados) / len(jugados)

    check("primera fila usa el valor por defecto, no la media final",
          filas[0]["liga_partidos_previos"] == 0)
    # A mitad de temporada la media acumulada debe coincidir con la de lo ya jugado.
    k = len(filas) // 2
    previos = jugados[:k]
    esperada = round(sum(p.goles_local for p in previos) / len(previos), 4)
    check("media de liga a mitad = media de lo ya jugado",
          abs(filas[k]["liga_media_goles_local"] - esperada) < 1e-6,
          f"{filas[k]['liga_media_goles_local']} vs {esperada}")
    check("la media acumulada difiere de la final (si no, no probaria nada)",
          abs(filas[k]["liga_media_goles_local"] - round(media_final, 4)) > 1e-9
          or len(previos) == len(jugados))


def test_features_crecen_con_el_historial() -> None:
    filas = construir(liga_sintetica())
    check("los partidos previos aumentan",
          filas[-1]["local_partidos_previos"] > filas[0]["local_partidos_previos"])
    fiables = [f for f in filas if f["fiable"] == 1]
    check("hay filas marcadas fiables al final de la temporada", len(fiables) > 0,
          str(len(fiables)))


def test_etiquetas_separadas_de_entradas() -> None:
    filas = construir(liga_sintetica())
    entradas = columnas_entrada(filas[0])
    check("ninguna etiqueta entre las columnas de entrada",
          not [c for c in entradas if c.startswith("y_")], str(entradas))
    check("ni identificadores ni claves entre las entradas",
          not [c for c in entradas if c in {"match_id", "kickoff_utc", "equipo_local"}])
    check("las etiquetas existen en la fila",
          {"y_resultado", "y_over25", "y_btts", "y_total_goles", "y_puntuacion_local"} <= set(filas[0]))
    check("y_over25 coherente con el marcador",
          all(f["y_over25"] == int((f["y_goles_local"] + f["y_goles_visitante"]) > 2.5)
              for f in filas if f["y_goles_local"] is not None))


def test_descanso_entre_partidos() -> None:
    """Dos partidos del mismo equipo separados 3 dias: congestion marcada."""
    kickoffs = ["2024-08-10T15:00:00Z", "2024-08-13T15:00:00Z"]
    partidos = []
    for i, ko in enumerate(kickoffs):
        partidos.append(Match(
            match_id=f"m{i}", liga="TEST", temporada="2024-25", kickoff_utc=ko,
            equipo_local="alfa", equipo_visitante=f"beta{i}",
            nombre_local="Alfa", nombre_visitante=f"Beta{i}",
            goles_local=1, goles_visitante=0, resultado="H", fuente="sintetico"))
    filas = construir(partidos)
    check("descanso calculado en dias", filas[1]["local_descanso_dias"] == 3.0,
          str(filas[1]["local_descanso_dias"]))
    check("congestion marcada por debajo de 4 dias", filas[1]["local_congestion"] == 1)
    check("sin congestion en el primer partido", filas[0]["local_congestion"] == 0)


# --------------------------------------------------------------------------
# Identidad de equipos y cuotas
# --------------------------------------------------------------------------

def test_slugs_de_equipo() -> None:
    check("alias resuelto", slug_equipo("Man United") == slug_equipo("Manchester United"),
          f"{slug_equipo('Man United')} vs {slug_equipo('Manchester United')}")
    check("tildes normalizadas", slug_equipo("Atlético Madrid") == slug_equipo("Atletico Madrid"))
    check("sufijo societario ignorado", slug_equipo("FC Koln") == slug_equipo("Koln"))
    check("equipos distintos no colisionan",
          slug_equipo("Manchester United") != slug_equipo("Manchester City"))
    check("cadena vacia no revienta", slug_equipo("") == "")


def test_match_id_determinista() -> None:
    a = match_id("E0", "2024-08-16T20:00:00Z", "Man United", "Fulham")
    b = match_id("E0", "2024-08-16T20:00:00Z", "Manchester United", "Fulham")
    c = match_id("E0", "2024-08-16T20:00:00Z", "Fulham", "Man United")
    check("match_id estable entre alias", a == b)
    check("match_id distingue local de visitante", a != c)


def test_validacion_de_partido() -> None:
    base = dict(liga="E0", temporada="2024-25", kickoff_utc="2024-08-16T20:00:00Z",
                nombre_local="A", nombre_visitante="B", fuente="t")
    ok, _ = Match(match_id="1", equipo_local="a", equipo_visitante="b",
                  goles_local=2, goles_visitante=1, resultado="H", **base).validar()
    check("partido coherente aceptado", ok)

    ok, motivo = Match(match_id="1", equipo_local="a", equipo_visitante="b",
                       goles_local=2, goles_visitante=1, resultado="A", **base).validar()
    check("resultado incoherente rechazado", not ok, motivo)

    ok, motivo = Match(match_id="1", equipo_local="a", equipo_visitante="a", **base).validar()
    check("equipo contra si mismo rechazado", not ok, motivo)

    malo = dict(base, kickoff_utc="16/08/2024")
    ok, motivo = Match(match_id="1", equipo_local="a", equipo_visitante="b", **malo).validar()
    check("kickoff no ISO rechazado", not ok, motivo)


def test_overround_y_probabilidades() -> None:
    cuota = OddsCierre(match_id="m", kickoff_utc="2024-08-16T20:00:00Z", casa="pinnacle",
                       mercado="1x2", cuota_1=2.0, cuota_x=3.5, cuota_2=4.0)
    over = cuota.calcular_overround()
    check("overround por encima de 1", over > 1.0, str(over))
    probs = cuota.probabilidades_implicitas()
    check("las probabilidades implicitas suman 1",
          abs(sum(probs.values()) - 1.0) < 1e-6, str(probs))
    check("el favorito tiene la probabilidad mas alta",
          max(probs, key=probs.get) == "1", str(probs))

    justa = OddsCierre(match_id="m", kickoff_utc="2024-08-16T20:00:00Z", casa="x",
                       mercado="1x2", cuota_1=3.0, cuota_x=3.0, cuota_2=3.0)
    check("mercado sin margen da overround 1", abs(justa.calcular_overround() - 1.0) < 1e-9)


if __name__ == "__main__":
    pruebas = [
        test_invariancia_por_truncamiento,
        test_invariancia_de_orden,
        test_partido_futuro_envenenado,
        test_primer_partido_sin_historial,
        test_elo_no_incluye_el_partido_actual,
        test_media_de_liga_es_acumulada,
        test_features_crecen_con_el_historial,
        test_etiquetas_separadas_de_entradas,
        test_descanso_entre_partidos,
        test_slugs_de_equipo,
        test_match_id_determinista,
        test_validacion_de_partido,
        test_overround_y_probabilidades,
    ]
    for prueba in pruebas:
        print("\n== " + prueba.__name__ + " ==")
        prueba()
    print("\n" + ("TODO OK" if not fallos else "FALLOS: " + json.dumps(fallos)))
    sys.exit(1 if fallos else 0)
