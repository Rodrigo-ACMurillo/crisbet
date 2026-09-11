"""Almacen analitico: Parquet particionado por fecha + vistas DuckDB.

Layout:
    facts/partition_date=YYYY-MM-DD/part-<n>.parquet

DuckDB lee ese arbol con `read_parquet(..., hive_partitioning=true)`, asi que
consultar un rango de fechas no toca los ficheros de las demas particiones.
Sin pyarrow el almacen degrada a JSONL en la misma estructura de carpetas: la
ingesta nunca se detiene por una dependencia opcional.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any, Dict, List, Optional

try:
    import pyarrow as pa
    import pyarrow.parquet as pq
except ImportError:
    pa = pq = None

COLUMNS = [
    "fact_id", "fact_type", "source_url", "source_domain", "content",
    "raw_snippet", "extracted_at", "fetched_at", "http_status", "published_at",
    "title", "language", "category", "subcategory", "extractor", "partition_date",
]


class FactStore:
    def __init__(self, root: str = "warehouse/facts"):
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.files_written = 0
        self.rows_written = 0
        self.backend = "parquet" if pq is not None else "jsonl"

    def write(self, rows: List[Dict[str, Any]]) -> Optional[str]:
        if not rows:
            return None
        by_date: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            by_date.setdefault(row["partition_date"], []).append(row)
        last = None
        for date, chunk in by_date.items():
            last = self._write_partition(date, chunk)
        return last

    def _write_partition(self, date: str, chunk: List[Dict[str, Any]]) -> str:
        directory = os.path.join(self.root, f"partition_date={date}")
        os.makedirs(directory, exist_ok=True)
        name = f"part-{uuid.uuid4().hex[:12]}"
        normalized = [{c: r.get(c) for c in COLUMNS} for r in chunk]

        if pq is not None:
            path = os.path.join(directory, name + ".parquet")
            table = pa.Table.from_pydict(
                {c: [r[c] for r in normalized] for c in COLUMNS},
                schema=pa.schema([
                    (c, pa.int32() if c == "http_status" else pa.string()) for c in COLUMNS
                ]),
            )
            pq.write_table(table, path, compression="zstd")
        else:
            path = os.path.join(directory, name + ".jsonl")
            with open(path, "w", encoding="utf-8") as fh:
                for row in normalized:
                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")

        self.files_written += 1
        self.rows_written += len(chunk)
        return path

    def glob(self) -> str:
        ext = "parquet" if self.backend == "parquet" else "jsonl"
        return os.path.join(self.root, "**", f"*.{ext}").replace("\\", "/")


def duckdb_view(root: str = "warehouse/facts", db: str = "warehouse/crisbet.duckdb"):
    """Crea/refresca la vista `facts` sobre el arbol Parquet. Devuelve la conexion."""
    import duckdb  # dependencia opcional: se importa solo al consultar

    os.makedirs(os.path.dirname(db) or ".", exist_ok=True)
    con = duckdb.connect(db)
    pattern = os.path.join(root, "**", "*.parquet").replace("\\", "/")
    # El almacen es append-only: reejecutar la ingesta vuelve a escribir la
    # misma pagina. `fact_id` es determinista (hash de url+tipo+contenido), asi
    # que la vista se queda con la extraccion mas reciente de cada hecho.
    # `facts_raw` conserva el historico completo para auditar.
    con.execute(
        f"CREATE OR REPLACE VIEW facts_raw AS "
        f"SELECT * FROM read_parquet('{pattern}', hive_partitioning=true)"
    )
    con.execute(
        "CREATE OR REPLACE VIEW facts AS "
        "SELECT * FROM facts_raw "
        "QUALIFY ROW_NUMBER() OVER (PARTITION BY fact_id ORDER BY extracted_at DESC) = 1"
    )
    return con
