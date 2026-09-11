"""Sprint 2: URLs -> HTML -> hechos con procedencia -> Parquet/DuckDB.

Lee el JSONL que produjo el Sprint 1, descarga cada URL respetando robots.txt
y convierte el HTML en filas de hechos. Cada fila conserva de donde salio.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import os
import sys
from typing import Dict, Iterator, List, Optional

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "crawler"))
from normalize import url_hash  # noqa: E402  (reutiliza el hashing del Sprint 1)

from extract import (  # noqa: E402
    TABLE_DOMAINS,
    extract_tables,
    extract_text,
    extract_understat_json,
    looks_client_rendered,
)
from facts import Fact, FactCollector, snippet  # noqa: E402
from fetcher import Fetcher, HostLimiter, RobotsGate, make_session  # noqa: E402
from httpcache import HttpCache  # noqa: E402
from store import FactStore  # noqa: E402

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def read_dataset(path: str, limit: Optional[int] = None,
                 domains: Optional[List[str]] = None) -> Iterator[Dict]:
    seen = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if domains and not any(d in rec.get("domain", "") for d in domains):
                continue
            yield rec
            seen += 1
            if limit and seen >= limit:
                return


def sample_stratified(path: str, limit: int, domains: Optional[List[str]] = None,
                      scan: int = 200_000) -> List[Dict]:
    """Toma URLs por turnos entre dominios en lugar de las N primeras del fichero.

    El JSONL sale ordenado por dominio, asi que un `head -n` deja la muestra
    entera en manos de una sola fuente — y si esa fuente resulta ser una SPA,
    el resultado es cero hechos y una conclusion falsa sobre el extractor.
    """
    por_dominio: Dict[str, List[Dict]] = {}
    for rec in read_dataset(path, scan, domains):
        por_dominio.setdefault(rec.get("domain", "?"), []).append(rec)

    salida: List[Dict] = []
    turno = 0
    while len(salida) < limit:
        avance = False
        for cola in por_dominio.values():
            if turno < len(cola):
                salida.append(cola[turno])
                avance = True
                if len(salida) >= limit:
                    break
        if not avance:
            break
        turno += 1
    return salida


def extraction_reason(html: str, facts: List["Fact"]) -> str:
    """Por que una pagina no dio hechos. Un descarte silencioso es un agujero ciego."""
    if facts:
        return "ok"
    return "js_rendered" if looks_client_rendered(html) else "texto_insuficiente"


def facts_from_page(rec: Dict, result, fetched_at: str) -> List[Fact]:
    """Convierte una pagina descargada en hechos. Todo hecho cita su origen."""
    url, html = result.url, result.body
    base = dict(
        source_url=url,
        source_domain=rec.get("domain") or "",
        fetched_at=fetched_at,
        http_status=result.status,
        language=rec.get("language"),
        category=rec.get("category"),
        subcategory=rec.get("subcategory"),
    )
    out: List[Fact] = []

    doc = extract_text(html, url)
    text = doc.get("text") or ""
    if len(text) >= 200:
        out.append(Fact(
            fact_type="article_text",
            content={"text": text, "n_chars": len(text), "author": doc.get("author")},
            raw_snippet=snippet(text),
            title=doc.get("title"),
            published_at=doc.get("published"),
            **base,
        ))

    if any(d in url for d in TABLE_DOMAINS):
        for table in extract_tables(html, url):
            out.append(Fact(
                fact_type="table_meta",
                content={k: table[k] for k in ("name", "index", "n_rows", "n_cols", "columns")},
                raw_snippet=snippet(json.dumps(table["columns"], ensure_ascii=False)),
                title=doc.get("title"),
                **base,
            ))
            for i, row in enumerate(table["rows"]):
                out.append(Fact(
                    fact_type="table_row",
                    content={"table": table["name"], "row_index": i, "cells": row},
                    raw_snippet=snippet(json.dumps(row, ensure_ascii=False)),
                    title=doc.get("title"),
                    **base,
                ))

    if "understat.com" in url:
        for name, payload in extract_understat_json(html).items():
            out.append(Fact(
                fact_type="dataset_json",
                content={"var": name, "data": payload},
                raw_snippet=snippet(json.dumps(payload, ensure_ascii=False)),
                title=doc.get("title"),
                **base,
            ))
    return out


def bump(stats: Dict, bucket: str, key: str) -> None:
    stats.setdefault(bucket, {})
    stats[bucket][key] = stats[bucket].get(key, 0) + 1


async def worker(queue: asyncio.Queue, fetcher: Fetcher, collector: FactCollector,
                 store: FactStore, flush_every: int, bar, stats: Dict[str, int]) -> None:
    while True:
        rec = await queue.get()
        if rec is None:
            queue.task_done()
            return
        try:
            url = rec["url"]
            result = await fetcher.fetch(url, url_hash(url))
            if not result.ok:
                bump(stats, "fetch_fallido", result.reason)
            else:
                fetched_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                page_facts = facts_from_page(rec, result, fetched_at)
                for fact in page_facts:
                    collector.add(fact)
                reason = extraction_reason(result.body, page_facts)
                bump(stats, "extraccion", reason)
                if reason != "ok":
                    bump(stats, "sin_hechos_por_dominio", rec.get("domain", "?"))
                if len(collector.rows) >= flush_every:
                    store.write(collector.drain())
            stats.setdefault("paginas", 0)
            stats["paginas"] += 1
            if bar:
                bar.update(1)
        except Exception as exc:  # una pagina rota no tumba el pipeline
            bump(stats, "errores", type(exc).__name__)
        finally:
            queue.task_done()


async def run(args) -> Dict:
    if args.secuencial:
        records = list(read_dataset(args.dataset, args.limit, args.domains))
    else:
        records = sample_stratified(args.dataset, args.limit, args.domains)
    if not records:
        raise SystemExit("Sin registros utilizables en " + args.dataset)

    cache = HttpCache(args.cache_db)
    store = FactStore(args.warehouse)
    collector = FactCollector()
    limiter = HostLimiter(default_delay=args.delay)
    sem = asyncio.Semaphore(args.concurrency)
    stats: Dict[str, int] = {}
    bar = tqdm(total=len(records), unit="pag") if tqdm else None

    async with make_session(args.concurrency) as session:
        fetcher = Fetcher(session, cache, RobotsGate(session, limiter), limiter, sem)
        queue: asyncio.Queue = asyncio.Queue(maxsize=args.concurrency * 4)
        workers = [
            asyncio.create_task(
                worker(queue, fetcher, collector, store, args.flush_every, bar, stats)
            )
            for _ in range(args.concurrency)
        ]
        for rec in records:
            await queue.put(rec)
        for _ in workers:
            await queue.put(None)
        await asyncio.gather(*workers)

    store.write(collector.drain())
    if bar:
        bar.close()
    cached_total, cached_bodies = cache.stats()
    cache.close()

    report = {
        "paginas_solicitadas": len(records),
        "fetch": dict(fetcher.counters),
        "paginas_procesadas": stats.pop("paginas", 0),
        "extraccion": stats.pop("extraccion", {}),
        "fetch_fallido": stats.pop("fetch_fallido", {}),
        "sin_hechos_por_dominio": stats.pop("sin_hechos_por_dominio", {}),
        "errores": stats.pop("errores", {}),
        "hechos_aceptados": collector.accepted,
        "hechos_rechazados": collector.rejected,
        "tasa_procedencia": round(collector.provenance_rate, 4),
        "backend_almacen": store.backend,
        "ficheros": store.files_written,
        "filas": store.rows_written,
        "cache": {"urls": cached_total, "con_cuerpo": cached_bodies},
        "warehouse": store.root,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)) or ".", exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, ensure_ascii=False, indent=2)
    return report


def parse_args():
    import argparse

    p = argparse.ArgumentParser(description="Sprint 2: ingesta y extraccion con procedencia")
    p.add_argument("--dataset", default="../crawler/dataset_futbol_1m.jsonl")
    p.add_argument("--limit", type=int, default=200, help="paginas a procesar")
    p.add_argument("--domains", nargs="*", help="filtrar por dominios concretos")
    p.add_argument("--secuencial", action="store_true",
                   help="leer las N primeras lineas en vez de muestrear por dominio")
    p.add_argument("--concurrency", type=int, default=10)
    p.add_argument("--delay", type=float, default=1.5,
                   help="segundos minimos entre peticiones al mismo host")
    p.add_argument("--flush-every", type=int, default=2000)
    p.add_argument("--cache-db", default="http_cache.sqlite")
    p.add_argument("--warehouse", default="warehouse/facts")
    p.add_argument("--report", default="reports/sprint2_ingest.json")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
