"""Extraccion de texto y de tablas a partir del HTML crudo.

Texto: trafilatura si esta disponible (mejor deteccion del cuerpo del
articulo); si no, un fallback con BeautifulSoup que elimina nav/aside/script.

Tablas: pandas.read_html sobre el HTML. FBref esconde casi todas sus tablas
dentro de comentarios HTML (`<!-- <table ...> -->`) para frenar el scraping
ingenuo; se descomentan antes de parsear. Understat publica sus datos como
JSON escapado dentro de un `<script>`, no como tabla.
"""
from __future__ import annotations

import io
import json
import re
from typing import Dict, List, Optional

try:
    import trafilatura
except ImportError:
    trafilatura = None

try:
    from bs4 import BeautifulSoup, Comment
except ImportError:
    BeautifulSoup = None
    Comment = None

try:
    import pandas as pd
except ImportError:
    pd = None

_COMMENT_TABLE = re.compile(r"<!--(\s*<table.*?</table>\s*)-->", re.S | re.I)
_UNDERSTAT_VAR = re.compile(
    r"var\s+(\w+)\s*=\s*JSON\.parse\(\s*'(.*?)'\s*\)\s*;", re.S
)
_WS = re.compile(r"[ \t ]+")
_BLANKS = re.compile(r"\n{3,}")

TABLE_DOMAINS = ("fbref.com", "understat.com", "transfermarkt", "worldfootball.net")


def looks_client_rendered(html: str) -> bool:
    """HTML sin un solo parrafo ni articulo: el contenido lo pinta JavaScript.

    Distinguirlo importa: no es un fallo del extractor sino una pagina que
    exige renderizado. Se registra como tal para decidir si vale la pena un
    navegador headless para ese dominio.
    """
    if re.search(r"<p[\s>]", html, re.I):
        return False
    return not re.search(r"<article[\s>]", html, re.I)


def clean_text(text: str) -> str:
    text = _WS.sub(" ", text.replace("\r", ""))
    return _BLANKS.sub("\n\n", text).strip()


def uncomment_tables(html: str) -> str:
    """Devuelve el HTML con las tablas comentadas de FBref ya visibles."""
    return _COMMENT_TABLE.sub(r"\1", html)


def extract_text(html: str, url: str) -> Dict[str, Optional[str]]:
    """Devuelve {title, text, published, author}. Nunca lanza."""
    out: Dict[str, Optional[str]] = {"title": None, "text": None, "published": None, "author": None}
    if trafilatura is not None:
        try:
            doc = trafilatura.bare_extraction(
                html, url=url, with_metadata=True, favor_precision=True
            )
            if doc:
                get = doc.get if isinstance(doc, dict) else lambda k, d=None: getattr(doc, k, d)
                out["text"] = clean_text(get("text") or "")
                out["title"] = get("title")
                out["published"] = get("date")
                out["author"] = get("author")
        except Exception:
            pass
    if not out["text"] and BeautifulSoup is not None:
        try:
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "aside", "footer", "form", "noscript"]):
                tag.decompose()
            node = soup.find("article") or soup.find("main") or soup.body or soup
            out["text"] = clean_text(node.get_text("\n"))
            if not out["title"] and soup.title:
                out["title"] = soup.title.get_text(strip=True)
        except Exception:
            pass
    return out


def extract_tables(html: str, url: str, max_tables: int = 25) -> List[Dict]:
    """Tablas como registros {name, n_rows, n_cols, columns, rows}."""
    if pd is None:
        return []
    prepared = uncomment_tables(html) if "fbref.com" in url else html
    ids = _table_ids(prepared)
    # pandas >= 2.1 exige un buffer, no una cadena suelta (FutureWarning -> error).
    try:
        frames = pd.read_html(io.StringIO(prepared), flavor="lxml")
    except Exception:
        try:
            frames = pd.read_html(io.StringIO(prepared))
        except Exception:
            return []

    tables: List[Dict] = []
    for i, df in enumerate(frames[:max_tables]):
        df = _flatten_columns(df).dropna(how="all").dropna(axis=1, how="all")
        if df.empty or len(df.columns) < 2:
            continue
        tables.append(
            {
                "name": ids[i] if i < len(ids) else f"table_{i}",
                "index": i,
                "n_rows": int(len(df)),
                "n_cols": int(len(df.columns)),
                "columns": [str(c) for c in df.columns],
                "rows": json.loads(df.astype(str).to_json(orient="records")),
            }
        )
    return tables


def extract_understat_json(html: str) -> Dict[str, object]:
    """Los datasets que Understat inyecta como JSON escapado en <script>."""
    found: Dict[str, object] = {}
    for name, payload in _UNDERSTAT_VAR.findall(html):
        try:
            found[name] = json.loads(payload.encode().decode("unicode_escape"))
        except (ValueError, UnicodeDecodeError):
            continue
    return found


def _table_ids(html: str) -> List[str]:
    """Un id por cada <table> en orden de aparicion, para casar con read_html."""
    ids = []
    for i, tag in enumerate(re.findall(r"<table[^>]*>", html, re.I)):
        m = re.search(r"id=[\"']([^\"']+)[\"']", tag, re.I)
        ids.append(m.group(1) if m else f"table_{i}")
    return ids


def _flatten_columns(df):
    if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
        df.columns = [
            " ".join(str(p) for p in col if not str(p).startswith("Unnamed")).strip()
            for col in df.columns
        ]
    return df
