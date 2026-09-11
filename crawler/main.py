"""Pipeline ETL: descubre URLs reales de futbol y las serializa a JSONL.

Uso:
    python main.py --target 1000000 --out dataset_futbol_1m.jsonl
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import uuid
from typing import Dict, List, Optional

from classify import classify, is_football_url
from ingest import (
    expand_sitemap,
    make_session,
    sitemaps_from_robots,
    urls_from_common_crawl,
    urls_from_rss,
)
from normalize import DedupeStore, normalize_url, registrable_domain, url_hash
from seeds import SEEDS, SITEMAP_CANDIDATES, Seed
from writer import JsonlWriter, PartitionedJsonWriter

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None


def build_record(url: str, seed: Seed, method: str, now: str) -> Dict:
    domain = registrable_domain(url)
    category, subcategory = classify(url, seed.default_category)
    return {
        "id": str(uuid.uuid4()),
        "url": url,
        "domain": domain,
        "category": category,
        "subcategory": subcategory,
        "language": seed.language,
        "region": seed.region,
        "source_method": method,
        "discovered_at": now,
    }


class Pipeline:
    """Acumula registros hasta `target`, con reparto justo entre dominios.

    Sin cupo por dominio, el primer sitemap grande que responde llena el
    objetivo entero y el dataset queda reducido a una sola fuente. La
    diversidad de fuentes es el producto, asi que el cupo es parte del diseno,
    no una optimizacion.
    """

    def __init__(self, writer, dedupe: DedupeStore, target: int, strict_football: bool,
                 per_domain_cap: Optional[int] = None):
        self.writer = writer
        self.dedupe = dedupe
        self.target = target
        self.strict_football = strict_football
        self.per_domain_cap = per_domain_cap
        self.by_domain: Dict[str, int] = {}
        self.emitted = 0
        self.bar = tqdm(total=target, unit="url") if tqdm else None

    @property
    def done(self) -> bool:
        return self.emitted >= self.target

    async def emit(self, raw_urls: List[str], seed: Seed, method: str) -> None:
        if not raw_urls or self.done:
            return
        now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        candidates = []
        for raw in raw_urls:
            if not raw.startswith("http"):
                continue
            norm = normalize_url(raw)
            if self.strict_football and not is_football_url(norm):
                continue
            candidates.append(norm)
        if not candidates:
            return
        by_hash = {url_hash(u): u for u in candidates}
        fresh = self.dedupe.filter_and_mark(by_hash.keys())
        records = [build_record(by_hash[h], seed, method, now) for h in fresh]
        remaining = self.target - self.emitted
        if self.per_domain_cap is not None:
            usado = self.by_domain.get(seed.domain, 0)
            remaining = min(remaining, self.per_domain_cap - usado)
        records = records[: max(remaining, 0)]
        if not records:
            return
        await self.writer.write_many(records)
        self.emitted += len(records)
        self.by_domain[seed.domain] = self.by_domain.get(seed.domain, 0) + len(records)
        if self.bar:
            self.bar.update(len(records))


async def harvest_domain(
    session, seed: Seed, sem: asyncio.Semaphore, pipe: Pipeline, use_cc: bool
) -> None:
    # 1) Sitemaps declarados en robots.txt (fuente canonica).
    sitemaps = await sitemaps_from_robots(session, seed.domain, sem)
    # 2) Rutas convencionales como respaldo.
    if not sitemaps:
        sitemaps = [f"https://{seed.domain}{p}" for p in SITEMAP_CANDIDATES]

    batch: List[str] = []
    for sm in sitemaps:
        if pipe.done:
            break
        async for page_url in expand_sitemap(session, sm, sem):
            batch.append(page_url)
            if len(batch) >= 5000:
                await pipe.emit(batch, seed, "sitemap_xml")
                batch.clear()
                if pipe.done:
                    return
    if batch:
        await pipe.emit(batch, seed, "sitemap_xml")
        batch.clear()

    # 3) Feeds RSS comunes.
    for feed in (f"https://{seed.domain}/rss", f"https://{seed.domain}/feed",
                 f"https://{seed.domain}/rss.xml"):
        if pipe.done:
            return
        await pipe.emit(await urls_from_rss(session, feed, sem), seed, "rss_feed")

    # 4) Common Crawl como fuente de cola larga.
    if use_cc and not pipe.done:
        await pipe.emit(
            await urls_from_common_crawl(session, seed.domain, sem), seed, "common_crawl"
        )


async def run(args: argparse.Namespace) -> None:
    dedupe = DedupeStore(args.dedupe_db)
    if args.format == "jsonl":
        writer = JsonlWriter(args.out)
    else:
        writer = PartitionedJsonWriter(args.out.rsplit(".", 1)[0], per_file=args.per_file)

    sem = asyncio.Semaphore(args.concurrency)
    cap = args.per_domain_cap
    if cap is None and not args.no_fairness:
        cap = max(50, -(-args.target // max(len(SEEDS), 1)) * 2)
    pipe = Pipeline(writer, dedupe, args.target, strict_football=not args.no_filter,
                    per_domain_cap=cap)

    async def harvest_all(session) -> None:
        """Una pasada sobre todas las semillas, cancelable al llegar al objetivo."""
        tasks = [
            asyncio.create_task(harvest_domain(session, s, sem, pipe, args.common_crawl))
            for s in SEEDS
        ]
        # Vigilante: en cuanto se alcanza el objetivo se cancela lo pendiente.
        # Sin esto, las corrutinas siguen pidiendo sitemaps sobre una sesion que
        # ya se esta cerrando y el proceso queda colgado sin vaciar el buffer.
        async def watch_target() -> None:
            while not pipe.done:
                await asyncio.sleep(0.25)

        watcher = asyncio.create_task(watch_target())
        try:
            pending = set(tasks)
            while pending and not pipe.done:
                _, pending = await asyncio.wait(
                    pending | {watcher}, return_when=asyncio.FIRST_COMPLETED
                )
                pending.discard(watcher)
        finally:
            for task in tasks + [watcher]:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, watcher, return_exceptions=True)

    async with make_session() as session:
        try:
            await harvest_all(session)
            # Si el cupo por dominio dejo el objetivo corto, se rellena sin cupo:
            # primero diversidad, despues volumen.
            if not pipe.done and pipe.per_domain_cap is not None:
                pipe.per_domain_cap = None
                await harvest_all(session)
        finally:
            await writer.close()
            if pipe.bar:
                pipe.bar.close()
            print(
                f"\nRegistros escritos: {pipe.emitted} | dominios: {len(pipe.by_domain)} | "
                f"URLs unicas en dedupe: {dedupe.count()} | salida: {args.out}"
            )
            dedupe.close()


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ETL de URLs de futbol a JSONL")
    p.add_argument("--target", type=int, default=1_000_000, help="numero de URLs a recolectar")
    p.add_argument("--out", default="dataset_futbol_1m.jsonl")
    p.add_argument("--format", choices=("jsonl", "json_partitioned"), default="jsonl")
    p.add_argument("--per-file", type=int, default=100_000)
    p.add_argument("--concurrency", type=int, default=50)
    p.add_argument("--dedupe-db", default="dedupe.sqlite")
    p.add_argument("--common-crawl", action="store_true", help="incluir indice CDX de Common Crawl")
    p.add_argument("--per-domain-cap", type=int, default=None,
                   help="maximo de URLs por dominio (por defecto, reparto justo)")
    p.add_argument("--no-fairness", action="store_true",
                   help="desactiva el cupo por dominio")
    p.add_argument("--no-filter", action="store_true", help="no filtrar por relevancia futbolistica")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
