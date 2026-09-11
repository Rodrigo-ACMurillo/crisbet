"""Consultas de comprobacion sobre el almacen (DuckDB).

    python query.py
    python query.py --sql "SELECT fact_type, COUNT(*) FROM facts GROUP BY 1"
"""
from __future__ import annotations

import argparse

from store import duckdb_view

DEFAULT_CHECKS = [
    ("hechos por tipo",
     "SELECT fact_type, COUNT(*) AS n FROM facts GROUP BY 1 ORDER BY n DESC"),
    ("hechos por dominio",
     "SELECT source_domain, COUNT(*) AS n FROM facts GROUP BY 1 ORDER BY n DESC LIMIT 15"),
    ("procedencia completa",
     "SELECT COUNT(*) AS total, "
     "SUM(CASE WHEN source_url IS NOT NULL AND raw_snippet IS NOT NULL "
     "AND extracted_at IS NOT NULL THEN 1 ELSE 0 END) AS con_procedencia FROM facts"),
    ("particiones",
     "SELECT partition_date, COUNT(*) AS n FROM facts GROUP BY 1 ORDER BY 1"),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", default="warehouse/facts")
    p.add_argument("--db", default="warehouse/crisbet.duckdb")
    p.add_argument("--sql")
    args = p.parse_args()

    con = duckdb_view(args.root, args.db)
    if args.sql:
        print(con.execute(args.sql).df().to_string(index=False))
        return
    for title, sql in DEFAULT_CHECKS:
        print("\n--- " + title + " ---")
        print(con.execute(sql).df().to_string(index=False))


if __name__ == "__main__":
    main()
