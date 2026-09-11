"""Fetcher respetuoso: robots.txt por dominio, rate-limit por host,
cache condicional y reintentos con backoff exponencial.

Reglas duras:
  - Si robots.txt prohibe la ruta para nuestro User-Agent, no se descarga. Punto.
  - `Crawl-delay` del propio robots.txt manda sobre el rate-limit por defecto.
  - Un host lento no bloquea a los demas: el limitador es por host.
"""
from __future__ import annotations

import asyncio
import random
import time
import urllib.robotparser as robotparser
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.parse import urlsplit

import aiohttp

USER_AGENT = "CrisbetBot/0.2 (+https://crisbet.example/bot; contacto: ops@crisbet.example)"
RETRYABLE = {408, 425, 429, 500, 502, 503, 504}
MAX_BODY_BYTES = 3_000_000


@dataclass
class FetchResult:
    url: str
    status: int
    body: Optional[str]
    content_type: str
    from_cache: bool
    reason: str = ""

    @property
    def ok(self) -> bool:
        return self.body is not None


class HostLimiter:
    """Un intervalo minimo entre peticiones al mismo host."""

    def __init__(self, default_delay: float = 1.0):
        self.default_delay = default_delay
        self._delays: Dict[str, float] = {}
        self._next_at: Dict[str, float] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    def set_delay(self, host: str, delay: float) -> None:
        self._delays[host] = max(delay, 0.2)

    async def acquire(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._delays.get(host, self.default_delay)
            now = time.monotonic()
            wait = self._next_at.get(host, 0.0) - now
            if wait > 0:
                await asyncio.sleep(wait)
            # Jitter: evita que N corrutinas golpeen el host en fase.
            self._next_at[host] = time.monotonic() + delay * random.uniform(0.9, 1.3)


class RobotsGate:
    """Cachea y aplica robots.txt por host. Ante duda, deniega."""

    def __init__(self, session: aiohttp.ClientSession, limiter: HostLimiter):
        self.session = session
        self.limiter = limiter
        self._parsers: Dict[str, Optional[robotparser.RobotFileParser]] = {}
        self._locks: Dict[str, asyncio.Lock] = {}

    async def _load(self, host: str) -> Optional[robotparser.RobotFileParser]:
        rp = robotparser.RobotFileParser()
        for scheme in ("https", "http"):
            try:
                async with self.session.get(
                    f"{scheme}://{host}/robots.txt", timeout=aiohttp.ClientTimeout(total=15)
                ) as resp:
                    if resp.status == 404:
                        rp.parse([])  # sin robots.txt: todo permitido
                        return rp
                    if resp.status >= 400:
                        continue
                    text = await resp.text(errors="replace")
                    rp.parse(text.splitlines())
                    return rp
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
                continue
        return None

    async def allowed(self, url: str) -> bool:
        host = urlsplit(url).netloc
        if host not in self._parsers:
            lock = self._locks.setdefault(host, asyncio.Lock())
            async with lock:
                if host not in self._parsers:
                    rp = await self._load(host)
                    self._parsers[host] = rp
                    if rp is not None:
                        delay = rp.crawl_delay(USER_AGENT)
                        if delay:
                            self.limiter.set_delay(host, float(delay))
        rp = self._parsers[host]
        if rp is None:
            return False  # robots.txt inaccesible => no se rastrea
        return rp.can_fetch(USER_AGENT, url)


def make_session(concurrency: int = 20) -> aiohttp.ClientSession:
    connector = aiohttp.TCPConnector(limit=concurrency, limit_per_host=2, ttl_dns_cache=600)
    return aiohttp.ClientSession(
        connector=connector,
        headers={"User-Agent": USER_AGENT, "Accept-Language": "es,en;q=0.8"},
        timeout=aiohttp.ClientTimeout(total=30, connect=10),
    )


class Fetcher:
    def __init__(self, session, cache, robots: RobotsGate, limiter: HostLimiter,
                 sem: asyncio.Semaphore, max_retries: int = 3):
        self.session = session
        self.cache = cache
        self.robots = robots
        self.limiter = limiter
        self.sem = sem
        self.max_retries = max_retries
        self.counters = {"fetched": 0, "not_modified": 0, "blocked": 0, "failed": 0}

    async def fetch(self, url: str, url_hash: str) -> FetchResult:
        if not await self.robots.allowed(url):
            self.counters["blocked"] += 1
            return FetchResult(url, 0, None, "", False, "robots_disallow")

        host = urlsplit(url).netloc
        headers = self.cache.validators(url_hash)

        for attempt in range(self.max_retries + 1):
            await self.limiter.acquire(host)
            async with self.sem:
                try:
                    async with self.session.get(url, headers=headers, allow_redirects=True) as resp:
                        if resp.status == 304:
                            self.cache.touch(url_hash)
                            self.counters["not_modified"] += 1
                            body = self.cache.get_body(url_hash)
                            return FetchResult(url, 304, body, "", True, "not_modified")

                        ctype = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
                        if resp.status >= 400:
                            if resp.status in RETRYABLE and attempt < self.max_retries:
                                await self._backoff(attempt, resp.headers.get("Retry-After"))
                                continue
                            self.cache.put(url_hash, url, resp.status, resp.headers, None)
                            self.counters["failed"] += 1
                            return FetchResult(url, resp.status, None, ctype, False, "http_error")

                        if "html" not in ctype and "xml" not in ctype and ctype:
                            self.counters["failed"] += 1
                            return FetchResult(url, resp.status, None, ctype, False, "not_html")

                        raw = await resp.content.read(MAX_BODY_BYTES)
                        text = raw.decode(resp.charset or "utf-8", "replace")
                        self.cache.put(url_hash, url, resp.status, resp.headers, text)
                        self.counters["fetched"] += 1
                        return FetchResult(str(resp.url), resp.status, text, ctype, False, "ok")
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    if attempt < self.max_retries:
                        await self._backoff(attempt, None)
                        continue
                    self.counters["failed"] += 1
                    return FetchResult(url, 0, None, "", False, f"network:{type(exc).__name__}")

        self.counters["failed"] += 1
        return FetchResult(url, 0, None, "", False, "retries_exhausted")

    @staticmethod
    async def _backoff(attempt: int, retry_after: Optional[str]) -> None:
        if retry_after and retry_after.isdigit():
            await asyncio.sleep(min(float(retry_after), 60.0))
            return
        await asyncio.sleep(min(2 ** attempt, 30) * random.uniform(0.8, 1.4))
