"""Proveedores de datos de partido, tras una interfaz comun.

La interfaz existe para que sustituir la fuente de verdad sea cambiar una
linea, no reescribir el feature store. Hoy la implementacion viva es
football-data.co.uk (gratuita, con resultados y **cuotas de cierre** desde
1993). API-Football queda declarada y sin credenciales: es la que aportara xG y
alineaciones cuando se contrate.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os
import time
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from typing import Dict, Iterable, List, Optional, Tuple

from canonical import LIGAS, Match, OddsCierre, match_id, slug_equipo

USER_AGENT = "CrisbetBot/0.3 (+https://crisbet.example/bot)"


def _temporada_actual() -> str:
    """Duplicado a proposito de build.py: providers no debe depender de el."""
    hoy = dt.date.today()
    inicio = hoy.year if hoy.month >= 7 else hoy.year - 1
    return f"{inicio}-{str(inicio + 1)[2:]}"


class MatchProvider(ABC):
    """Toda fuente de verdad numerica cumple esto."""

    nombre: str = "abstracto"

    @abstractmethod
    def partidos(self, liga: str, temporada: str) -> Tuple[List[Match], List[OddsCierre]]:
        """Devuelve (partidos, cuotas de cierre) de una liga y temporada."""

    @abstractmethod
    def ligas_disponibles(self) -> List[str]:
        ...


def _descargar(url: str, reintentos: int = 3, cache_dir: Optional[str] = None,
               max_edad_horas: Optional[float] = None) -> Optional[str]:
    """GET con reintentos y cache en disco.

    Los CSV de temporadas cerradas no cambian nunca, asi que cachearlos para
    siempre es correcto. El de la temporada EN CURSO es otra cosa: crece cada
    jornada. Cachearlo sin caducidad congela el modelo en la jornada del dia en
    que se descargo por primera vez, y no hay ningun sintoma: el pipeline corre
    entero, sin errores, con datos viejos.

    Por eso `max_edad_horas` invalida la copia local pasado ese tiempo.
    """
    ruta_cache = None
    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        ruta_cache = os.path.join(cache_dir, url.replace("://", "_").replace("/", "_"))
        if os.path.exists(ruta_cache) and os.path.getsize(ruta_cache) > 0:
            fresca = True
            if max_edad_horas is not None:
                edad = (time.time() - os.path.getmtime(ruta_cache)) / 3600.0
                fresca = edad < max_edad_horas
            if fresca:
                with open(ruta_cache, "r", encoding="utf-8") as fh:
                    return fh.read()

    for intento in range(reintentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=40) as resp:
                texto = resp.read().decode("utf-8-sig", "replace")
            if ruta_cache:
                with open(ruta_cache, "w", encoding="utf-8") as fh:
                    fh.write(texto)
            return texto
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError):
            if intento == reintentos - 1:
                return None
            time.sleep(2 ** intento)
    return None


def _entero(valor: str) -> Optional[int]:
    try:
        return int(float(valor))
    except (TypeError, ValueError):
        return None


def _decimal(valor: str) -> Optional[float]:
    try:
        f = float(valor)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


class FootballDataUK(MatchProvider):
    """football-data.co.uk: resultados, estadisticas basicas y cuotas de cierre.

    La columna que importa de verdad es el bloque `*C*` (closing): B365CH,
    PSCH, AvgC>2.5... Son las cuotas en el ultimo instante antes del pitido,
    el unico precio contra el que un backtest honesto puede medirse. Las cuotas
    de apertura sobreestiman el edge porque el mercado aun no ha corregido.
    """

    nombre = "football-data.co.uk"
    BASE = "https://www.football-data.co.uk/mmz4281"

    # (prefijo en el CSV, nombre de casa). Pinnacle es la referencia afilada.
    CASAS_1X2 = [("PSC", "pinnacle"), ("B365C", "bet365"), ("AvgC", "media_mercado"),
                 ("MaxC", "mejor_disponible")]

    def __init__(self, cache_dir: str = "cache_csv",
                 temporada_en_curso: Optional[str] = None):
        self.cache_dir = cache_dir
        self.temporada_en_curso = temporada_en_curso or _temporada_actual()

    def ligas_disponibles(self) -> List[str]:
        return list(LIGAS.keys())

    @staticmethod
    def _url(liga: str, temporada: str) -> str:
        """temporada '2024-25' -> '2425'."""
        ini, fin = temporada.split("-")
        return f"{FootballDataUK.BASE}/{ini[2:]}{fin[-2:]}/{liga}.csv"

    @staticmethod
    def _kickoff(fecha: str, hora: str) -> Optional[str]:
        """'16/08/2024' + '20:00' -> ISO. Sin hora, se asume 15:00 UTC.

        Esa suposicion es deliberada y conservadora: solo se usa para ordenar
        partidos del mismo dia, y adelantar la hora nunca puede colar en un
        partido informacion de otro posterior.
        """
        fecha = (fecha or "").strip()
        if not fecha:
            return None
        for formato in ("%d/%m/%Y", "%d/%m/%y"):
            try:
                dia = dt.datetime.strptime(fecha, formato)
                break
            except ValueError:
                dia = None
        if dia is None:
            return None
        hora = (hora or "").strip()
        if hora and ":" in hora:
            try:
                h, m = hora.split(":")[:2]
                dia = dia.replace(hour=int(h), minute=int(m))
            except ValueError:
                dia = dia.replace(hour=15)
        else:
            dia = dia.replace(hour=15)
        return dia.strftime("%Y-%m-%dT%H:%M:%SZ")

    def partidos(self, liga: str, temporada: str) -> Tuple[List[Match], List[OddsCierre]]:
        # La temporada en curso se refresca cada 6 horas; las cerradas, nunca.
        en_curso = temporada == self.temporada_en_curso
        texto = _descargar(self._url(liga, temporada), cache_dir=self.cache_dir,
                           max_edad_horas=6.0 if en_curso else None)
        if not texto:
            return [], []

        partidos: List[Match] = []
        cuotas: List[OddsCierre] = []
        for fila in csv.DictReader(io.StringIO(texto)):
            local, visitante = fila.get("HomeTeam", ""), fila.get("AwayTeam", "")
            if not local or not visitante:
                continue  # las filas finales de estos CSV suelen venir vacias
            kickoff = self._kickoff(fila.get("Date", ""), fila.get("Time", ""))
            if not kickoff:
                continue

            mid = match_id(liga, kickoff, local, visitante)
            partido = Match(
                match_id=mid,
                liga=liga,
                temporada=temporada,
                kickoff_utc=kickoff,
                equipo_local=slug_equipo(local),
                equipo_visitante=slug_equipo(visitante),
                nombre_local=local,
                nombre_visitante=visitante,
                goles_local=_entero(fila.get("FTHG", "")),
                goles_visitante=_entero(fila.get("FTAG", "")),
                resultado=(fila.get("FTR") or None),
                goles_local_ht=_entero(fila.get("HTHG", "")),
                goles_visitante_ht=_entero(fila.get("HTAG", "")),
                tiros_local=_entero(fila.get("HS", "")),
                tiros_visitante=_entero(fila.get("AS", "")),
                tiros_puerta_local=_entero(fila.get("HST", "")),
                tiros_puerta_visitante=_entero(fila.get("AST", "")),
                corners_local=_entero(fila.get("HC", "")),
                corners_visitante=_entero(fila.get("AC", "")),
                faltas_local=_entero(fila.get("HF", "")),
                faltas_visitante=_entero(fila.get("AF", "")),
                amarillas_local=_entero(fila.get("HY", "")),
                amarillas_visitante=_entero(fila.get("AY", "")),
                rojas_local=_entero(fila.get("HR", "")),
                rojas_visitante=_entero(fila.get("AR", "")),
                arbitro=(fila.get("Referee") or None),
                fuente=self.nombre,
            )
            ok, _ = partido.validar()
            if not ok:
                continue
            partidos.append(partido)
            cuotas.extend(self._cuotas(fila, mid, kickoff))
        return partidos, cuotas

    def _cuotas(self, fila: Dict[str, str], mid: str, kickoff: str) -> List[OddsCierre]:
        salida: List[OddsCierre] = []
        for prefijo, casa in self.CASAS_1X2:
            uno = _decimal(fila.get(prefijo + "H", ""))
            equis = _decimal(fila.get(prefijo + "D", ""))
            dos = _decimal(fila.get(prefijo + "A", ""))
            if uno and equis and dos:
                salida.append(OddsCierre(
                    match_id=mid, kickoff_utc=kickoff, casa=casa, mercado="1x2",
                    cuota_1=uno, cuota_x=equis, cuota_2=dos, fuente=self.nombre,
                ))

        for prefijo, casa in (("B365C", "bet365"), ("PC", "pinnacle"),
                              ("AvgC", "media_mercado"), ("MaxC", "mejor_disponible")):
            over = _decimal(fila.get(prefijo + ">2.5", ""))
            under = _decimal(fila.get(prefijo + "<2.5", ""))
            if over and under:
                salida.append(OddsCierre(
                    match_id=mid, kickoff_utc=kickoff, casa=casa, mercado="ou25",
                    cuota_1=over, cuota_2=under, linea=2.5, fuente=self.nombre,
                ))

        linea_ah = _decimal(fila.get("AHCh", "")) if fila.get("AHCh") else None
        if fila.get("AHCh") not in (None, ""):
            try:
                linea_ah = float(fila["AHCh"])
            except ValueError:
                linea_ah = None
        for prefijo, casa in (("B365C", "bet365"), ("PC", "pinnacle"), ("AvgC", "media_mercado")):
            local = _decimal(fila.get(prefijo + "AHH", ""))
            visitante = _decimal(fila.get(prefijo + "AHA", ""))
            if local and visitante:
                salida.append(OddsCierre(
                    match_id=mid, kickoff_utc=kickoff, casa=casa, mercado="ah",
                    cuota_1=local, cuota_2=visitante, linea=linea_ah, fuente=self.nombre,
                ))
        return salida


class ApiFootball(MatchProvider):
    """API-Football (de pago). Declarada, sin credenciales.

    Aporta lo que football-data.co.uk no tiene: xG, alineaciones confirmadas y
    bajas. Cuando se contrate, basta implementar `partidos()` contra el mismo
    contrato: el feature store y las pruebas anti-fuga no cambian.
    """

    nombre = "api-football"

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("API_FOOTBALL_KEY")

    def disponible(self) -> bool:
        return bool(self.api_key)

    def ligas_disponibles(self) -> List[str]:
        return list(LIGAS.keys()) if self.disponible() else []

    def partidos(self, liga: str, temporada: str) -> Tuple[List[Match], List[OddsCierre]]:
        if not self.disponible():
            raise RuntimeError(
                "API_FOOTBALL_KEY no configurada. Fuente de pago pendiente de "
                "contratar (ver ESTADO.json). Usa FootballDataUK mientras tanto."
            )
        raise NotImplementedError(
            "Implementar contra /fixtures y /odds cuando existan credenciales."
        )


def obtener_proveedor(nombre: str = "football-data", **kwargs) -> MatchProvider:
    if nombre in ("football-data", "football-data.co.uk", "fduk"):
        return FootballDataUK(**kwargs)
    if nombre in ("api-football", "apifootball"):
        return ApiFootball(**kwargs)
    raise ValueError("Proveedor desconocido: " + nombre)
