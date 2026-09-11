"""Normalizacion de URLs y deduplicacion persistente en SQLite.

La dedup vive en disco (no en un set de Python) para que 1.000.000+ de URLs no
consuman RAM y para que el proceso sea reanudable tras una interrupcion.
"""
from __future__ import annotations

import hashlib
import sqlite3
from typing import Iterable, List
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_", "pk_", "mtm_", "ga_", "_hs")
TRACKING_EXACT = {
    "fbclid", "gclid", "msclkid", "dclid", "twclid", "igshid", "yclid",
    "ref", "referrer", "referer", "source", "session_id", "sessionid", "sid",
    "cmpid", "campaign", "mc_cid", "mc_eid", "spm", "share", "s_kwcid",
    "at_medium", "at_campaign", "ns_campaign", "CMP", "cmp", "icid", "ito",
}


def normalize_url(raw: str) -> str:
    """Canonicaliza el esquema, el host y los parametros de rastreo."""
    raw = raw.strip()
    parts = urlsplit(raw)
    scheme = "https" if parts.scheme in ("", "http", "https") else parts.scheme
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    if netloc.endswith(":443"):
        netloc = netloc[:-4]
    if netloc.endswith(":80"):
        netloc = netloc[:-3]

    path = parts.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")

    kept = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if k not in TRACKING_EXACT and not k.lower().startswith(TRACKING_PREFIXES)
    ]
    kept.sort()
    # El fragmento (#) nunca identifica un recurso distinto: se descarta.
    return urlunsplit((scheme, netloc, path, urlencode(kept), ""))


def url_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def registrable_domain(normalized: str) -> str:
    return urlsplit(normalized).netloc


class DedupeStore:
    """Conjunto de hashes SHA-256 respaldado por SQLite, apto para millones de filas."""

    def __init__(self, path: str = "dedupe.sqlite"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.execute("CREATE TABLE IF NOT EXISTS seen (h TEXT PRIMARY KEY)")
        self.conn.commit()

    def filter_and_mark(self, hashes: Iterable[str]) -> List[str]:
        """Devuelve solo los hashes no vistos y los marca como vistos."""
        unique = list(dict.fromkeys(hashes))
        if not unique:
            return []
        placeholders = ",".join("?" * len(unique))
        existing = {
            row[0]
            for row in self.conn.execute(
                f"SELECT h FROM seen WHERE h IN ({placeholders})", unique
            )
        }
        fresh = [h for h in unique if h not in existing]
        if fresh:
            self.conn.executemany(
                "INSERT OR IGNORE INTO seen (h) VALUES (?)", ((h,) for h in fresh)
            )
            self.conn.commit()
        return fresh

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0]

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
