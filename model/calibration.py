"""Calibracion isotonica multiclase y ensamblado.

Un modelo puede ordenar bien los partidos y aun asi equivocarse en la magnitud:
decir 0.75 donde la frecuencia real es 0.69. Ordenar bien sirve para elegir; el
valor exacto es lo que decide si una cuota tiene value. La regresion isotonica
corrige la magnitud sin tocar el orden, que es justo lo que hace falta.

**Donde se ajusta importa mas que como.** Calibrar sobre el mismo conjunto que
se evalua produce una curva perfecta y una mentira. Aqui el calibrador se ajusta
sobre un tramo de validacion recortado del final del entrenamiento —anterior en
el tiempo al periodo de prueba— y se aplica sin volver a mirarlo.
"""
from __future__ import annotations

from typing import Dict, List, Optional

import numpy as np
from sklearn.isotonic import IsotonicRegression

EPS = 1e-9


def _brier(probs: np.ndarray, y: np.ndarray) -> float:
    """Brier medio por clase. Duplicado a proposito de metrics.py: calibration
    no debe depender del modulo de metricas para tomar su propia decision."""
    reales = np.zeros_like(probs)
    reales[np.arange(len(y)), y] = 1.0
    return float(np.mean(np.sum((probs - reales) ** 2, axis=1)) / probs.shape[1])


class CalibradorMulticlase:
    """Una isotonica por clase, y renormalizacion al final.

    Calibrar cada clase por separado rompe la suma a 1, asi que despues se
    renormaliza. Es el metodo estandar (Zadrozny-Elkan) y su defecto conocido es
    que la renormalizacion reintroduce algo de descalibracion; a cambio no
    necesita asumir ninguna forma parametrica.
    """

    def __init__(self, n_clases: int = 3):
        self.n_clases = n_clases
        self.calibradores: List[Optional[IsotonicRegression]] = [None] * n_clases
        self.ajustado = False
        self.motivo = "sin ajustar"

    def fit(self, probs: np.ndarray, y: np.ndarray) -> "CalibradorMulticlase":
        if len(y) < 100:
            # Con pocos datos la isotonica sobreajusta escalones. Mejor no calibrar
            # que calibrar mal: se queda en identidad y se declara.
            self.ajustado = False
            self.motivo = "menos de 100 filas"
            return self
        for clase in range(self.n_clases):
            iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
            iso.fit(probs[:, clase], (y == clase).astype(float))
            self.calibradores[clase] = iso
        self.ajustado = True
        return self

    def fit_con_control(self, probs: np.ndarray, y: np.ndarray) -> "CalibradorMulticlase":
        """Ajusta, y solo se queda calibrando si demuestra que mejora.

        Un modelo ya bien calibrado no gana nada con una isotonica: gana ruido.
        En la primera medicion de este sprint la calibracion empeoraba el Brier
        (0.1972 frente a 0.1932) y casi triplicaba el ECE. El problema no era la
        isotonica sino aplicarla a ciegas.

        El control tiene que ser limpio: se ajusta con la primera mitad de la
        validacion y se decide con la segunda. Decidir sobre las mismas filas
        con las que se ajusto siempre diria que si.
        """
        if len(y) < 200:
            self.ajustado = False
            self.motivo = "pocos datos para ajustar y controlar"
            return self

        mitad = len(y) // 2
        tanteo = CalibradorMulticlase(self.n_clases).fit(probs[:mitad], y[:mitad])
        if not tanteo.ajustado:
            self.ajustado = False
            self.motivo = "el ajuste de control no salio"
            return self

        control_probs, control_y = probs[mitad:], y[mitad:]
        antes = _brier(control_probs, control_y)
        despues = _brier(tanteo.transform(control_probs), control_y)
        if despues >= antes:
            self.ajustado = False
            self.motivo = f"no mejora en control ({despues:.5f} >= {antes:.5f})"
            return self

        # Demostrado que ayuda: ahora si, se reajusta con toda la validacion.
        self.fit(probs, y)
        self.motivo = f"mejora en control ({despues:.5f} < {antes:.5f})"
        return self

    def transform(self, probs: np.ndarray) -> np.ndarray:
        if not self.ajustado:
            return probs
        salida = np.zeros_like(probs)
        for clase in range(self.n_clases):
            salida[:, clase] = self.calibradores[clase].predict(probs[:, clase])
        salida = np.clip(salida, EPS, None)
        return salida / salida.sum(axis=1, keepdims=True)


def ensamblar(predicciones: Dict[str, np.ndarray], pesos: Dict[str, float]) -> np.ndarray:
    """Media ponderada de probabilidades, renormalizada.

    Se promedian probabilidades y no log-odds a proposito: es mas conservador
    ante un modelo que se equivoca con seguridad, que es el fallo caro aqui.
    """
    total_peso = sum(pesos.get(k, 0.0) for k in predicciones)
    if total_peso <= 0:
        raise ValueError("Los pesos del ensamble suman cero")
    acumulado = None
    for nombre, probs in predicciones.items():
        peso = pesos.get(nombre, 0.0) / total_peso
        acumulado = probs * peso if acumulado is None else acumulado + probs * peso
    acumulado = np.clip(acumulado, EPS, None)
    return acumulado / acumulado.sum(axis=1, keepdims=True)


def pesos_por_verosimilitud(predicciones: Dict[str, np.ndarray], y: np.ndarray) -> Dict[str, float]:
    """Peso de cada modelo segun su log-loss en validacion. Peor log-loss, menos peso.

    Se ajusta en el tramo de validacion, nunca en el de prueba.
    """
    perdidas = {}
    for nombre, probs in predicciones.items():
        p = np.clip(probs[np.arange(len(y)), y], EPS, 1.0)
        perdidas[nombre] = float(-np.mean(np.log(p)))
    mejor = min(perdidas.values())
    # exp(-(perdida - mejor)) da 1 al mejor modelo y decae para los demas.
    crudos = {k: float(np.exp(-(v - mejor))) for k, v in perdidas.items()}
    total = sum(crudos.values())
    return {k: round(v / total, 4) for k, v in crudos.items()}
