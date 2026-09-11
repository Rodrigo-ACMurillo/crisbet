"""Modelos que parten del mercado en lugar de competir con el desde cero.

La lectura del Sprint 4 fue que el mercado sabe mas que nuestro modelo. La
respuesta razonable no es insistir en predecir el partido, sino cambiar la
pregunta: **dado lo que dice el mercado, ¿hay algo que anada informacion?**

Dos variantes, porque distinguen cosas distintas:

`GBMConMercado`
    Las probabilidades implicitas entran como tres features mas. El arbol es
    libre de usarlas o ignorarlas. Simple, pero no garantiza nada: si la senal
    propia es ruido, puede alejarse del mercado igual que antes.

`GBMResidual`
    El mercado entra como `init_score`: el modelo **arranca exactamente en la
    probabilidad del mercado** y solo aprende la desviacion. Es la formulacion
    honesta de la pregunta. Si no hay senal que anadir, la regularizacion deja
    los arboles planos y el resultado converge al mercado en lugar de degradarlo.
    Un empate tecnico aqui no es un fracaso: es la respuesta.

**Aviso operativo que no hay que perder de vista.** Estas cuotas son de cierre,
o sea del instante previo al pitido. Un modelo que las usa solo puede operar en
ese momento, no la vispera. Y el precio que se consigue apostando al cierre es
peor que el de horas antes. El Sprint 5 tendra que medir con cuotas de apertura
o asumir esa penalizacion explicitamente.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

try:
    import lightgbm as lgb
except ImportError:
    lgb = None

from gbm import PARAMS_BASE, ModeloGBM

EPS = 1e-9

# Mas conservador que el GBM libre: aqui solo interesa capturar desviaciones
# respaldadas por muchos partidos, no matices que el mercado ya ha valorado.
PARAMS_RESIDUAL = dict(
    PARAMS_BASE,
    learning_rate=0.02,
    num_leaves=7,
    max_depth=3,
    min_data_in_leaf=300,
    lambda_l2=20.0,
)

COLUMNAS_MERCADO = ["mercado_p1", "mercado_px", "mercado_p2",
                    "mercado_logit_1", "mercado_logit_x", "mercado_favorito",
                    "mercado_incertidumbre"]


def features_de_mercado(probs: np.ndarray) -> List[Dict[str, float]]:
    """Convierte las probabilidades del mercado en features utilizables.

    Ademas de las tres crudas se anaden dos log-odds (el arbol parte mejor en
    esa escala, donde las diferencias en los extremos no se aplastan) y la
    entropia normalizada, que resume en un numero si el partido esta claro o
    abierto.
    """
    p = np.clip(probs, EPS, 1.0)
    p = p / p.sum(axis=1, keepdims=True)
    entropia = -(p * np.log(p)).sum(axis=1) / np.log(3.0)
    salida = []
    for i in range(len(p)):
        salida.append({
            "mercado_p1": float(p[i, 0]),
            "mercado_px": float(p[i, 1]),
            "mercado_p2": float(p[i, 2]),
            "mercado_logit_1": float(np.log(p[i, 0] / p[i, 2])),
            "mercado_logit_x": float(np.log(p[i, 1] / p[i, 2])),
            "mercado_favorito": float(np.argmax(p[i])),
            "mercado_incertidumbre": float(entropia[i]),
        })
    return salida


def _adjuntar(filas: Sequence[dict], probs: np.ndarray) -> List[dict]:
    extras = features_de_mercado(probs)
    return [dict(fila, **extra) for fila, extra in zip(filas, extras)]


class GBMConMercado(ModeloGBM):
    """GBM normal, con las features de mercado anadidas al conjunto."""

    def __init__(self, columnas: Sequence[str], **kwargs):
        super().__init__(list(columnas) + COLUMNAS_MERCADO, **kwargs)

    def fit_con_mercado(self, entrenamiento, y_entrenamiento, probs_entrenamiento,
                        validacion=None, y_validacion=None, probs_validacion=None):
        return self.fit(
            _adjuntar(entrenamiento, probs_entrenamiento), y_entrenamiento,
            _adjuntar(validacion, probs_validacion) if validacion is not None else None,
            y_validacion,
        )

    def predict_con_mercado(self, filas, probs) -> np.ndarray:
        return self.predict(_adjuntar(filas, probs))


class GBMResidual:
    """El mercado como punto de partida; el modelo solo aprende la desviacion.

    `init_score` fija el margen inicial de cada clase en log(p_mercado). Sin
    ningun arbol, la salida del modelo ES el mercado. Cada arbol que se anade
    tiene que justificar su desviacion contra la funcion de perdida.
    """

    def __init__(self, columnas: Sequence[str], params: Optional[Dict] = None,
                 rondas_max: int = 600, parada_temprana: int = 40):
        if lgb is None:
            raise ImportError("lightgbm no instalado")
        self.columnas = list(columnas) + COLUMNAS_MERCADO
        self.params = dict(PARAMS_RESIDUAL, **(params or {}))
        self.rondas_max = rondas_max
        self.parada_temprana = parada_temprana
        self.modelo = None
        self.mejor_ronda = 0

    @staticmethod
    def _init_score(probs: np.ndarray) -> np.ndarray:
        """Margen inicial = log(p_mercado), en el orden que espera LightGBM.

        Para multiclase, LightGBM lee el init_score aplanado por columnas
        (todas las filas de la clase 0, luego las de la 1...). Aplanarlo por
        filas rompe la correspondencia en silencio y el modelo arranca en un
        punto absurdo sin dar ningun error.
        """
        p = np.clip(probs, EPS, 1.0)
        p = p / p.sum(axis=1, keepdims=True)
        return np.log(p).ravel(order="F")

    def fit(self, entrenamiento, y_entrenamiento, probs_entrenamiento,
            validacion=None, y_validacion=None, probs_validacion=None) -> "GBMResidual":
        x = ModeloGBM._matriz(_adjuntar(entrenamiento, probs_entrenamiento), self.columnas)
        dtrain = lgb.Dataset(x, label=y_entrenamiento, feature_name=self.columnas,
                             init_score=self._init_score(probs_entrenamiento))

        conjuntos, nombres, callbacks = [], [], [lgb.log_evaluation(period=0)]
        if validacion is not None and probs_validacion is not None and len(validacion):
            dval = lgb.Dataset(
                ModeloGBM._matriz(_adjuntar(validacion, probs_validacion), self.columnas),
                label=y_validacion, feature_name=self.columnas,
                init_score=self._init_score(probs_validacion), reference=dtrain)
            conjuntos, nombres = [dval], ["validacion"]
            callbacks.append(lgb.early_stopping(self.parada_temprana, verbose=False))

        self.modelo = lgb.train(self.params, dtrain, num_boost_round=self.rondas_max,
                                valid_sets=conjuntos, valid_names=nombres, callbacks=callbacks)
        self.mejor_ronda = self.modelo.best_iteration or self.rondas_max
        return self

    def predict(self, filas, probs) -> np.ndarray:
        """Prediccion cruda + margen del mercado, y softmax al final.

        `raw_score=True` es imprescindible: sin el, LightGBM aplica softmax a la
        desviacion sola y se pierde el punto de partida.
        """
        if self.modelo is None:
            raise RuntimeError("El modelo no esta entrenado")
        crudo = self.modelo.predict(
            ModeloGBM._matriz(_adjuntar(filas, probs), self.columnas),
            num_iteration=self.mejor_ronda, raw_score=True)
        crudo = np.asarray(crudo, dtype=float).reshape(len(filas), 3)

        p = np.clip(probs, EPS, 1.0)
        p = p / p.sum(axis=1, keepdims=True)
        margen = np.log(p) + crudo
        margen -= margen.max(axis=1, keepdims=True)   # estabilidad numerica
        exp = np.exp(margen)
        return exp / exp.sum(axis=1, keepdims=True)

    def desviacion_media(self, filas, probs) -> float:
        """Cuanto se aparta del mercado, en puntos de probabilidad.

        Cerca de 0 significa que el modelo no encontro nada que anadir. Es un
        resultado legitimo y hay que poder verlo.
        """
        pred = self.predict(filas, probs)
        p = probs / probs.sum(axis=1, keepdims=True)
        return round(float(np.abs(pred - p).mean()), 5)

    def importancias(self, top: int = 15):
        if self.modelo is None:
            return []
        valores = self.modelo.feature_importance(importance_type="gain")
        pares = sorted(zip(self.columnas, valores), key=lambda kv: -kv[1])
        return [(n, round(float(v), 1)) for n, v in pares[:top] if v > 0]
