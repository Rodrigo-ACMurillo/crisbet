"""Sprint 3: construye el almacen canonico de partidos y el feature store.

    python build.py --ligas E0 SP1 I1 D1 F1 --desde 2019-20 --hasta 2024-25

Produce, bajo `warehouse/`:
    matches/temporada=YYYY-YY/*.parquet    partidos canonicos
    odds/temporada=YYYY-YY/*.parquet       cuotas de cierre por mercado
    features/temporada=YYYY-YY/*.parquet   una fila por partido, punto-en-el-tiempo

Las features se construyen sobre el historico COMPLETO y en orden cronologico,
no temporada a temporada: el Elo y la forma de agosto dependen de mayo anterior.
Particionar la escritura no altera ese orden — solo dice en que fichero cae
cada fila ya calculada.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Sequence

from canonical import LIGAS, Match, OddsCierre
from features import columnas_entrada, construir, ordenar_cronologico
from providers import obtener_proveedor

try:
    import pandas as pd
except ImportError:
    pd = None


def temporadas_entre(desde: str, hasta: str) -> List[str]:
    """'2019-20' .. '2024-25' -> lista de temporadas intermedias."""
    inicio, fin = int(desde.split("-")[0]), int(hasta.split("-")[0])
    return [f"{a}-{str(a + 1)[2:]}" for a in range(inicio, fin + 1)]


def escribir(filas: Sequence[Dict[str, Any]], raiz: str, dataset: str,
             particion: str = "temporada") -> int:
    if not filas:
        return 0
    if pd is None:
        raise SystemExit("pandas/pyarrow son necesarios: pip install -r requirements.txt")
    df = pd.DataFrame(list(filas))
    escritas = 0
    for valor, grupo in df.groupby(particion):
        carpeta = os.path.join(raiz, dataset, f"{particion}={valor}")
        os.makedirs(carpeta, exist_ok=True)
        grupo.to_parquet(os.path.join(carpeta, "part-000.parquet"),
                         index=False, compression="zstd")
        escritas += len(grupo)
    return escritas


def informe_mercado(cuotas: Sequence[OddsCierre]) -> Dict[str, Any]:
    """Overround por casa: el margen que hay que batir antes de ganar un peso.

    Pinnacle suele salir cerca de 1.02-1.03 y las casas generalistas por encima
    de 1.06. Esa diferencia es, literalmente, el coste de operar en cada una.
    """
    por_casa: Dict[str, List[float]] = {}
    for cuota in cuotas:
        if cuota.mercado != "1x2":
            continue
        valor = cuota.calcular_overround()
        if valor:
            por_casa.setdefault(cuota.casa, []).append(valor)
    salida = {}
    for casa, valores in sorted(por_casa.items()):
        medio = sum(valores) / len(valores)
        entrada = {
            "n": len(valores),
            "overround_medio": round(medio, 5),
            "margen_pct": round((medio - 1) * 100, 3),
        }
        if medio < 1.0:
            # No es un error de calculo: "mejor_disponible" toma el precio mas
            # alto de cada resultado entre TODAS las casas, y esa combinacion no
            # existe en ninguna sola. Como referencia sirve; como precio operable,
            # no: exige tener cuenta abierta en todas y que la cuota aguante.
            entrada["aviso"] = ("overround < 1: precio compuesto entre casas, "
                                "no operable en una sola")
        salida[casa] = entrada
    return salida


def benchmark_mercado(partidos: Sequence[Match], cuotas: Sequence[OddsCierre]) -> Dict[str, Any]:
    """Cuanto acierta el propio mercado. Es el piso contra el que se compara todo.

    Si el modelo del Sprint 4 no mejora el Brier de las cuotas de cierre, no
    tiene edge: tiene ruido.
    """
    resultado_de = {p.match_id: p.resultado for p in partidos if p.jugado}
    aciertos = n = 0
    brier_total = 0.0
    for cuota in cuotas:
        if cuota.mercado != "1x2" or cuota.casa != "pinnacle":
            continue
        real = resultado_de.get(cuota.match_id)
        if not real:
            continue
        probs = cuota.probabilidades_implicitas()
        if len(probs) != 3:
            continue
        favorito = max(probs, key=probs.get)
        etiqueta = {"H": "1", "D": "X", "A": "2"}[real]
        aciertos += int(favorito == etiqueta)
        # Brier multiclase: suma de (p - indicador)^2 sobre los tres resultados.
        brier_total += sum((probs[k] - (1.0 if k == etiqueta else 0.0)) ** 2 for k in probs)
        n += 1
    if not n:
        return {"n": 0}
    suma = brier_total / n
    return {
        "n": n,
        "casa": "pinnacle (cuota de cierre)",
        "acierto_favorito": round(aciertos / n, 4),
        # Dos convenciones, porque confundirlas invalida el go/no-go del Sprint 5.
        # La suma sobre los 3 resultados va de 0 a 2; el promedio por clase, de 0
        # a 0.667. El objetivo "< 0.21" del plan solo tiene sentido en la segunda.
        "brier_multiclase_suma": round(suma, 4),
        "brier_medio_por_clase": round(suma / 3.0, 4),
        "nota": ("Piso de referencia: esto es lo que acierta el propio mercado. "
                 "El modelo del Sprint 4 debe batir este Brier sobre el mismo "
                 "conjunto y con la misma convencion."),
    }


def cobertura_features(filas: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Que porcentaje de cada feature viene informado. Un hueco silencioso es
    una feature que el modelo aprendera a ignorar sin que nadie se entere."""
    if not filas:
        return {}
    columnas = columnas_entrada(filas[0])
    total = len(filas)
    cobertura = {
        col: round(sum(1 for f in filas if f.get(col) is not None) / total, 4)
        for col in columnas
    }
    vacias = [c for c, v in cobertura.items() if v == 0.0]
    return {
        "n_features": len(columnas),
        "filas": total,
        "fiables_pct": round(sum(f["fiable"] for f in filas) / total, 4),
        "peor_cobertura": dict(sorted(cobertura.items(), key=lambda kv: kv[1])[:6]),
        "features_siempre_vacias": vacias,
    }


