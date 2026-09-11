"""Todos los mercados de un partido, derivados de una sola matriz de marcadores.

Dixon-Coles no predice "quien gana": calcula P(marcador) para cada resultado
posible. De ahi sale absolutamente todo lo demas —resultado correcto, totales de
cualquier linea, handicaps asiaticos, ambos marcan, totales por equipo— sin
modelar nada nuevo. El Sprint 4 leia tres numeros de esa matriz (P1/PX/P2) y
tiraba el resto, que es el 95% de lo que Betplay oferta.

Ventaja que no es solo comodidad: como todo sale de la misma distribucion, los
mercados **no pueden contradecirse**. Un clasificador por mercado puede afirmar
a la vez que hay 60% de over 2.5 y 70% de que el partido acabe 1-0.

**Lineas asiaticas.** Son la parte con trampa. Una apuesta asiatica no tiene dos
resultados sino tres: ganar, perder y *empatar* (devolucion del importe). Y las
lineas de cuarto (-0.25, +0.75) parten la apuesta en dos mitades. Tratarlas como
un binario normal da probabilidades mal calculadas y un EV inventado; aqui se
devuelven las tres piezas por separado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass
class Resultado:
    """Una linea apostable ya valorada.

    `prob_gana` + `prob_empate` + `prob_pierde` suma 1. Para comparar con una
    cuota decimal hay que usar `prob_efectiva`, que reparte la devolucion.
    """

    mercado: str
    seleccion: str
    prob_gana: float
    prob_pierde: float
    prob_empate: float = 0.0          # devolucion del importe (solo asiaticas)
    linea: Optional[float] = None

    @property
    def prob_efectiva(self) -> float:
        """Probabilidad con la que comparar una cuota decimal.

        Con devolucion parcial, el dinero en riesgo es menor: la cuota justa es
        la que equilibra ganancia y perdida *sobre lo que realmente se arriesga*.
        """
        en_juego = self.prob_gana + self.prob_pierde
        return self.prob_gana / en_juego if en_juego > 0 else 0.0

    @property
    def cuota_justa(self) -> float:
        p = self.prob_efectiva
        return 1.0 / p if p > 0 else float("inf")

    def ev(self, cuota: float) -> float:
        """Valor esperado por unidad apostada, a la cuota que ofrece la casa."""
        return self.prob_gana * (cuota - 1.0) - self.prob_pierde

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mercado": self.mercado, "seleccion": self.seleccion, "linea": self.linea,
            "prob_gana": round(self.prob_gana, 6),
            "prob_empate": round(self.prob_empate, 6),
            "prob_pierde": round(self.prob_pierde, 6),
            "prob_efectiva": round(self.prob_efectiva, 6),
            "cuota_justa": round(self.cuota_justa, 4),
        }


def _partes_asiaticas(linea: float) -> List[float]:
    """Una linea de cuarto es media apuesta en cada linea adyacente.

    -0.25 = mitad en 0 y mitad en -0.5. Tratarla como -0.25 "a secas" no
    significa nada: no existe medio gol.
    """
    resto = abs(linea * 4) % 2
    if abs(resto - 1.0) < 1e-9:          # cuarto de linea
        return [linea - 0.25, linea + 0.25]
    return [linea]


class MercadosDerivados:
    """Valora cualquier mercado a partir de la matriz `P[goles_local, goles_visitante]`."""

    def __init__(self, matriz: np.ndarray):
        total = float(matriz.sum())
        self.m = matriz / total if total > 0 else matriz
        n = self.m.shape[0]
        self._gl = np.arange(n)[:, None] * np.ones((1, self.m.shape[1]))
        self._gv = np.ones((n, 1)) * np.arange(self.m.shape[1])[None, :]
        self._dif = self._gl - self._gv          # diferencia de goles
        self._tot = self._gl + self._gv          # goles totales

    # -- 1X2 y derivados ------------------------------------------------------

    def resultado_final(self) -> List[Resultado]:
        p1 = float(self.m[self._dif > 0].sum())
        px = float(self.m[self._dif == 0].sum())
        p2 = float(self.m[self._dif < 0].sum())
        return [Resultado("Resultado Final", "1", p1, 1 - p1),
                Resultado("Resultado Final", "X", px, 1 - px),
                Resultado("Resultado Final", "2", p2, 1 - p2)]

    def doble_oportunidad(self) -> List[Resultado]:
        p1 = float(self.m[self._dif > 0].sum())
        px = float(self.m[self._dif == 0].sum())
        p2 = float(self.m[self._dif < 0].sum())
        return [Resultado("Doble Oportunidad", "1X", p1 + px, p2),
                Resultado("Doble Oportunidad", "12", p1 + p2, px),
                Resultado("Doble Oportunidad", "X2", px + p2, p1)]

    def apuesta_sin_empate(self) -> List[Resultado]:
        """Empate = devolucion. Por eso `prob_empate` no es cero aqui."""
        p1 = float(self.m[self._dif > 0].sum())
        px = float(self.m[self._dif == 0].sum())
        p2 = float(self.m[self._dif < 0].sum())
        return [Resultado("Apuesta sin empate", "1", p1, p2, px),
                Resultado("Apuesta sin empate", "2", p2, p1, px)]

    # -- totales --------------------------------------------------------------

    def total_goles(self, linea: float) -> List[Resultado]:
        """Over/Under de linea entera o media. Con linea entera hay devolucion."""
        over = float(self.m[self._tot > linea].sum())
        under = float(self.m[self._tot < linea].sum())
        push = float(self.m[self._tot == linea].sum())
        return [Resultado("Total de goles", "Mas de", over, under, push, linea),
                Resultado("Total de goles", "Menos de", under, over, push, linea)]

    def total_asiatico(self, linea: float) -> List[Resultado]:
        """Totales con lineas de cuarto, promediando las dos mitades."""
        partes = _partes_asiaticas(linea)
        peso = 1.0 / len(partes)
        acum = {"Mas de": [0.0, 0.0, 0.0], "Menos de": [0.0, 0.0, 0.0]}
        for parte in partes:
            for r in self.total_goles(parte):
                a = acum[r.seleccion]
                a[0] += r.prob_gana * peso
                a[1] += r.prob_pierde * peso
                a[2] += r.prob_empate * peso
        return [Resultado("Total asiatico", sel, v[0], v[1], v[2], linea)
                for sel, v in acum.items()]

    def total_por_equipo(self, lado: str, linea: float) -> List[Resultado]:
        goles = self._gl if lado == "local" else self._gv
        over = float(self.m[goles > linea].sum())
        under = float(self.m[goles < linea].sum())
        push = float(self.m[goles == linea].sum())
        etiqueta = f"Total de goles ({lado})"
        return [Resultado(etiqueta, "Mas de", over, under, push, linea),
                Resultado(etiqueta, "Menos de", under, over, push, linea)]

    # -- handicaps ------------------------------------------------------------

    def handicap_3way(self, linea: float) -> List[Resultado]:
        """Handicap europeo: el empate con handicap es un resultado, no devolucion."""
        ajustada = self._dif + linea
        p1 = float(self.m[ajustada > 0].sum())
        px = float(self.m[ajustada == 0].sum())
        p2 = float(self.m[ajustada < 0].sum())
        return [Resultado("Handicap 3-Way", "1", p1, 1 - p1, 0.0, linea),
                Resultado("Handicap 3-Way", "X", px, 1 - px, 0.0, linea),
                Resultado("Handicap 3-Way", "2", p2, 1 - p2, 0.0, linea)]

    def handicap_asiatico(self, linea: float) -> List[Resultado]:
        """Handicap asiatico con soporte de lineas de cuarto y devolucion."""
        partes = _partes_asiaticas(linea)
        peso = 1.0 / len(partes)
        local = [0.0, 0.0, 0.0]
        visitante = [0.0, 0.0, 0.0]
        for parte in partes:
            ajustada = self._dif + parte
            gana_l = float(self.m[ajustada > 0].sum())
            empata = float(self.m[ajustada == 0].sum())
            pierde_l = float(self.m[ajustada < 0].sum())
            local[0] += gana_l * peso
            local[1] += pierde_l * peso
            local[2] += empata * peso
            visitante[0] += pierde_l * peso
            visitante[1] += gana_l * peso
            visitante[2] += empata * peso
        return [Resultado("Handicap asiatico", "1", *local, linea),
                Resultado("Handicap asiatico", "2", *visitante, linea)]

    # -- marcadores -----------------------------------------------------------

    def ambos_marcan(self) -> List[Resultado]:
        si = float(self.m[1:, 1:].sum())
        return [Resultado("Ambos Equipos Marcaran", "Si", si, 1 - si),
                Resultado("Ambos Equipos Marcaran", "No", 1 - si, si)]

    def resultado_correcto(self, max_goles: int = 5,
                           minimo: float = 0.002) -> List[Resultado]:
        """Marcador exacto. Se omiten los marcadores practicamente imposibles:
        una probabilidad de 0.0001 no sostiene ninguna apuesta y solo mete ruido."""
        salida = []
        for gl in range(min(max_goles + 1, self.m.shape[0])):
            for gv in range(min(max_goles + 1, self.m.shape[1])):
                p = float(self.m[gl, gv])
                if p >= minimo:
                    salida.append(Resultado("Resultado Correcto", f"{gl}-{gv}", p, 1 - p))
        return sorted(salida, key=lambda r: -r.prob_gana)

    def equipo_marca(self, lado: str, minimo: int = 1) -> List[Resultado]:
        goles = self._gl if lado == "local" else self._gv
        p = float(self.m[goles >= minimo].sum())
        return [Resultado(f"Marca al menos {minimo} ({lado})", "Si", p, 1 - p)]

    # -- todo junto -----------------------------------------------------------

    def todos(self, lineas_totales=(0.5, 1.5, 2.5, 3.5, 4.5),
              lineas_handicap=(-2.5, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.5)
              ) -> List[Resultado]:
        """El catalogo completo, para cruzar contra lo que Betplay oferta."""
        salida: List[Resultado] = []
        salida += self.resultado_final()
        salida += self.doble_oportunidad()
        salida += self.apuesta_sin_empate()
        salida += self.ambos_marcan()
        salida += self.resultado_correcto()
        for linea in lineas_totales:
            salida += self.total_goles(linea)
            salida += self.total_por_equipo("local", linea)
            salida += self.total_por_equipo("visitante", linea)
        for linea in lineas_handicap:
            salida += self.handicap_asiatico(linea)
            if linea == int(linea):
                salida += self.handicap_3way(linea)
        for minimo in (1, 2, 3):
            salida += self.equipo_marca("local", minimo)
            salida += self.equipo_marca("visitante", minimo)
        return salida


def matriz_de_poisson(media: float, maximo: int = 15) -> np.ndarray:
    """Distribucion de un conteo simple (corners, tarjetas), no de un marcador."""
    from scipy.stats import poisson
    p = poisson.pmf(np.arange(maximo + 1), media)
    return p / p.sum()


class MercadoDeConteo:
    """Totales sobre un conteo unico: corners del partido, tarjetas, etc.

    Los corners no son goles: su media es ~10 y la Poisson simple los describe
    razonablemente, pero **estan correlacionados con el dominio del partido**.
    Modelarlos aparte y despues combinarlos con el marcador como si fueran
    independientes subestima la correlacion; queda anotado como deuda.
    """

    def __init__(self, media: float, nombre: str = "Total de Tiros de Esquina"):
        self.media = media
        self.nombre = nombre
        self.p = matriz_de_poisson(media, maximo=30)

    def total(self, linea: float) -> List[Resultado]:
        valores = np.arange(len(self.p))
        over = float(self.p[valores > linea].sum())
        under = float(self.p[valores < linea].sum())
        push = float(self.p[valores == linea].sum())
        return [Resultado(self.nombre, "Mas de", over, under, push, linea),
                Resultado(self.nombre, "Menos de", under, over, push, linea)]
