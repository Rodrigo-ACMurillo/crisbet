"""Hechos con procedencia. Un hecho sin procedencia no entra al almacen.

Esta es la capa anti-alucinacion del Sprint 2: cada fila que sale de aqui
lleva `source_url`, `extracted_at` y `raw_snippet` (el fragmento literal del
que se derivo). Si falta uno de los tres, `validate` lo rechaza y el hecho se
descarta con motivo registrado. No hay ruta alternativa de entrada.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

SNIPPET_MAX = 1200
REQUIRED = ("source_url", "extracted_at", "raw_snippet", "fact_type", "content")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def snippet(text: str, limit: int = SNIPPET_MAX) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass
class Fact:
    fact_type: str                 # article_text | table_row | table_meta | dataset_json
    source_url: str
    source_domain: str
    content: Dict[str, Any]
    raw_snippet: str
    extracted_at: str = field(default_factory=_now)
    fetched_at: Optional[str] = None
    http_status: Optional[int] = None
    published_at: Optional[str] = None
    title: Optional[str] = None
    language: Optional[str] = None
    category: Optional[str] = None
    subcategory: Optional[str] = None
    extractor: str = "crisbet.extractor/0.2"
    fact_id: str = ""

    def __post_init__(self) -> None:
        if not self.fact_id:
            payload = f"{self.source_url}|{self.fact_type}|{json.dumps(self.content, sort_keys=True, default=str)}"
            self.fact_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def validate(self) -> Tuple[bool, str]:
        d = asdict(self)
        for key in REQUIRED:
            value = d.get(key)
            if value is None or (isinstance(value, (str, dict, list)) and len(value) == 0):
                return False, f"missing:{key}"
        if not self.source_url.startswith("http"):
            return False, "bad:source_url"
        try:
            dt.datetime.strptime(self.extracted_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            return False, "bad:extracted_at"
        return True, ""

    def to_row(self) -> Dict[str, Any]:
        row = asdict(self)
        # Parquet quiere columnas de tipo estable: el contenido variable va como JSON.
        row["content"] = json.dumps(self.content, ensure_ascii=False, default=str)
        row["partition_date"] = self.extracted_at[:10]
        return row


class FactCollector:
    """Acumula hechos y lleva la cuenta de los rechazados y por que."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.rejected: Dict[str, int] = {}
        self.accepted = 0

    def add(self, fact: Fact) -> bool:
        ok, reason = fact.validate()
        if not ok:
            self.rejected[reason] = self.rejected.get(reason, 0) + 1
            return False
        self.rows.append(fact.to_row())
        self.accepted += 1
        return True

    def drain(self) -> List[Dict[str, Any]]:
        rows, self.rows = self.rows, []
        return rows

    @property
    def provenance_rate(self) -> float:
        total = self.accepted + sum(self.rejected.values())
        return 1.0 if total == 0 else self.accepted / total
