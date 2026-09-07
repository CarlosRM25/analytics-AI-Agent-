"""Dataset source abstraction (architecture doc section 4.1).

A ``SourceSpec`` is one frozen description of a civic dataset: where to pull it
from (Socrata), how its columns map onto our tables, how to upsert it
idempotently, where its human-readable catalog lives, and whether a trained
model sits behind the ``predict`` tool for it.

Adding a second dataset (SPD crime, Metro transit) is a new module that builds a
``SourceSpec`` and calls ``register()`` — no change to ingestion, storage, the
agent, or the interface.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldMap:
    """One source column -> one target ``(table, column)``, optionally transformed.

    ``field_map`` is the isolation layer: when Socrata renames a column or
    republishes under a new resource id, only these entries change. A source
    column that feeds more than one table (a natural key written to both a
    parent and a child table) maps to a tuple of ``FieldMap``.
    """

    table: str
    column: str
    dtype: str
    transform: Callable[[object], object] | None = None


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """Immutable description of one dataset. One per source module."""

    key: str
    socrata_domain: str
    resource_id: str
    app_token_env: str
    page_size: int
    field_map: Mapping[str, FieldMap | Sequence[FieldMap]]
    target_tables: Sequence[str]
    upsert_keys: Mapping[str, Sequence[str]]
    catalog_path: str
    model_module: str | None = None
    refresh: str = "annual"

    def field_targets(self) -> Iterator[tuple[str, FieldMap]]:
        """Yield ``(source_column, FieldMap)`` once per target, flattening any
        source column that maps to several tables."""
        for src_col, target in self.field_map.items():
            if isinstance(target, FieldMap):
                yield src_col, target
            else:
                for fm in target:
                    yield src_col, fm


REGISTRY: dict[str, SourceSpec] = {}


def register(spec: SourceSpec) -> SourceSpec:
    """Add ``spec`` to the module-level registry, keyed by ``spec.key``."""
    if spec.key in REGISTRY:
        raise ValueError(f"source {spec.key!r} is already registered")
    REGISTRY[spec.key] = spec
    return spec


def get(key: str) -> SourceSpec:
    """Look up a registered spec, or raise ``KeyError`` listing the known keys."""
    try:
        return REGISTRY[key]
    except KeyError:
        known = ", ".join(sorted(REGISTRY)) or "(none registered)"
        raise KeyError(f"unknown source {key!r}; registered: {known}") from None


def load(key: str) -> SourceSpec:
    """Import ``sources.<key>``, register its module-level ``SPEC``, and return it.

    Registration is explicit (here) rather than an import side effect, so tests
    can import a source module and inspect its ``SPEC`` without mutating the
    shared ``REGISTRY``.
    """
    if key not in REGISTRY:
        module = importlib.import_module(f"{__package__}.{key}")
        register(module.SPEC)
    return REGISTRY[key]
