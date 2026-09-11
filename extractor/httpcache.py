"""Cache HTTP condicional (ETag / Last-Modified) respaldada por SQLite.

Guardar el validador junto al cuerpo permite revisitar una URL con
`If-None-Match` / `If-Modified-Since`: si el servidor responde 304 no se
descarga nada y se reutiliza el cuerpo almacenado. Es la diferencia entre
recrawlear 1M de URLs y recrawlear solo lo que cambio.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
import zlib
from typing import Dict, Optional, Tuple

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url_hash    TEXT PRIMARY KEY,
    url         TEXT NOT NULL,
    etag        TEXT,
    last_mod    TEXT,
    status      INTEGER,
    content_type TEXT,
    body        BLOB,
    fetched_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pages_fetched ON pages (fetched_at);
"""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpCache:
    def __init__(self, path: str = "http_cache.sqlite"):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def validators(self, url_hash: str) -> Dict[str, str]:
        row = self.conn.execute(
            "SELECT etag, last_mod FROM pages WHERE url_hash = ?", (url_hash,)
        ).fetchone()
        if not row:
            return {}
        headers = {}
        if row[0]:
            headers["If-None-Match"] = row[0]
        if row[1]:
            headers["If-Modified-Since"] = row[1]
        return headers

    def get_body(self, url_hash: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT body FROM pages WHERE url_hash = ?", (url_hash,)
        ).fetchone()
        if not row or row[0] is None:
            return None
        return zlib.decompress(row[0]).decode("utf-8", "replace")

    def put(
        self,
        url_hash: str,
        url: str,
        status: int,
        headers: Dict[str, str],
        body: Optional[str],
    ) -> None:
        blob = zlib.compress(body.encode("utf-8"), 6) if body is not None else None
        self.conn.execute(
            "INSERT INTO pages (url_hash, url, etag, last_mod, status, content_type,"
            " body, fetched_at) VALUES (?,?,?,?,?,?,?,?)"
            " ON CONFLICT(url_hash) DO UPDATE SET etag=excluded.etag,"
            " last_mod=excluded.last_mod, status=excluded.status,"
            " content_type=excluded.content_type,"
            " body=COALESCE(excluded.body, pages.body),"
            " fetched_at=excluded.fetched_at",
            (
                url_hash,
                url,
                headers.get("ETag"),
                headers.get("Last-Modified"),
                status,
                (headers.get("Content-Type") or "").split(";")[0].strip(),
                blob,
                _now(),
            ),
        )
        self.conn.commit()

    def touch(self, url_hash: str) -> None:
        """Marca un 304: el cuerpo sigue vigente."""
        self.conn.execute(
            "UPDATE pages SET fetched_at = ? WHERE url_hash = ?", (_now(), url_hash)
        )
        self.conn.commit()

    def stats(self) -> Tuple[int, int]:
        total = self.conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
        with_body = self.conn.execute(
            "SELECT COUNT(*) FROM pages WHERE body IS NOT NULL"
        ).fetchone()[0]
        return total, with_body

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()
