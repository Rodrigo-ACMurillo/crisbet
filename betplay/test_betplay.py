"""Pruebas del capturador. Sin red: todo sobre una respuesta JSON de ejemplo.

El fixture reproduce la forma real del feed de Kambi, incluida la parte que mas
facil es entender mal: las cuotas en milesimas y la diferencia entre un mercado
de equipo y uno de jugador.

    python test_betplay.py
"""
from __future__ import annotations

import json
import sys
from typing import List

from esquema import (VERSION_ESQUEMA, ahora_utc, _cuota, _linea,
                     _outcomes_de_combinacion, fila_evento, filas_cuotas,
                     filas_prepacks)
from kambi import KambiClient

fallos: List[str] = []


def check(nombre: str, condicion: bool, detalle: str = "") -> None:
    estado = "PASS" if condicion else "FAIL"
    print(f"[{estado}] {nombre}" + (f" -> {detalle}" if detalle and not condicion else ""))
    if not condicion:
        fallos.append(nombre)


RESPUESTA = {
    "events": [{
        "id": 1028436161,
        "name": "Jaguares de Cordoba - Fortaleza FC",
        "start": "2026-09-11T23:15:00Z",
        "group": "Liga BetPlay Dimayor",
        "state": "NOT_STARTED",
        "path": [
            {"id": 1, "name": "Futbol", "englishName": "Football", "termKey": "football"},
            {"id": 2, "name": "Colombia", "englishName": "Colombia", "termKey": "colombia"},
            {"id": 3, "name": "Liga BetPlay Dimayor", "englishName": "Liga Dimayor",
             "termKey": "liga_betplay_dimayor"},
        ],
    }],
    "betOffers": [
        {   # mercado de EQUIPO: participant trae el nombre del club
            "id": 111, "criterion": {"id": 9, "label": "Resultado Final"},
            "betOfferType": {"name": "Partido"},
            "outcomes": [
                {"id": 1001, "label": "1", "odds": 2380, "participant": "Jaguares de Cordoba",
                 "participantId": 500, "type": "OT_ONE", "status": "OPEN"},
                {"id": 1002, "label": "X", "odds": 3050, "type": "OT_CROSS", "status": "OPEN"},
                {"id": 1003, "label": "2", "odds": 3300, "participant": "Fortaleza FC",
                 "participantId": 501, "type": "OT_TWO", "status": "OPEN"},
            ],
        },
        {   # mercado de JUGADOR: lo delata eventParticipantId
            "id": 222, "criterion": {"id": 77, "label": "Anotara"},
            "betOfferType": {"name": "Jugador"},
            "outcomes": [
                {"id": 2001, "label": "Si", "odds": 2950, "participant": "Andres Renteria",
                 "participantId": 900, "eventParticipantId": 77001, "type": "OT_YES",
                 "status": "OPEN"},
            ],
        },
        {   # linea en milesimas + un outcome sin precio, que debe descartarse
            "id": 333, "criterion": {"id": 12, "label": "Total de goles"},
            "betOfferType": {"name": "Total"},
            "outcomes": [
                {"id": 3001, "label": "Mas de", "odds": 2280, "line": 2500, "status": "OPEN"},
                {"id": 3002, "label": "Menos de", "odds": None, "line": 2500,
                 "status": "SUSPENDED"},
            ],
        },
    ],
    "prePacks": [{
        "id": 7007705,
        "tags": ["AUTO"],
        "prePackSelections": [{
            "selectionId": 7915297, "label": [], "status": "OPEN",
            "combinations": [{
                "odds": {"decimal": 4600},
                "groups": [{"operation": "AND", "groups": [
                    {"operation": "AND", "outcomes": [{"id": 1001}]},
                    {"operation": "AND", "outcomes": [{"id": 2001}]},
                ]}],
            }],
        }],
    }],
}
SELLO = "2026-09-11T16:00:00Z"


def test_conversion_de_cuotas() -> None:
    check("2380 milesimas son 2.38", _cuota(2380) == 2.38)
    check("una cuota de 1.00 o menos no es apostable", _cuota(1000) is None)
    check("None se propaga", _cuota(None) is None)
    check("basura no revienta", _cuota("x") is None)
    check("la linea 2500 son 2.5 goles", _linea(2500) == 2.5)


def test_evento() -> None:
    f = fila_evento(RESPUESTA["events"][0], SELLO)
    check("id del evento", f["event_id"] == 1028436161)
    check("liga", f["liga"] == "Liga BetPlay Dimayor")
    check("clave de liga desde el path", f["liga_key"] == "liga_betplay_dimayor")
    check("pais desde el path", f["pais"] == "Colombia")
    check("local y visitante del nombre",
          f["local"] == "Jaguares de Cordoba" and f["visitante"] == "Fortaleza FC",
          f"{f['local']} / {f['visitante']}")
    check("particion por fecha de captura", f["fecha_captura"] == "2026-09-11")
    check("procedencia", f["fuente"] == "kambi/betplay" and f["capturado_en"] == SELLO)


