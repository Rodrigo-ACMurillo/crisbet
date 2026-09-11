"""Captura diaria del feed de cuotas de Betplay.

    python capturar.py                         # ligas principales, partidos proximos
    python capturar.py --todas --max-eventos 400
    python capturar.py --ligas liga_betplay_dimayor premier_league

Escribe en `almacen/`, particionado por fecha de captura:

    eventos/fecha_captura=YYYY-MM-DD/*.parquet
    cuotas/fecha_captura=YYYY-MM-DD/*.parquet
    prepacks/fecha_captura=YYYY-MM-DD/*.parquet

**Es append-only a proposito.** Capturar el mismo partido dos veces en un dia no
es un duplicado que limpiar: es el movimiento de linea, que es justamente lo que
no se puede reconstruir despues. El historico de cuotas de props no existe
gratis en ninguna parte; solo se consigue acumulandolo desde hoy.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from esquema import ahora_utc, fila_evento, filas_cuotas, filas_prepacks
from kambi import KambiClient

try:
    import pandas as pd
except ImportError:
    pd = None

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

# Competiciones con volumen y mercados ricos. Es un punto de partida, no un dogma:
# `--todas` recorre lo que haya abierto.
# Seleccion por defecto: volumen y variedad, no una sola liga. Se identifican
# por (pais, liga_key) porque `liga_key` NO es unico en el feed de Kambi:
# "premier_league" vale para Inglaterra, Rusia, Ucrania, Kazajistan y Jordania.
LIGAS_POR_DEFECTO = [
    ("Inglaterra", "premier_league"), ("Inglaterra", "the_championship"),
    ("Inglaterra", "league_one"), ("Inglaterra", "league_two"),
    ("Espana", "la_liga"), ("Espana", "la_liga_2"),
    ("Italia", "serie_a"), ("Italia", "serie_b"),
    ("Alemania", "bundesliga"), ("Francia", "ligue_1"),
    ("Paises Bajos", "eredivisie"), ("Portugal", "primeira_liga"),
    ("Brasil", "brasileirao_serie_a"), ("Brasil", "brasileirao_serie_b"),
    ("Colombia", "liga_betplay_dimayor"),
    ("Argentina", "liga_profesional_argentina"), ("Argentina", "primera_nacional"),
    ("Mexico", "liga_mx"), ("Estados Unidos", "mls"),
    ("Belgica", "jupiler_pro_league"), ("Suecia", "allsvenskan"),
    ("Peru", "liga_1"), ("Paraguay", "primera_paraguay"),
]


def escribir(filas: List[Dict[str, Any]], raiz: str, tabla: str, sello: str) -> int:
    if not filas:
        return 0
    if pd is None:
        raise SystemExit("pandas/pyarrow necesarios: pip install -r requirements.txt")
    df = pd.DataFrame(filas)
    escritas = 0
    for fecha, grupo in df.groupby("fecha_captura"):
        carpeta = os.path.join(raiz, tabla, f"fecha_captura={fecha}")
        os.makedirs(carpeta, exist_ok=True)
        # El sello en el nombre evita que dos capturas del mismo dia se pisen.
        ruta = os.path.join(carpeta, f"part-{sello}.parquet")
        grupo.to_parquet(ruta, index=False, compression="zstd")
        escritas += len(grupo)
    return escritas


def run(args: argparse.Namespace) -> Dict[str, Any]:
    cliente = KambiClient(retardo=args.retardo)
    capturado_en = ahora_utc()
    sello = capturado_en.replace(":", "").replace("-", "").replace("T", "_").rstrip("Z")

    ligas = cliente.ligas_de_futbol()
    if not ligas:
        raise SystemExit("El feed no devolvio ligas. Revisa conectividad antes de insistir.")
    print(f"{len(ligas)} competiciones de futbol con eventos abiertos")

    if args.todas:
        elegidas = ligas
    elif args.ligas:
        pedidas = set(args.ligas)
        elegidas = [l for l in ligas if l["liga_key"] in pedidas]
        faltan = pedidas - {l["liga_key"] for l in elegidas}
        if faltan:
            print(f"  sin eventos abiertos ahora: {sorted(faltan)}")
    else:
        import unicodedata

        def clave(pais, liga_key):
            p = unicodedata.normalize("NFKD", str(pais or ""))
            return ("".join(c for c in p if not unicodedata.combining(c)).strip(), liga_key)

        queridas = set(LIGAS_POR_DEFECTO)
        elegidas = [l for l in ligas if clave(l["pais"], l["liga_key"]) in queridas]
    print(f"{len(elegidas)} seleccionadas\n")

    # 1) Eventos de cada competicion.
    eventos: List[Dict[str, Any]] = []
    vistos = set()
    for liga in elegidas:
        for ev in cliente.partidos_de_liga(liga["ruta"]):
            datos = ev.get("event", ev)
            eid = datos.get("id")
            if eid is None or eid in vistos:
                continue
            vistos.add(eid)
            fila = fila_evento(datos, capturado_en)
            fila["liga"] = fila["liga"] or liga["liga"]
            fila["liga_key"] = fila["liga_key"] or liga["liga_key"]
            eventos.append(fila)
    eventos.sort(key=lambda e: e.get("inicio_utc") or "")
    if args.max_eventos:
        eventos = eventos[:args.max_eventos]
    print(f"{len(eventos)} partidos a consultar "
          f"(~{len(eventos) * cliente.retardo / 60:.1f} min al ritmo actual)\n")

    # 2) Mercados y prePacks de cada partido.
    cuotas: List[Dict[str, Any]] = []
    prepacks: List[Dict[str, Any]] = []
    sin_datos: List[int] = []
    barra = tqdm(eventos, unit="partido") if tqdm else eventos
    for ev in barra:
        eid = ev["event_id"]
        resp = cliente.cuotas_de_evento(eid)
        if not resp:
            sin_datos.append(eid)
            continue
        cuotas.extend(filas_cuotas(resp, eid, capturado_en, ev.get("liga")))
        prepacks.extend(filas_prepacks(resp, eid, capturado_en, ev.get("liga")))
    if tqdm and hasattr(barra, "close"):
        barra.close()

    escritas = {
        "eventos": escribir(eventos, args.almacen, "eventos", sello),
        "cuotas": escribir(cuotas, args.almacen, "cuotas", sello),
        "prepacks": escribir(prepacks, args.almacen, "prepacks", sello),
    }

    de_jugador = [c for c in cuotas if c["es_mercado_de_jugador"]]
    mercados: Dict[str, int] = {}
    for c in cuotas:
        mercados[c["mercado"]] = mercados.get(c["mercado"], 0) + 1

    informe = {
        "capturado_en": capturado_en,
        "competiciones_disponibles": len(ligas),
        "competiciones_capturadas": len(elegidas),
        "partidos": len(eventos),
        "partidos_sin_datos": len(sin_datos),
        "filas_escritas": escritas,
        "mercados_distintos": len(mercados),
        "lineas_de_jugador": len(de_jugador),
        "jugadores_distintos": len({c["participante"] for c in de_jugador}),
        "mercados_mas_frecuentes": dict(sorted(mercados.items(), key=lambda kv: -kv[1])[:12]),
        "mercados_de_jugador": sorted({c["mercado"] for c in de_jugador}),
        "http": dict(cliente.contadores.por_codigo),
        "peticiones": cliente.contadores.peticiones,
        "reintentos": cliente.contadores.reintentos,
        "errores": cliente.contadores.errores,
        "mb_descargados": round(cliente.contadores.bytes / 1e6, 2),
        "almacen": args.almacen,
    }
    print("\n" + json.dumps(informe, ensure_ascii=False, indent=2))

    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)
    return informe


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Captura diaria de cuotas de Betplay")
    p.add_argument("--almacen", default="almacen")
    p.add_argument("--ligas", nargs="*", help="claves de liga (liga_key) concretas")
    p.add_argument("--todas", action="store_true", help="todas las competiciones abiertas")
    p.add_argument("--max-eventos", type=int, default=150)
    p.add_argument("--retardo", type=float, default=1.0,
                   help="segundos entre peticiones (minimo 0.5)")
    p.add_argument("--informe", default="reports/captura.json")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
