"""Ingestores asincronos de URLs reales: robots.txt -> sitemaps XML -> RSS -> Common Crawl.

Ninguna URL se fabrica. Todas provienen de un documento servido por el origen.
"""
from __future__ import annotations

import asyncio
import gzip
import io
import re
from typing import AsyncIterator, List, Optional, Tuple

import aiohttp

UA = "crisbet-research-crawler/1.0 (+contacto: casromur@gmail.com)"
LOC_RE = re.compile(rb"<loc>\s*(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?\s*</loc>", re.S | re.I)
SITEMAP_TAG_RE = re.compile(rb"<sitemapindex", re.I)
RSS_LINK_RE = re.compile(rb"<link[^>]*>([^<]+)</link>|<link[^>]*href=\"([^\"]+)\"", re.I)


async def fetch_bytes(
    session: aiohttp.ClientSession, url: str, sem: asyncio.Semaphore, timeout: int = 25
) -> Optional[bytes]:
    """GET tolerante a fallos. Devuelve None en cualquier error, nunca lanza."""
    async with sem:
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True
            ) as resp:
                if resp.status != 200:
                    return None
                body = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeDecodeError, OSError):
            return None
    if body[:2] == b"\x1f\x8b" or url.endswith(".gz"):
        try:
            body = gzip.decompress(body)
        except (OSError, EOFError, gzip.BadGzipFile):
            return None
    return body


async def sitemaps_from_robots(
    session: aiohttp.ClientSession, domain: str, sem: asyncio.Semaphore
) -> List[str]:
    """Lee robots.txt y extrae las directivas Sitemap: declaradas por el sitio."""
    body = await fetch_bytes(session, f"https://{domain}/robots.txt", sem, timeout=15)
    if not body:
        return []
    found = []
    for line in body.decode("utf-8", "ignore").splitlines():
        if line.lower().startswith("sitemap:"):
            found.append(line.split(":", 1)[1].strip())
    return found


async def expand_sitemap(
    session: aiohttp.ClientSession,
    url: str,
    sem: asyncio.Semaphore,
    depth: int = 0,
    max_depth: int = 3,
) -> AsyncIterator[str]:
    """Expande recursivamente un sitemap o sitemapindex y emite URLs de paginas."""
    if depth > max_depth:
        return
    body = await fetch_bytes(session, url, sem)
    if not body:
        return
    locs = [m.group(1).decode("utf-8", "ignore").strip() for m in LOC_RE.finditer(body)]
    if not locs:
        return
    is_index = bool(SITEMAP_TAG_RE.search(body[:4000]))
    if is_index:
        # Expansion concurrente de los hijos, acotada por el semaforo global.
        queues: List[asyncio.Task] = []

        async def drain(child: str) -> List[str]:
            return [u async for u in expand_sitemap(session, child, sem, depth + 1, max_depth)]

        for child in locs[:500]:  # cota por indice para no explotar un solo dominio
            queues.append(asyncio.create_task(drain(child)))
        for task in asyncio.as_completed(queues):
            for page_url in await task:
                yield page_url
    else:
        for page_url in locs:
            yield page_url


async def urls_from_rss(
    session: aiohttp.ClientSession, feed_url: str, sem: asyncio.Semaphore
) -> List[str]:
    body = await fetch_bytes(session, feed_url, sem, timeout=15)
    if not body:
        return []
    out = []
    for m in RSS_LINK_RE.finditer(body):
        val = m.group(1) or m.group(2)
        if val:
            val = val.decode("utf-8", "ignore").strip()
            if val.startswith("http"):
                out.append(val)
    return out


CC_INDEX = "https://index.commoncrawl.org/{index}-index"


async def urls_from_common_crawl(
    session: aiohttp.ClientSession,
    domain: str,
    sem: asyncio.Semaphore,
    index: str = "CC-MAIN-2026-22",
    page_limit: int = 5,
) -> List[str]:
    """Consulta el indice CDX publico de Common Crawl para un dominio."""
    out: List[str] = []
    base = CC_INDEX.format(index=index)
    for page in range(page_limit):
        q = f"{base}?url=*.{domain}%2F*&output=json&limit=10000&page={page}"
        body = await fetch_bytes(session, q, sem, timeout=60)
        if not body:
            break
        import json as _json

        for line in body.splitlines():
            if not line:
                continue
            try:
                rec = _json.loads(line)
            except ValueError:
                continue
            u = rec.get("url")
            if u:
                out.append(u)
        if len(out) == 0:
            break
    return out


def make_session() -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(limit=100, limit_per_host=4, ttl_dns_cache=300)
    return aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": UA, "Accept-Encoding": "gzip, deflate"},
    )
