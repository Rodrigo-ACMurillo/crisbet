# Crawler de URLs de futbol -> JSONL

## Instalacion
```
pip install aiohttp orjson beautifulsoup4 tqdm
```

## Ejecucion (comando exacto)
```
python main.py --target 1000000 --out dataset_futbol_1m.jsonl --concurrency 50 --common-crawl
```

Variantes:
```
# JSON clasico particionado en volumenes de 100.000 registros
python main.py --target 1000000 --out dataset_futbol.json --format json_partitioned

# Prueba rapida, solo sitemaps
python main.py --target 5000 --out muestra.jsonl
```

## Modulos
| Archivo | Responsabilidad |
|---|---|
| `seeds.py` | 56 dominios semilla verificados + rutas candidatas de sitemap |
| `ingest.py` | Fetch asincrono: robots.txt, sitemaps XML (recursivo), RSS, Common Crawl CDX |
| `classify.py` | URL -> (category, subcategory) + filtro de relevancia futbolistica |
| `normalize.py` | Canonicalizacion de URL, hash SHA-256, dedup en SQLite (WAL) |
| `writer.py` | Escritores en streaming: JSONL y JSON particionado |
| `main.py` | Orquestacion y CLI |

## Garantias
- **Cero URLs inventadas.** Cada registro proviene de un sitemap, feed o indice servido por el origen.
- **Dedup en disco:** SQLite con clave primaria sobre SHA-256. Reanudable: si el proceso muere, relanzarlo no reescribe URLs ya vistas.
- **RAM constante:** buffers de 2.000 lineas, sin acumular el dataset en memoria.
- **Reanudable:** el JSONL se abre en modo append (`ab`) y la dedup persiste.

## Notas operativas
- `--concurrency 50` es el semaforo global; `limit_per_host=4` evita saturar un solo dominio.
- Alcanzar 1M de URLs requiere `--common-crawl`; solo con sitemaps se obtienen del orden de 200-400k.
- El User-Agent se identifica. Respeta `robots.txt` antes de escalar el volumen.
