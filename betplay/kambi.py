"""Cliente del feed de cuotas de Betplay (plataforma Kambi).

Betplay es un white-label de Kambi, y su cliente web lee las cuotas de una API
JSON publica. Eso hace innecesario raspar HTML de una SPA Angular y, sobre todo,
innecesario iniciar sesion: **este modulo nunca usa credenciales**. Las cuotas
son publicas y el `robots.txt` de betplay.com.co permite el rastreo.

Datos de conexion averiguados leyendo el bundle del propio sitio:

    host    us.offering-api.kambicdn.com   (region US; el host `eu-` NO sirve
                                            para esta marca y devuelve 429)
    marca   betplay
    query   lang=es_CO&market=CO&client_id=200&channel_id=1

Las cabeceras `Referer` y `Origin` son obligatorias: sin ellas la API responde
403. No es un truco para colarse, es lo que envia el cliente legitimo.

Sobre el ritmo: el limitador esta puesto a una peticion por segundo y no es
negociable a la baja sin motivo. Un capturador diario no tiene ninguna prisa, y
un feed publico que se consulta con cabeza dura meses; uno que se martillea,
dias.
"""
from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

HOST = "https://us.offering-api.kambicdn.com"
MARCA = "betplay"
CABECERAS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-CO,es;q=0.9",
    "Referer": "https://betplay.com.co/",
    "Origin": "https://betplay.com.co",
}
PARAMS_BASE = {"lang": "es_CO", "market": "CO", "client_id": "200", "channel_id": "1"}
REINTENTABLES = {429, 500, 502, 503, 504}


@dataclass
class Contadores:
    peticiones: int = 0
    errores: int = 0
    reintentos: int = 0
    bytes: int = 0
    por_codigo: Dict[str, int] = field(default_factory=dict)

    def anota(self, clave: str) -> None:
        self.por_codigo[clave] = self.por_codigo.get(clave, 0) + 1


class KambiClient:
    def __init__(self, retardo: float = 1.0, reintentos: int = 3,
                 timeout: int = 30, host: str = HOST, marca: str = MARCA):
        self.retardo = max(retardo, 0.5)   # suelo duro: nunca por debajo de 0.5 s
        self.reintentos = reintentos
        self.timeout = timeout
        self.base = f"{host}/offering/v2018/{marca}"
        self.contadores = Contadores()
        self._siguiente = 0.0

    def _esperar(self) -> None:
        ahora = time.monotonic()
        if ahora < self._siguiente:
            time.sleep(self._siguiente - ahora)
        # Un poco de jitter para no golpear en fase exacta.
        self._siguiente = time.monotonic() + self.retardo * random.uniform(0.9, 1.2)

    def get(self, ruta: str, extra: Optional[Dict[str, str]] = None) -> Optional[Dict[str, Any]]:
        """GET con reintentos y backoff. Devuelve None si no se pudo obtener."""
        params = dict(PARAMS_BASE, **(extra or {}))
        url = f"{self.base}/{ruta.lstrip('/')}?{urllib.parse.urlencode(params)}"

        for intento in range(self.reintentos + 1):
            self._esperar()
            try:
                req = urllib.request.Request(url, headers=CABECERAS)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    crudo = r.read()
                self.contadores.peticiones += 1
                self.contadores.bytes += len(crudo)
                self.contadores.anota("200")
                return json.loads(crudo.decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                self.contadores.anota(str(e.code))
                if e.code in REINTENTABLES and intento < self.reintentos:
                    self.contadores.reintentos += 1
                    # Ante un 429 hay que apartarse de verdad, no reintentar rapido.
                    time.sleep(min(2 ** intento * 5, 60) * random.uniform(0.9, 1.3))
                    continue
                self.contadores.errores += 1
                return None
            except (urllib.error.URLError, TimeoutError, ValueError) as e:
                self.contadores.anota(type(e).__name__)
                if intento < self.reintentos:
                    self.contadores.reintentos += 1
                    time.sleep(2 ** intento * random.uniform(0.8, 1.4))
                    continue
                self.contadores.errores += 1
                return None
        return None

    # -- endpoints ------------------------------------------------------------

    def deportes(self) -> Dict[str, Any]:
        """Arbol de deportes y competiciones, con el numero de eventos de cada una."""
        return self.get("group.json") or {}

    def ligas_de_futbol(self, min_eventos: int = 1) -> List[Dict[str, Any]]:
        """Todas las competiciones de futbol con eventos abiertos.

        El arbol de Kambi es pais -> liga; se aplana a una lista de rutas
        utilizables directamente en `partidos_de_liga`.
        """
        arbol = self.deportes().get("group", {})
        futbol = next((g for g in arbol.get("groups", [])
                       if g.get("termKey") == "football"), None)
        if not futbol:
            return []
        salida = []
        for pais in futbol.get("groups", []):
            for liga in pais.get("groups", []):
                n = liga.get("eventCount") or 0
                if n >= min_eventos:
                    salida.append({
                        "pais": pais.get("name"),
                        "pais_key": pais.get("termKey"),
                        "liga": liga.get("name"),
                        "liga_key": liga.get("termKey"),
                        "eventos": n,
                        "ruta": f"football/{pais.get('termKey')}/{liga.get('termKey')}",
                    })
        return sorted(salida, key=lambda r: -r["eventos"])

    def partidos_de_liga(self, ruta: str) -> List[Dict[str, Any]]:
        """Eventos proximos de una competicion. `ruta` viene de `ligas_de_futbol`."""
        d = self.get(f"listView/{ruta}/all/matches.json")
        return (d or {}).get("events", [])

    def cuotas_de_evento(self, event_id: int) -> Dict[str, Any]:
        """Todos los mercados de un partido, incluidos los prePacks (Bet Builder).

        `includeParticipants` es lo que trae el nombre del jugador en los
        mercados de jugador; sin el, un outcome dice solo "Si" y no de quien.
        """
        return self.get(f"betoffer/event/{event_id}.json",
                        {"includeParticipants": "true"}) or {}
