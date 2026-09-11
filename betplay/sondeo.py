"""¿Responde el feed de Kambi desde aqui?

La captura funciona desde una maquina en Colombia. Los runners de GitHub Actions
son IPs de datacenter en EE.UU., y Kambi filtra por origen: durante la
investigacion devolvio 429 en `eu-offering-api` y 403 en `settings-api`. Si
tambien bloquea a los runners, no tiene sentido montar el resto del despliegue.

Este sondeo responde esa pregunta y nada mas. Sale con codigo 0 si el feed
responde y con 1 si no, para que el workflow falle de forma visible en lugar de
publicar una pagina vacia.

    python sondeo.py
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from kambi import CABECERAS, HOST, MARCA, PARAMS_BASE, KambiClient


def ip_publica() -> str:
    """De donde sale la peticion. Util para entender un bloqueo."""
    try:
        with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=15) as r:
            return json.loads(r.read().decode()).get("ip", "?")
    except Exception:
        return "desconocida"


def probar(nombre: str, ruta: str, extra: Dict[str, str] | None = None) -> Dict[str, Any]:
    cliente = KambiClient(retardo=1.0, reintentos=1)
    inicio = time.monotonic()
    datos = cliente.get(ruta, extra)
    ms = round((time.monotonic() - inicio) * 1000)
    codigos = cliente.contadores.por_codigo
    return {
        "prueba": nombre,
        "ok": datos is not None,
        "ms": ms,
        "codigos_http": dict(codigos),
        "bytes": cliente.contadores.bytes,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Sondeo del feed de Betplay/Kambi")
    p.add_argument("--informe", default="reports/sondeo.json")
    args = p.parse_args()

    entorno = {
        "ip_publica": ip_publica(),
        "host_del_runner": socket.gethostname(),
        "en_github_actions": bool(os.environ.get("GITHUB_ACTIONS")),
        "region_del_feed": HOST,
        "marca": MARCA,
        "mercado": PARAMS_BASE.get("market"),
    }
    print(json.dumps(entorno, ensure_ascii=False, indent=2))

    pruebas: List[Dict[str, Any]] = []
    pruebas.append(probar("arbol de deportes", "group.json"))
    pruebas.append(probar("partidos de una liga",
                          "listView/football/colombia/liga_betplay_dimayor/all/matches.json"))

    # Si el listado responde, se prueba tambien el detalle de un partido real:
    # es la peticion que mas se repite en la captura diaria.
    cliente = KambiClient(retardo=1.0)
    eventos = cliente.partidos_de_liga("football/colombia/liga_betplay_dimayor")
    if eventos:
        eid = (eventos[0].get("event", eventos[0])).get("id")
        pruebas.append(probar("mercados de un partido", f"betoffer/event/{eid}.json",
                              {"includeParticipants": "true"}))

    print("\n" + json.dumps(pruebas, ensure_ascii=False, indent=2))

    ok = all(x["ok"] for x in pruebas) and len(pruebas) >= 2
    informe = {
        "entorno": entorno,
        "pruebas": pruebas,
        "feed_accesible": ok,
        "veredicto": (
            "El feed responde desde aqui: la captura puede ejecutarse en este entorno."
            if ok else
            "El feed NO responde desde aqui. La captura tiene que correr en una maquina "
            "cuya IP acepte Kambi (el PC de casa con el Programador de tareas, o un VPS "
            "en Colombia), y este entorno limitarse a publicar lo ya capturado."),
    }
    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)

    print("\n" + informe["veredicto"])
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
