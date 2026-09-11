# Sprint 2 — Ingesta de contenido y extracción estructurada

Convierte las URLs del Sprint 1 en **hechos con procedencia**, almacenados en
Parquet particionado y consultables con DuckDB.

```
URLs (JSONL del Sprint 1)
   └─ fetcher.py    robots.txt + rate-limit por host + caché 304 + backoff
       └─ extract.py   texto (trafilatura) y tablas (pandas/lxml)
           └─ facts.py    valida procedencia — sin ella, no entra
               └─ store.py   Parquet por fecha  ->  DuckDB
```

## Uso

```bash
pip install -r requirements.txt

# 1) URLs (Sprint 1)
cd ../crawler && python main.py --target 4000 --out dataset.jsonl

# 2) Ingesta y extracción
cd ../extractor
python pipeline.py --dataset ../crawler/dataset.jsonl --limit 120 --concurrency 12

# 3) Consulta
python query.py
python query.py --sql "SELECT source_domain, COUNT(*) FROM facts GROUP BY 1"

# 4) Pruebas (sin red)
python test_offline.py
```

## Decisiones que conviene conocer

**La procedencia es un portón, no un campo.** `Fact.validate()` exige
`source_url`, `extracted_at`, `raw_snippet`, `fact_type` y `content`. Si falta
uno, el hecho se descarta y el motivo queda contado en el informe. No hay una
segunda vía de entrada al almacén: es la única defensa real contra que un
número inventado acabe pareciendo un dato.

**Robots.txt manda, incluso cuando falla.** Si `robots.txt` no se puede leer,
el dominio no se rastrea. `Crawl-delay` sobrescribe el retardo por defecto.

**El muestreo es estratificado por dominio.** El JSONL del Sprint 1 sale
agrupado por dominio; un `head -n` deja la muestra entera en manos de una sola
fuente. En la primera prueba real eso dio 0 hechos de 120 páginas — todas de
una SPA — y parecía un fallo del extractor. Es el motivo de `sample_stratified`.

**Las páginas sin hechos se clasifican.** `js_rendered` (el HTML no trae ni un
`<p>`: lo pinta JavaScript) frente a `texto_insuficiente`. La distinción decide
si merece la pena un navegador headless para ese dominio; un descarte silencioso
no decide nada.

**FBref esconde sus tablas en comentarios HTML.** `uncomment_tables()` las
descomenta antes de parsear. Understat publica JSON escapado dentro de
`<script>`, no tablas: `extract_understat_json()`.

**El almacén es append-only.** `fact_id` es un hash determinista de
url+tipo+contenido; la vista `facts` se queda con la extracción más reciente de
cada hecho y `facts_raw` conserva el histórico para auditar.

## Resultado medido (120 páginas, 16 dominios)

| Métrica | Valor |
|---|---|
| Descargadas | 113 (7 bloqueadas por robots.txt) |
| Con hechos | 60 |
| Sin hechos | 22 `js_rendered` + 31 `texto_insuficiente` |
| Procedencia | 100 % (0 hechos rechazados) |
| 2.ª pasada | 46 respuestas 304 desde caché |

Los `js_rendered` son el límite conocido: sin renderizado, sitios como
flashscore.com o abola.pt no entregan texto. Queda anotado para el Sprint 3,
donde la verdad numérica pasa a venir de una API de pago y estas fuentes se
usan solo como contexto.
