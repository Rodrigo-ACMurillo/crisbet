"""¿Hay edge en alguna parte? Diagnostico sobre las predicciones walk-forward.

El Brier global ya dice que el modelo no bate al mercado en conjunto. Eso no
zanja la pregunta del Sprint 5, que es mas concreta: **cuando el modelo
discrepa del mercado, ¿quien tiene razon?** Un modelo peor de media puede aun
asi aportar informacion en un nicho —cuotas altas, una liga concreta, partidos
con mucho descanso— y ahi es donde viviria el value.

La prueba clave es la de discrepancias: se toman los partidos donde el modelo da
una probabilidad bastante mas alta que el mercado y se mira que paso de verdad.
Si el mercado tiene razon, la frecuencia observada se parece a la del mercado y
no hay nada que rascar. Si la tiene el modelo, hay una veta.

    python diagnostico.py --predicciones predicciones/walkforward.parquet
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, List

import numpy as np
import pandas as pd

RESULTADO_A_COL = {"H": "p1", "D": "px", "A": "p2"}


def _largo(df: pd.DataFrame, modelo: str) -> pd.DataFrame:
    """Una fila por (partido, resultado): asi se comparan los tres desenlaces."""
    trozos = []
    for etiqueta, sufijo in RESULTADO_A_COL.items():
        trozos.append(pd.DataFrame({
            "match_id": df["match_id"].values,
            "liga": df["liga"].values,
            "temporada": df["temporada"].values,
            "resultado": etiqueta,
            "p_modelo": df[f"{modelo}_{sufijo}"].values,
            "p_mercado": df[f"mercado_{sufijo}"].values,
            "ocurrio": (df["y_resultado"] == etiqueta).astype(int).values,
        }))
    return pd.concat(trozos, ignore_index=True)


def brier_por_tramo(df: pd.DataFrame, modelo: str, bins: int = 6) -> List[Dict]:
    """Brier de modelo y mercado segun lo probable que el mercado creia el suceso."""
    largo = _largo(df, modelo)
    largo["tramo"] = pd.cut(largo["p_mercado"], bins=[0, .1, .2, .3, .45, .65, 1.0])
    salida = []
    for tramo, g in largo.groupby("tramo", observed=True):
        if len(g) < 100:
            continue
        salida.append({
            "tramo_prob_mercado": str(tramo),
            "cuota_equivalente": f"{1/tramo.right:.2f}-{1/max(tramo.left,1e-6):.2f}"
                                 if tramo.left > 0 else f">{1/tramo.right:.2f}",
            "n": int(len(g)),
            "brier_modelo": round(float(((g["p_modelo"] - g["ocurrio"]) ** 2).mean()), 5),
            "brier_mercado": round(float(((g["p_mercado"] - g["ocurrio"]) ** 2).mean()), 5),
        })
    for fila in salida:
        fila["diferencia"] = round(fila["brier_modelo"] - fila["brier_mercado"], 5)
        fila["modelo_mejor"] = fila["diferencia"] < 0
    return salida


def discrepancias(df: pd.DataFrame, modelo: str, umbrales=(0.03, 0.05, 0.08)) -> List[Dict]:
    """La prueba que decide: cuando el modelo dice mas que el mercado, ¿acierta?

    Se compara la frecuencia real con lo que decia cada uno. El que este mas
    cerca de lo observado es el que tenia razon en esas situaciones.
    """
    largo = _largo(df, modelo)
    largo["exceso"] = largo["p_modelo"] - largo["p_mercado"]
    salida = []
    for umbral in umbrales:
        for direccion, mascara in (("modelo_mas_alto", largo["exceso"] > umbral),
                                   ("modelo_mas_bajo", largo["exceso"] < -umbral)):
            g = largo[mascara]
            if len(g) < 100:
                continue
            observado = float(g["ocurrio"].mean())
            dice_modelo = float(g["p_modelo"].mean())
            dice_mercado = float(g["p_mercado"].mean())
            salida.append({
                "umbral": umbral,
                "direccion": direccion,
                "n": int(len(g)),
                "dice_el_modelo": round(dice_modelo, 4),
                "dice_el_mercado": round(dice_mercado, 4),
                "ocurrio_de_verdad": round(observado, 4),
                "error_modelo": round(abs(dice_modelo - observado), 4),
                "error_mercado": round(abs(dice_mercado - observado), 4),
                "acierta": ("modelo" if abs(dice_modelo - observado)
                            < abs(dice_mercado - observado) else "mercado"),
            })
    return salida


def por_liga(df: pd.DataFrame, modelo: str) -> List[Dict]:
    salida = []
    for liga, g in df.groupby("liga"):
        largo = _largo(g, modelo)
        bm = float(((largo["p_modelo"] - largo["ocurrio"]) ** 2).mean())
        bmk = float(((largo["p_mercado"] - largo["ocurrio"]) ** 2).mean())
        salida.append({
            "liga": liga, "n_partidos": int(len(g)),
            "brier_modelo": round(bm, 5), "brier_mercado": round(bmk, 5),
            "diferencia": round(bm - bmk, 5), "modelo_mejor": bm < bmk,
        })
    return sorted(salida, key=lambda r: r["diferencia"])


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--predicciones", default="predicciones/walkforward.parquet")
    p.add_argument("--modelo", default="ensamble_calibrado")
    p.add_argument("--informe", default="reports/sprint4_diagnostico.json")
    args = p.parse_args()

    df = pd.read_parquet(args.predicciones)
    informe = {
        "modelo_analizado": args.modelo,
        "n_partidos": int(len(df)),
        "ligas": sorted(df["liga"].unique().tolist()),
        "brier_por_tramo_de_probabilidad": brier_por_tramo(df, args.modelo),
        "prueba_de_discrepancias": discrepancias(df, args.modelo),
        "por_liga": por_liga(df, args.modelo),
    }

    tramos_ganados = [t for t in informe["brier_por_tramo_de_probabilidad"] if t["modelo_mejor"]]
    ligas_ganadas = [l for l in informe["por_liga"] if l["modelo_mejor"]]
    discrepancias_ganadas = [d for d in informe["prueba_de_discrepancias"]
                             if d["acierta"] == "modelo"]
    informe["conclusion"] = {
        "tramos_donde_el_modelo_gana": [t["tramo_prob_mercado"] for t in tramos_ganados],
        "ligas_donde_el_modelo_gana": [l["liga"] for l in ligas_ganadas],
        "discrepancias_a_favor_del_modelo": len(discrepancias_ganadas),
        "discrepancias_totales": len(informe["prueba_de_discrepancias"]),
        "hay_veta": bool(tramos_ganados or ligas_ganadas or discrepancias_ganadas),
    }
    informe["conclusion"]["lectura"] = (
        "Hay al menos un nicho donde el modelo aporta informacion que el mercado no "
        "tiene. El Sprint 5 debe restringir el selector a ese nicho y medir si el "
        "margen sobrevive a la comision."
        if informe["conclusion"]["hay_veta"] else
        "El mercado gana en todos los cortes examinados. No hay donde buscar value "
        "con este modelo: el Sprint 5 no debe arrancar hasta mejorar el Sprint 4."
    )

    print(json.dumps(informe, ensure_ascii=False, indent=2))
    if args.informe:
        import os
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
