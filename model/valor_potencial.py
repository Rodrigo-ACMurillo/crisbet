"""¿Las desviaciones del modelo superan el margen de la casa?

Esto **no es el backtest del Sprint 5**. Es la pregunta previa que decide si
ese sprint tiene sentido: si se apostara cada vez que el modelo supera al
mercado por encima de un umbral, ¿el retorno cubriria la comision?

La diferencia con el analisis de Brier es que aqui se usan las **cuotas reales
de cierre**, con su margen dentro. Una probabilidad puede estar mejor estimada
que la del mercado y aun asi no dar dinero, porque el precio ya se ha comido la
ventaja. Es la trampa clasica del betting cuantitativo.

**Sesgo que hay que tener presente al leer esto.** Los umbrales se examinan
despues de haber visto los datos. Eso infla cualquier resultado positivo. La
cifra de aqui sirve para dimensionar si merece la pena seguir, no para decidir
que se apuesta: eso exige elegir el umbral con datos anteriores al periodo
evaluado, y eso es exactamente el trabajo del Sprint 5.

    python valor_potencial.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
from typing import Dict, List

import numpy as np
import pandas as pd

SUFIJOS = {"H": "p1", "D": "px", "A": "p2"}
CUOTA_COL = {"H": "cuota_1", "D": "cuota_x", "A": "cuota_2"}


def cargar_odds(raiz: str, casa: str) -> pd.DataFrame:
    trozos = []
    for carpeta, _, ficheros in os.walk(os.path.join(raiz, "odds")):
        for fichero in ficheros:
            if fichero.endswith(".parquet"):
                trozos.append(pd.read_parquet(os.path.join(carpeta, fichero)))
    odds = pd.concat(trozos, ignore_index=True)
    return odds[(odds["mercado"] == "1x2") & (odds["casa"] == casa)]


def apuestas(pred: pd.DataFrame, odds: pd.DataFrame, modelo: str) -> pd.DataFrame:
    """Una fila por (partido, resultado) con la cuota real y lo que ocurrio."""
    cuotas = odds.set_index("match_id")[["cuota_1", "cuota_x", "cuota_2"]]
    unido = pred.join(cuotas, on="match_id", how="inner")
    filas = []
    for etiqueta, sufijo in SUFIJOS.items():
        sub = pd.DataFrame({
            "match_id": unido["match_id"].values,
            "liga": unido["liga"].values,
            "fold": unido["fold"].values,
            "resultado_apostado": etiqueta,
            "p_modelo": unido[f"{modelo}_{sufijo}"].values,
            "p_mercado": unido[f"mercado_{sufijo}"].values,
            "cuota": unido[CUOTA_COL[etiqueta]].values,
            "gano": (unido["y_resultado"] == etiqueta).astype(int).values,
        })
        filas.append(sub)
    todo = pd.concat(filas, ignore_index=True)
    todo = todo[todo["cuota"] > 1.0].copy()
    todo["exceso"] = todo["p_modelo"] - todo["p_mercado"]
    # Edge segun el modelo: cuanto cree que vale la apuesta por unidad arriesgada.
    todo["edge"] = todo["p_modelo"] * todo["cuota"] - 1.0
    todo["retorno"] = todo["gano"] * todo["cuota"] - 1.0
    return todo


def resumen(sub: pd.DataFrame, etiqueta: str) -> Dict:
    n = len(sub)
    if n == 0:
        return {"regla": etiqueta, "n": 0}
    roi = float(sub["retorno"].mean())
    # Error estandar del ROI: sin esto, un +3% sobre 200 apuestas parece una
    # conclusion cuando es indistinguible de cero.
    ee = float(sub["retorno"].std(ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
    return {
        "regla": etiqueta,
        "n": int(n),
        "roi_pct": round(roi * 100, 3),
        "error_estandar_pct": round(ee * 100, 3),
        "ic95_pct": [round((roi - 1.96 * ee) * 100, 3), round((roi + 1.96 * ee) * 100, 3)],
        "significativo": bool(roi - 1.96 * ee > 0),
        "cuota_media": round(float(sub["cuota"].mean()), 3),
        "acierto_pct": round(float(sub["gano"].mean()) * 100, 2),
        "edge_medio_declarado_pct": round(float(sub["edge"].mean()) * 100, 3),
    }


def robustez(sub: pd.DataFrame, n_reglas_examinadas: int) -> Dict:
    """Somete una regla ganadora a las pruebas que suelen tumbarla.

    Un ROI positivo sobre pocas apuestas casi siempre es una de estas tres
    cosas: la suerte de una temporada, un punado de cuotas altas que entraron,
    o el premio por haber mirado muchos umbrales. Las tres se comprueban aqui.
    """
    n = len(sub)
    if n < 30:
        return {"n": n, "nota": "muestra insuficiente"}
    roi = float(sub["retorno"].mean())
    ee = float(sub["retorno"].std(ddof=1) / math.sqrt(n))
    z = roi / ee if ee else float("nan")
    # Bonferroni: si se han mirado k reglas, el listón sube.
    z_exigido = 1.96 if n_reglas_examinadas <= 1 else float(
        abs(_z_bilateral(0.05 / n_reglas_examinadas)))

    por_fold = sub.groupby("fold")["retorno"].agg(["count", "mean"])
    mejor_fold = por_fold["mean"].idxmax()
    resto = sub[sub["fold"] != mejor_fold]
    roi_resto = float(resto["retorno"].mean()) if len(resto) > 30 else float("nan")
    ee_resto = (float(resto["retorno"].std(ddof=1) / math.sqrt(len(resto)))
                if len(resto) > 30 else float("nan"))

    ganancias = sub.loc[sub["gano"] == 1, "retorno"].sort_values(ascending=False)
    total = float(sub["retorno"].sum())
    concentracion = (float(ganancias.head(10).sum() / total) if total > 0 else float("nan"))

    fold_dominante = por_fold["count"].idxmax()
    cuota_dominante = float(por_fold["count"].max() / n)

    return {
        "n": n,
        "roi_pct": round(roi * 100, 3),
        "z": round(z, 3),
        "z_exigido_tras_corregir_por_{}_reglas".format(n_reglas_examinadas): round(z_exigido, 3),
        "sobrevive_a_la_correccion": bool(abs(z) > z_exigido),
        "temporadas_con_apuestas": int(len(por_fold)),
        "temporadas_con_roi_positivo": int((por_fold["mean"] > 0).sum()),
        "temporada_que_mas_aporta": str(fold_dominante),
        "pct_apuestas_en_esa_temporada": round(cuota_dominante * 100, 1),
        "roi_sin_la_mejor_temporada_pct": (round(roi_resto * 100, 3)
                                           if roi_resto == roi_resto else None),
        "ic95_sin_la_mejor_temporada_pct": (
            [round((roi_resto - 1.96 * ee_resto) * 100, 3),
             round((roi_resto + 1.96 * ee_resto) * 100, 3)]
            if ee_resto == ee_resto else None),
        "pct_del_beneficio_en_las_10_mejores": (round(concentracion * 100, 1)
                                                if concentracion == concentracion else None),
    }


def _z_bilateral(alfa: float) -> float:
    """Cuantil normal para un contraste bilateral, sin depender de scipy aqui."""
    from statistics import NormalDist
    return NormalDist().inv_cdf(1.0 - alfa / 2.0)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--predicciones", default="predicciones/residual_mercado.parquet")
    p.add_argument("--warehouse", default="../matchdata/warehouse")
    p.add_argument("--modelo", default="gbm_residual")
    p.add_argument("--casa", default="pinnacle")
    p.add_argument("--informe", default="reports/sprint4b_valor.json")
    args = p.parse_args()

    pred = pd.read_parquet(args.predicciones)
    odds = cargar_odds(args.warehouse, args.casa)
    todo = apuestas(pred, odds, args.modelo)

    informe: Dict = {
        "aviso": ("Umbrales examinados despues de ver los datos: cualquier resultado "
                  "positivo esta inflado. Sirve para dimensionar, no para decidir."),
        "modelo": args.modelo,
        "casa": args.casa,
        "n_lineas_evaluables": int(len(todo)),
        "referencia_apostar_todo": resumen(todo, "apostar todas las lineas"),
        "por_umbral_de_exceso": [],
        "por_umbral_de_edge": [],
    }

    for umbral in (0.0, 0.005, 0.01, 0.015, 0.02, 0.03):
        informe["por_umbral_de_exceso"].append(
            resumen(todo[todo["exceso"] >= umbral], f"modelo supera al mercado en >= {umbral}"))

    for umbral in (0.0, 0.02, 0.05, 0.10):
        informe["por_umbral_de_edge"].append(
            resumen(todo[todo["edge"] >= umbral], f"edge declarado >= {umbral}"))

    # El filtro de cuota del plan: 1.50-4.00. Fuera de ese rango el staking de
    # Kelly se vuelve inestable y la varianza domina el resultado.
    en_rango = todo[(todo["cuota"] >= 1.5) & (todo["cuota"] <= 4.0)]
    informe["con_filtro_de_cuota_1.50_4.00"] = [
        resumen(en_rango[en_rango["exceso"] >= u], f"exceso >= {u} y cuota 1.50-4.00")
        for u in (0.01, 0.015, 0.02)
    ]

    reglas_examinadas = (len(informe["por_umbral_de_exceso"]) +
                         len(informe["por_umbral_de_edge"]) +
                         len(informe["con_filtro_de_cuota_1.50_4.00"]))
    candidatas = [r for r in informe["por_umbral_de_exceso"] +
                  informe["con_filtro_de_cuota_1.50_4.00"]
                  if r.get("n", 0) >= 300 and r.get("significativo")]

    informe["reglas_examinadas"] = reglas_examinadas
    informe["robustez"] = []
    for r in candidatas:
        umbral = float(r["regla"].split(">= ")[-1].split(" ")[0])
        sub = todo[todo["exceso"] >= umbral]
        if "cuota 1.50-4.00" in r["regla"]:
            sub = sub[(sub["cuota"] >= 1.5) & (sub["cuota"] <= 4.0)]
        informe["robustez"].append(dict(regla=r["regla"],
                                        **robustez(sub, reglas_examinadas)))

    solidas = [r for r in informe["robustez"] if r.get("sobrevive_a_la_correccion")]
    informe["conclusion"] = {
        "reglas_con_roi_positivo_sin_corregir": [r["regla"] for r in candidatas],
        "reglas_que_sobreviven_a_la_correccion": [r["regla"] for r in solidas],
        "hay_indicio": bool(candidatas),
        "hay_prueba": bool(solidas),
        "lectura": (
            "PRUEBA: hay una regla con ROI positivo que sobrevive a la correccion por "
            "multiples comparaciones. Aun asi el Sprint 5 debe reelegir el umbral fuera "
            "de muestra antes de arriesgar dinero."
            if solidas else
            "INDICIO, NO PRUEBA: hay reglas con ROI positivo, pero ninguna sobrevive a "
            "corregir por el numero de umbrales examinados. Justifica ejecutar el "
            "Sprint 5 con protocolo estricto (umbral elegido con datos anteriores al "
            "periodo evaluado); NO justifica arriesgar dinero."
            if candidatas else
            "Ninguna regla produce un ROI distinguible de cero. El margen de la casa se "
            "come la ventaja: no hay negocio en este mercado con este modelo."),
    }

    print(json.dumps(informe, ensure_ascii=False, indent=2))
    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
