"""Que dice lo capturado. Tres preguntas, en orden de importancia.

1. **¿Cuanto cobra Betplay por combinar?** Cada prePack trae su cuota y los ids
   de sus patas, y las patas estan en la misma captura. Comparar la cuota
   ofrecida con el producto de las patas mide el ajuste que aplica la casa.
   Ojo con la lectura: un ratio por debajo de 1 no prueba abuso, porque las
   patas correlacionadas *deben* pagarse por debajo del producto. Lo que prueba
   es que la casa NO multiplica ingenuamente, y por tanto que no hay regalo.

2. **¿Que margen tiene cada mercado?** El overround por mercado dice donde
   cobra poco (mercados vigilados) y donde cobra mucho (mercados de relleno).
   Un margen alto es mala noticia para apostar, pero suele venir acompanado de
   lineas menos afinadas; ahi es donde puede haber algo.

3. **¿Se mueven las lineas?** Con dos capturas o mas del mismo outcome se ve el
   movimiento. Una linea que no se mueve nunca es una linea que nadie vigila.

    python analizar.py
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Any, Dict, List

import numpy as np
import pandas as pd


def cargar(raiz: str, tabla: str) -> pd.DataFrame:
    ficheros = glob.glob(os.path.join(raiz, tabla, "**", "*.parquet"), recursive=True)
    if not ficheros:
        raise SystemExit(f"Sin capturas en {raiz}/{tabla}. Ejecuta capturar.py primero.")
    return pd.concat([pd.read_parquet(f) for f in ficheros], ignore_index=True)


def coste_de_combinar(prepacks: pd.DataFrame, cuotas: pd.DataFrame) -> Dict[str, Any]:
    """Ratio entre la cuota del combinado y el producto de sus patas."""
    # El indice es (captura, outcome) porque una misma pata cambia de precio
    # entre capturas: cruzarlas mezclaria precios de momentos distintos.
    precio = {(r.capturado_en, r.outcome_id): r.cuota
              for r in cuotas.itertuples()}

    filas = []
    for r in prepacks.itertuples():
        ids = [int(x) for x in str(r.patas).split(",") if x]
        precios = [precio.get((r.capturado_en, i)) for i in ids]
        if not precios or any(p is None for p in precios):
            continue   # alguna pata no se capturo: el ratio seria falso
        producto = float(np.prod(precios))
        filas.append({
            "event_id": r.event_id, "liga": r.liga, "n_patas": len(ids),
            "cuota_combinada": r.cuota_combinada, "producto_patas": round(producto, 3),
            "ratio": round(r.cuota_combinada / producto, 4),
            "capturado_en": r.capturado_en,
        })
    if not filas:
        return {"n": 0, "nota": "ningun prepack con todas sus patas en la captura"}

    df = pd.DataFrame(filas)
    por_patas = [{"n_patas": int(k), "n": int(len(g)),
                  "ratio_mediano": round(float(g["ratio"].median()), 4),
                  "ratio_medio": round(float(g["ratio"].mean()), 4)}
                 for k, g in df.groupby("n_patas")]
    return {
        "n": int(len(df)),
        "ratio_mediano": round(float(df["ratio"].median()), 4),
        "ratio_medio": round(float(df["ratio"].mean()), 4),
        "pct_por_debajo_del_producto": round(float((df["ratio"] < 0.99).mean()) * 100, 1),
        "pct_por_encima": round(float((df["ratio"] > 1.01).mean()) * 100, 1),
        "por_numero_de_patas": por_patas,
        "lectura": ("Betplay NO multiplica ingenuamente: ajusta por correlacion. "
                    "El edge por combinar patas correlacionadas no esta disponible aqui."
                    if abs(float(df["ratio"].median()) - 1.0) > 0.03 else
                    "Los combinados salen cerca del producto de sus patas: convendria "
                    "revisar si el ajuste por correlacion es insuficiente."),
        "ejemplos_mas_recortados": df.nsmallest(5, "ratio")[
            ["n_patas", "cuota_combinada", "producto_patas", "ratio"]].to_dict("records"),
        "ejemplos_mas_generosos": df.nlargest(5, "ratio")[
            ["n_patas", "cuota_combinada", "producto_patas", "ratio"]].to_dict("records"),
    }


def margen_por_mercado(cuotas: pd.DataFrame, minimo: int = 30) -> List[Dict[str, Any]]:
    """Overround por mercado: la suma de probabilidades implicitas menos 1.

    Solo tiene sentido en mercados cuyos outcomes forman un conjunto completo y
    excluyente (1X2, over/under). Se agrupa por oferta concreta y se descartan
    las que no suman por encima de 1, que son las que no cumplen esa condicion.
    """
    filas = []
    for (mercado, _), g in cuotas.groupby(["mercado", "bet_offer_id"]):
        if len(g) < 2:
            continue
        suma = float((1.0 / g["cuota"]).sum())
        if not (1.0 < suma < 2.0):
            continue
        filas.append({"mercado": mercado, "overround": suma, "n_outcomes": len(g),
                      "es_jugador": bool(g["es_mercado_de_jugador"].any())})
    if not filas:
        return []
    df = pd.DataFrame(filas)
    salida = []
    for mercado, g in df.groupby("mercado"):
        if len(g) < minimo:
            continue
        salida.append({
            "mercado": mercado, "n_ofertas": int(len(g)),
            "margen_pct": round((float(g["overround"].median()) - 1) * 100, 2),
            "de_jugador": bool(g["es_jugador"].any()),
        })
    return sorted(salida, key=lambda r: -r["margen_pct"])


def movimiento_de_linea(cuotas: pd.DataFrame) -> Dict[str, Any]:
    """Cuanto se mueven las cuotas entre capturas del mismo outcome."""
    capturas = cuotas["capturado_en"].nunique()
    if capturas < 2:
        return {"capturas": int(capturas),
                "nota": ("Hace falta mas de una captura para medir movimiento. "
                         "Es la razon de ser del capturador diario: esto solo se "
                         "acumula hacia adelante.")}
    g = cuotas.groupby("outcome_id")["cuota"]
    agg = g.agg(["count", "min", "max", "first", "last"])
    agg = agg[agg["count"] > 1]
    agg["variacion_pct"] = (agg["max"] - agg["min"]) / agg["min"] * 100
    return {
        "capturas": int(capturas),
        "outcomes_con_historial": int(len(agg)),
        "variacion_mediana_pct": round(float(agg["variacion_pct"].median()), 3),
        "pct_que_no_se_movieron": round(float((agg["variacion_pct"] < 0.01).mean()) * 100, 1),
        "outcomes_mas_volatiles": int((agg["variacion_pct"] > 10).sum()),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--almacen", default="almacen")
    p.add_argument("--informe", default="reports/analisis.json")
    args = p.parse_args()

    cuotas = cargar(args.almacen, "cuotas")
    prepacks = cargar(args.almacen, "prepacks")
    eventos = cargar(args.almacen, "eventos")

    jugador = cuotas[cuotas["es_mercado_de_jugador"]]
    informe = {
        "capturas": int(cuotas["capturado_en"].nunique()),
        "rango": [str(cuotas["capturado_en"].min()), str(cuotas["capturado_en"].max())],
        "partidos": int(eventos["event_id"].nunique()),
        "lineas": int(len(cuotas)),
        "mercados_distintos": int(cuotas["mercado"].nunique()),
        "lineas_de_jugador": int(len(jugador)),
        "jugadores": int(jugador["participante"].nunique()),
        "mercados_de_jugador": sorted(jugador["mercado"].dropna().unique().tolist()),
        "coste_de_combinar": coste_de_combinar(prepacks, cuotas),
        "margen_por_mercado": margen_por_mercado(cuotas)[:15],
        "movimiento_de_linea": movimiento_de_linea(cuotas),
    }
    print(json.dumps(informe, ensure_ascii=False, indent=2, default=str))
    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2, default=str)


if __name__ == "__main__":
    main()
