"""Modelo discriminativo: LightGBM multiclase sobre el feature store.

Complementa a Dixon-Coles, no lo sustituye. Dixon-Coles solo sabe de identidad
de equipo y goles; el GBM ve las 42 features del Sprint 3 —forma, descanso,
congestion, fuerza relativa, Elo— y puede capturar interacciones que el modelo
de Poisson no representa.

Su punto debil es el contrario: aprende cualquier regularidad, incluidas las
espurias. Por eso el ensamble lleva los dos y por eso la validacion es
walk-forward.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

PARAMS_BASE = {
    "objective": "multiclass",
    "num_class": 3,
    "metric": "multi_logloss",
    "learning_rate": 0.03,
    # Arboles cortos y hojas pocas: con ~30k filas y una senal debil, un arbol
    # profundo memoriza equipos concretos en lugar de aprender la estructura.
    "num_leaves": 15,
    "max_depth": 4,
    "min_data_in_leaf": 120,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "verbose": -1,
    "num_threads": 4,
    "seed": 42,
    "deterministic": True,
}


class ModeloGBM:
    def __init__(self, columnas: Sequence[str], params: Optional[Dict] = None,
                 rondas_max: int = 800, parada_temprana: int = 50):
        if lgb is None:
            raise ImportError("lightgbm no instalado: pip install -r requirements.txt")
        self.columnas = list(columnas)
        self.params = dict(PARAMS_BASE, **(params or {}))
        self.rondas_max = rondas_max
        self.parada_temprana = parada_temprana
        self.modelo = None
        self.mejor_ronda = 0

    @staticmethod
    def _matriz(filas: Sequence[dict], columnas: Sequence[str]) -> np.ndarray:
        """Las ausencias se dejan como NaN: LightGBM las trata como una rama
        propia. Rellenarlas con la media inventaria un valor que nunca existio."""
        datos = np.full((len(filas), len(columnas)), np.nan)
        for i, fila in enumerate(filas):
            for j, col in enumerate(columnas):
                valor = fila.get(col)
                if valor is not None and not (isinstance(valor, float) and np.isnan(valor)):
                    try:
                        datos[i, j] = float(valor)
                    except (TypeError, ValueError):
                        pass
        return datos

    def fit(self, entrenamiento: Sequence[dict], y_entrenamiento: np.ndarray,
            validacion: Optional[Sequence[dict]] = None,
            y_validacion: Optional[np.ndarray] = None) -> "ModeloGBM":
        x = self._matriz(entrenamiento, self.columnas)
        dtrain = lgb.Dataset(x, label=y_entrenamiento, feature_name=self.columnas)

        conjuntos, nombres = [], []
        if validacion is not None and y_validacion is not None and len(validacion) > 0:
            dval = lgb.Dataset(self._matriz(validacion, self.columnas), label=y_validacion,
                               feature_name=self.columnas, reference=dtrain)
            conjuntos, nombres = [dval], ["validacion"]

        callbacks = [lgb.log_evaluation(period=0)]
        if conjuntos:
            callbacks.append(lgb.early_stopping(self.parada_temprana, verbose=False))

        self.modelo = lgb.train(self.params, dtrain, num_boost_round=self.rondas_max,
                                valid_sets=conjuntos, valid_names=nombres, callbacks=callbacks)
        self.mejor_ronda = self.modelo.best_iteration or self.rondas_max
        return self

    def predict(self, filas: Sequence[dict]) -> np.ndarray:
        if self.modelo is None:
            raise RuntimeError("El modelo no esta entrenado")
        probs = self.modelo.predict(self._matriz(filas, self.columnas),
                                    num_iteration=self.mejor_ronda)
        return np.asarray(probs, dtype=float).reshape(len(filas), 3)

    def importancias(self, top: int = 15) -> List[Tuple[str, float]]:
        if self.modelo is None:
            return []
        valores = self.modelo.feature_importance(importance_type="gain")
        pares = sorted(zip(self.columnas, valores), key=lambda kv: -kv[1])
        return [(nombre, round(float(v), 1)) for nombre, v in pares[:top]]
