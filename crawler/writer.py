"""Escritores en streaming: JSONL (por defecto) y JSON clasico particionado."""
from __future__ import annotations

import asyncio
import os
from typing import Dict, List, Optional

try:
    import orjson

    def _dumps(obj: Dict) -> bytes:
        return orjson.dumps(obj)
except ImportError:  # fallback sin dependencia
    import json

    def _dumps(obj: Dict) -> bytes:
        return json.dumps(obj, ensure_ascii=False).encode("utf-8")


class JsonlWriter:
    """Append en modo binario con buffer de disco. Un objeto JSON por linea."""

    def __init__(self, path: str, buffer_lines: int = 2000):
        self.path = path
        self.buffer_lines = buffer_lines
        self._buf: List[bytes] = []
        self._lock = asyncio.Lock()
        self.written = 0
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._fh = open(path, "ab", buffering=1024 * 1024)

    async def write_many(self, records: List[Dict]) -> None:
        if not records:
            return
        async with self._lock:
            for rec in records:
                try:
                    self._buf.append(_dumps(rec) + b"\n")
                except (TypeError, ValueError):
                    continue  # registro no serializable: se omite, no se aborta
            self.written += len(records)
            if len(self._buf) >= self.buffer_lines:
                self._flush()

    def _flush(self) -> None:
        if self._buf:
            self._fh.writelines(self._buf)
            self._fh.flush()
            self._buf.clear()

    async def close(self) -> None:
        async with self._lock:
            self._flush()
            self._fh.close()


class PartitionedJsonWriter:
    """Array JSON clasico, particionado cada `per_file` registros."""

    def __init__(self, prefix: str, per_file: int = 100_000):
        self.prefix = prefix
        self.per_file = per_file
        self._lock = asyncio.Lock()
        self._part = 0
        self._in_part = 0
        self._fh = None
        self.written = 0
        self._open_next()

    def _open_next(self) -> None:
        if self._fh is not None:
            self._fh.write(b"\n]\n")
            self._fh.close()
        self._part += 1
        self._in_part = 0
        path = f"{self.prefix}_part_{self._part:02d}.json"
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self._fh = open(path, "wb", buffering=1024 * 1024)
        self._fh.write(b"[\n")

    async def write_many(self, records: List[Dict]) -> None:
        async with self._lock:
            for rec in records:
                if self._in_part >= self.per_file:
                    self._open_next()
                sep = b"" if self._in_part == 0 else b",\n"
                try:
                    self._fh.write(sep + _dumps(rec))
                except (TypeError, ValueError):
                    continue
                self._in_part += 1
                self.written += 1

    async def close(self) -> None:
        async with self._lock:
            if self._fh is not None:
                self._fh.write(b"\n]\n")
                self._fh.flush()
                self._fh.close()
                self._fh = None
