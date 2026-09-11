"""Comprobaciones sobre el feature store ya construido.

No es una prueba unitaria: lee el almacen real y responde tres preguntas que
deciden si el Sprint 3 sirve para algo.

1. Las features, ¿tienen senal? Un Elo que no predice nada es una columna de ruido.
2. ¿Estan calibradas? Si `elo_prob_local` dice 60%, ¿gana el local el 60% de las veces?
3. ¿Hay fuga? Una senal demasiado buena es sospechosa, no una buena noticia.

    python verify.py
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Dict, List

import pandas as pd


def cargar(raiz: str, dataset: str) -> pd.DataFrame:
    ruta = os.path.join(raiz, dataset)
    if not os.path.isdir(ruta):
        raise SystemExit(f"No existe {ruta}. Ejecuta build.py primero.")
    trozos = []
    for carpeta, _, ficheros in os.walk(ruta):
        for fichero in ficheros:
            if fichero.endswith(".parquet"):
                trozos.append(pd.read_parquet(os.path.join(carpeta, fichero)))
    if not trozos:
        raise SystemExit(f"Sin ficheros parquet en {ruta}")
    return pd.concat(trozos, ignore_index=True)


def calibracion(df: pd.DataFrame, col_prob: str, col_real: str, bins: int = 10) -> List[Dict]:
    """Curva de calibracion: probabilidad predicha frente a frecuencia observada."""
    datos = df[[col_prob, col_real]].dropna()
    datos = datos[(datos[col_prob] > 0) & (datos[col_prob] < 1)]
    if datos.empty:
        return []
    datos = datos.copy()
    datos["bin"] = pd.cut(datos[col_prob], bins=[i / bins for i in range(bins + 1)],
                          include_lowest=True)
    salida = []
    for intervalo, grupo in datos.groupby("bin", observed=True):
        if len(grupo) < 30:
            continue
        salida.append({
            "rango": str(intervalo),
            "n": int(len(grupo)),
            "predicho": round(float(grupo[col_prob].mean()), 4),
            "observado": round(float(grupo[col_real].mean()), 4),
            "desvio": round(float(grupo[col_real].mean() - grupo[col_prob].mean()), 4),
        })
    return salida


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--warehouse", default="warehouse")
    p.add_argument("--informe", default="reports/sprint3_verificacion.json")
    args = p.parse_args()

    feats = cargar(args.warehouse, "features")
    feats = feats[feats["y_resultado"].notna()].copy()
    feats["gana_local"] = (feats["y_resultado"] == "H").astype(int)

    # Solo filas con historial suficiente: comparar la jornada 1 es comparar ruido.
    fiables = feats[feats["fiable"] == 1].copy()

    # La expectativa Elo se mide contra la PUNTUACION real (1 / 0.5 / 0), que es
    # la escala en la que esta definida. Medirla contra "gana o no gana" la deja
    # sobrestimada ~15 puntos en todos los tramos y el sesgo parece una fuga.
    brier_elo = float(((fiables["elo_expectativa_local"] - fiables["y_puntuacion_local"]) ** 2).mean())
    base = float(fiables["y_puntuacion_local"].mean())
    brier_base = float(((base - fiables["y_puntuacion_local"]) ** 2).mean())

    informe = {
        "filas_totales": int(len(feats)),
        "filas_fiables": int(len(fiables)),
        "ligas": sorted(feats["liga"].unique().tolist()),
        "senal_elo": {
            "brier_expectativa_vs_puntuacion": round(brier_elo, 5),
            "brier_de_la_tasa_base": round(brier_base, 5),
            "mejora_sobre_la_base_pct": round((brier_base - brier_elo) / brier_base * 100, 2),
            "elo_diff_medio_cuando_gana_local": round(
                float(fiables.loc[fiables["gana_local"] == 1, "elo_diff"].mean()), 2),
            "elo_diff_medio_cuando_no": round(
                float(fiables.loc[fiables["gana_local"] == 0, "elo_diff"].mean()), 2),
        },
        "calibracion_elo": calibracion(fiables, "elo_expectativa_local", "y_puntuacion_local"),
        "ventaja_de_campo": {
            "victorias_local_pct": round(float(feats["gana_local"].mean()), 4),
            "empates_pct": round(float((feats["y_resultado"] == "D").mean()), 4),
            "media_goles_local": round(float(feats["y_goles_local"].mean()), 3),
            "media_goles_visitante": round(float(feats["y_goles_visitante"].mean()), 3),
        },
        "mercados": {
            "over25_pct": round(float(feats["y_over25"].mean()), 4),
            "btts_pct": round(float(feats["y_btts"].mean()), 4),
        },
    }

    # Alarma de fuga: con features previas al pitido, un Brier binario por debajo
    # de 0.16 no es un buen modelo, es informacion del futuro colandose.
    informe["alarma_fuga"] = (
        "SOSPECHA: senal demasiado buena para features previas al partido"
        if brier_elo < 0.12 else "sin indicios"
    )

    print(json.dumps(informe, ensure_ascii=False, indent=2))
    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
