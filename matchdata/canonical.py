"""Esquema canonico de partido y normalizacion de nombres de equipo.

El texto rastreado en el Sprint 2 es contexto. Esto es la verdad numerica: un
partido tiene una fecha de comienzo, dos equipos, un resultado y unas cuotas de
cierre. Todo lo demas se deriva.

El problema real al unir fuentes no son los goles, son los nombres: "Man
United", "Manchester United", "Man Utd" y "Manchester Utd" son el mismo club.
Sin una clave canonica, unir dos proveedores produce equipos fantasma y un
feature store en silencio equivocado.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Optional

# Alias observados en football-data.co.uk frente a la forma larga habitual en
# las APIs de estadisticas. Se amplia a medida que se integran proveedores.
ALIAS: Dict[str, str] = {
    "man united": "manchester united",
    "man utd": "manchester united",
    "man city": "manchester city",
    "nott'm forest": "nottingham forest",
    "notts forest": "nottingham forest",
    "sheffield weds": "sheffield wednesday",
    "sheffield united": "sheffield united",
    "wolves": "wolverhampton wanderers",
    "west brom": "west bromwich albion",
    "west ham": "west ham united",
    "spurs": "tottenham hotspur",
    "tottenham": "tottenham hotspur",
    "newcastle": "newcastle united",
    "leeds": "leeds united",
    "leicester": "leicester city",
    "norwich": "norwich city",
    "stoke": "stoke city",
    "hull": "hull city",
    "cardiff": "cardiff city",
    "swansea": "swansea city",
    "brighton": "brighton and hove albion",
    "qpr": "queens park rangers",
    "ath madrid": "atletico madrid",
    "ath bilbao": "athletic club",
    "atletico bilbao": "athletic club",
    "espanol": "espanyol",
    "sociedad": "real sociedad",
    "betis": "real betis",
    "celta": "celta vigo",
    "vallecano": "rayo vallecano",
    "la coruna": "deportivo la coruna",
    "sp gijon": "sporting gijon",
    "inter": "internazionale",
    "milan": "ac milan",
    "roma": "as roma",
    "verona": "hellas verona",
    "bayern munich": "bayern munich",
    "fc koln": "koln",
    "ein frankfurt": "eintracht frankfurt",
    "m'gladbach": "borussia monchengladbach",
    "dortmund": "borussia dortmund",
    "leverkusen": "bayer leverkusen",
    "hertha": "hertha berlin",
    "paris sg": "paris saint germain",
    "psg": "paris saint germain",
    "st etienne": "saint etienne",
    "marseille": "olympique marseille",
    "lyon": "olympique lyonnais",
}

# Codigo de liga de football-data.co.uk -> nombre y pais.
LIGAS: Dict[str, Dict[str, str]] = {
    "E0": {"nombre": "Premier League", "pais": "Inglaterra", "nivel": "1"},
    "E1": {"nombre": "Championship", "pais": "Inglaterra", "nivel": "2"},
    "SP1": {"nombre": "LaLiga", "pais": "Espana", "nivel": "1"},
    "SP2": {"nombre": "LaLiga 2", "pais": "Espana", "nivel": "2"},
    "I1": {"nombre": "Serie A", "pais": "Italia", "nivel": "1"},
    "D1": {"nombre": "Bundesliga", "pais": "Alemania", "nivel": "1"},
    "F1": {"nombre": "Ligue 1", "pais": "Francia", "nivel": "1"},
    "N1": {"nombre": "Eredivisie", "pais": "Paises Bajos", "nivel": "1"},
    "P1": {"nombre": "Primeira Liga", "pais": "Portugal", "nivel": "1"},
}


def slug_equipo(nombre: str) -> str:
    """Clave canonica y estable de un equipo.

    Minusculas, sin tildes, sin puntuacion, sin sufijos societarios (FC, CF,
    SC, AC, CD...) y con los alias conocidos resueltos.
    """
    if not nombre:
        return ""
    texto = unicodedata.normalize("NFKD", nombre.strip().lower())
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = texto.replace("&", " and ").replace("'", "'")
    texto = re.sub(r"[^a-z0-9' ]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto).strip()

    if texto in ALIAS:
        texto = ALIAS[texto]

    # Los sufijos societarios se quitan despues del alias: "fc koln" -> "koln".
    partes = [p for p in texto.split(" ") if p not in
              {"fc", "cf", "sc", "ac", "cd", "ud", "sd", "afc", "cfc", "ss", "as", "us"}]
    texto = " ".join(partes) or texto
    return texto.replace(" ", "_")


def match_id(liga: str, kickoff: str, local: str, visitante: str) -> str:
    """Identificador determinista: la misma fila da siempre la misma clave."""
    crudo = f"{liga}|{kickoff[:10]}|{slug_equipo(local)}|{slug_equipo(visitante)}"
    return hashlib.sha256(crudo.encode("utf-8")).hexdigest()[:24]


@dataclass
class Match:
    """Un partido en forma canonica. Los campos opcionales pueden faltar segun
    el proveedor; los obligatorios no."""

    match_id: str
    liga: str
    temporada: str
    kickoff_utc: str          # ISO-8601, el instante que ordena todo el sistema
    equipo_local: str         # slug canonico
    equipo_visitante: str
    nombre_local: str         # nombre tal como lo dio la fuente
    nombre_visitante: str
    goles_local: Optional[int] = None
    goles_visitante: Optional[int] = None
    resultado: Optional[str] = None      # H | D | A
    goles_local_ht: Optional[int] = None
    goles_visitante_ht: Optional[int] = None
    tiros_local: Optional[int] = None
    tiros_visitante: Optional[int] = None
    tiros_puerta_local: Optional[int] = None
    tiros_puerta_visitante: Optional[int] = None
    corners_local: Optional[int] = None
    corners_visitante: Optional[int] = None
    faltas_local: Optional[int] = None
    faltas_visitante: Optional[int] = None
    amarillas_local: Optional[int] = None
    amarillas_visitante: Optional[int] = None
    rojas_local: Optional[int] = None
    rojas_visitante: Optional[int] = None
    arbitro: Optional[str] = None
    xg_local: Optional[float] = None
    xg_visitante: Optional[float] = None
    fuente: str = ""
    ingerido_en: str = field(default_factory=lambda:
                             dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    @property
    def jugado(self) -> bool:
        return self.goles_local is not None and self.goles_visitante is not None

    def validar(self) -> tuple[bool, str]:
        if not self.match_id:
            return False, "sin match_id"
        if not self.equipo_local or not self.equipo_visitante:
            return False, "equipo vacio"
        if self.equipo_local == self.equipo_visitante:
            return False, "equipo contra si mismo"
        try:
            dt.datetime.strptime(self.kickoff_utc[:19], "%Y-%m-%dT%H:%M:%S")
        except (ValueError, TypeError):
            return False, "kickoff invalido"
        if self.jugado:
            if self.goles_local < 0 or self.goles_visitante < 0:
                return False, "goles negativos"
            esperado = ("H" if self.goles_local > self.goles_visitante
                        else "A" if self.goles_local < self.goles_visitante else "D")
            if self.resultado and self.resultado != esperado:
                return False, "resultado incoherente con el marcador"
        return True, ""

    def to_row(self) -> Dict[str, Any]:
        fila = asdict(self)
        fila["fecha"] = self.kickoff_utc[:10]
        return fila


@dataclass
class OddsCierre:
    """Cuotas de cierre por mercado. Son el benchmark de eficiencia: el mercado
    en su ultimo instante es el pronosticador mas dificil de batir."""

    match_id: str
    kickoff_utc: str
    casa: str                 # B365 | PS (Pinnacle) | Max | Avg
    mercado: str              # 1x2 | ou25 | ah
    cuota_1: Optional[float] = None    # 1X2: local / OU: over / AH: local
    cuota_x: Optional[float] = None
    cuota_2: Optional[float] = None    # 1X2: visitante / OU: under / AH: visitante
    linea: Optional[float] = None      # OU 2.5, AH -0.5...
    overround: Optional[float] = None
    fuente: str = ""

    def calcular_overround(self) -> Optional[float]:
        cuotas = [c for c in (self.cuota_1, self.cuota_x, self.cuota_2) if c and c > 1.0]
        if len(cuotas) < 2:
            return None
        self.overround = round(sum(1.0 / c for c in cuotas), 6)
        return self.overround

    def probabilidades_implicitas(self) -> Dict[str, float]:
        """Probabilidades del mercado con el margen repartido proporcionalmente.

        Es el metodo simple (normalizacion multiplicativa). Sesga a favor de los
        favoritos frente a metodos como Shin, pero es transparente y suficiente
        como referencia; el modelo del Sprint 4 se compara contra esto.
        """
        self.calcular_overround()
        if not self.overround:
            return {}
        etiquetas = ("1", "X", "2") if self.mercado == "1x2" else ("over", "-", "under")
        salida = {}
        for etiqueta, cuota in zip(etiquetas, (self.cuota_1, self.cuota_x, self.cuota_2)):
            if cuota and cuota > 1.0 and etiqueta != "-":
                salida[etiqueta] = round((1.0 / cuota) / self.overround, 6)
        return salida

    def to_row(self) -> Dict[str, Any]:
        self.calcular_overround()
        fila = asdict(self)
        fila["fecha"] = self.kickoff_utc[:10]
        return fila
