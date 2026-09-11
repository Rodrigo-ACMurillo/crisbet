"""Sprint 4: entrena y evalua el modelo de probabilidad walk-forward.

    python train.py --warehouse ../matchdata/warehouse

El criterio no es acertar mas partidos: es dar probabilidades mejores que las
del mercado. Por eso todo se compara contra la cuota de cierre de Pinnacle en el
mismo conjunto de partidos, con la misma convencion de Brier.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Sequence

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from calibration import CalibradorMulticlase, ensamblar, pesos_por_verosimilitud
from dixon_coles import DixonColes
from gbm import ModeloGBM
from metrics import a_indices, comparar, curva_calibracion, evaluar
from walkforward import Fold, generar_folds, ordenar, verificar_fold

EXCLUIR = {"match_id", "liga", "temporada", "kickoff_utc", "fecha",
           "equipo_local", "equipo_visitante", "feature_version"}


def cargar_parquet(raiz: str, dataset: str) -> pd.DataFrame:
    ruta = os.path.join(raiz, dataset)
    if not os.path.isdir(ruta):
        raise SystemExit(f"No existe {ruta}. Ejecuta matchdata/build.py primero.")
    trozos = []
    for carpeta, _, ficheros in os.walk(ruta):
        for fichero in ficheros:
            if fichero.endswith(".parquet"):
                trozos.append(pd.read_parquet(os.path.join(carpeta, fichero)))
    if not trozos:
        raise SystemExit(f"Sin parquet en {ruta}")
    return pd.concat(trozos, ignore_index=True)


def columnas_modelo(df: pd.DataFrame) -> List[str]:
    return [c for c in df.columns
            if not c.startswith("y_") and c not in EXCLUIR and c != "fiable"
            and pd.api.types.is_numeric_dtype(df[c])]


def probs_mercado(filas: Sequence[dict], odds: pd.DataFrame,
                  casa: str = "pinnacle") -> Optional[np.ndarray]:
    """Probabilidades implicitas de la cuota de cierre, con el margen repartido."""
    sub = odds[(odds["mercado"] == "1x2") & (odds["casa"] == casa)]
    por_id = {r.match_id: (r.cuota_1, r.cuota_x, r.cuota_2) for r in sub.itertuples()}
    salida = np.full((len(filas), 3), np.nan)
    for i, fila in enumerate(filas):
        cuotas = por_id.get(fila["match_id"])
        if not cuotas or any(c is None or c <= 1.0 for c in cuotas):
            continue
        inversas = np.array([1.0 / c for c in cuotas])
        salida[i] = inversas / inversas.sum()
    return salida


def entrenar_fold(fold: Fold, columnas: List[str], semivida: float) -> Dict[str, np.ndarray]:
    """Entrena los dos modelos y devuelve sus predicciones sobre validacion y prueba."""
    y_entrenamiento = a_indices([f["y_resultado"] for f in fold.entrenamiento])
    y_validacion = a_indices([f["y_resultado"] for f in fold.validacion])

    # Dixon-Coles: la fecha de referencia del decaimiento es el corte del fold,
    # no "hoy". Si fuese hoy, los pesos cambiarian segun cuando se ejecute.
    dc = DixonColes(semivida_dias=semivida)
    dc.fit(fold.entrenamiento + fold.validacion, fecha_referencia=fold.corte_kickoff)
    dc_solo_entrenamiento = DixonColes(semivida_dias=semivida)
    dc_solo_entrenamiento.fit(fold.entrenamiento, fecha_referencia=fold.corte_kickoff)

    gbm = ModeloGBM(columnas)
    gbm.fit(fold.entrenamiento, y_entrenamiento, fold.validacion, y_validacion)

    return {
        # En validacion, Dixon-Coles debe venir del modelo que NO vio validacion.
        "dc_val": dc_solo_entrenamiento.predecir_muchos(fold.validacion),
        "gbm_val": gbm.predict(fold.validacion),
        "dc_test": dc.predecir_muchos(fold.prueba),
        "gbm_test": gbm.predict(fold.prueba),
        "_dc": dc,
        "_gbm": gbm,
        "_y_val": y_validacion,
    }


def run(args: argparse.Namespace) -> Dict:
    feats = cargar_parquet(args.warehouse, "features")
    odds = cargar_parquet(args.warehouse, "odds")
    feats = feats[feats["y_resultado"].notna()].copy()
    if args.ligas:
        feats = feats[feats["liga"].isin(args.ligas)]

    columnas = columnas_modelo(feats)
    filas = ordenar(feats.to_dict("records"))
    print(f"{len(filas)} partidos, {len(columnas)} features, "
          f"{len(set(f['temporada'] for f in filas))} temporadas")

    folds = generar_folds(filas, temporadas_minimas=args.temporadas_minimas)
    print(f"{len(folds)} folds walk-forward\n")

    violaciones: List[Dict] = []
    acumulado: Dict[str, List[np.ndarray]] = {}
    y_acumulado: List[np.ndarray] = []
    detalle_folds: List[Dict] = []
    importancias: List = []
    pesos_vistos: List[Dict[str, float]] = []
    predicciones_por_partido: List[Dict] = []

    for fold in folds:
        problemas = verificar_fold(fold)
        if problemas:
            violaciones.append({"fold": fold.nombre, "problemas": problemas})
            print(f"  {fold.nombre}: VIOLACION TEMPORAL {problemas} -- fold descartado")
            continue

        y_prueba = a_indices([f["y_resultado"] for f in fold.prueba])
        salida = entrenar_fold(fold, columnas, args.semivida)
        y_val = salida["_y_val"]

        # Pesos del ensamble y calibrador: ajustados SOLO en validacion.
        pesos = pesos_por_verosimilitud(
            {"dixon_coles": salida["dc_val"], "gbm": salida["gbm_val"]}, y_val)
        pesos_vistos.append(pesos)
        ens_val = ensamblar({"dixon_coles": salida["dc_val"], "gbm": salida["gbm_val"]}, pesos)
        calibrador = CalibradorMulticlase().fit_con_control(ens_val, y_val)

        ens_test = ensamblar({"dixon_coles": salida["dc_test"], "gbm": salida["gbm_test"]}, pesos)
        predicciones = {
            "dixon_coles": salida["dc_test"],
            "gbm": salida["gbm_test"],
            "ensamble": ens_test,
            "ensamble_calibrado": calibrador.transform(ens_test),
        }
        mercado = probs_mercado(fold.prueba, odds, args.casa)
        if mercado is not None:
            predicciones["mercado"] = mercado

        # Solo partidos con cuota de mercado: comparar sobre conjuntos distintos
        # no compara nada.
        validos = ~np.isnan(predicciones.get("mercado", np.zeros((len(y_prueba), 3)))).any(axis=1)
        for nombre, probs in predicciones.items():
            acumulado.setdefault(nombre, []).append(probs[validos])
        y_acumulado.append(y_prueba[validos])

        # Se guarda la prediccion de cada partido evaluado. El Sprint 5 necesita
        # estas probabilidades junto a la cuota para medir value y simular el
        # bankroll; sin esto habria que reentrenar los 7 folds solo para eso.
        for pos in np.flatnonzero(validos):
            partido = fold.prueba[int(pos)]
            registro = {
                "match_id": partido["match_id"],
                "fold": fold.nombre,
                "liga": partido["liga"],
                "temporada": partido["temporada"],
                "kickoff_utc": partido["kickoff_utc"],
                "equipo_local": partido["equipo_local"],
                "equipo_visitante": partido["equipo_visitante"],
                "y_resultado": partido["y_resultado"],
                "fiable": partido.get("fiable"),
            }
            for nombre, probs in predicciones.items():
                registro[f"{nombre}_p1"] = float(probs[pos, 0])
                registro[f"{nombre}_px"] = float(probs[pos, 1])
                registro[f"{nombre}_p2"] = float(probs[pos, 2])
            predicciones_por_partido.append(registro)

        resumen = fold.resumen()
        resumen["n_evaluados"] = int(validos.sum())
        resumen["pesos_ensamble"] = pesos
        resumen["dc_convergio"] = salida["_dc"].params.convergio
        resumen["gbm_rondas"] = salida["_gbm"].mejor_ronda
        resumen["calibracion"] = ("aplicada" if calibrador.ajustado else "omitida") +                                  f" ({calibrador.motivo})"
        for nombre, probs in predicciones.items():
            resumen[nombre] = evaluar(probs[validos], y_prueba[validos],
                                      nombre)["brier_medio_por_clase"]
        detalle_folds.append(resumen)
        print(f"  {fold.nombre}: n={resumen['n_evaluados']:>4}  "
              f"dc={resumen['dixon_coles']:.5f}  gbm={resumen['gbm']:.5f}  "
              f"ens_cal={resumen['ensamble_calibrado']:.5f}  "
              f"mercado={resumen.get('mercado', float('nan')):.5f}")
        importancias = salida["_gbm"].importancias()

    if not y_acumulado:
        raise SystemExit("Ningun fold valido")

    y_total = np.concatenate(y_acumulado)
    resultados = [evaluar(np.vstack(acumulado[n]), y_total, n) for n in acumulado]
    resultados = comparar(resultados, referencia="mercado")

    mejor = min((r for r in resultados if r["modelo"] != "mercado"),
                key=lambda r: r["brier_medio_por_clase"])
    referencia = next((r for r in resultados if r["modelo"] == "mercado"), None)

    informe = {
        "sprint": 4,
        "validacion": "walk-forward por temporada (sin k-fold aleatorio)",
        "n_partidos_evaluados": int(len(y_total)),
        "folds": len(detalle_folds),
        "violaciones_temporales": violaciones,
        "features_usadas": len(columnas),
        "semivida_dixon_coles_dias": args.semivida,
        "resultados": resultados,
        "por_fold": detalle_folds,
        "pesos_ensamble_medios": {
            k: round(float(np.mean([p[k] for p in pesos_vistos])), 4)
            for k in (pesos_vistos[0] if pesos_vistos else {})
        },
        "importancias_gbm_ultimo_fold": importancias,
        "calibracion_mejor_modelo": curva_calibracion(
            np.vstack(acumulado[mejor["modelo"]]), y_total, clase=0),
        "veredicto": {
            "mejor_modelo": mejor["modelo"],
            "brier_medio_por_clase": mejor["brier_medio_por_clase"],
            "brier_mercado": referencia["brier_medio_por_clase"] if referencia else None,
            "bate_al_mercado": bool(referencia and
                                    mejor["brier_medio_por_clase"] < referencia["brier_medio_por_clase"]),
        },
    }
    informe["veredicto"]["lectura"] = (
        "El modelo bate al mercado en Brier: hay margen para buscar value en el Sprint 5."
        if informe["veredicto"]["bate_al_mercado"] else
        "El modelo NO bate al mercado. Sin edge en probabilidad no puede haber edge "
        "en cuota: el Sprint 5 arrancaria condenado. Ver README para las vias abiertas."
    )

    if args.predicciones and predicciones_por_partido:
        os.makedirs(os.path.dirname(os.path.abspath(args.predicciones)) or ".", exist_ok=True)
        pd.DataFrame(predicciones_por_partido).to_parquet(
            args.predicciones, index=False, compression="zstd")
        informe["predicciones"] = {
            "ruta": args.predicciones,
            "filas": len(predicciones_por_partido),
            "modelos": sorted({k[:-3] for k in predicciones_por_partido[0]
                               if k.endswith(("_p1", "_px", "_p2"))}),
        }

    print("\n" + json.dumps({k: informe[k] for k in
                             ("n_partidos_evaluados", "folds", "resultados", "veredicto")},
                            ensure_ascii=False, indent=2))
    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)
        print(f"\nInforme completo: {args.informe}")
    return informe


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sprint 4: modelo de probabilidad")
    p.add_argument("--warehouse", default="../matchdata/warehouse")
    p.add_argument("--ligas", nargs="*")
    p.add_argument("--temporadas-minimas", type=int, default=3)
    p.add_argument("--semivida", type=float, default=365.0)
    p.add_argument("--casa", default="pinnacle")
    p.add_argument("--informe", default="reports/sprint4_modelo.json")
    p.add_argument("--predicciones", default="predicciones/walkforward.parquet",
                   help="parquet con la prediccion de cada partido (insumo del Sprint 5)")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
