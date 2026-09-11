"""El selector: cruza el modelo con las cuotas reales de Betplay y arma tickets.

Esta es la pieza que convierte todo lo anterior en algo apostable:

    cuotas capturadas de Betplay
        + matriz de marcadores de Dixon-Coles (por partido)
        + tasas de goleador (donde hay datos)
        -> EV de cada linea = p_modelo * cuota_betplay - 1
        -> combinado de patas de PARTIDOS DISTINTOS hasta la cuota objetivo

**Por que partidos distintos.** Dentro de un mismo partido las patas estan
correlacionadas y Betplay lo sabe: su Bet Builder recorta la cuota (ratio
mediano medido: 0.68). Entre partidos distintos las patas si son independientes,
la casa multiplica limpio y el EV del combinado es exactamente el producto de
los EV de sus patas.

**Lo que multiplica tambien amplifica.** Si las patas tienen EV negativo, el
combinado lo empeora: cinco patas al -4% dan -18.7%. Por eso el selector exige
EV positivo pata a pata y no "compensa" una mala con cuatro buenas.

    python selector.py --cuota-objetivo 50 --tickets 5
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "matchdata"))

from dixon_coles import DixonColes, partidos_de_primera_parte
from emparejar import Emparejador
from mercados import MercadosDerivados, Resultado

# Competiciones de Betplay que el modelo sabe valorar, y su codigo interno.
# La clave lleva el pais porque `liga_key` no es unico en el feed de Kambi.
LIGAS = {
    ("Inglaterra", "premier_league"): "E0",
    ("Inglaterra", "the_championship"): "E1",
    ("Espana", "la_liga"): "SP1",
    ("Italia", "serie_a"): "I1",
    ("Alemania", "bundesliga"): "D1",
    ("Francia", "ligue_1"): "F1",
    ("Paises Bajos", "eredivisie"): "N1",
    ("Portugal", "primeira_liga"): "P1",
}


def _sin_tildes(t: str) -> str:
    t = unicodedata.normalize("NFKD", str(t or ""))
    return "".join(c for c in t if not unicodedata.combining(c)).strip()


def cargar(raiz: str, tabla: str, ultima_captura: bool = True) -> pd.DataFrame:
    ficheros = glob.glob(os.path.join(raiz, tabla, "**", "*.parquet"), recursive=True)
    if not ficheros:
        raise SystemExit(f"Sin datos en {raiz}/{tabla}. Ejecuta betplay/capturar.py primero.")
    df = pd.concat([pd.read_parquet(f) for f in ficheros], ignore_index=True)
    if ultima_captura and "capturado_en" in df:
        df = df[df["capturado_en"] == df["capturado_en"].max()]
    return df


# --------------------------------------------------------------------------
# Traduccion de los mercados de Betplay al catalogo del modelo
# --------------------------------------------------------------------------

def _norm_mercado(m: str) -> str:
    return _sin_tildes(str(m)).lower().strip()


# El ordinal femenino se escribe de varias formas y NFKD lo convierte en "a":
# "1.a parte", "1a parte", "primera parte", "1er tiempo"... Detectarlo mal no
# lanza ningun error: valora un mercado de media parte con el modelo del
# partido completo, y "mas de 0.5 goles" pasa de ~0.50 a ~0.86 de probabilidad.
# Ese fue exactamente el origen de unos tickets con EV del +183%.
_RE_PRIMERA = re.compile(r"\b(1\s*\.?\s*a|1\s*er|primera|primer)\s+(parte|tiempo|mitad)\b")
_RE_SEGUNDA = re.compile(r"\b(2\s*\.?\s*a|2\s*o|segunda|segundo)\s+(parte|tiempo|mitad)\b")


def periodo_del_mercado(nombre_normalizado: str) -> str:
    """'completo' | 'primera' | 'segunda'. La segunda parte no tiene modelo."""
    if _RE_SEGUNDA.search(nombre_normalizado) or "descanso" in nombre_normalizado:
        return "segunda"
    if _RE_PRIMERA.search(nombre_normalizado):
        return "primera"
    return "completo"


def quitar_periodo(nombre_normalizado: str) -> str:
    n = _RE_PRIMERA.sub(" ", _RE_SEGUNDA.sub(" ", nombre_normalizado))
    return re.sub(r"\s*-\s*$|\s{2,}", " ", n).strip(" -").strip()


def contexto_de_linea(fila: pd.Series) -> Tuple[str, Optional[str]]:
    """(periodo, lado) de una linea, para poder liquidarla mas adelante."""
    m = _norm_mercado(fila["mercado"])
    periodo = periodo_del_mercado(m)
    base = quitar_periodo(m)
    local = _sin_tildes(str(fila.get("_local") or "")).lower().strip()
    visitante = _sin_tildes(str(fila.get("_visitante") or "")).lower().strip()
    lado = None
    if base.startswith("total de goles de"):
        equipo = base.split("total de goles de", 1)[-1].strip()
        lado = "local" if equipo == local else "visitante" if equipo == visitante else None
    elif base.startswith("handicap asiatico") or base == "handicap":
        participante = _sin_tildes(str(fila.get("participante") or "")).lower().strip()
        lado = "local" if participante == local else "visitante" if participante == visitante else None
    return periodo, lado


def valorar_linea(fila: pd.Series, cat: MercadosDerivados,
                  cat_ht: Optional[MercadosDerivados]) -> Optional[Resultado]:
    """Devuelve la valoracion del modelo para una linea concreta de Betplay.

    None cuando el mercado no esta cubierto. Devolver None es la respuesta
    correcta: un mercado mal traducido produce un EV inventado que parece bueno.
    """
    m = _norm_mercado(fila["mercado"])
    etiqueta = _sin_tildes(str(fila.get("etiqueta") or "")).lower().strip()
    linea = fila.get("linea")
    linea = float(linea) if linea is not None and not pd.isna(linea) else None

    periodo = periodo_del_mercado(m)
    if periodo == "segunda":
        return None            # no hay modelo de segunda parte: no se valora
    if periodo == "primera" and cat_ht is None:
        return None
    c = cat_ht if periodo == "primera" else cat
    base = quitar_periodo(m)

    def busca(lista: List[Resultado], sel: str) -> Optional[Resultado]:
        for r in lista:
            if _sin_tildes(r.seleccion).lower() == sel:
                return r
        return None

    participante = _sin_tildes(str(fila.get("participante") or "")).lower().strip()
    local = _sin_tildes(str(fila.get("_local") or "")).lower().strip()
    es_local = bool(participante) and participante == local

    if base in ("resultado final", "resultado"):
        return busca(c.resultado_final(), etiqueta)
    if base == "doble oportunidad":
        return busca(c.doble_oportunidad(), etiqueta.replace(" ", "").replace("o", ""))
    if base == "apuesta sin empate":
        return busca(c.apuesta_sin_empate(), "1" if es_local else "2")
    if base in ("ambos equipos marcaran", "ambos equipos marcan"):
        return busca(c.ambos_marcan(), "si" if etiqueta.startswith("s") else "no")
    if base == "resultado correcto":
        return busca(c.resultado_correcto(max_goles=9, minimo=0.0), etiqueta.replace(" ", ""))

    if base in ("total de goles", "total asiatico") and linea is not None:
        sel = "mas de" if etiqueta.startswith("mas") else "menos de"
        lista = c.total_asiatico(linea) if base == "total asiatico" else c.total_goles(linea)
        return busca(lista, sel)

    # "Total de goles de Fortaleza FC": el equipo va en el NOMBRE del mercado,
    # no en `participante`, que aqui viene vacio. Leerlo del campo equivocado
    # asignaba siempre el mismo lado y valoraba el equipo que no era.
    if base.startswith("total de goles de") and linea is not None:
        # El equipo se lee de `base`, NO del nombre crudo: en "Total de goles de
        # Real Madrid - 1a parte" el nombre crudo deja el equipo como
        # "real madrid - 1a parte", que jamas coincide con el local, y la linea
        # acababa asignada al visitante. Silencioso y sistematico: TODOS los
        # mercados de media parte valoraban al equipo contrario.
        equipo = base.split("total de goles de", 1)[-1].strip()
        if equipo == local:
            lado = "local"
        elif equipo == _sin_tildes(str(fila.get("_visitante") or "")).lower().strip():
            lado = "visitante"
        else:
            return None       # no se sabe de quien habla la linea: no se valora
        sel = "mas de" if etiqueta.startswith("mas") else "menos de"
        return busca(c.total_por_equipo(lado, linea), sel)

    # Handicap asiatico y handicap europeo de 2 vias: cada seleccion trae su
    # PROPIO signo, desde la perspectiva de su equipo. Para el visitante hay que
    # invertir la linea, porque el modelo mide la diferencia como local - visitante.
    # Pasar la misma linea a los dos lados daba probabilidades del 96% y un EV
    # del +260%: el error no lanza nada, solo produce tickets imposibles.
    if (base.startswith("handicap asiatico") or base == "handicap") and linea is not None:
        linea_local = linea if es_local else -linea
        return busca(c.handicap_asiatico(linea_local), "1" if es_local else "2")

    # El handicap 3-way comparte una unica linea entre las tres opciones, y esa
    # linea SI esta en perspectiva del local (verificado en el feed).
    if base == "handicap 3-way" and linea is not None:
        return busca(c.handicap_3way(linea), etiqueta)

    return None


# --------------------------------------------------------------------------
# Valoracion de un partido completo
# --------------------------------------------------------------------------

@dataclass
class Pata:
    """Una linea apostable con su EV, lista para entrar en un ticket."""

    event_id: int
    partido: str
    liga: str
    inicio: str
    mercado: str
    seleccion: str
    linea: Optional[float]
    cuota: float
    prob_modelo: float
    ev: float
    outcome_id: int
    # Necesarios para liquidar despues: sin el periodo, un mercado de primera
    # parte se resolveria con el marcador final; sin el lado, un total por
    # equipo se resolveria contra el equipo equivocado.
    periodo: str = "completo"
    lado: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "partido": self.partido, "liga": self.liga, "inicio": self.inicio,
            "mercado": self.mercado, "seleccion": self.seleccion, "linea": self.linea,
            "cuota": round(self.cuota, 2),
            "periodo": self.periodo, "lado": self.lado,
            "outcome_id": self.outcome_id, "event_id": self.event_id,
            "prob_modelo": round(self.prob_modelo, 4),
            "prob_implicita_casa": round(1 / self.cuota, 4),
            "ev_pct": round(self.ev * 100, 2),
        }


def patas_de_evento(cuotas_ev: pd.DataFrame, cat: MercadosDerivados,
                    cat_ht: Optional[MercadosDerivados], meta: Dict[str, Any],
                    ev_minimo: float, cuota_min: float, cuota_max: float,
                    desvio_maximo: float = 0.06) -> List[Pata]:
    """`desvio_maximo` es el guardarrail que da sentido a todo lo demas.

    Ordenar las patas por EV y quedarse con las mejores selecciona, por
    construccion, las lineas donde el modelo MAS se aparta del mercado. Y el
    Sprint 4 midio precisamente eso: cuando este modelo se aparta mucho, se
    equivoca el modelo (en los 6 cortes de la prueba de discrepancias ganaba el
    mercado). El Sprint 4b anadio el matiz util: las desviaciones PEQUENAS
    (~2 puntos) si apuntaban en la direccion correcta.

    Asi que un EV del +90% no es una oportunidad: es la senal de que el modelo
    esta roto para esa linea. Se descarta todo lo que se aparte mas de
    `desvio_maximo` de la probabilidad implicita de la casa.
    """
    salida: List[Pata] = []
    for _, fila in cuotas_ev.iterrows():
        cuota = float(fila["cuota"])
        if not (cuota_min <= cuota <= cuota_max):
            continue
        r = valorar_linea(fila, cat, cat_ht)
        if r is None or r.prob_efectiva <= 0:
            continue
        ev = r.ev(cuota)
        if ev < ev_minimo:
            continue
        # Guardarrail: descartar la cola donde el modelo delira.
        if abs(r.prob_efectiva - 1.0 / cuota) > desvio_maximo:
            continue
        periodo, lado = contexto_de_linea(fila)
        salida.append(Pata(
            event_id=int(fila["event_id"]), partido=meta["partido"], liga=meta["liga"],
            inicio=meta["inicio"], mercado=str(fila["mercado"]), seleccion=r.seleccion,
            linea=r.linea, cuota=cuota, prob_modelo=r.prob_efectiva, ev=ev,
            outcome_id=int(fila["outcome_id"]), periodo=periodo, lado=lado))
    return salida


# --------------------------------------------------------------------------
# Construccion de tickets
# --------------------------------------------------------------------------

@dataclass
class Ticket:
    patas: List[Pata] = field(default_factory=list)

    @property
    def cuota(self) -> float:
        return float(np.prod([p.cuota for p in self.patas])) if self.patas else 0.0

    @property
    def probabilidad(self) -> float:
        """Patas de partidos distintos: independientes, se multiplican."""
        return float(np.prod([p.prob_modelo for p in self.patas])) if self.patas else 0.0

    @property
    def ev(self) -> float:
        return self.probabilidad * self.cuota - 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "n_patas": len(self.patas),
            "cuota_combinada": round(self.cuota, 2),
            "probabilidad_real_pct": round(self.probabilidad * 100, 3),
            "probabilidad_implicita_casa_pct": round(100 / self.cuota, 3) if self.cuota else None,
            "ev_pct": round(self.ev * 100, 2),
            "una_de_cada": int(round(1 / self.probabilidad)) if self.probabilidad > 0 else None,
            "patas": [p.to_dict() for p in self.patas],
        }


def construir_tickets(patas: List[Pata], objetivo: float, n_tickets: int,
                      max_patas: int = 6, tolerancia: float = 0.35) -> List[Ticket]:
    """Arma combinados de partidos distintos hasta acercarse a la cuota objetivo.

    Estrategia: ordenar por EV y recorrer, anadiendo la mejor pata de un partido
    que el ticket aun no use, hasta alcanzar el objetivo. Cada ticket consume
    sus patas para que los cinco del dia no sean el mismo ticket repetido.
    """
    disponibles = sorted(patas, key=lambda p: -p.ev)
    usadas: set = set()
    tickets: List[Ticket] = []

    for _ in range(n_tickets):
        t = Ticket()
        eventos_usados: set = set()
        for p in disponibles:
            if p.outcome_id in usadas or p.event_id in eventos_usados:
                continue
            if len(t.patas) >= max_patas:
                break
            # No pasarse: si esta pata dispara la cuota muy por encima, se busca otra.
            if t.patas and t.cuota * p.cuota > objetivo * (1 + tolerancia):
                continue
            t.patas.append(p)
            eventos_usados.add(p.event_id)
            if t.cuota >= objetivo * (1 - tolerancia):
                break
        if len(t.patas) >= 2 and t.cuota >= objetivo * (1 - tolerancia):
            tickets.append(t)
            usadas.update(p.outcome_id for p in t.patas)
        else:
            break
    return tickets


# --------------------------------------------------------------------------

def run(args: argparse.Namespace) -> Dict[str, Any]:
    eventos = cargar(args.almacen, "eventos")
    cuotas = cargar(args.almacen, "cuotas")
    historico = pd.concat([pd.read_parquet(f) for f in
                           glob.glob(os.path.join(args.warehouse, "matches", "**", "*.parquet"),
                                     recursive=True)], ignore_index=True)

    eventos["liga_modelo"] = [LIGAS.get((_sin_tildes(p), k))
                              for p, k in zip(eventos["pais"], eventos["liga_key"])]
    cubiertos = eventos[eventos["liga_modelo"].notna()].copy()
    print(f"{len(eventos)} partidos capturados, {len(cubiertos)} en ligas que el modelo conoce")

    modelos: Dict[str, Tuple[DixonColes, Optional[DixonColes], Emparejador]] = {}
    todas_las_patas: List[Pata] = []
    sin_emparejar: List[str] = []
    valorados = 0

    for liga_modelo, grupo in cubiertos.groupby("liga_modelo"):
        h = historico[historico["liga"] == liga_modelo].copy()
        h = h.rename(columns={"goles_local": "y_goles_local",
                              "goles_visitante": "y_goles_visitante"})
        filas = h.sort_values("kickoff_utc").to_dict("records")[-args.partidos_historicos:]
        if len(filas) < 300:
            continue
        ref = max(f["kickoff_utc"] for f in filas)
        dc = DixonColes(semivida_dias=args.semivida).fit(filas, fecha_referencia=ref)
        ht_filas = partidos_de_primera_parte(filas)
        dc_ht = None
        if len(ht_filas) >= 300:
            try:
                dc_ht = DixonColes(semivida_dias=args.semivida).fit(ht_filas, fecha_referencia=ref)
            except ValueError:
                dc_ht = None
        emp = Emparejador(set(h["equipo_local"]) | set(h["equipo_visitante"]))
        modelos[liga_modelo] = (dc, dc_ht, emp)
        print(f"  {liga_modelo}: modelo ajustado sobre {len(filas)} partidos"
              f"{' (+1a parte)' if dc_ht else ''}")

        for ev in grupo.itertuples():
            m_local, m_visit = emp.emparejar(ev.local), emp.emparejar(ev.visitante)
            if not (m_local.ok and m_visit.ok):
                sin_emparejar.extend([n.nombre_origen for n in (m_local, m_visit) if not n.ok])
                continue
            cat = MercadosDerivados(dc.matriz_marcadores(m_local.slug, m_visit.slug))
            cat_ht = (MercadosDerivados(dc_ht.matriz_marcadores(m_local.slug, m_visit.slug))
                      if dc_ht else None)
            sub = cuotas[cuotas["event_id"] == ev.event_id].copy()
            sub["_local"] = ev.local
            sub["_visitante"] = ev.visitante
            meta = {"partido": f"{ev.local} - {ev.visitante}", "liga": ev.liga,
                    "inicio": ev.inicio_utc}
            todas_las_patas.extend(patas_de_evento(sub, cat, cat_ht, meta, args.ev_minimo,
                                                   args.cuota_min, args.cuota_max,
                                                   args.desvio_maximo))
            valorados += 1

    tickets = construir_tickets(todas_las_patas, args.cuota_objetivo, args.tickets,
                                max_patas=args.max_patas)

    informe = {
        "generado_en": str(pd.Timestamp.utcnow()),
        "partidos_valorados": valorados,
        "equipos_sin_emparejar": sorted(set(sin_emparejar)),
        "patas_con_ev_positivo": len(todas_las_patas),
        "ev_minimo_exigido_pct": round(args.ev_minimo * 100, 2),
        "desvio_maximo_permitido_pct": round(args.desvio_maximo * 100, 2),
        "cuota_objetivo": args.cuota_objetivo,
        "tickets": [t.to_dict() for t in tickets],
        "aviso": ("El EV se calcula con probabilidades del modelo, que NO ha demostrado "
                  "batir al mercado. Un EV positivo aqui significa que el modelo discrepa "
                  "de Betplay, no que tenga razon. Ver model/README.md."),
    }
    print("\n" + json.dumps({k: informe[k] for k in
                             ("partidos_valorados", "patas_con_ev_positivo", "cuota_objetivo")},
                            ensure_ascii=False, indent=2))
    for i, t in enumerate(tickets, 1):
        d = t.to_dict()
        print(f"\n--- TICKET {i}: cuota {d['cuota_combinada']} | "
              f"probabilidad real {d['probabilidad_real_pct']}% "
              f"(1 de cada {d['una_de_cada']}) | EV {d['ev_pct']:+.1f}% ---")
        for p in d["patas"]:
            print(f"   {p['cuota']:>6.2f}  {p['partido'][:34]:<34} {p['mercado'][:24]:<24} "
                  f"{str(p['seleccion'])[:12]:<12} EV {p['ev_pct']:+6.1f}%")

    if args.informe:
        os.makedirs(os.path.dirname(os.path.abspath(args.informe)) or ".", exist_ok=True)
        with open(args.informe, "w", encoding="utf-8") as fh:
            json.dump(informe, fh, ensure_ascii=False, indent=2)
        print("\nInforme: " + args.informe)
    return informe


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Selector de patas y constructor de tickets")
    p.add_argument("--almacen", default="../betplay/almacen")
    p.add_argument("--warehouse", default="../matchdata/warehouse")
    p.add_argument("--cuota-objetivo", type=float, default=50.0)
    p.add_argument("--tickets", type=int, default=5)
    p.add_argument("--max-patas", type=int, default=6)
    p.add_argument("--ev-minimo", type=float, default=0.02)
    p.add_argument("--desvio-maximo", type=float, default=0.06,
                   help="descarta patas donde el modelo se aparta mas de esto de la casa")
    p.add_argument("--cuota-min", type=float, default=1.5)
    p.add_argument("--cuota-max", type=float, default=4.0)
    p.add_argument("--semivida", type=float, default=365.0)
    p.add_argument("--partidos-historicos", type=int, default=2000)
    p.add_argument("--informe", default="reports/tickets.json")
    return p.parse_args()


if __name__ == "__main__":
    run(parse_args())
