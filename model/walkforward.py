"""Validacion walk-forward por temporada. Nunca k-fold aleatorio.

Un k-fold sobre series temporales entrena con partidos de mayo para predecir
partidos de septiembre del mismo ano. El resultado sale precioso y no significa
nada: en produccion no existe el partido de mayo cuando hay que apostar en
septiembre.

Aqui cada temporada se predice usando **solo** lo anterior a su primer partido:

    temporadas:  15-16 16-17 17-18 | 18-19        <- predice 18-19
                 15-16 ... 18-19   | 19-20        <- predice 19-20
                 ...

Dentro del entrenamiento se recorta un tramo final de validacion —tambien
anterior en el tiempo al periodo de prueba— para la parada temprana del GBM, el
peso del ensamble y la calibracion. Nada de eso mira el periodo de prueba.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class Fold:
    """Un corte temporal: entrenar con el pasado, validar con su cola, probar en el futuro."""

    nombre: str
    entrenamiento: List[dict]
    validacion: List[dict]
    prueba: List[dict]
    corte_kickoff: str

    def resumen(self) -> Dict:
        return {
            "fold": self.nombre,
            "n_entrenamiento": len(self.entrenamiento),
            "n_validacion": len(self.validacion),
            "n_prueba": len(self.prueba),
            "corte": self.corte_kickoff,
        }


def ordenar(filas: Sequence[dict]) -> List[dict]:
    return sorted(filas, key=lambda f: (f["kickoff_utc"], f["match_id"]))


def generar_folds(filas: Sequence[dict], temporadas_minimas: int = 3,
                  fraccion_validacion: float = 0.15) -> List[Fold]:
    """Un fold por temporada, a partir de la `temporadas_minimas`+1-esima."""
    filas = ordenar(filas)
    temporadas = sorted({f["temporada"] for f in filas})
    if len(temporadas) <= temporadas_minimas:
        raise ValueError(
            f"Hacen falta mas de {temporadas_minimas} temporadas; hay {len(temporadas)}")

    folds: List[Fold] = []
    for temporada in temporadas[temporadas_minimas:]:
        prueba = [f for f in filas if f["temporada"] == temporada]
        if not prueba:
            continue
        corte = prueba[0]["kickoff_utc"]
        # El corte es por kickoff, no por etiqueta de temporada: si un partido
        # aplazado de la temporada anterior se juega despues del corte, no puede
        # entrar al entrenamiento por mucho que su etiqueta diga que es antiguo.
        pasado = [f for f in filas if f["kickoff_utc"] < corte]
        if len(pasado) < 500:
            continue
        n_val = max(200, int(len(pasado) * fraccion_validacion))
        folds.append(Fold(
            nombre=temporada,
            entrenamiento=pasado[:-n_val],
            validacion=pasado[-n_val:],
            prueba=prueba,
            corte_kickoff=corte,
        ))
    return folds


def verificar_fold(fold: Fold) -> List[str]:
    """Comprobaciones de integridad temporal. Devuelve la lista de violaciones."""
    problemas = []
    if fold.entrenamiento and fold.validacion:
        if max(f["kickoff_utc"] for f in fold.entrenamiento) > \
           min(f["kickoff_utc"] for f in fold.validacion):
            problemas.append("entrenamiento se solapa con validacion")
    if fold.validacion and fold.prueba:
        if max(f["kickoff_utc"] for f in fold.validacion) >= \
           min(f["kickoff_utc"] for f in fold.prueba):
            problemas.append("validacion se solapa con prueba")
    if fold.entrenamiento and fold.prueba:
        if max(f["kickoff_utc"] for f in fold.entrenamiento) >= \
           min(f["kickoff_utc"] for f in fold.prueba):
            problemas.append("entrenamiento se solapa con prueba")
    ids_prueba = {f["match_id"] for f in fold.prueba}
    if ids_prueba & {f["match_id"] for f in fold.entrenamiento + fold.validacion}:
        problemas.append("hay partidos de prueba dentro del entrenamiento")
    return problemas
