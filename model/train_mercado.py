"""¿Anade algo el modelo a lo que ya dice el mercado?

Misma disciplina walk-forward que `train.py`, distinta pregunta. Aqui el
mercado no es el rival a batir desde cero sino el punto de partida, y lo que se
mide es si queda informacion por encima de el.

Tres resultados posibles, y los tres son informativos:
  - el residual mejora al mercado  -> hay veta, el Sprint 5 tiene por donde entrar
  - el residual converge al mercado -> no hay senal propia, pero tampoco se pierde nada
  - el residual empeora al mercado  -> el modelo esta aprendiendo ruido

    python train_mercado.py --ligas E0
    python train_mercado.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mercado_modelo import GBMConMercado, GBMResidual
from metrics import a_indices, comparar, curva_calibracion, evaluar
from train import cargar_parquet, columnas_modelo, probs_mercado
from walkforward import generar_folds, ordenar, verificar_fold


def prueba_discrepancias(pred: np.ndarray, mercado: np.ndarray, y: np.ndarray,
                         umbrales=(0.005, 0.01, 0.02, 0.05)) -> List[Dict]:
    """La misma prueba que hundio al modelo del Sprint 4, aplicada aqui.

    Si el modelo se aparta del mercado, alguien tiene que estar equivocado.
    """
    salida = []
    reales = np.zeros_like(pred)
    reales[np.arange(len(y)), y] = 1.0
    exceso = (pred - mercado).ravel()
    p_modelo, p_mercado, ocurrio = pred.ravel(), mercado.ravel(), reales.ravel()

    # Varios umbrales a proposito: un modelo anclado al mercado casi nunca se
    # aparta 5 puntos, y con un solo umbral alto la prueba se queda sin muestra
    # y no concluye nada. Las desviaciones pequenas tambien pueden ser
    # informativas si van sistematicamente en la direccion correcta.
    for umbral in umbrales:
        for nombre, mascara in (("modelo_mas_alto", exceso > umbral),
                                ("modelo_mas_bajo", exceso < -umbral)):
            if mascara.sum() < 100:
                salida.append({"umbral": umbral, "direccion": nombre,
                               "n": int(mascara.sum()),
                               "nota": "muy pocos casos para concluir"})
                continue
            obs = float(ocurrio[mascara].mean())
            dm, dk = float(p_modelo[mascara].mean()), float(p_mercado[mascara].mean())
            salida.append({
                "umbral": umbral, "direccion": nombre, "n": int(mascara.sum()),
                "dice_el_modelo": round(dm, 5), "dice_el_mercado": round(dk, 5),
                "ocurrio_de_verdad": round(obs, 5),
                "error_modelo": round(abs(dm - obs), 5),
                "error_mercado": round(abs(dk - obs), 5),
                "acierta": "modelo" if abs(dm - obs) < abs(dk - obs) else "mercado",
            })
    return salida


def run(args: argparse.Namespace) -> Dict:
    feats = cargar_parquet(args.warehouse, "features")
    odds = cargar_parquet(args.warehouse, "odds")
    feats = feats[feats["y_resultado"].notna()].copy()
    if args.ligas:
        feats = feats[feats["liga"].isin(args.ligas)]

    columnas = columnas_modelo(feats)
    filas = ordenar(feats.to_dict("records"))

    # Sin cuota no hay punto de partida: esas filas no sirven ni para entrenar.
    mercado_todo = probs_mercado(filas, odds, args.casa)
    con_cuota = ~np.isnan(mercado_todo).any(axis=1)
    descartadas = int((~con_cuota).sum())
    mercado_por_id = {fila["match_id"]: mercado_todo[i]
                      for i, fila in enumerate(filas) if con_cuota[i]}
    filas = [fila for i, fila in enumerate(filas) if con_cuota[i]]

    print(f"{len(filas)} partidos con cuota de cierre ({descartadas} descartados), "
          f"{len(columnas)} features")

    folds = generar_folds(filas, temporadas_minimas=args.temporadas_minimas)
    print(f"{len(folds)} folds walk-forward\n")

    def probs_de(sub) -> np.ndarray:
        return np.vstack([mercado_por_id[f["match_id"]] for f in sub])

    acumulado: Dict[str, List[np.ndarray]] = {}
    y_acumulado: List[np.ndarray] = []
    registros: List[Dict] = []
    detalle: List[Dict] = []
    violaciones: List[Dict] = []
    importancias = []

    for fold in folds:
        problemas = verificar_fold(fold)
        if problemas:
            violaciones.append({"fold": fold.nombre, "problemas": problemas})
            continue

        y_ent = a_indices([f["y_resultado"] for f in fold.entrenamiento])
        y_val = a_indices([f["y_resultado"] for f in fold.validacion])
        y_pru = a_indices([f["y_resultado"] for f in fold.prueba])
        m_ent, m_val, m_pru = (probs_de(fold.entrenamiento), probs_de(fold.validacion),
                               probs_de(fold.prueba))

        con_mercado = GBMConMercado(columnas)
        con_mercado.fit_con_mercado(fold.entrenamiento, y_ent, m_ent,
                                    fold.validacion, y_val, m_val)

        residual = GBMResidual(columnas)
        residual.fit(fold.entrenamiento, y_ent, m_ent, fold.validacion, y_val, m_val)

        predicciones = {
            "mercado": m_pru,
            "gbm_con_mercado": con_mercado.predict_con_mercado(fold.prueba, m_pru),
            "gbm_residual": residual.predict(fold.prueba, m_pru),
        }
        for nombre, probs in predicciones.items():
            acumulado.setdefault(nombre, []).append(probs)
        y_acumulado.append(y_pru)

        for i, partido in enumerate(fold.prueba):
            reg = {"match_id": partido["match_id"], "fold": fold.nombre,
                   "liga": partido["liga"], "kickoff_utc": partido["kickoff_utc"],
                   "y_resultado": partido["y_resultado"]}
            for nombre, probs in predicciones.items():
                reg[f"{nombre}_p1"] = float(probs[i, 0])
                reg[f"{nombre}_px"] = float(probs[i, 1])
                reg[f"{nombre}_p2"] = float(probs[i, 2])
            registros.append(reg)

        fila = fold.resumen()
        fila["desviacion_del_mercado"] = residual.desviacion_media(fold.prueba, m_pru)
        fila["rondas_residual"] = residual.mejor_ronda
        for nombre, probs in predicciones.items():
            fila[nombre] = evaluar(probs, y_pru, nombre)["brier_medio_por_clase"]
        detalle.append(fila)
        print(f"  {fold.nombre}: n={len(y_pru):>4}  mercado={fila['mercado']:.5f}  "
              f"con_mercado={fila['gbm_con_mercado']:.5f}  "
              f"residual={fila['gbm_residual']:.5f}  "
              f"desviacion={fila['desviacion_del_mercado']:.5f}  "
              f"rondas={fila['rondas_residual']}")
        importancias = residual.importancias()

    y_total = np.concatenate(y_acumulado)
    resultados = comparar([evaluar(np.vstack(acumulado[n]), y_total, n) for n in acumulado],
                          referencia="mercado")

    mercado_total = np.vstack(acumulado["mercado"])
    mejor = min((r for r in resultados if r["modelo"] != "mercado"),
                key=lambda r: r["brier_medio_por_clase"])
    base = next(r for r in resultados if r["modelo"] == "mercado")
    diferencia = mejor["brier_medio_por_clase"] - base["brier_medio_por_clase"]

    informe = {
        "sprint": "4b — el mercado como punto de partida",
        "n_partidos_evaluados": int(len(y_total)),
        "folds": len(detalle),
        "violaciones_temporales": violaciones,
        "resultados": resultados,
        "por_fold": detalle,
        "importancias_residual": importancias,
        "discrepancias_residual": prueba_discrepancias(
            np.vstack(acumulado["gbm_residual"]), mercado_total, y_total),
        "calibracion_mejor": curva_calibracion(
            np.vstack(acumulado[mejor["modelo"]]), y_total, clase=0),
    }

    # El umbral de 0.0005 no es cosmetico: por debajo de eso la diferencia es
    # menor que el ruido de muestreo con ~20.000 partidos y no sostiene ninguna
    # decision de dinero.
    if diferencia < -0.0005:
        lectura = ("HAY VETA: el modelo mejora al mercado de forma apreciable. "
                   "El Sprint 5 puede arrancar restringido a donde aparezca esa mejora.")
    elif diferencia < 0.0005:
        lectura = ("EMPATE: el modelo converge al mercado sin degradarlo. No hay senal "
                   "propia que anadir con los datos actuales, pero la formulacion es "
                   "correcta y sirve de base cuando lleguen alineaciones y bajas.")
    else:
        lectura = ("PEOR: partir del mercado y aun asi empeorarlo significa que el "
                   "modelo esta aprendiendo ruido. Reducir capacidad o descartar la via.")

    informe["veredicto"] = {
        "mejor_modelo": mejor["modelo"],
        "brier_modelo": mejor["brier_medio_por_clase"],
        "brier_mercado": base["brier_medio_por_clase"],
        "diferencia": round(diferencia, 5),
        "desviacion_media_del_mercado": round(
            float(np.mean([f["desviacion_del_mercado"] for f in detalle])), 5),
        "lectura": lectura,
    }

    if args.predicciones and registros:
        os.makedirs(os.path.dirname(os.path.abspath(args.predicciones)) or ".", exist_ok=True)
        pd.DataFrame(registros).to_parquet(args.predicciones, index=False, compression="zstd")
        informe["predicciones"] = {"ruta": args.predicciones, "filas": len(registros)}

    print("\n" + json.dumps(informe["veredicto"], ensure_ascii=False, indent=2))
    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)
        print("\nInforme: " + args.informe)
    return informe


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="El mercado como punto de partida")
    p.add_argument("--warehouse", default="../matchdata/warehouse")
    p.add_argument("--ligas", nargs="*")
    p.add_argument("--temporadas-minimas", type=int, default=3)
    p.add_argument("--casa", default="pinnacle")
    p.add_argument("--informe", default="reports/sprint4b_mercado.json")
    p.add_argument("--predicciones", default="predicciones/residual_mercado.parquet")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
