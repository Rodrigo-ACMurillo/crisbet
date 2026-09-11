"""Libro de tickets en papel: registrar lo emitido y liquidarlo con resultados.

Esto es lo que convierte el proyecto de "el modelo promete un EV" a "el modelo
devolvio esto". Sin este registro, cada ejecucion del selector produce un numero
optimista que nadie comprueba nunca.

Dos reglas que hacen honesto el ejercicio:

**Se registra ANTES de conocer el resultado.** El fichero guarda la cuota y la
probabilidad del modelo en el instante de emitir. Reconstruirlo despues, con los
resultados a la vista, permitiria elegir sin querer los tickets que salieron
bien.

**Un ticket no se re-registra.** Su identificador sale del contenido (partidos,
mercados, selecciones, cuotas), asi que volver a ejecutar el selector no duplica
apuestas ni permite "mejorar" una emision anterior.

    python registro.py anotar --tickets ../model/reports/tickets.json
    python registro.py liquidar
    python registro.py resumen
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from liquidacion import Marcador, liquidar_ticket
from resultados import ResultadosFootballData

try:
    import pandas as pd
except ImportError:
    pd = None

LIBRO = "libro/tickets.parquet"


def ahora() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def id_de_ticket(patas: List[Dict[str, Any]]) -> str:
    """Identificador determinista: el mismo ticket no se registra dos veces."""
    clave = "|".join(sorted(
        f"{p['event_id']}:{p['mercado']}:{p['seleccion']}:{p.get('linea')}:{p['cuota']}"
        for p in patas))
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()[:20]


def anotar(ruta_tickets: str, libro: str, importe: float = 1.0) -> Dict[str, Any]:
    if pd is None:
        raise SystemExit("pandas/pyarrow necesarios")
    with open(ruta_tickets, "r", encoding="utf-8") as fh:
        informe = json.load(fh)
    tickets = informe.get("tickets", [])
    if not tickets:
        raise SystemExit("El informe no trae tickets")

    existentes = set()
    if os.path.exists(libro):
        existentes = set(pd.read_parquet(libro)["ticket_id"])

    filas = []
    for t in tickets:
        tid = id_de_ticket(t["patas"])
        if tid in existentes:
            continue
        filas.append({
            "ticket_id": tid,
            "emitido_en": ahora(),
            "cuota": t["cuota_combinada"],
            "prob_modelo": t["probabilidad_real_pct"] / 100.0,
            "ev_prometido": t["ev_pct"] / 100.0,
            "n_patas": t["n_patas"],
            "importe": importe,
            "estado": "pendiente",
            "pago": None,
            "retorno": None,
            "patas_json": json.dumps(t["patas"], ensure_ascii=False),
            "primer_inicio": min(p["inicio"] for p in t["patas"]),
            "ultimo_inicio": max(p["inicio"] for p in t["patas"]),
        })

    if filas:
        os.makedirs(os.path.dirname(os.path.abspath(libro)) or ".", exist_ok=True)
        nuevo = pd.DataFrame(filas)
        if os.path.exists(libro):
            nuevo = pd.concat([pd.read_parquet(libro), nuevo], ignore_index=True)
        nuevo.to_parquet(libro, index=False, compression="zstd")

    return {"tickets_en_el_informe": len(tickets), "anotados": len(filas),
            "ya_registrados": len(tickets) - len(filas), "libro": libro}


def liquidar(libro: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Busca resultados de los tickets pendientes y los liquida."""
    if pd is None or not os.path.exists(libro):
        raise SystemExit(f"No hay libro en {libro}. Anota tickets primero.")
    df = pd.read_parquet(libro)
    pendientes = df[df["estado"] == "pendiente"]
    if pendientes.empty:
        return {"pendientes": 0, "liquidados": 0}

    fuente = ResultadosFootballData(token)
    if not fuente.disponible():
        raise SystemExit("Falta FOOTBALL_DATA_TOKEN para consultar resultados")

    # Se piden los resultados por partido, no por ticket: varios tickets
    # comparten partidos y no tiene sentido consultar dos veces.
    necesarios: Dict[int, Dict[str, Any]] = {}
    for _, fila in pendientes.iterrows():
        for p in json.loads(fila["patas_json"]):
            necesarios[p["event_id"]] = {"partido": p["partido"], "liga": p["liga"],
                                         "inicio": p["inicio"]}
    marcadores = fuente.marcadores(necesarios)
    print(f"  resultados encontrados: {len(marcadores)} de {len(necesarios)} partidos")

    liquidados = 0
    for idx, fila in pendientes.iterrows():
        patas = json.loads(fila["patas_json"])
        r = liquidar_ticket(patas, marcadores)
        if r is None:
            continue
        df.at[idx, "estado"] = "liquidado"
        df.at[idx, "pago"] = r["pago"]
        df.at[idx, "retorno"] = r["retorno"] * fila["importe"]
        liquidados += 1

    df.to_parquet(libro, index=False, compression="zstd")
    return {"pendientes": int(len(pendientes)), "liquidados": liquidados,
            "siguen_pendientes": int(len(pendientes)) - liquidados}


