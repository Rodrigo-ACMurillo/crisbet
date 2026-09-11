"""Liquidacion de apuestas: dado un marcador, ¿que paga cada pata?

Logica pura y sin red, para que se pueda probar exhaustivamente. Es la pieza
que convierte el selector de "promete un EV" en "sabemos cuanto devolvio".

**Una pata no gana o pierde: tiene cinco desenlaces.** Gana entera, gana media,
empata (devolucion), pierde media o pierde entera. Las lineas asiaticas de
cuarto (-0.25, +0.75) reparten la apuesta en dos mitades y pueden acabar en
media ganancia. Tratar eso como un binario infla el retorno medido, que es
justo el error que arruinaria el paper trading: se estaria midiendo con la
misma lente rota con la que se aposto.

Todo se expresa como `(ganado, devuelto)`, fracciones del importe:

    pago = ganado * cuota + devuelto

    gana entera   (1.0, 0.0)   -> cuota
    gana media    (0.5, 0.5)   -> cuota/2 + 1/2
    devolucion    (0.0, 1.0)   -> 1.0
    pierde media  (0.0, 0.5)   -> 0.5
    pierde entera (0.0, 0.0)   -> 0.0
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import List, Optional, Tuple

GANA = (1.0, 0.0)
GANA_MEDIA = (0.5, 0.5)
DEVUELVE = (0.0, 1.0)
PIERDE_MEDIA = (0.0, 0.5)
PIERDE = (0.0, 0.0)


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or "").lower())
    return "".join(c for c in t if not unicodedata.combining(c)).strip()


@dataclass
class Marcador:
    """El resultado de un partido, con el descanso si se conoce."""

    goles_local: int
    goles_visitante: int
    ht_local: Optional[int] = None
    ht_visitante: Optional[int] = None

    def del_periodo(self, periodo: str) -> Optional["Marcador"]:
        if periodo == "completo":
            return self
        if periodo == "primera":
            if self.ht_local is None or self.ht_visitante is None:
                return None
            return Marcador(self.ht_local, self.ht_visitante)
        return None       # la segunda parte no se apuesta en este sistema

    @property
    def total(self) -> int:
        return self.goles_local + self.goles_visitante

    @property
    def diferencia(self) -> int:
        return self.goles_local - self.goles_visitante


def _partes(linea: float) -> List[float]:
    """Una linea de cuarto es media apuesta en cada linea adyacente."""
    if abs((abs(linea * 4) % 2) - 1.0) < 1e-9:
        return [linea - 0.25, linea + 0.25]
    return [linea]


def _promediar(resultados: List[Tuple[float, float]]) -> Tuple[float, float]:
    n = len(resultados)
    return (sum(r[0] for r in resultados) / n, sum(r[1] for r in resultados) / n)


def _umbral(valor: int, linea: float, es_mas: bool) -> Tuple[float, float]:
    """Resuelve un over/under simple, con devolucion si la linea es entera."""
    if valor > linea:
        return GANA if es_mas else PIERDE
    if valor < linea:
        return PIERDE if es_mas else GANA
    return DEVUELVE       # solo puede pasar con linea entera


def _handicap(diferencia: int, linea: float) -> Tuple[float, float]:
    """Handicap asiatico desde la perspectiva del equipo apostado."""
    ajustada = diferencia + linea
    if ajustada > 0:
        return GANA
    if ajustada < 0:
        return PIERDE
    return DEVUELVE


def liquidar_pata(mercado: str, seleccion: str, linea: Optional[float],
                  marcador: Marcador, periodo: str = "completo",
                  lado: Optional[str] = None) -> Optional[Tuple[float, float]]:
    """(ganado, devuelto) de una pata. None si el mercado no se sabe liquidar.

    `lado` es "local"/"visitante" para los mercados que dependen de un equipo.
    Devolver None es deliberado: dar por perdida una pata que no se sabe
    resolver falsearia el ROI a la baja, y darla por ganada, al alza.
    """
    m = _norm(mercado)
    sel = _norm(seleccion)
    mk = marcador.del_periodo(periodo)
    if mk is None:
        return None

    if "resultado final" in m or m == "resultado":
        real = "1" if mk.diferencia > 0 else "2" if mk.diferencia < 0 else "x"
        return GANA if sel == real else PIERDE

    if "doble oportunidad" in m:
        real = "1" if mk.diferencia > 0 else "2" if mk.diferencia < 0 else "x"
        return GANA if real in sel else PIERDE

    if "sin empate" in m:
        if mk.diferencia == 0:
            return DEVUELVE
        real = "1" if mk.diferencia > 0 else "2"
        return GANA if sel == real else PIERDE

    if "ambos equipos" in m:
        ambos = mk.goles_local > 0 and mk.goles_visitante > 0
        return GANA if (ambos == sel.startswith("s")) else PIERDE

    if "resultado correcto" in m:
        return GANA if sel.replace(" ", "") == f"{mk.goles_local}-{mk.goles_visitante}" else PIERDE

    if "total de goles de" in m or (m.startswith("total") and lado):
        # Sin saber de que equipo habla la linea no se puede liquidar. Asumir
        # un lado la resolveria siempre contra el mismo equipo y falsearia el ROI.
        if lado not in ("local", "visitante") or linea is None:
            return None
        goles = mk.goles_local if lado == "local" else mk.goles_visitante
        return _promediar([_umbral(goles, p, sel.startswith("mas")) for p in _partes(linea)])

    if m.startswith("total"):
        if linea is None:
            return None
        return _promediar([_umbral(mk.total, p, sel.startswith("mas")) for p in _partes(linea)])

    if "handicap 3-way" in m:
        if linea is None:
            return None
        ajustada = mk.diferencia + linea
        real = "1" if ajustada > 0 else "2" if ajustada < 0 else "x"
        return GANA if sel == real else PIERDE

    if "handicap" in m:
        if linea is None or lado is None:
            return None
        # La linea viene en perspectiva del equipo apostado.
        dif = mk.diferencia if lado == "local" else -mk.diferencia
        return _promediar([_handicap(dif, p) for p in _partes(linea)])

    return None


def pago_de_pata(resultado: Tuple[float, float], cuota: float) -> float:
    """Lo que devuelve una unidad apostada."""
    ganado, devuelto = resultado
    return ganado * cuota + devuelto


def liquidar_ticket(patas: List[dict], marcadores: dict) -> Optional[dict]:
    """Liquida un combinado. `marcadores` va indexado por event_id.

    Si falta el resultado de una sola pata, el ticket entero queda pendiente:
    un combinado no se puede liquidar a medias.
    """
    pagos = []
    detalle = []
    for p in patas:
        mk = marcadores.get(p["event_id"])
        if mk is None:
            return None
        r = liquidar_pata(p["mercado"], p["seleccion"], p.get("linea"), mk,
                          p.get("periodo", "completo"), p.get("lado"))
        if r is None:
            return None
        pago = pago_de_pata(r, p["cuota"])
        pagos.append(pago)
        detalle.append({**p, "ganado": r[0], "devuelto": r[1], "pago": round(pago, 4)})

    total = 1.0
    for pago in pagos:
        total *= pago
    return {
        "pago": round(total, 4),
        "retorno": round(total - 1.0, 4),
        "acertado": total > 1.0,
        "patas": detalle,
    }
