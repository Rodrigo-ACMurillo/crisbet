"""Pruebas offline del Sprint 2: extraccion, procedencia y almacen.

No tocan la red. Se ejecutan con:  python test_offline.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from extract import extract_tables, extract_text, extract_understat_json, uncomment_tables
from facts import Fact, FactCollector
from store import FactStore

ARTICLE_HTML = """
<html><head><title>Analisis: Millonarios vs Nacional</title></head>
<body><nav>menu menu menu</nav>
<article><h1>Millonarios llega con tres bajas</h1>
<p>El equipo local afronta la fecha 12 con tres ausencias confirmadas en defensa,
segun el parte medico publicado por el club esta manana. El tecnico confirmo que
el lateral derecho no viaja y que el central titular arrastra una molestia
muscular desde el partido anterior contra el conjunto de la costa.</p>
<p>El rival, por su parte, encadena cuatro partidos sin perder fuera de casa y
ha marcado en todos ellos, con un promedio de goles esperados cercano a 1.7 por
encuentro en ese tramo de la temporada.</p></article>
<footer>copyright</footer></body></html>
"""

FBREF_HTML = """
<html><body><div id="wrap">
<!--
<table id="stats_standard"><thead><tr><th>Jugador</th><th>Goles</th><th>xG</th></tr></thead>
<tbody>
<tr><td>Radamel Falcao</td><td>7</td><td>6.4</td></tr>
<tr><td>Leonardo Castro</td><td>5</td><td>4.9</td></tr>
</tbody></table>
-->
</div></body></html>
"""

UNDERSTAT_HTML = """
<html><body><script>
var shotsData = JSON.parse('[{\\x22minute\\x22:\\x2223\\x22,\\x22xG\\x22:\\x220.42\\x22}]');
</script></body></html>
"""

failures = []


def check(name, condition, detail=""):
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {name}" + (f" -> {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(name)


def test_text():
    doc = extract_text(ARTICLE_HTML, "https://example.com/a")
    check("texto extraido", doc["text"] and len(doc["text"]) > 200, repr(doc["text"])[:120])
    check("nav y footer descartados", "menu menu menu" not in (doc["text"] or ""))
    check("titulo detectado", bool(doc["title"]), repr(doc["title"]))


def test_fbref_comments():
    check("tabla comentada revelada", "<table id=\"stats_standard\"" in uncomment_tables(FBREF_HTML))
    tables = extract_tables(FBREF_HTML, "https://fbref.com/es/comps/1/stats")
    check("tabla de FBref parseada", len(tables) == 1, str(len(tables)))
    if tables:
        t = tables[0]
        check("id de tabla conservado", t["name"] == "stats_standard", t["name"])
        check("filas de la tabla", t["n_rows"] == 2, str(t["n_rows"]))
        check("celda legible", t["rows"][0].get("Jugador") == "Radamel Falcao", str(t["rows"][0]))


def test_understat():
    data = extract_understat_json(UNDERSTAT_HTML)
    check("json de understat", data.get("shotsData", [{}])[0].get("minute") == "23", str(data))


def test_provenance_gate():
    collector = FactCollector()
    good = Fact(fact_type="article_text", source_url="https://example.com/a",
                source_domain="example.com", content={"text": "x"}, raw_snippet="x")
    check("hecho con procedencia aceptado", collector.add(good))

    sin_snippet = Fact(fact_type="article_text", source_url="https://example.com/b",
                       source_domain="example.com", content={"text": "y"}, raw_snippet="")
    check("hecho sin raw_snippet rechazado", not collector.add(sin_snippet))

    sin_url = Fact(fact_type="article_text", source_url="", source_domain="example.com",
                   content={"text": "z"}, raw_snippet="z")
    check("hecho sin source_url rechazado", not collector.add(sin_url))

    sin_contenido = Fact(fact_type="article_text", source_url="https://example.com/c",
                         source_domain="example.com", content={}, raw_snippet="w")
    check("hecho sin contenido rechazado", not collector.add(sin_contenido))

    check("tasa de procedencia de lo aceptado", collector.provenance_rate == 0.25,
          str(collector.provenance_rate))
    check("motivos de rechazo registrados", set(collector.rejected) ==
          {"missing:raw_snippet", "missing:source_url", "missing:content"}, str(collector.rejected))


def test_fact_id_stable():
    kw = dict(fact_type="table_row", source_url="https://fbref.com/x",
              source_domain="fbref.com", content={"a": 1}, raw_snippet="a")
    check("fact_id determinista", Fact(**kw).fact_id == Fact(**kw).fact_id)
    otro = dict(kw, content={"a": 2})
    check("fact_id distingue contenido", Fact(**kw).fact_id != Fact(**otro).fact_id)


def test_store_roundtrip():
    tmp = tempfile.mkdtemp(prefix="crisbet_")
    try:
        store = FactStore(os.path.join(tmp, "facts"))
        rows = [Fact(fact_type="article_text", source_url=f"https://example.com/{i}",
                     source_domain="example.com", content={"text": "hola"},
                     raw_snippet="hola").to_row() for i in range(5)]
        path = store.write(rows)
        check("particion hive por fecha", "partition_date=" in path, path)
        check("filas escritas", store.rows_written == 5, str(store.rows_written))

        if store.backend == "parquet":
            from store import duckdb_view
            con = duckdb_view(os.path.join(tmp, "facts"), os.path.join(tmp, "c.duckdb"))
            n, con_proc = con.execute(
                "SELECT COUNT(*), SUM(CASE WHEN raw_snippet IS NOT NULL THEN 1 ELSE 0 END)"
                " FROM facts").fetchone()
            check("duckdb lee el parquet", n == 5, str(n))
            check("procedencia al 100% en el almacen", con_proc == 5, str(con_proc))
            fechas = con.execute("SELECT DISTINCT partition_date FROM facts").fetchall()
            check("columna de particion visible", len(fechas) == 1, str(fechas))
            con.close()
        else:
            check("backend parquet disponible", False, "pyarrow no instalado")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_facts_from_page():
    from pipeline import facts_from_page

    class R:
        url = "https://fbref.com/es/comps/1/stats"
        body = FBREF_HTML
        status = 200

    rec = {"domain": "fbref.com", "language": "es", "category": "estadisticas",
           "subcategory": "equipos"}
    out = facts_from_page(rec, R(), "2026-09-10T00:00:00Z")
    kinds = [f.fact_type for f in out]
    check("genera table_meta", kinds.count("table_meta") == 1, str(kinds))
    check("genera una fila por registro", kinds.count("table_row") == 2, str(kinds))
    check("todo hecho cita su url", all(f.source_url == R.url for f in out))
    check("todo hecho lleva snippet", all(f.raw_snippet for f in out))


if __name__ == "__main__":
    for fn in (test_text, test_fbref_comments, test_understat, test_provenance_gate,
               test_fact_id_stable, test_store_roundtrip, test_facts_from_page):
        print("\n== " + fn.__name__ + " ==")
        fn()
    print("\n" + ("TODO OK" if not failures else "FALLOS: " + json.dumps(failures)))
    sys.exit(1 if failures else 0)