def test_cuotas() -> None:
    filas = filas_cuotas(RESPUESTA, 1028436161, SELLO, "Liga BetPlay Dimayor")
    check("se descarta el outcome sin precio", len(filas) == 5, str(len(filas)))

    por_id = {f["outcome_id"]: f for f in filas}
    check("cuota convertida", por_id[1001]["cuota"] == 2.38)
    check("probabilidad implicita", abs(por_id[1001]["prob_implicita"] - 1 / 2.38) < 1e-6)
    check("linea convertida", por_id[3001]["linea"] == 2.5)
    check("mercado etiquetado", por_id[1001]["mercado"] == "Resultado Final")
    check("toda fila lleva instante de captura",
          all(f["capturado_en"] == SELLO for f in filas))
    check("toda fila lleva version de esquema",
          all(f["version_esquema"] == VERSION_ESQUEMA for f in filas))


def test_jugador_vs_equipo() -> None:
    """El bug que hubo: `participant` tambien trae equipos.

    En "Resultado Final" el participante es el club. Usar ese campo como senal
    marcaba 960 lineas de jugador donde solo habia 566.
    """
    filas = filas_cuotas(RESPUESTA, 1028436161, SELLO)
    por_id = {f["outcome_id"]: f for f in filas}
    check("el 1X2 NO es mercado de jugador",
          por_id[1001]["es_mercado_de_jugador"] is False,
          str(por_id[1001]))
    check("aunque traiga nombre de participante",
          por_id[1001]["participante"] == "Jaguares de Cordoba")
    check("'Anotara' SI es mercado de jugador",
          por_id[2001]["es_mercado_de_jugador"] is True)
    check("con el nombre del jugador", por_id[2001]["participante"] == "Andres Renteria")
    check("un outcome sin participante tampoco lo es",
          por_id[1002]["es_mercado_de_jugador"] is False)


def test_prepacks() -> None:
    filas = filas_prepacks(RESPUESTA, 1028436161, SELLO, "Liga BetPlay Dimayor")
    check("un combinado", len(filas) == 1, str(len(filas)))
    f = filas[0]
    check("cuota del combinado", f["cuota_combinada"] == 4.6)
    check("dos patas", f["n_patas"] == 2)
    check("ids de las patas", f["patas"] == "1001,2001", f["patas"])
    check("hash estable del combinado", len(f["combinado_hash"]) == 16)

    # La comparacion que da sentido a la tabla.
    precios = {c["outcome_id"]: c["cuota"] for c in filas_cuotas(RESPUESTA, 1, SELLO)}
    producto = precios[1001] * precios[2001]
    ratio = f["cuota_combinada"] / producto
    check("el ratio ofrecida/producto se puede calcular", 0 < ratio < 2, str(ratio))
    check("Betplay recorta en este combinado correlacionado", ratio < 1,
          f"ratio={ratio:.3f} (producto={producto:.2f}, ofrece={f['cuota_combinada']})")


def test_extraccion_de_patas_anidadas() -> None:
    """Kambi anida los grupos; un extractor plano perderia patas."""
    comb = {"groups": [{"operation": "AND", "groups": [
        {"operation": "AND", "outcomes": [{"id": 1}]},
        {"operation": "AND", "groups": [{"operation": "AND", "outcomes": [{"id": 2}, {"id": 3}]}]},
    ]}]}
    check("recorre grupos anidados", _outcomes_de_combinacion(comb) == [1, 2, 3],
          str(_outcomes_de_combinacion(comb)))
    check("sin grupos devuelve vacio", _outcomes_de_combinacion({}) == [])


def test_respuesta_vacia() -> None:
    check("respuesta vacia no revienta en cuotas", filas_cuotas({}, 1, SELLO) == [])
    check("respuesta vacia no revienta en prepacks", filas_prepacks({}, 1, SELLO) == [])
    check("betOffers nulo tampoco", filas_cuotas({"betOffers": None}, 1, SELLO) == [])


def test_limitador_del_cliente() -> None:
    c = KambiClient(retardo=0.1)
    check("el retardo tiene suelo de 0.5 s", c.retardo == 0.5, str(c.retardo))
    check("la url base apunta a la marca correcta",
          c.base.endswith("/offering/v2018/betplay"), c.base)
    check("el host es el de la region US", "us.offering-api" in c.base, c.base)


def test_no_hay_credenciales() -> None:
    """El capturador no debe leer .env ni mandar cabeceras de sesion."""
    import kambi
    fuente = open(kambi.__file__, encoding="utf-8").read()
    for prohibido in ("dotenv", "getenv", "environ", "Authorization", "Cookie", "password"):
        check(f"kambi.py no usa '{prohibido}'", prohibido not in fuente)


def test_sello_de_tiempo() -> None:
    t = ahora_utc()
    check("formato ISO en UTC", t.endswith("Z") and len(t) == 20, t)


if __name__ == "__main__":
    for prueba in (test_conversion_de_cuotas, test_evento, test_cuotas,
                   test_jugador_vs_equipo, test_prepacks,
                   test_extraccion_de_patas_anidadas, test_respuesta_vacia,
                   test_limitador_del_cliente, test_no_hay_credenciales,
                   test_sello_de_tiempo):
        print("\n== " + prueba.__name__ + " ==")
        prueba()
    print("\n" + ("TODO OK" if not fallos else "FALLOS: " + json.dumps(fallos)))
    sys.exit(1 if fallos else 0)
