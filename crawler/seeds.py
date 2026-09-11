"""Semillas verificadas: dominios reales de futbol en Europa y Latinoamerica.

Cada entrada declara el dominio, idioma, region y la categoria dominante. El
clasificador heuristico puede sobreescribir la categoria por URL, pero esto da
un fallback correcto cuando la URL no tiene senales lexicas.
"""
from typing import List, NamedTuple


class Seed(NamedTuple):
    domain: str
    language: str
    region: str
    default_category: str


SEEDS: List[Seed] = [
    # --- Noticias / diarios deportivos ---
    Seed("marca.com", "es", "europa", "noticias"),
    Seed("as.com", "es", "europa", "noticias"),
    Seed("mundodeportivo.com", "es", "europa", "noticias"),
    Seed("sport.es", "es", "europa", "noticias"),
    Seed("relevo.com", "es", "europa", "noticias"),
    Seed("estadiodeportivo.com", "es", "europa", "noticias"),
    Seed("lequipe.fr", "fr", "europa", "noticias"),
    Seed("gazzetta.it", "it", "europa", "noticias"),
    Seed("tuttosport.com", "it", "europa", "noticias"),
    Seed("corrieredellosport.it", "it", "europa", "noticias"),
    Seed("kicker.de", "de", "europa", "noticias"),
    Seed("bild.de", "de", "europa", "noticias"),
    Seed("record.pt", "pt", "europa", "noticias"),
    Seed("abola.pt", "pt", "europa", "noticias"),
    Seed("bbc.com", "en", "europa", "noticias"),
    Seed("skysports.com", "en", "europa", "noticias"),
    Seed("theguardian.com", "en", "europa", "noticias"),
    Seed("goal.com", "en", "global", "noticias"),
    Seed("espn.com", "en", "global", "noticias"),
    Seed("ole.com.ar", "es", "latam", "noticias"),
    Seed("tycsports.com", "es", "latam", "noticias"),
    Seed("lanacion.com.ar", "es", "latam", "noticias"),
    Seed("globoesporte.globo.com", "pt", "latam", "noticias"),
    Seed("lance.com.br", "pt", "latam", "noticias"),
    Seed("futbolred.com", "es", "latam", "noticias"),
    Seed("golcaracol.com", "es", "latam", "noticias"),
    Seed("antena2.com.co", "es", "latam", "noticias"),
    Seed("elcolombiano.com", "es", "latam", "noticias"),
    Seed("record.com.mx", "es", "latam", "noticias"),
    Seed("mediotiempo.com", "es", "latam", "noticias"),

    # --- Estadisticas avanzadas ---
    Seed("fbref.com", "es", "global", "estadisticas_avanzadas"),
    Seed("understat.com", "en", "global", "estadisticas_avanzadas"),
    Seed("whoscored.com", "en", "global", "estadisticas_avanzadas"),
    Seed("soccerway.com", "en", "global", "estadisticas_avanzadas"),
    Seed("footystats.org", "en", "global", "estadisticas_avanzadas"),
    Seed("worldfootball.net", "en", "global", "estadisticas_avanzadas"),

    # --- Predicciones y cuotas ---
    Seed("forebet.com", "en", "global", "predicciones"),
    Seed("predictz.com", "en", "global", "predicciones"),
    Seed("clubelo.com", "en", "global", "predicciones"),
    Seed("oddsportal.com", "en", "global", "predicciones"),
    Seed("betexplorer.com", "en", "global", "predicciones"),
    Seed("windrawwin.com", "en", "global", "predicciones"),

    # --- Fichajes y mercado ---
    Seed("transfermarkt.es", "es", "global", "rumores_fichajes"),
    Seed("fichajes.com", "es", "global", "rumores_fichajes"),
    Seed("footballtransfers.com", "en", "global", "rumores_fichajes"),

    # --- Analisis tactico ---
    Seed("coachesvoice.com", "en", "global", "analisis_tactico"),
    Seed("spielverlagerung.com", "de", "europa", "analisis_tactico"),
    Seed("totalfootballanalysis.com", "en", "global", "analisis_tactico"),

    # --- Resultados en vivo ---
    Seed("flashscore.com", "en", "global", "resultados_vivo"),
    Seed("sofascore.com", "en", "global", "resultados_vivo"),
    Seed("365scores.com", "en", "global", "resultados_vivo"),
    Seed("livescore.com", "en", "global", "resultados_vivo"),
    Seed("besoccer.com", "es", "global", "resultados_vivo"),

    # --- Comunidades ---
    Seed("reddit.com", "en", "global", "comunidades_foros"),
    Seed("bigsoccer.com", "en", "global", "comunidades_foros"),
]

# Rutas candidatas de sitemap (se prueban en orden; robots.txt tiene prioridad).
SITEMAP_CANDIDATES = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemaps/sitemap.xml",
    "/news-sitemap.xml",
    "/sitemap/sitemap-index.xml",
)
