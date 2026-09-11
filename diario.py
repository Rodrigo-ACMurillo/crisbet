"""El bucle diario del agente, de principio a fin.

    python diario.py                       # todo: capturar, emitir, anotar, liquidar
    python diario.py --solo liquidar       # solo una etapa
    python diario.py --cuota-objetivo 100 --tickets 3

Cuatro etapas, en este orden y por este motivo:

1. **capturar**  las cuotas de Betplay de hoy. Es lo unico que caduca: una cuota
                 de ayer no sirve para apostar hoy, y ademas alimenta el
                 historico de movimiento de linea, que solo se acumula.
2. **emitir**    los tickets del dia con el selector.
3. **anotar**    lo emitido en el libro, ANTES de conocer resultados.
4. **liquidar**  los tickets viejos cuyos partidos ya terminaron.

La liquidacion va al final a proposito: los partidos de hoy no han acabado, asi
que lo que se liquida siempre son emisiones de dias anteriores. Ejecutar esto
una vez al dia basta; dos veces (manana y tarde) mejora el historico de
movimiento de linea sin duplicar tickets, porque el libro los deduplica.

**Esto NO apuesta dinero.** Escribe tickets en un fichero. Lo que valida o
tumba el sistema es el resumen del libro tras unos cientos de tickets.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from typing import Dict, List

RAIZ = os.path.dirname(os.path.abspath(__file__))


def cargar_env() -> None:
    """Lee .env sin imprimir valores. El token de estadisticas vive ahi."""
    ruta = os.path.join(RAIZ, ".env")
    if not os.path.exists(ruta):
        return
    for linea in open(ruta, encoding="utf-8"):
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", linea)
        if m:
            os.environ.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))


def ejecutar(nombre: str, carpeta: str, argumentos: List[str]) -> Dict:
    inicio = time.monotonic()
    proc = subprocess.run([sys.executable] + argumentos, cwd=os.path.join(RAIZ, carpeta),
                          capture_output=True, text=True, env=os.environ)
    duracion = round(time.monotonic() - inicio, 1)
    ok = proc.returncode == 0
    print(f"{'OK ' if ok else 'ERR'} {nombre} ({duracion}s)")
    if not ok:
        # Se muestra el error pero no se aborta: liquidar debe intentarse
        # aunque la captura haya fallado, y viceversa.
        print("    " + (proc.stderr or proc.stdout or "").strip().splitlines()[-1][:160])
    return {"etapa": nombre, "ok": ok, "segundos": duracion,
            "salida": (proc.stdout or "").strip()[-4000:],
            "error": None if ok else (proc.stderr or "").strip()[-600:]}


def main() -> None:
    p = argparse.ArgumentParser(description="Bucle diario del agente")
    p.add_argument("--solo", choices=("capturar", "emitir", "anotar", "liquidar"),
                   help="ejecutar una sola etapa")
    p.add_argument("--cuota-objetivo", type=float, default=50.0)
    p.add_argument("--tickets", type=int, default=5)
    p.add_argument("--max-eventos", type=int, default=140)
    p.add_argument("--importe", type=float, default=1.0)
    p.add_argument("--informe", default="reports/diario.json")
    args = p.parse_args()

    cargar_env()
    etapas = [args.solo] if args.solo else ["capturar", "emitir", "anotar", "liquidar"]
    resultados = []

    if "capturar" in etapas:
        resultados.append(ejecutar("capturar cuotas de Betplay", "betplay",
                                   ["capturar.py", "--max-eventos", str(args.max_eventos)]))
    if "emitir" in etapas:
        resultados.append(ejecutar("emitir tickets", "model",
                                   ["selector.py", "--cuota-objetivo", str(args.cuota_objetivo),
                                    "--tickets", str(args.tickets)]))
    if "anotar" in etapas:
        resultados.append(ejecutar("anotar en el libro", "papel",
                                   ["registro.py", "anotar", "--importe", str(args.importe)]))
    if "liquidar" in etapas:
        resultados.append(ejecutar("liquidar tickets vencidos", "papel",
                                   ["registro.py", "liquidar"]))

    resumen = ejecutar("resumen del libro", "papel", ["registro.py", "resumen"])
    resultados.append(resumen)

    informe = {
        "ejecutado_en": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "etapas": resultados,
        "todo_ok": all(r["ok"] for r in resultados),
    }
    try:
        informe["libro"] = json.loads(resumen["salida"])
    except (ValueError, KeyError):
        pass

    ruta = os.path.join(RAIZ, args.informe)
    os.makedirs(os.path.dirname(ruta) or ".", exist_ok=True)
    with open(ruta, "w", encoding="utf-8") as fh:
        json.dump(informe, fh, ensure_ascii=False, indent=2)

    if "libro" in informe:
        print("\n--- libro ---")
        print(json.dumps(informe["libro"], ensure_ascii=False, indent=2))
    print(f"\nInforme: {args.informe}")
    sys.exit(0 if informe["todo_ok"] else 1)


if __name__ == "__main__":
    main()
