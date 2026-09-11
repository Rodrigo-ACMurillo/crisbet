"""Modelo Dixon-Coles: Poisson bivariante con correccion de marcadores bajos.

La idea original (Maher, 1982) es que los goles de cada equipo siguen una
Poisson con media `ataque_local * defensa_visitante * ventaja_local`. Funciona,
pero falla justo donde mas duele: los marcadores 0-0, 1-0, 0-1 y 1-1 ocurren
mas a menudo de lo que predice la independencia. Dixon y Coles (1997) anaden un
factor `tau` que corrige esas cuatro celdas, y un decaimiento temporal para que
un partido de hace tres anos pese menos que el del mes pasado.

Por que este modelo y no solo un GBM: aqui las probabilidades salen de una
distribucion de marcadores completa, asi que P(over 2.5), P(BTTS) y cualquier
handicap se derivan de la misma matriz y son coherentes entre si. Un
clasificador por mercado puede decir a la vez que hay 60% de over 2.5 y 70% de
que el partido acabe 1-0.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import minimize
from scipy.stats import poisson

MAX_GOLES = 10          # la cola por encima de 10 goles es despreciable
SEMIVIDA_DIAS = 365.0   # a un ano de distancia, un partido pesa la mitad


def tau(gl: int, gv: int, lam: float, mu: float, rho: float) -> float:
    """Correccion de Dixon-Coles para los cuatro marcadores bajos.

    Fuera de esas celdas vale 1 y el modelo es Poisson independiente puro.
    """
    if gl == 0 and gv == 0:
        return 1.0 - lam * mu * rho
    if gl == 0 and gv == 1:
        return 1.0 + lam * rho
    if gl == 1 and gv == 0:
        return 1.0 + mu * rho
    if gl == 1 and gv == 1:
        return 1.0 - rho
    return 1.0


def peso_temporal(dias_atras: float, semivida: float = SEMIVIDA_DIAS) -> float:
    """Decaimiento exponencial. Un equipo de hace dos anos ya no es el mismo."""
    return 0.5 ** (dias_atras / semivida)


@dataclass
class ParametrosDC:
    equipos: List[str]
    ataque: Dict[str, float]
    defensa: Dict[str, float]
    ventaja_local: float
    rho: float
    n_partidos: int = 0
    convergio: bool = False
    ataque_medio: float = 1.0
    defensa_media: float = 1.0

    def medias(self, local: str, visitante: str) -> Tuple[float, float]:
        """Goles esperados de cada equipo. Un equipo desconocido usa la media
        de la liga: es menos informativo, pero no rompe ni inventa fuerza."""
        a_local = self.ataque.get(local, self.ataque_medio)
        d_local = self.defensa.get(local, self.defensa_media)
        a_visitante = self.ataque.get(visitante, self.ataque_medio)
        d_visitante = self.defensa.get(visitante, self.defensa_media)
        lam = math.exp(a_local + d_visitante + self.ventaja_local)
        mu = math.exp(a_visitante + d_local)
        return min(lam, 8.0), min(mu, 8.0)


class DixonColes:
    """Ajuste por maxima verosimilitud con ponderacion temporal."""

    def __init__(self, semivida_dias: float = SEMIVIDA_DIAS, max_iter: int = 200):
        self.semivida = semivida_dias
        self.max_iter = max_iter
        self.params: Optional[ParametrosDC] = None

    # -- ajuste ---------------------------------------------------------------

    def fit(self, partidos: Sequence[dict], fecha_referencia: Optional[str] = None) -> "DixonColes":
        """`partidos`: dicts con equipo_local, equipo_visitante, y_goles_local,
        y_goles_visitante, kickoff_utc. `fecha_referencia` es el instante desde
        el que se mide la antiguedad: SIEMPRE el inicio del periodo a predecir,
        nunca "hoy", o el peso de un partido dependeria de cuando se ejecuta."""
        datos = [p for p in partidos
                 if p.get("y_goles_local") is not None and p.get("y_goles_visitante") is not None]
        if len(datos) < 50:
            raise ValueError(f"Muy pocos partidos para ajustar Dixon-Coles: {len(datos)}")

        equipos = sorted({p["equipo_local"] for p in datos} | {p["equipo_visitante"] for p in datos})
        indice = {e: i for i, e in enumerate(equipos)}
        n = len(equipos)

        ref = dt.datetime.strptime(
            (fecha_referencia or max(p["kickoff_utc"] for p in datos))[:19], "%Y-%m-%dT%H:%M:%S")
        pesos = np.array([
            peso_temporal((ref - dt.datetime.strptime(p["kickoff_utc"][:19],
                                                      "%Y-%m-%dT%H:%M:%S")).days, self.semivida)
            for p in datos
        ])
        idx_local = np.array([indice[p["equipo_local"]] for p in datos])
        idx_visitante = np.array([indice[p["equipo_visitante"]] for p in datos])
        goles_local = np.array([p["y_goles_local"] for p in datos], dtype=int)
        goles_visitante = np.array([p["y_goles_visitante"] for p in datos], dtype=int)

        # Parametros: n ataques, n defensas, ventaja de local, rho.
        # El ataque medio se fija a 0 para quitar la indeterminacion (se puede
        # sumar una constante a todos los ataques y restarla de las defensas sin
        # cambiar nada); sin esa restriccion el optimizador deriva sin converger.
        x0 = np.concatenate([np.zeros(n), np.zeros(n), [0.25], [-0.05]])

        def neg_log_verosimilitud(x: np.ndarray) -> float:
            ataque = x[:n] - x[:n].mean()      # restriccion aplicada aqui
            defensa = x[n:2 * n]
            ventaja, rho = x[2 * n], x[2 * n + 1]
            lam = np.exp(ataque[idx_local] + defensa[idx_visitante] + ventaja)
            mu = np.exp(ataque[idx_visitante] + defensa[idx_local])
            lam = np.clip(lam, 1e-6, 8.0)
            mu = np.clip(mu, 1e-6, 8.0)

            log_p = (poisson.logpmf(goles_local, lam) + poisson.logpmf(goles_visitante, mu))

            # Correccion tau solo en las cuatro celdas bajas, vectorizada.
            ajuste = np.ones_like(lam)
            m00 = (goles_local == 0) & (goles_visitante == 0)
            m01 = (goles_local == 0) & (goles_visitante == 1)
            m10 = (goles_local == 1) & (goles_visitante == 0)
            m11 = (goles_local == 1) & (goles_visitante == 1)
            ajuste[m00] = 1.0 - lam[m00] * mu[m00] * rho
            ajuste[m01] = 1.0 + lam[m01] * rho
            ajuste[m10] = 1.0 + mu[m10] * rho
            ajuste[m11] = 1.0 - rho
            ajuste = np.clip(ajuste, 1e-9, None)

            return -float(np.sum(pesos * (log_p + np.log(ajuste))))

        limites = ([(-3.0, 3.0)] * n + [(-3.0, 3.0)] * n +
                   [(-0.5, 1.0)] + [(-0.2, 0.2)])
        resultado = minimize(neg_log_verosimilitud, x0, method="L-BFGS-B",
                             bounds=limites, options={"maxiter": self.max_iter})

        x = resultado.x
        ataque = x[:n] - x[:n].mean()
        defensa = x[n:2 * n]
        self.params = ParametrosDC(
            equipos=equipos,
            ataque={e: float(ataque[indice[e]]) for e in equipos},
            defensa={e: float(defensa[indice[e]]) for e in equipos},
            ventaja_local=float(x[2 * n]),
            rho=float(x[2 * n + 1]),
            n_partidos=len(datos),
            convergio=bool(resultado.success),
            ataque_medio=0.0,
            defensa_media=float(defensa.mean()),
        )
        return self

    # -- prediccion -----------------------------------------------------------

    def matriz_marcadores(self, local: str, visitante: str) -> np.ndarray:
        """P(marcador) para todos los marcadores hasta MAX_GOLES-1 goles.

        De aqui sale todo lo demas: 1X2, over/under, BTTS, hándicaps. Ser una
        sola distribucion es lo que hace que los mercados no se contradigan.
        """
        if self.params is None:
            raise RuntimeError("El modelo no esta ajustado")
        lam, mu = self.params.medias(local, visitante)
        p_local = poisson.pmf(np.arange(MAX_GOLES), lam)
        p_visitante = poisson.pmf(np.arange(MAX_GOLES), mu)
        matriz = np.outer(p_local, p_visitante)

        rho = self.params.rho
        matriz[0, 0] *= 1.0 - lam * mu * rho
        matriz[0, 1] *= 1.0 + lam * rho
        matriz[1, 0] *= 1.0 + mu * rho
        matriz[1, 1] *= 1.0 - rho
        matriz = np.clip(matriz, 0.0, None)
        total = matriz.sum()
        return matriz / total if total > 0 else matriz

    def predecir(self, local: str, visitante: str) -> Dict[str, float]:
        """Todos los mercados derivados de una unica matriz de marcadores."""
        matriz = self.matriz_marcadores(local, visitante)
        p_local = float(np.tril(matriz, -1).sum())     # goles_local > goles_visitante
        p_empate = float(np.trace(matriz))
        p_visitante = float(np.triu(matriz, 1).sum())

        indices = np.arange(MAX_GOLES)
        total_goles = indices[:, None] + indices[None, :]
        p_over25 = float(matriz[total_goles > 2].sum())
        p_btts = float(matriz[1:, 1:].sum())

        lam, mu = self.params.medias(local, visitante)
        return {
            "p_1": p_local, "p_x": p_empate, "p_2": p_visitante,
            "p_over25": p_over25, "p_under25": 1.0 - p_over25,
            "p_btts": p_btts,
            "goles_esperados_local": lam, "goles_esperados_visitante": mu,
        }

    def predecir_muchos(self, partidos: Sequence[dict]) -> np.ndarray:
        """Matriz (n, 3) con P(1), P(X), P(2) en ese orden."""
        salida = np.zeros((len(partidos), 3))
        for i, p in enumerate(partidos):
            pred = self.predecir(p["equipo_local"], p["equipo_visitante"])
            salida[i] = [pred["p_1"], pred["p_x"], pred["p_2"]]
        return salida


def partidos_de_primera_parte(partidos: Sequence[dict]) -> List[dict]:
    """Reetiqueta los partidos para ajustar un Dixon-Coles del descanso.

    Los mercados de primera parte son el 20% de lo que oferta Betplay y no
    necesitan modelo nuevo: son el mismo Dixon-Coles entrenado con los goles al
    descanso, que el almacen ya guarda. La unica diferencia real es que se marca
    menos —alrededor de 1.2 goles por partido frente a 2.7—, y el ajuste lo
    recoge solo.
    """
    salida: List[dict] = []
    for p in partidos:
        gl = p.get("y_goles_local_ht", p.get("goles_local_ht"))
        gv = p.get("y_goles_visitante_ht", p.get("goles_visitante_ht"))
        # Un parquet devuelve NaN, no None, cuando falta el marcador al descanso.
        if gl is None or gv is None or gl != gl or gv != gv:
            continue
        salida.append(dict(p, y_goles_local=int(gl), y_goles_visitante=int(gv)))
    return salida
