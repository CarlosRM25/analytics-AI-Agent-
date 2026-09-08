"""Read a source's curated column catalog (`catalog/<key>.yaml`).

The agent never sees raw schema internals — `describe_schema` and the system
prompt are both assembled from this file (+ live `PRAGMA table_info` / counts in
the tool). See architecture doc §4.5 / §8.3.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def load(source_key: str) -> dict:
    path = _ROOT / "catalog" / f"{source_key}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no catalog for source {source_key!r} at {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def summary_text(source_key: str) -> str:
    """A compact, stable schema summary for the system prompt."""
    cat = load(source_key)
    ds = cat.get("dataset", {})
    lines = [f"# {ds.get('title', source_key)}", ds.get("description", "").strip(), ""]
    for table, spec in cat.get("tables", {}).items():
        lines.append(f"## {table}")
        desc = (spec.get("description") or "").strip()
        if desc:
            lines.append(desc)
        for col, meta in (spec.get("columns") or {}).items():
            meta = meta or {}
            bits = [meta.get("type", "?")]
            if meta.get("pk"):
                bits.append("PK")
            if meta.get("fk"):
                bits.append(f"FK->{meta['fk']}")
            if meta.get("enum"):
                bits.append("enum " + "/".join(str(v) for v in meta["enum"]))
            doc = (meta.get("doc") or "").strip()
            lines.append(f"  {col} ({', '.join(bits)}){' — ' + doc if doc else ''}")
        lines.append("")
    return "\n".join(line for line in lines if line is not None).strip()
