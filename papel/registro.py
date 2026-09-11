"""Libro de tickets en papel: registrar lo emitido y liquidarlo con resultados.

Esto es lo que convierte el proyecto de "el modelo promete un EV" a "el modelo
devolvio esto". Sin este registro, cada ejecucion del selector produce un numero
optimista que nadie comprueba nunca.

**El libro vive en el repositorio, en JSONL.** Antes vivia en una cache de
GitHub Actions, que caduca a los 7 dias sin uso: dos semanas de parada y se
perdia el historial entero — justo lo unico capaz de validar el sistema.
Guardarlo en el repo lo hace duradero, versionado y auditable: cada commit deja
constancia de que se emitio y cuando, y reescribir el pasado exige un commit que
queda a la vista.

JSON por lineas y no parquet porque git versiona texto: un ticket nuevo es una
linea nueva en el diff, no un binario entero que cambia. A 5 tickets diarios son
~1.800 lineas al ano, menos de 1 MB.

Dos reglas que hacen honesto el ejercicio:

**Se registra ANTES de conocer el resultado.** El libro guarda la cuota y la
probabilidad del modelo en el instante de emitir. Reconstruirlo despues, con los
resultados a la vista, permitiria elegir sin querer los tickets que salieron bien.

**Un ticket no se re-registra.** Su identificador sale del contenido, asi que
volver a ejecutar el selector no duplica apuestas ni permite "mejorar" una
emision anterior.

    python registro.py anotar --tickets ../model/reports/tickets.json
    python registro.py liquidar
    python registro.py resumen
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from liquidacion import liquidar_ticket
from resultados import ResultadosFootballData

LIBRO = "libro/tickets.jsonl"


def ahora() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def id_de_ticket(patas: List[Dict[str, Any]]) -> str:
    """Identificador determinista: el mismo ticket no se registra dos veces."""
    clave = "|".join(sorted(
        f"{p['event_id']}:{p['mercado']}:{p['seleccion']}:{p.get('linea')}:{p['cuota']}"
        for p in patas))
    return hashlib.sha256(clave.encode("utf-8")).hexdigest()[:20]


def leer_libro(libro: str) -> List[Dict[str, Any]]:
    """Lee el libro. Una linea ilegible no puede tumbar el historial entero."""
    if not os.path.exists(libro):
        return []
    filas: List[Dict[str, Any]] = []
    with open(libro, encoding="utf-8") as fh:
        for n, linea in enumerate(fh, 1):
            linea = linea.strip()
            if not linea:
                continue
            try:
                filas.append(json.loads(linea))
            except ValueError:
                print(f"  aviso: linea {n} ilegible, se omite del calculo")
    return filas


def escribir_libro(libro: str, filas: List[Dict[str, Any]]) -> None:
    """Escritura atomica: si el proceso muere a mitad, el libro viejo sobrevive."""
    os.makedirs(os.path.dirname(os.path.abspath(libro)) or ".", exist_ok=True)
    tmp = libro + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        for fila in filas:
            fh.write(json.dumps(fila, ensure_ascii=False))
            fh.write("\n")
    os.replace(tmp, libro)


def anotar(ruta_tickets: str, libro: str, importe: float = 1.0) -> Dict[str, Any]:
    with open(ruta_tickets, encoding="utf-8") as fh:
        informe = json.load(fh)
    tickets = informe.get("tickets", [])
    if not tickets:
        raise SystemExit("El informe no trae tickets")

    libro_actual = leer_libro(libro)
    existentes = {f["ticket_id"] for f in libro_actual}

    nuevos = []
    for t in tickets:
        tid = id_de_ticket(t["patas"])
        if tid in existentes:
            continue
        nuevos.append({
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
            "patas": t["patas"],
            "primer_inicio": min(p["inicio"] for p in t["patas"]),
            "ultimo_inicio": max(p["inicio"] for p in t["patas"]),
        })

    if nuevos:
        escribir_libro(libro, libro_actual + nuevos)

    return {"tickets_en_el_informe": len(tickets), "anotados": len(nuevos),
            "ya_registrados": len(tickets) - len(nuevos), "libro": libro}


def liquidar(libro: str, token: Optional[str] = None) -> Dict[str, Any]:
    """Busca resultados de los tickets pendientes y los liquida."""
    filas = leer_libro(libro)
    if not filas:
        raise SystemExit(f"No hay libro en {libro}. Anota tickets primero.")
    pendientes = [f for f in filas if f["estado"] == "pendiente"]
    if not pendientes:
        return {"pendientes": 0, "liquidados": 0}

    fuente = ResultadosFootballData(token)
    if not fuente.disponible():
        raise SystemExit("Falta FOOTBALL_DATA_TOKEN para consultar resultados")

    # Se piden los resultados por partido, no por ticket: varios tickets
    # comparten partidos y no tiene sentido consultar dos veces.
    necesarios: Dict[int, Dict[str, Any]] = {}
    for fila in pendientes:
        for p in fila["patas"]:
            necesarios[p["event_id"]] = {"partido": p["partido"], "liga": p["liga"],
                                         "inicio": p["inicio"]}
    marcadores = fuente.marcadores(necesarios)
    print(f"  resultados encontrados: {len(marcadores)} de {len(necesarios)} partidos")

    liquidados = 0
    for fila in filas:
        if fila["estado"] != "pendiente":
            continue
        r = liquidar_ticket(fila["patas"], marcadores)
        if r is None:
            continue
        fila["estado"] = "liquidado"
        fila["liquidado_en"] = ahora()
        fila["pago"] = r["pago"]
        fila["retorno"] = round(r["retorno"] * fila["importe"], 4)
        liquidados += 1

    if liquidados:
        escribir_libro(libro, filas)
    return {"pendientes": len(pendientes), "liquidados": liquidados,
            "siguen_pendientes": len(pendientes) - liquidados}


def resumen(libro: str) -> Dict[str, Any]:
    """La unica cifra que importa: ROI real frente a EV prometido."""
    filas = leer_libro(libro)
    liq = [f for f in filas if f["estado"] == "liquidado"]
    salida: Dict[str, Any] = {
        "tickets_registrados": len(filas),
        "liquidados": len(liq),
        "pendientes": sum(1 for f in filas if f["estado"] == "pendiente"),
    }
    if not liq:
        salida["lectura"] = ("Aun no hay tickets liquidados. El ROI real solo existe "
                             "cuando se juegan los partidos: no hay atajo.")
        return salida

    importe = sum(f["importe"] for f in liq)
    retorno = sum(f["retorno"] for f in liq)
    roi = retorno / importe if importe else 0.0
    ev_medio = sum(f["ev_prometido"] for f in liq) / len(liq)
    aciertos = sum(1 for f in liq if f["pago"] > 1.0)

    # Error estandar del ROI: sin el, 10 tickets no dicen nada.
    ee = float("nan")
    if len(liq) > 1:
        media = retorno / len(liq)
        var = sum((f["retorno"] - media) ** 2 for f in liq) / (len(liq) - 1)
        ee = math.sqrt(var) / math.sqrt(len(liq))

    salida.update({
        "importe_arriesgado": round(importe, 2),
        "retorno_neto": round(retorno, 2),
        "roi_real_pct": round(roi * 100, 2),
        "ev_prometido_medio_pct": round(ev_medio * 100, 2),
        "diferencia_pct": round((roi - ev_medio) * 100, 2),
        "tickets_acertados": aciertos,
        "tasa_de_acierto_pct": round(aciertos / len(liq) * 100, 2),
        "prob_media_del_modelo_pct": round(
            sum(f["prob_modelo"] for f in liq) / len(liq) * 100, 2),
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
