"""Clasificador heuristico de URLs a (category, subcategory).

Opera solo sobre la URL (path + query), sin descargar el documento, para que la
clasificacion sea O(1) y no agregue trafico. Las reglas se evaluan en orden de
especificidad: subcategoria explicita primero, luego categoria, luego el
default del dominio semilla.
"""
from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

# (regex sobre la URL en minusculas) -> (category, subcategory)
RULES: Tuple[Tuple[re.Pattern, str, str], ...] = tuple(
    (re.compile(pat), cat, sub)
    for pat, cat, sub in [
        # estadisticas_avanzadas
        (r"[/\-_](xg|expected-goals|expected_goals)\b", "estadisticas_avanzadas", "xg_metricas"),
        (r"\b(ppda|packing|progressive-passes)\b", "estadisticas_avanzadas", "metricas_presion"),
        (r"[/\-_](heatmap|mapa-de-calor|shotmap|radar)", "estadisticas_avanzadas", "visualizacion"),
        (r"[/\-_](stats|estadisticas|statistics|squads|plantillas)\b", "estadisticas_avanzadas", "general"),
        (r"[/\-_](partidos|matches|matchlogs)/", "estadisticas_avanzadas", "match_report"),
        # predicciones
        (r"[/\-_](prediction|predicciones|pronostico|pronosticos|tips|betting-tips)", "predicciones", "pronostico"),
        (r"[/\-_](odds|cuotas|quote|quoten)\b", "predicciones", "cuotas"),
        (r"\b(elo|poisson|ratings?)\b", "predicciones", "modelo_estadistico"),
        (r"[/\-_](value-?bets?|surebets?|arbitrage)", "predicciones", "value_bet"),
        # resultados_vivo
        (r"[/\-_](live|en-vivo|envivo|directo|minuto-a-minuto|livescore)", "resultados_vivo", "marcador_vivo"),
        (r"[/\-_](h2h|head-to-head|enfrentamientos)", "resultados_vivo", "h2h"),
        (r"[/\-_](resultados|results|scores|clasificacion|standings|tabla)\b", "resultados_vivo", "resultados_tabla"),
        # rumores_fichajes
        (r"[/\-_](fichajes|transfer|transfers|mercato|gerucht|rumours|rumores)", "rumores_fichajes", "mercado_pases"),
        (r"[/\-_](marktwert|market-value|valor-de-mercado|contrato|contract)", "rumores_fichajes", "valoracion_contratos"),
        # analisis_tactico
        (r"[/\-_](tactic|tactica|tactico|taktik|analisis-tactico|tactical)", "analisis_tactico", "pizarra_tactica"),
        (r"[/\-_](scouting|scout-report|informe-de-ojeador)", "analisis_tactico", "scouting"),
        (r"[/\-_](analysis|analisis|opinion|columna|column)\b", "analisis_tactico", "analisis_editorial"),
        # comunidades_foros
        (r"[/\-_](r/soccer|r/futbol|forum|foro|thread|comments/)", "comunidades_foros", "debate"),
        # noticias (mas generico, al final)
        (r"[/\-_](noticias|news|nachrichten|notizie|actualite|ultimas)", "noticias", "portada_general"),
        (r"[/\-_](lesion|injury|injuries|bajas|convocatoria|lineup|alineacion)", "noticias", "bajas_alineaciones"),
        (r"/\d{4}/\d{2}/\d{2}/", "noticias", "articulo_fechado"),
    ]
)

# Competencia detectada en la URL -> usada como enriquecimiento de subcategory.
LEAGUE_HINTS = re.compile(
    r"\b(laliga|la-liga|premier-?league|serie-?a|bundesliga|ligue-?1|"
    r"champions|libertadores|sudamericana|liga-?betplay|mls|brasileirao|"
    r"eredivisie|primeira-?liga|world-?cup|mundial|copa-?america)\b"
)


def classify(url: str, default_category: str = "noticias") -> Tuple[str, str]:
    """Devuelve (category, subcategory) para una URL."""
    low = url.lower()
    for pattern, cat, sub in RULES:
        if pattern.search(low):
            league = LEAGUE_HINTS.search(low)
            if league and sub in ("general", "portada_general", "articulo_fechado"):
                sub = f"{sub}:{league.group(1).replace('-', '')}"
            return cat, sub
    return default_category, "sin_clasificar"


def is_football_url(url: str) -> bool:
    """Filtro de relevancia: descarta secciones no futbolisticas de portales mixtos."""
    low = url.lower()
    negative = (
        "/baloncesto", "/basket", "/nba", "/tenis", "/tennis", "/f1", "/formula-1",
        "/motogp", "/ciclismo", "/cycling", "/golf", "/boxeo", "/boxing", "/nfl",
        "/mlb", "/nhl", "/rugby", "/handball", "/balonmano", "/voleibol", "/ufc",
        "/politica", "/economia", "/horoscopo", "/television", "/lifestyle",
        # falsos positivos lexicos: contienen "football"/"futbol" pero no son futbol
        "football-americain", "football-american", "futbol-americano",
        "football-australien", "futbol-sala", "futsal", "football-gaelique",
    )
    if any(token in low for token in negative):
        return False
    positive = (
        "futbol", "football", "soccer", "fussball", "calcio", "liga", "league",
        "champions", "premier", "bundesliga", "serie-a", "ligue", "laliga",
        "partido", "match", "equipo", "team", "club", "jugador", "player",
        "libertadores", "mundial", "world-cup", "fifa", "uefa", "conmebol",
    )
    return any(token in low for token in positive)