def run(args: argparse.Namespace) -> Dict[str, Any]:
    proveedor = obtener_proveedor(args.proveedor, cache_dir=args.cache)
    temporadas = temporadas_entre(args.desde, args.hasta)

    partidos: List[Match] = []
    cuotas: List[OddsCierre] = []
    fallos: List[str] = []
    for liga in args.ligas:
        for temporada in temporadas:
            p, c = proveedor.partidos(liga, temporada)
            if not p:
                fallos.append(f"{liga}/{temporada}")
                continue
            partidos.extend(p)
            cuotas.extend(c)
            print(f"  {liga} {temporada}: {len(p)} partidos, {len(c)} lineas de cuota")

    if not partidos:
        raise SystemExit("Ningun partido descargado. Revisa ligas y temporadas.")

    # Un mismo partido no puede entrar dos veces (temporadas solapadas, reejecuciones).
    unicos: Dict[str, Match] = {}
    for partido in partidos:
        unicos[partido.match_id] = partido
    duplicados = len(partidos) - len(unicos)
    partidos = ordenar_cronologico(list(unicos.values()))

    print(f"\nConstruyendo features sobre {len(partidos)} partidos en orden cronologico...")
    filas = construir(partidos)

    escritos = {
        "matches": escribir([p.to_row() for p in partidos], args.warehouse, "matches"),
        "odds": escribir([c.to_row() for c in cuotas], args.warehouse, "odds",
                         particion="fecha") if args.particion_odds_por_fecha else
                escribir([dict(c.to_row(), temporada=unicos[c.match_id].temporada)
                          for c in cuotas if c.match_id in unicos],
                         args.warehouse, "odds"),
        "features": escribir(filas, args.warehouse, "features"),
    }

    informe = {
        "sprint": 3,
        "proveedor": proveedor.nombre,
        "ligas": args.ligas,
        "temporadas": temporadas,
        "descargas_fallidas": fallos,
        "partidos": len(partidos),
        "partidos_duplicados_descartados": duplicados,
        "lineas_de_cuota": len(cuotas),
        "equipos_distintos": len({p.equipo_local for p in partidos} |
                                 {p.equipo_visitante for p in partidos}),
        "rango_fechas": [partidos[0].kickoff_utc[:10], partidos[-1].kickoff_utc[:10]],
        "filas_escritas": escritos,
        "overround_por_casa": informe_mercado(cuotas),
        "benchmark_mercado": benchmark_mercado(partidos, cuotas),
        "cobertura_features": cobertura_features(filas),
        "feature_version": filas[0]["feature_version"] if filas else None,
    }
    print("\n" + json.dumps(informe, ensure_ascii=False, indent=2))
    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)
    return informe


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sprint 3: datos canonicos y feature store")
    p.add_argument("--ligas", nargs="+", default=["E0", "SP1", "I1", "D1", "F1"],
                   choices=list(LIGAS.keys()))
    p.add_argument("--desde", default="2019-20")
    p.add_argument("--hasta", default="2024-25")
    p.add_argument("--proveedor", default="football-data")
    p.add_argument("--warehouse", default="warehouse")
    p.add_argument("--cache", default="cache_csv")
    p.add_argument("--informe", default="reports/sprint3_featurestore.json")
    p.add_argument("--particion-odds-por-fecha", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
