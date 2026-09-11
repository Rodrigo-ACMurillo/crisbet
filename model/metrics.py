"""Metricas de probabilidad. Accuracy no esta entre ellas por accidente.

Un modelo que acierta el 60% de los partidos pero da 0.95 a favoritos que ganan
el 70% de las veces pierde dinero en cada apuesta. Lo que importa es si la
probabilidad es correcta, y eso lo miden Brier y log-loss, no el acierto.

**Convencion del Brier.** Para 3 resultados hay dos formas de reportarlo y se
confunden todo el tiempo:
  - suma sobre las clases: rango 0-2, un modelo decente ronda 0.57
  - media por clase (suma / 3): rango 0-0.667, un modelo decente ronda 0.19
Aqui se devuelven las dos siempre, con nombre explicito.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

CLASES = ("1", "X", "2")
EPS = 1e-15


def a_indices(resultados: Sequence[str]) -> np.ndarray:
    """'H'/'D'/'A' -> 0/1/2."""
    mapa = {"H": 0, "D": 1, "A": 2, "1": 0, "X": 1, "2": 2}
    return np.array([mapa[r] for r in resultados], dtype=int)


def brier_multiclase(probs: np.ndarray, y: np.ndarray) -> Dict[str, float]:
    reales = np.zeros_like(probs)
    reales[np.arange(len(y)), y] = 1.0
    suma = float(np.mean(np.sum((probs - reales) ** 2, axis=1)))
    return {"brier_suma": round(suma, 5), "brier_medio_por_clase": round(suma / probs.shape[1], 5)}


def log_loss(probs: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(probs[np.arange(len(y)), y], EPS, 1.0)
    return round(float(-np.mean(np.log(p))), 5)


def acierto(probs: np.ndarray, y: np.ndarray) -> float:
    return round(float(np.mean(np.argmax(probs, axis=1) == y)), 5)


def curva_calibracion(probs: np.ndarray, y: np.ndarray, clase: int = 0,
                      bins: int = 10, minimo: int = 30) -> List[Dict]:
    """Probabilidad predicha frente a frecuencia observada, por tramos."""
    p = probs[:, clase]
    real = (y == clase).astype(float)
    bordes = np.linspace(0.0, 1.0, bins + 1)
    salida = []
    for i in range(bins):
        mascara = (p >= bordes[i]) & (p < bordes[i + 1] if i < bins - 1 else p <= 1.0)
        if mascara.sum() < minimo:
            continue
        salida.append({
            "rango": f"[{bordes[i]:.1f}, {bordes[i + 1]:.1f})",
            "n": int(mascara.sum()),
            "predicho": round(float(p[mascara].mean()), 4),
            "observado": round(float(real[mascara].mean()), 4),
            "desvio": round(float(real[mascara].mean() - p[mascara].mean()), 4),
        })
    return salida


def error_calibracion_esperado(probs: np.ndarray, y: np.ndarray, bins: int = 10) -> float:
    """ECE: desvio medio entre lo predicho y lo observado, ponderado por volumen.

    Un ECE de 0.05 significa que las probabilidades estan, de media, 5 puntos
    desviadas. Con margenes del 2-5%, eso se come el edge entero.
    """
    total = 0.0
    n = len(y)
    for clase in range(probs.shape[1]):
        p = probs[:, clase]
        real = (y == clase).astype(float)
        bordes = np.linspace(0.0, 1.0, bins + 1)
        for i in range(bins):
            mascara = (p >= bordes[i]) & (p < bordes[i + 1] if i < bins - 1 else p <= 1.0)
            if not mascara.any():
                continue
            total += (mascara.sum() / n) * abs(real[mascara].mean() - p[mascara].mean())
    return round(float(total / probs.shape[1]), 5)


def evaluar(probs: np.ndarray, y: np.ndarray, nombre: str = "") -> Dict:
    probs = np.clip(np.asarray(probs, dtype=float), EPS, 1.0)
    probs = probs / probs.sum(axis=1, keepdims=True)
    salida = {"modelo": nombre, "n": int(len(y))}
    salida.update(brier_multiclase(probs, y))
    salida["log_loss"] = log_loss(probs, y)
    salida["acierto"] = acierto(probs, y)
    salida["ece"] = error_calibracion_esperado(probs, y)
    return salida


def comparar(resultados: Sequence[Dict], referencia: str = "mercado") -> List[Dict]:
    """Cada modelo frente al mercado. La columna que decide es la diferencia."""
    base = next((r for r in resultados if r["modelo"] == referencia), None)
    salida = []
    for r in resultados:
        fila = dict(r)
        if base and r["modelo"] != referencia:
            fila["brier_vs_mercado"] = round(r["brier_medio_por_clase"]
                                             - base["brier_medio_por_clase"], 5)
            fila["bate_al_mercado"] = fila["brier_vs_mercado"] < 0
        salida.append(fila)
    return salida
