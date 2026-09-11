"""Resultados de partido para liquidar los tickets.

Se usa football-data.org porque cubre —en su plan gratuito— exactamente las
mismas competiciones que el selector sabe valorar, y porque entrega el marcador
**al descanso** ademas del final, sin el cual no se pueden liquidar los mercados
de primera parte.

El emparejamiento entre el partido de Betplay y el de football-data.org se hace
por fecha y nombres de equipo, que son distintos en las dos fuentes ("Marsella"
frente a "Olympique de Marseille"). Se reutiliza el emparejador del modelo y,
como alli, **ante la duda no se empareja**: un ticket liquidado con el resultado
de otro partido es peor que un ticket pendiente.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "model"))

from emparejar import Emparejador, normalizar  # noqa: E402
from liquidacion import Marcador  # noqa: E402

# Liga de Betplay -> codigo de competicion en football-data.org.
COMPETICIONES = {
    "Premier League": 2021, "Championship": 2016, "La Liga": 2014,
    "Serie A": 2019, "Bundesliga": 2002, "Ligue 1": 2015,
    "Eredivisie": 2003, "Primeira Liga": 2017, "Brasileirao Serie A": 2013,
}


class ResultadosFootballData:
    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("FOOTBALL_DATA_TOKEN")
        self._cache: Dict[str, Any] = {}

    def disponible(self) -> bool:
        return bool(self.token)

    def _get(self, url: str) -> Optional[Dict[str, Any]]:
        if url in self._cache:
            return self._cache[url]
        try:
            req = urllib.request.Request(url, headers={"X-Auth-Token": self.token})
            with urllib.request.urlopen(req, timeout=30) as r:
                datos = json.loads(r.read().decode("utf-8", "replace"))
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
            return None
        self._cache[url] = datos
        return datos

    def partidos_de(self, competicion: int, desde: str, hasta: str) -> list:
        url = (f"https://api.football-data.org/v4/competitions/{competicion}/matches"
               f"?dateFrom={desde}&dateTo={hasta}")
        d = self._get(url)
        return (d or {}).get("matches", [])

    def marcadores(self, necesarios: Dict[int, Dict[str, Any]]) -> Dict[int, Marcador]:
        """{event_id de Betplay -> Marcador}. Solo partidos ya terminados."""
        por_liga: Dict[str, list] = {}
        for eid, meta in necesarios.items():
            por_liga.setdefault(meta["liga"], []).append((eid, meta))

        salida: Dict[int, Marcador] = {}
        for liga, entradas in por_liga.items():
            comp = COMPETICIONES.get(liga)
            if comp is None:
                continue
            fechas = [e[1]["inicio"][:10] for e in entradas]
            partidos = self.partidos_de(comp, min(fechas), max(fechas))
            terminados = [p for p in partidos if p.get("status") == "FINISHED"]
            if not terminados:
                continue

            # Indice por dia, para no cruzar partidos de jornadas distintas.
            por_dia: Dict[str, list] = {}
            for p in terminados:
                por_dia.setdefault(str(p.get("utcDate", ""))[:10], []).append(p)

            for eid, meta in entradas:
                dia = meta["inicio"][:10]
                # Un partido puede caer en el dia siguiente por zona horaria.
                candidatos = por_dia.get(dia, []) + por_dia.get(_dia_siguiente(dia), [])
                if not candidatos:
                    continue
                local_betplay = meta["partido"].split(" - ")[0]
                emp = Emparejador([normalizar((p.get("homeTeam") or {}).get("name", ""))
                                   .replace(" ", "_") for p in candidatos])
                r = emp.emparejar(local_betplay)
                if not r.ok:
                    continue
                objetivo = r.slug.replace("_", " ")
                elegido = next((p for p in candidatos
                                if normalizar((p.get("homeTeam") or {}).get("name", "")) == objetivo),
                               None)
                if elegido is None:
                    continue
                marcador = _a_marcador(elegido)
                if marcador is not None:
                    salida[eid] = marcador
        return salida


def _dia_siguiente(dia: str) -> str:
    try:
        return (dt.date.fromisoformat(dia) + dt.timedelta(days=1)).isoformat()
    except ValueError:
        return dia


def _a_marcador(partido: Dict[str, Any]) -> Optional[Marcador]:
    score = partido.get("score") or {}
    ft = score.get("fullTime") or {}
    ht = score.get("halfTime") or {}
    if ft.get("home") is None or ft.get("away") is None:
        return None
    return Marcador(goles_local=int(ft["home"]), goles_visitante=int(ft["away"]),
                    ht_local=ht.get("home"), ht_visitante=ht.get("away"))
