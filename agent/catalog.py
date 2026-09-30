"""Read a source's curated column catalog (`catalog/<key>.yaml`).

The agent never sees raw schema internals — `describe_schema` and the system
prompt are both assembled from this file (+ live `PRAGMA table_info` / counts in
the tool). See architecture doc §4.5 / §8.3.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from sources import base as sources_base

_ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def load(source_key: str) -> dict:
    # source_key reaches here from two directions with no validation upstream:
    # the public /ask endpoint's `source` field, and the agent's own
    # describe_schema(dataset_key) tool call. An unchecked
    # {_ROOT}/"catalog"/f"{source_key}.yaml" path join is a path-traversal
    # vector -- `source_key="../deploy/cloudrun"` reads deploy/cloudrun.yaml
    # straight off disk (security review, 2026-09-30, proved against this
    # exact repo). sources_base.load() already does the "is this a real,
    # registered source" check via a plain Python import, which can't be
    # tricked into escaping a directory the way a raw path join can: a module
    # name with a `/` or `..` in it just fails to import. Reusing it here
    # means every current and future caller of catalog.load() is covered by
    # one check instead of needing its own.
    try:
        sources_base.load(source_key)
    except Exception as exc:  # noqa: BLE001 - any failure here means "not a real source"
        raise FileNotFoundError(f"no catalog for source {source_key!r}") from exc

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
