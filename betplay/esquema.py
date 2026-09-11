"""Aplanado del JSON de Kambi a filas tabulares, con procedencia.

Mismo principio que el Sprint 2: cada fila guarda de donde salio y cuando se
capturo. Aqui el `capturado_en` no es burocracia — es la dimension que hace util
todo el almacen. Una cuota sin instante de captura no sirve para nada: el
producto entero depende de comparar el mismo mercado en dos momentos.

Tres tablas:

`eventos`    un partido, con su competicion y hora de comienzo
`cuotas`     una fila por outcome (la unidad apostable)
`prepacks`   los combinados del Bet Builder, con las patas que los componen

Las cuotas de Kambi vienen en milesimas (2380 = 2.38). Convertirlas al leer y no
al usar evita el error de multiplicar por mil en algun sitio y no enterarse.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Dict, List, Optional

VERSION_ESQUEMA = "1.0.0"


def ahora_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cuota(milesimas: Optional[int]) -> Optional[float]:
    """Kambi expresa las cuotas en milesimas enteras."""
    if milesimas is None:
        return None
    try:
        valor = float(milesimas) / 1000.0
    except (TypeError, ValueError):
        return None
    return valor if valor > 1.0 else None


def _linea(valor: Optional[int]) -> Optional[float]:
    """Las lineas (2.5 goles, handicaps) tambien vienen en milesimas."""
    if valor is None:
        return None
    try:
        return float(valor) / 1000.0
    except (TypeError, ValueError):
        return None


def fila_evento(ev: Dict[str, Any], capturado_en: str) -> Dict[str, Any]:
    ruta = ev.get("path") or []
    return {
        "event_id": ev.get("id"),
        "nombre": ev.get("name"),
        "inicio_utc": ev.get("start"),
        "deporte": next((p.get("englishName") for p in ruta if p.get("termKey") == "football"),
                        ruta[0].get("englishName") if ruta else None),
        "pais": ruta[1].get("name") if len(ruta) > 1 else None,
        "liga": ev.get("group") or (ruta[2].get("name") if len(ruta) > 2 else None),
        "liga_key": ruta[2].get("termKey") if len(ruta) > 2 else None,
        "local": (ev.get("homeName") or (ev.get("name") or "").split(" - ")[0].strip()),
        "visitante": (ev.get("awayName")
                      or ((ev.get("name") or "").split(" - ") + [None])[1]),
        "estado": ev.get("state"),
        "capturado_en": capturado_en,
        "fecha_captura": capturado_en[:10],
        "fuente": "kambi/betplay",
        "version_esquema": VERSION_ESQUEMA,
    }


def filas_cuotas(respuesta: Dict[str, Any], event_id: int,
                 capturado_en: str, liga: Optional[str] = None) -> List[Dict[str, Any]]:
    """Una fila por outcome apostable."""
    filas: List[Dict[str, Any]] = []
    for oferta in respuesta.get("betOffers", []) or []:
        criterio = oferta.get("criterion") or {}
        tipo = oferta.get("betOfferType") or {}
        for res in oferta.get("outcomes", []) or []:
            cuota = _cuota(res.get("odds"))
            if cuota is None:
                continue   # outcome suspendido o sin precio: no es apostable
            participante = res.get("participant")
            # `eventParticipantId` solo viene en mercados de JUGADOR. El campo
            # `participant` por si solo no distingue: en "Resultado Final"
            # contiene el nombre del EQUIPO, asi que usarlo como senal marca
            # como linea de jugador cualquier handicap o 1X2.
            es_jugador = res.get("eventParticipantId") is not None
            filas.append({
                "outcome_id": res.get("id"),
                "bet_offer_id": oferta.get("id"),
                "event_id": event_id,
                "liga": liga,
                "mercado": criterio.get("label"),
                "mercado_id": criterio.get("id"),
                "tipo_oferta": tipo.get("name"),
                "etiqueta": res.get("label"),
                "participante": participante,
                "participante_id": res.get("participantId"),
                "es_mercado_de_jugador": es_jugador,
                "tipo_outcome": res.get("type"),
                "linea": _linea(res.get("line")),
                "cuota": cuota,
                "prob_implicita": round(1.0 / cuota, 6),
                "estado": res.get("status"),
                "capturado_en": capturado_en,
                "fecha_captura": capturado_en[:10],
                "version_esquema": VERSION_ESQUEMA,
            })
    return filas


def _outcomes_de_combinacion(comb: Dict[str, Any]) -> List[int]:
    """Los ids de las patas, que Kambi anida en grupos con operaciones AND."""
    ids: List[int] = []

    def anda(grupo: Dict[str, Any]) -> None:
        for o in grupo.get("outcomes", []) or []:
            if o.get("id") is not None:
                ids.append(o["id"])
        for hijo in grupo.get("groups", []) or []:
            anda(hijo)

    for g in comb.get("groups", []) or []:
        anda(g)
    return ids


def filas_prepacks(respuesta: Dict[str, Any], event_id: int,
                   capturado_en: str, liga: Optional[str] = None) -> List[Dict[str, Any]]:
    """Combinados del Bet Builder, con su cuota y las patas que los forman.

    Esta tabla es la que permite responder si la casa multiplica o recotiza: se
    compara `cuota_combinada` contra el producto de las cuotas de `patas`, que
    estan en la tabla `cuotas` de la misma captura.
    """
    filas: List[Dict[str, Any]] = []
    for pack in respuesta.get("prePacks", []) or []:
        for sel in pack.get("prePackSelections", []) or []:
            for comb in sel.get("combinations", []) or []:
                cuota = _cuota((comb.get("odds") or {}).get("decimal"))
                if cuota is None:
                    continue
                patas = _outcomes_de_combinacion(comb)
                if not patas:
                    continue
                clave = f"{event_id}|{sorted(patas)}"
                filas.append({
                    "prepack_id": pack.get("id"),
                    "seleccion_id": sel.get("selectionId"),
                    "combinado_hash": hashlib.sha256(clave.encode()).hexdigest()[:16],
                    "event_id": event_id,
                    "liga": liga,
                    "n_patas": len(patas),
                    "patas": ",".join(str(p) for p in patas),
                    "cuota_combinada": cuota,
                    "etiquetas": ",".join(str(x) for x in (sel.get("label") or [])),
                    "estado": sel.get("status"),
                    "tags": ",".join(pack.get("tags") or []),
                    "capturado_en": capturado_en,
                    "fecha_captura": capturado_en[:10],
                    "version_esquema": VERSION_ESQUEMA,
                })
    return filas
