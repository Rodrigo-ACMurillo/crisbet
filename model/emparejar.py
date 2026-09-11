"""Emparejar los nombres de equipo de Betplay con los del modelo.

Dos fuentes, dos formas de llamar al mismo club. Betplay usa el nombre oficial
completo ("VfB Stuttgart", "1. FC Union Berlin", "Mainz 05") y a veces la forma
castellana ("Marsella", "Milan"); el modelo, entrenado con football-data.co.uk,
usa la forma corta inglesa ("stuttgart", "union_berlin", "marseille").

**Un emparejamiento equivocado es peor que ninguno.** Si "Racing Santander" se
empareja con "racing" de otra liga, el sistema valora un partido con la fuerza
de un equipo que no juega, y el numero resultante parece perfectamente normal.
Por eso esto es deliberadamente conservador: exige una similitud alta, y lo que
no llega se declara sin emparejar en lugar de arriesgar. Un partido sin valorar
solo cuesta una oportunidad; uno mal valorado cuesta dinero.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Set, Tuple

# Palabras que acompanan al nombre pero no lo identifican. Se quitan antes de
# comparar: "VfB Stuttgart" y "Stuttgart" son el mismo club.
RUIDO = {
    "fc", "cf", "sc", "ac", "cd", "ud", "sd", "afc", "cfc", "ss", "as", "us",
    "vfb", "vfl", "tsg", "tsv", "sv", "bsc", "fsv", "spvgg", "ado", "psv",
    "rc", "rcd", "cp", "sl", "gd", "cs", "ca", "club", "de", "the", "1", "04",
    "05", "07", "08", "09", "96", "1899", "1900", "1904", "1907", "united",
    "city", "town", "county", "rovers", "athletic", "albion", "wanderers",
    "hotspur", "north", "end", "fk", "sk", "bk", "if", "ff", "aik",
}
# Terminos que SI distinguen y no se pueden tirar: sin ellos, "manchester
# united" y "manchester city" colapsarian en "manchester".
NUCLEO_PROTEGIDO = {"united", "city", "town", "county", "rovers", "athletic",
                    "albion", "wanderers", "hotspur"}

# Diferencias de idioma o de uso que ninguna heuristica resuelve.
ALIAS_MANUAL = {
    "marsella": "marseille", "olympique marsella": "marseille",
    "lyon": "olympique lyonnais", "olympique lyon": "olympique lyonnais",
    "milan": "ac milan", "inter de milan": "internazionale", "inter": "internazionale",
    "juventus de turin": "juventus", "napoles": "napoli", "roma": "as roma",
    "bayern de munich": "bayern munich", "borussia moenchengladbach": "borussia monchengladbach",
    "colonia": "koln", "friburgo": "freiburg", "hamburgo": "hamburg",
    "la coruna": "deportivo la coruna", "betis": "real betis",
    "atletico de madrid": "atletico madrid", "athletic de bilbao": "athletic club",
    "real sociedad de futbol": "real sociedad", "celta de vigo": "celta vigo",
    "paris saint-germain": "paris saint germain", "paris sg": "paris saint germain",
    "oporto": "porto", "lisboa": "sporting lisbon", "sporting de lisboa": "sporting lisbon",
    "benfica de lisboa": "benfica", "az alkmaar": "az", "psv eindhoven": "psv",
    "ajax de amsterdam": "ajax", "feyenoord rotterdam": "feyenoord",
    "estrasburgo": "strasbourg", "niza": "nice", "paris fc": "paris fc",
    "lila": "lille", "burdeos": "bordeaux", "saint etienne": "saint etienne",
    "vitoria guimaraes": "guimaraes", "academico viseu": "academico viseu",
    "nacional madeira": "nacional", "nec nijmegen": "nec",
    "cambuur leeuwarden": "cambuur", "fortuna sittard": "fortuna sittard",
    "west bromwich": "west bromwich albion", "west ham": "west ham united",
    "athletic bilbao": "athletic club", "racing santander": "racing santander",
}

UMBRAL_SIMILITUD = 0.86      # por debajo de esto, no se empareja


def _sin_tildes(texto: str) -> str:
    t = unicodedata.normalize("NFKD", str(texto or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c))


def normalizar(nombre: str) -> str:
    """Nombre reducido a lo que de verdad identifica al club."""
    t = _sin_tildes(nombre)
    t = t.replace("&", " and ").replace("'", "").replace(".", " ")
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    if t in ALIAS_MANUAL:
        return ALIAS_MANUAL[t]
    return t


def _tokens(nombre: str) -> Tuple[Set[str], Set[str]]:
    """Devuelve (todos los tokens, tokens identificativos)."""
    brutos = [t for t in normalizar(nombre).split(" ") if t]
    utiles = [t for t in brutos if t not in RUIDO or t in NUCLEO_PROTEGIDO]
    return set(brutos), set(utiles or brutos)


@dataclass
class Emparejamiento:
    nombre_origen: str
    slug: Optional[str]
    confianza: float
    metodo: str

    @property
    def ok(self) -> bool:
        return self.slug is not None


class Emparejador:
    """Empareja nombres contra un catalogo de slugs conocidos."""

    def __init__(self, slugs_conocidos: Iterable[str]):
        self.slugs = sorted(set(slugs_conocidos))
        # Indices para no recorrer los 240 equipos en cada intento.
        self._por_normal: Dict[str, str] = {}
        self._por_nucleo: Dict[frozenset, List[str]] = {}
        for s in self.slugs:
            legible = s.replace("_", " ")
            self._por_normal.setdefault(normalizar(legible), s)
            _, nucleo = _tokens(legible)
            self._por_nucleo.setdefault(frozenset(nucleo), []).append(s)

    def emparejar(self, nombre: str) -> Emparejamiento:
        norm = normalizar(nombre)

        # 1) Coincidencia exacta tras normalizar.
        if norm in self._por_normal:
            return Emparejamiento(nombre, self._por_normal[norm], 1.0, "exacto")

        # 2) Mismo nucleo identificativo: "VfB Stuttgart" == "stuttgart".
        _, nucleo = _tokens(nombre)
        candidatos = self._por_nucleo.get(frozenset(nucleo))
        if candidatos and len(candidatos) == 1:
            return Emparejamiento(nombre, candidatos[0], 0.97, "nucleo")
        if candidatos and len(candidatos) > 1:
            # Ambiguo: dos clubes comparten nucleo. No se elige a ciegas.
            return Emparejamiento(nombre, None, 0.0, f"ambiguo:{len(candidatos)}")

        # 2b) Subconjunto en cualquiera de las dos direcciones.
        #
        # Las dos fuentes abrevian de forma distinta y ninguna es "la corta":
        # Betplay dice "Blackburn Rovers" donde el modelo dice "blackburn", pero
        # dice "Tottenham" donde el modelo dice "tottenham hotspur". Exigir
        # igualdad de nucleos falla en ambos casos. Se acepta el subconjunto
        # solo si UN unico equipo encaja: con dos, seria una moneda al aire.
        encajes = [slug for nucleo_conocido, slugs in self._por_nucleo.items()
                   for slug in slugs
                   if nucleo and nucleo_conocido and
                   (nucleo <= nucleo_conocido or nucleo_conocido <= nucleo)]
        if len(set(encajes)) == 1:
            return Emparejamiento(nombre, encajes[0], 0.93, "subconjunto")
        if len(set(encajes)) > 1:
            return Emparejamiento(nombre, None, 0.0, f"subconjunto_ambiguo:{len(set(encajes))}")

        # 3) Similitud textual, con umbral alto y sin ambiguedad.
        puntuaciones = [(difflib.SequenceMatcher(None, norm, k).ratio(), v)
                        for k, v in self._por_normal.items()]
        puntuaciones.sort(reverse=True)
        if puntuaciones:
            mejor, slug = puntuaciones[0]
            segundo = puntuaciones[1][0] if len(puntuaciones) > 1 else 0.0
            # Si el segundo esta casi igual de cerca, el "mejor" no es fiable.
            if mejor >= UMBRAL_SIMILITUD and mejor - segundo >= 0.04:
                return Emparejamiento(nombre, slug, round(mejor, 3), "similitud")
            if mejor >= UMBRAL_SIMILITUD:
                return Emparejamiento(nombre, None, round(mejor, 3), "similitud_ambigua")

        return Emparejamiento(nombre, None, round(puntuaciones[0][0], 3) if puntuaciones else 0.0,
                              "sin_coincidencia")

    def informe(self, nombres: Iterable[str]) -> Dict[str, object]:
        resultados = [self.emparejar(n) for n in nombres]
        ok = [r for r in resultados if r.ok]
        fallos = [r for r in resultados if not r.ok]
        por_metodo: Dict[str, int] = {}
        for r in resultados:
            por_metodo[r.metodo] = por_metodo.get(r.metodo, 0) + 1
        return {
            "total": len(resultados),
            "emparejados": len(ok),
            "tasa": round(len(ok) / len(resultados), 4) if resultados else 0.0,
            "por_metodo": por_metodo,
            "sin_emparejar": sorted({r.nombre_origen for r in fallos}),
        }
