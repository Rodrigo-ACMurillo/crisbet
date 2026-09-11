"""Mercados de jugador: "Anotara", "Marca al menos 2", "Anotador del primer gol".

Son el 27% de las lineas que oferta Betplay y lo que originalmente se queria
combinar. Tambien son las unicas que este proyecto no puede valorar con datos
propios, y conviene tener clarisimo por que.

**El problema no es el modelo, es el dato.** Para estimar si un jugador marca
hace falta saber cuanto marca ese jugador. Las fuentes libres que habia se han
cerrado: Understat ya no publica los disparos en el HTML, FBref responde 403.
Queda `football-data.org`, gratuito con registro.

Su plan libre cubre las grandes ligas europeas mas el Brasileirao, que en
Betplay son unas 280 de las 2.700 jornadas de futbol abiertas. No cubre la Liga
BetPlay ni el resto de competiciones sudamericanas y menores, que son la mayor
parte del catalogo. Dicho de otro modo: los mercados de jugador se pueden
valorar en las ligas grandes y no en las pequenas, y el selector tiene que
saberlo partido a partido en vez de suponerlo.

**El modelo, cuando hay dato.** La tasa de gol de un jugador se combina con los
goles esperados de su equipo, que Dixon-Coles ya calcula:

    cuota_jugador = goles_del_jugador / goles_del_equipo     (su parte del pastel)
    lambda_jugador = lambda_equipo * cuota_jugador
    P(marca al menos n) = 1 - P(Poisson(lambda_jugador) < n)

Es deliberadamente simple, y tiene dos debilidades que hay que declarar en vez
de esconder: supone que el reparto de goles del equipo se mantiene, y **no sabe
si el jugador va a jugar**. Un suplente y un titular indiscutible reciben la
misma estimacion. Sin alineaciones confirmadas —que tambien exigen proveedor de
pago— este mercado no se puede valorar con seriedad, y el selector debe
descartarlo antes que publicar un numero inventado.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

try:
    from scipy.stats import poisson
except ImportError:
    poisson = None

# Competiciones del plan gratuito de football-data.org.
#
# La clave es (pais, liga_key) y NO solo liga_key, porque en el feed de Betplay
# `liga_key` NO es unico: "premier_league" identifica a la vez la Premier
# inglesa y las Premier Ligas de Rusia, Ucrania, Kazajistan y Jordania;
# "bundesliga" vale para Alemania y para Austria. Mapear solo por la clave
# aplicaria las tasas de gol de la Premier inglesa a jugadores jordanos sin
# lanzar ningun error: numeros plausibles y completamente falsos.
LIGAS_GRATUITAS = {
    ("Inglaterra", "premier_league"): 2021,
    ("Inglaterra", "the_championship"): 2016,
    ("Espana", "la_liga"): 2014,
    ("Italia", "serie_a"): 2019,
    ("Alemania", "bundesliga"): 2002,
    ("Francia", "ligue_1"): 2015,
    ("Paises Bajos", "eredivisie"): 2003,
    ("Portugal", "primeira_liga"): 2017,
    ("Brasil", "brasileirao_serie_a"): 2013,
}

LIGAS_SIN_FUENTE_LIBRE = {
    ("Colombia", "liga_betplay_dimayor"): "Primera A (Colombia) es TIER_FOUR (de pago) en football-data.org",
}


def _clave(pais: str, liga_key: str) -> tuple:
    """Normaliza el pais para que 'Espana' y 'España' sean la misma clave."""
    import unicodedata
    p = unicodedata.normalize("NFKD", str(pais or ""))
    p = "".join(ch for ch in p if not unicodedata.combining(ch)).strip()
    return (p, str(liga_key or ""))


@dataclass
class TasaDeGol:
    """Lo que se sabe de un jugador. `partidos` importa tanto como `goles`:
    3 goles en 4 partidos y 3 en 30 no son lo mismo."""

    jugador: str
    equipo: str
    goles: int
    partidos: int
    competicion: str
    fuente: str
    temporada: Optional[str] = None

    @property
    def goles_por_partido(self) -> float:
        return self.goles / self.partidos if self.partidos else 0.0

    @property
    def fiable(self) -> bool:
        """Por debajo de 5 partidos la tasa es ruido."""
        return self.partidos >= 5


class ProveedorJugadores(ABC):
    nombre = "abstracto"

    @abstractmethod
    def goleadores(self, pais: str, liga_key: str) -> List[TasaDeGol]:
        ...

    @abstractmethod
    def disponible_para(self, pais: str, liga_key: str) -> bool:
        ...


class FootballDataOrg(ProveedorJugadores):
    """football-data.org. Token gratuito con registro en football-data.org/client/register.

    El token va en la variable de entorno `FOOTBALL_DATA_TOKEN`. Es una clave de
    una API de estadisticas, no una credencial de casa de apuestas: no da acceso
    a dinero ni a ninguna cuenta personal.
    """

    nombre = "football-data.org"
    BASE = "https://api.football-data.org/v4"

    def __init__(self, token: Optional[str] = None):
        self.token = token or os.environ.get("FOOTBALL_DATA_TOKEN")

    def disponible_para(self, pais: str, liga_key: str) -> bool:
        return bool(self.token) and _clave(pais, liga_key) in LIGAS_GRATUITAS

    def motivo_no_disponible(self, pais: str, liga_key: str) -> str:
        clave = _clave(pais, liga_key)
        if clave in LIGAS_SIN_FUENTE_LIBRE:
            return LIGAS_SIN_FUENTE_LIBRE[clave]
        if clave not in LIGAS_GRATUITAS:
            return f"'{liga_key}' de {pais} no esta en el plan gratuito"
        if not self.token:
            return ("falta FOOTBALL_DATA_TOKEN (registro gratuito en "
                    "football-data.org/client/register)")
        return "no disponible"

    def goleadores(self, pais: str, liga_key: str, limite: int = 100,
                   temporada: Optional[int] = None) -> List[TasaDeGol]:
        """`temporada` es el ano de inicio (2024 = temporada 2024/25). Sin el,
        devuelve la temporada en curso."""
        if not self.disponible_para(pais, liga_key):
            raise RuntimeError(self.motivo_no_disponible(pais, liga_key))
        cid = LIGAS_GRATUITAS[_clave(pais, liga_key)]
        url = f"{self.BASE}/competitions/{cid}/scorers?limit={limite}"
        if temporada:
            url += f"&season={temporada}"
        req = urllib.request.Request(url, headers={"X-Auth-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                datos = json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"football-data.org respondio {e.code}") from e

        salida = []
        for s in datos.get("scorers", []):
            salida.append(TasaDeGol(
                jugador=(s.get("player") or {}).get("name", "?"),
                equipo=(s.get("team") or {}).get("name", "?"),
                goles=s.get("goals") or 0,
                partidos=s.get("playedMatches") or 0,
                competicion=(datos.get("competition") or {}).get("name", liga_key),
                temporada=str((datos.get("season") or {}).get("startDate", ""))[:4] or None,
                fuente=self.nombre,
            ))
        return salida


PESO_DEL_PRIOR = 10.0      # partidos "virtuales" que aporta la temporada anterior
MIN_PARTIDOS_TOTALES = 8   # sumando ambas temporadas


def combinar_temporadas(actual: List[TasaDeGol], anterior: List[TasaDeGol],
                        peso_prior: float = PESO_DEL_PRIOR) -> Dict[str, "TasaCombinada"]:
    """Mezcla la temporada en curso con la anterior, encogiendo hacia el pasado.

    El problema que resuelve es concreto: en la jornada 3, Haaland lleva 3 goles
    en 3 partidos. Tomarlo al pie de la letra da una tasa de 1.00 goles por
    partido, que es absurda; descartarlo por "pocos partidos" deja el modelo sin
    ningun jugador hasta noviembre. Ninguna de las dos cosas sirve.

    La salida es una media ponderada donde la temporada anterior actua como un
    prior de `peso_prior` partidos:

        tasa = (goles_actuales + peso * tasa_anterior) / (partidos_actuales + peso)

    En la jornada 3 manda el pasado; en la 30, el presente. Un jugador sin
    historial y con pocos partidos se queda fuera, que es lo correcto.
    """
    por_jugador: Dict[str, TasaCombinada] = {}
    prev = {t.jugador: t for t in anterior}

    for t in actual:
        p = prev.get(t.jugador)
        if p and p.partidos >= 5:
            tasa_prior = p.goles_por_partido
            goles_eq = t.goles + peso_prior * tasa_prior
            partidos_eq = t.partidos + peso_prior
        else:
            tasa_prior = None
            goles_eq, partidos_eq = float(t.goles), float(t.partidos)
        por_jugador[t.jugador] = TasaCombinada(
            jugador=t.jugador, equipo=t.equipo,
            goles_actual=t.goles, partidos_actual=t.partidos,
            tasa_anterior=tasa_prior,
            partidos_anteriores=p.partidos if p else 0,
            tasa=goles_eq / partidos_eq if partidos_eq else 0.0,
            partidos_efectivos=partidos_eq,
            competicion=t.competicion, fuente=t.fuente)

    # Jugadores que la temporada pasada marcaban y este ano aun no aparecen en
    # la tabla: siguen siendo goleadores, solo que todavia no han marcado.
    for t in anterior:
        if t.jugador in por_jugador or t.partidos < 5:
            continue
        por_jugador[t.jugador] = TasaCombinada(
            jugador=t.jugador, equipo=t.equipo,
            goles_actual=0, partidos_actual=0,
            tasa_anterior=t.goles_por_partido, partidos_anteriores=t.partidos,
            tasa=t.goles_por_partido, partidos_efectivos=peso_prior,
            competicion=t.competicion, fuente=t.fuente + " (solo temporada anterior)")
    return por_jugador


@dataclass
class TasaCombinada:
    """Tasa de gol de un jugador mezclando la temporada actual y la anterior."""

    jugador: str
    equipo: str
    goles_actual: int
    partidos_actual: int
    tasa_anterior: Optional[float]
    partidos_anteriores: int
    tasa: float                  # goles por partido, ya encogida
    partidos_efectivos: float
    competicion: str
    fuente: str

    @property
    def fiable(self) -> bool:
        return (self.partidos_actual + self.partidos_anteriores) >= MIN_PARTIDOS_TOTALES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "jugador": self.jugador, "equipo": self.equipo,
            "tasa_goles_por_partido": round(self.tasa, 4),
            "goles_actual": self.goles_actual, "partidos_actual": self.partidos_actual,
            "tasa_anterior": (round(self.tasa_anterior, 4)
                              if self.tasa_anterior is not None else None),
            "partidos_anteriores": self.partidos_anteriores,
            "fiable": self.fiable, "fuente": self.fuente,
        }


class ModeloGoleador:
    """P(un jugador marque n goles) en un partido concreto.

    El metodo anterior repartia los goles del equipo entre los goleadores de la
    tabla, lo que **sobreestimaba** a los que aparecen en ella: la tabla no
    lista a todos los que marcaron alguna vez, asi que las partes no suman el
    total. Este parte de la tasa propia del jugador y solo la escala segun lo
    que se espera que marque su equipo en ESTE partido:

        lambda_jugador = tasa_del_jugador * (goles_esperados_equipo / media_liga)

    Contra un rival flojo el delantero sube; contra uno duro, baja. Y no depende
    de que la tabla de goleadores este completa.
    """

    def __init__(self, tasas: Dict[str, TasaCombinada],
                 media_goles_por_equipo: float = 1.35):
        self.tasas = tasas
        self.media_liga = max(media_goles_por_equipo, 0.1)

    def probabilidades(self, jugador: str, lambda_equipo: float) -> Optional[Dict[str, Any]]:
        """None cuando no hay base para un numero. Inventarlo seria peor."""
        if poisson is None:
            return None
        t = self.tasas.get(jugador)
        if t is None or not t.fiable:
            return None
        factor = lambda_equipo / self.media_liga
        lam = max(t.tasa * factor, 1e-6)
        return {
            "jugador": jugador,
            "lambda_jugador": round(lam, 5),
            "tasa_base": round(t.tasa, 4),
            "factor_del_partido": round(factor, 4),
            "p_marca": round(float(1 - poisson.cdf(0, lam)), 6),
            "p_marca_2+": round(float(1 - poisson.cdf(1, lam)), 6),
            "p_marca_3+": round(float(1 - poisson.cdf(2, lam)), 6),
            "partidos_efectivos": round(t.partidos_efectivos, 1),
            "aviso": ("no incorpora alineaciones confirmadas: un suplente recibe "
                      "la misma estimacion que un titular"),
        }


def diagnostico_de_cobertura(pais: str, liga_key: str,
                             proveedor: Optional[ProveedorJugadores] = None) -> Dict[str, Any]:
    """Dice si los mercados de jugador de una liga se pueden valorar, y si no, por que."""
    prov = proveedor or FootballDataOrg()
    disponible = prov.disponible_para(pais, liga_key)
    return {
        "pais": pais,
        "liga": liga_key,
        "proveedor": prov.nombre,
        "se_puede_valorar": disponible,
        "motivo": None if disponible else prov.motivo_no_disponible(pais, liga_key),
        "consecuencia": (None if disponible else
                         "El selector debe DESCARTAR los mercados de jugador de esta liga. "
                         "Publicar una probabilidad sin datos del jugador seria inventarla."),
        "falta_ademas": ("alineaciones confirmadas: sin ellas no se sabe si el jugador juega, "
                         "y un suplente recibe la misma estimacion que un titular"),
    }