def resumen(libro: str) -> Dict[str, Any]:
    """La unica cifra que importa: ROI real frente a EV prometido."""
    if pd is None or not os.path.exists(libro):
        raise SystemExit(f"No hay libro en {libro}")
    df = pd.read_parquet(libro)
    liq = df[df["estado"] == "liquidado"]
    salida: Dict[str, Any] = {
        "tickets_registrados": int(len(df)),
        "liquidados": int(len(liq)),
        "pendientes": int((df["estado"] == "pendiente").sum()),
    }
    if liq.empty:
        salida["lectura"] = ("Aun no hay tickets liquidados. El ROI real solo existe "
                             "cuando se juegan los partidos: no hay atajo.")
        return salida

    importe = float(liq["importe"].sum())
    retorno = float(liq["retorno"].sum())
    roi = retorno / importe if importe else 0.0
    ev_medio = float(liq["ev_prometido"].mean())
    aciertos = int((liq["pago"] > 1.0).sum())
    # Error estandar del ROI: sin el, 10 tickets no dicen nada.
    ee = float(liq["retorno"].std(ddof=1) / (len(liq) ** 0.5)) if len(liq) > 1 else float("nan")

    salida.update({
        "importe_arriesgado": round(importe, 2),
        "retorno_neto": round(retorno, 2),
        "roi_real_pct": round(roi * 100, 2),
        "ev_prometido_medio_pct": round(ev_medio * 100, 2),
        "diferencia_pct": round((roi - ev_medio) * 100, 2),
        "tickets_acertados": aciertos,
        "tasa_de_acierto_pct": round(aciertos / len(liq) * 100, 2),
        "prob_media_del_modelo_pct": round(float(liq["prob_modelo"].mean()) * 100, 2),
        "error_estandar_roi_pct": round(ee * 100, 2) if ee == ee else None,
        "ic95_roi_pct": ([round((roi - 1.96 * ee) * 100, 2), round((roi + 1.96 * ee) * 100, 2)]
                         if ee == ee else None),
    })
    n = len(liq)
    salida["lectura"] = (
        f"Con {n} tickets liquidados la cifra todavia no decide nada: hacen falta "
        "cientos antes de distinguir un ROI real del azar."
        if n < 100 else
        ("El ROI real esta por debajo del EV prometido: el modelo sobreestima su ventaja."
         if roi < ev_medio else
         "El ROI real acompana al EV prometido."))
    return salida


def main() -> None:
    p = argparse.ArgumentParser(description="Libro de tickets en papel")
    p.add_argument("accion", choices=("anotar", "liquidar", "resumen"))
    p.add_argument("--tickets", default="../model/reports/tickets.json")
    p.add_argument("--libro", default=LIBRO)
    p.add_argument("--importe", type=float, default=1.0)
    args = p.parse_args()

    if args.accion == "anotar":
        print(json.dumps(anotar(args.tickets, args.libro, args.importe),
                         ensure_ascii=False, indent=2))
    elif args.accion == "liquidar":
        print(json.dumps(liquidar(args.libro), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(resumen(args.libro), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
