"""Feature store con rigor temporal.

La regla es una sola: **cada feature de un partido se calcula usando unicamente
partidos con kickoff anterior a ese partido.** No se hace cumplir con revisiones
manuales sino con la forma del codigo: se recorren los partidos en orden
cronologico y, para cada uno, primero se fotografia el estado (eso son las
features) y despues se actualiza el estado con el resultado. La actualizacion
nunca puede adelantar a la lectura porque ocurre una linea mas abajo.

Dos fugas sutiles que este diseno evita a proposito:

1. **Medias de liga de temporada completa.** "Fuerza de ataque relativa a la
   media de la liga" calculada con la media final de la temporada mete en la
   jornada 1 informacion de la jornada 38. Aqui la media de liga es acumulada.
2. **Normalizacion global.** Escalar una columna con su media y desviacion de
   todo el dataset filtra el futuro en el pasado. Este modulo no normaliza:
   deja los valores crudos y esa decision es del Sprint 4, dentro del fold.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence

from canonical import Match

ELO_INICIAL = 1500.0
ELO_K = 20.0
ELO_VENTAJA_LOCAL = 60.0
VENTANA_LARGA = 10
VENTANA_CORTA = 5
DECAIMIENTO_FORMA = 0.85     # peso del partido i-esimo hacia atras
MIN_PARTIDOS_FIABLE = 5      # por debajo, las features se marcan poco fiables


@dataclass
class EstadoEquipo:
    """Lo que se sabe de un equipo justo antes de un partido."""

    elo: float = ELO_INICIAL
    partidos: int = 0
    ultimo_kickoff: Optional[dt.datetime] = None
    # Colas de los ultimos encuentros (mas reciente al final).
    goles_favor: Deque[int] = field(default_factory=lambda: deque(maxlen=VENTANA_LARGA))
    goles_contra: Deque[int] = field(default_factory=lambda: deque(maxlen=VENTANA_LARGA))
    puntos: Deque[int] = field(default_factory=lambda: deque(maxlen=VENTANA_LARGA))
    tiros_puerta_favor: Deque[int] = field(default_factory=lambda: deque(maxlen=VENTANA_LARGA))
    tiros_puerta_contra: Deque[int] = field(default_factory=lambda: deque(maxlen=VENTANA_LARGA))
    xg_favor: Deque[float] = field(default_factory=lambda: deque(maxlen=VENTANA_LARGA))
    xg_contra: Deque[float] = field(default_factory=lambda: deque(maxlen=VENTANA_LARGA))
    # Acumulados de toda la historia conocida hasta ahora.
    goles_favor_total: int = 0
    goles_contra_total: int = 0
    partidos_local: int = 0
    puntos_local: int = 0
    partidos_visitante: int = 0
    puntos_visitante: int = 0


@dataclass
class EstadoLiga:
    """Medias acumuladas de la liga. Nunca de temporada completa."""

    goles_local_total: int = 0
    goles_visitante_total: int = 0
    partidos: int = 0

    @property
    def media_goles_local(self) -> float:
        return self.goles_local_total / self.partidos if self.partidos else 1.45

    @property
    def media_goles_visitante(self) -> float:
        return self.goles_visitante_total / self.partidos if self.partidos else 1.15


def _media(cola: Sequence[float], n: Optional[int] = None) -> Optional[float]:
    datos = list(cola)[-n:] if n else list(cola)
    return round(sum(datos) / len(datos), 4) if datos else None


def _forma_ponderada(puntos: Sequence[int], decaimiento: float = DECAIMIENTO_FORMA) -> Optional[float]:
    """Puntos recientes con peso exponencial: el ultimo partido pesa mas.

    Se normaliza por la suma de pesos para que el valor siga en escala 0-3 y no
    dependa de cuantos partidos haya en el historial.
    """
    datos = list(puntos)
    if not datos:
        return None
    total = peso_total = 0.0
    for i, p in enumerate(reversed(datos)):
        peso = decaimiento ** i
        total += p * peso
        peso_total += peso
    return round(total / peso_total, 4)


def _puntos(goles_favor: int, goles_contra: int) -> int:
    return 3 if goles_favor > goles_contra else 1 if goles_favor == goles_contra else 0


def _parse_kickoff(valor: str) -> dt.datetime:
    return dt.datetime.strptime(valor[:19], "%Y-%m-%dT%H:%M:%S")


class FeatureStore:
    """Construye features punto-en-el-tiempo a partir de partidos canonicos."""

    VERSION = "1.0.0"

    def __init__(self) -> None:
        self.equipos: Dict[str, EstadoEquipo] = {}
        self.ligas: Dict[str, EstadoLiga] = {}

    # -- lectura del estado (antes del pitido) --------------------------------

    def _estado(self, equipo: str) -> EstadoEquipo:
        return self.equipos.setdefault(equipo, EstadoEquipo())

    def _liga(self, liga: str) -> EstadoLiga:
        return self.ligas.setdefault(liga, EstadoLiga())

    def _lado(self, estado: EstadoEquipo, prefijo: str, kickoff: dt.datetime,
              liga: EstadoLiga, es_local: bool) -> Dict[str, Any]:
        """Features de un equipo. Solo lee `estado`; jamas lo modifica."""
        descanso = None
        if estado.ultimo_kickoff is not None:
            descanso = round((kickoff - estado.ultimo_kickoff).total_seconds() / 86400.0, 2)

        gf_medio = _media(estado.goles_favor, VENTANA_LARGA)
        gc_medio = _media(estado.goles_contra, VENTANA_LARGA)
        referencia_favor = liga.media_goles_local if es_local else liga.media_goles_visitante
        referencia_contra = liga.media_goles_visitante if es_local else liga.media_goles_local

        return {
            f"{prefijo}_elo": round(estado.elo, 2),
            f"{prefijo}_partidos_previos": estado.partidos,
            f"{prefijo}_forma_ponderada": _forma_ponderada(estado.puntos),
            f"{prefijo}_puntos_medios_5": _media(estado.puntos, VENTANA_CORTA),
            f"{prefijo}_goles_favor_5": _media(estado.goles_favor, VENTANA_CORTA),
            f"{prefijo}_goles_contra_5": _media(estado.goles_contra, VENTANA_CORTA),
            f"{prefijo}_goles_favor_10": gf_medio,
            f"{prefijo}_goles_contra_10": gc_medio,
            f"{prefijo}_tiros_puerta_favor_5": _media(estado.tiros_puerta_favor, VENTANA_CORTA),
            f"{prefijo}_tiros_puerta_contra_5": _media(estado.tiros_puerta_contra, VENTANA_CORTA),
            f"{prefijo}_xg_favor_5": _media(estado.xg_favor, VENTANA_CORTA),
            f"{prefijo}_xg_contra_5": _media(estado.xg_contra, VENTANA_CORTA),
            # Fuerza relativa a la media ACUMULADA de la liga, no a la final.
            f"{prefijo}_fuerza_ataque": (round(gf_medio / referencia_favor, 4)
                                         if gf_medio is not None and referencia_favor else None),
            f"{prefijo}_fuerza_defensa": (round(gc_medio / referencia_contra, 4)
                                          if gc_medio is not None and referencia_contra else None),
            f"{prefijo}_descanso_dias": descanso,
            f"{prefijo}_congestion": (1 if descanso is not None and descanso < 4 else 0),
            f"{prefijo}_puntos_por_partido_lado": (
                round(estado.puntos_local / estado.partidos_local, 4)
                if es_local and estado.partidos_local
                else round(estado.puntos_visitante / estado.partidos_visitante, 4)
                if not es_local and estado.partidos_visitante else None),
        }

    def features_de(self, partido: Match) -> Dict[str, Any]:
        """Fotografia del estado justo antes de este partido."""
        kickoff = _parse_kickoff(partido.kickoff_utc)
        local = self._estado(partido.equipo_local)
        visitante = self._estado(partido.equipo_visitante)
        liga = self._liga(partido.liga)

        fila: Dict[str, Any] = {
            "match_id": partido.match_id,
            "liga": partido.liga,
            "temporada": partido.temporada,
            "kickoff_utc": partido.kickoff_utc,
            "fecha": partido.kickoff_utc[:10],
            "equipo_local": partido.equipo_local,
            "equipo_visitante": partido.equipo_visitante,
            "feature_version": self.VERSION,
        }
        fila.update(self._lado(local, "local", kickoff, liga, es_local=True))
        fila.update(self._lado(visitante, "visitante", kickoff, liga, es_local=False))

        fila["elo_diff"] = round(local.elo - visitante.elo, 2)
        # OJO con el nombre: esto es la PUNTUACION ESPERADA del Elo (victoria=1,
        # empate=0.5, derrota=0), no la probabilidad de que gane el local. En un
        # deporte con un 25% de empates son cosas distintas: llamarla
        # "probabilidad de victoria" la deja sobrestimada unos 15 puntos en
        # todos los tramos. Convertirla en P(1)/P(X)/P(2) es tarea del Sprint 4.
        fila["elo_expectativa_local"] = round(
            1.0 / (1.0 + 10 ** (-(local.elo + ELO_VENTAJA_LOCAL - visitante.elo) / 400.0)), 6)
        fila["liga_media_goles_local"] = round(liga.media_goles_local, 4)
        fila["liga_media_goles_visitante"] = round(liga.media_goles_visitante, 4)
        fila["liga_partidos_previos"] = liga.partidos

        forma_local = fila.get("local_forma_ponderada")
        forma_visitante = fila.get("visitante_forma_ponderada")
        fila["forma_diff"] = (round(forma_local - forma_visitante, 4)
                              if forma_local is not None and forma_visitante is not None else None)
        descanso_local = fila.get("local_descanso_dias")
        descanso_visitante = fila.get("visitante_descanso_dias")
        fila["descanso_diff"] = (round(descanso_local - descanso_visitante, 2)
                                 if descanso_local is not None and descanso_visitante is not None
                                 else None)

        # Historial fino = feature poco fiable. Se marca en vez de fingir.
        fila["fiable"] = int(min(local.partidos, visitante.partidos) >= MIN_PARTIDOS_FIABLE)

        # Etiquetas: son el objetivo, no una feature. Van aparte para que no se
        # puedan colar en el conjunto de entrada por descuido.
        fila["y_resultado"] = partido.resultado
        fila["y_goles_local"] = partido.goles_local
        fila["y_goles_visitante"] = partido.goles_visitante
        fila["y_total_goles"] = (partido.goles_local + partido.goles_visitante
                                 if partido.jugado else None)
        fila["y_over25"] = (int((partido.goles_local + partido.goles_visitante) > 2.5)
                            if partido.jugado else None)
        fila["y_btts"] = (int(partido.goles_local > 0 and partido.goles_visitante > 0)
                          if partido.jugado else None)
        # Puntuacion real del local en la escala del Elo: es la etiqueta contra
        # la que `elo_expectativa_local` se puede comparar de forma honesta.
        fila["y_puntuacion_local"] = (
            (1.0 if partido.goles_local > partido.goles_visitante
             else 0.5 if partido.goles_local == partido.goles_visitante else 0.0)
            if partido.jugado else None)
        return fila

    # -- actualizacion del estado (despues del pitido final) ------------------

    def actualizar(self, partido: Match) -> None:
        """Incorpora el resultado. Se llama SIEMPRE despues de `features_de`."""
        if not partido.jugado:
            return
        kickoff = _parse_kickoff(partido.kickoff_utc)
        local = self._estado(partido.equipo_local)
        visitante = self._estado(partido.equipo_visitante)
        gl, gv = partido.goles_local, partido.goles_visitante

        esperado_local = 1.0 / (1.0 + 10 ** (
            -(local.elo + ELO_VENTAJA_LOCAL - visitante.elo) / 400.0))
        real_local = 1.0 if gl > gv else 0.5 if gl == gv else 0.0
        # Multiplicador por diferencia de goles: un 4-0 mueve mas que un 1-0.
        margen = 1.0 + math.log1p(abs(gl - gv))
        ajuste = ELO_K * margen * (real_local - esperado_local)
        local.elo += ajuste
        visitante.elo -= ajuste

        for estado, favor, contra, tp_favor, tp_contra, xg_favor, xg_contra, es_local in (
            (local, gl, gv, partido.tiros_puerta_local, partido.tiros_puerta_visitante,
             partido.xg_local, partido.xg_visitante, True),
            (visitante, gv, gl, partido.tiros_puerta_visitante, partido.tiros_puerta_local,
             partido.xg_visitante, partido.xg_local, False),
        ):
            estado.goles_favor.append(favor)
            estado.goles_contra.append(contra)
            puntos = _puntos(favor, contra)
            estado.puntos.append(puntos)
            if tp_favor is not None:
                estado.tiros_puerta_favor.append(tp_favor)
            if tp_contra is not None:
                estado.tiros_puerta_contra.append(tp_contra)
            # Sin xG del proveedor, se usa un proxy por tiros a puerta. Queda
            # etiquetado como proxy: cuando llegue la API de pago, se sustituye.
            if xg_favor is not None:
                estado.xg_favor.append(float(xg_favor))
            elif tp_favor is not None:
                estado.xg_favor.append(round(tp_favor * 0.32, 4))
            if xg_contra is not None:
                estado.xg_contra.append(float(xg_contra))
            elif tp_contra is not None:
                estado.xg_contra.append(round(tp_contra * 0.32, 4))

            estado.goles_favor_total += favor
            estado.goles_contra_total += contra
            estado.partidos += 1
            estado.ultimo_kickoff = kickoff
            if es_local:
                estado.partidos_local += 1
                estado.puntos_local += puntos
            else:
                estado.partidos_visitante += 1
                estado.puntos_visitante += puntos

        liga = self._liga(partido.liga)
        liga.goles_local_total += gl
        liga.goles_visitante_total += gv
        liga.partidos += 1


def ordenar_cronologico(partidos: Sequence[Match]) -> List[Match]:
    """Orden estable por kickoff. El desempate por match_id hace la
    construccion reproducible: dos ejecuciones dan exactamente las mismas filas."""
    return sorted(partidos, key=lambda m: (m.kickoff_utc, m.match_id))


def construir(partidos: Sequence[Match]) -> List[Dict[str, Any]]:
    """Recorre los partidos en orden y devuelve una fila de features por partido.

    Este bucle es toda la garantia anti-fuga del sistema: leer, luego escribir.
    """
    store = FeatureStore()
    filas: List[Dict[str, Any]] = []
    for partido in ordenar_cronologico(partidos):
        filas.append(store.features_de(partido))   # lee el pasado
        store.actualizar(partido)                  # y solo despues, aprende
    return filas


COLUMNAS_FEATURE = None  # se calcula al vuelo; las etiquetas empiezan por "y_"


def columnas_entrada(fila: Dict[str, Any]) -> List[str]:
    """Columnas utilizables como entrada del modelo: ni etiquetas ni claves."""
    excluir = {"match_id", "liga", "temporada", "kickoff_utc", "fecha",
               "equipo_local", "equipo_visitante", "feature_version"}
    return [k for k in fila if not k.startswith("y_") and k not in excluir]
