"""Feature layer for the Seattle high-emitter model (architecture doc §4.4).

``build_features(raw)`` is pure — it takes an ``energy_records`` frame joined to
``buildings`` and returns the model frame (engineered features + target +
``is_outlier`` + ``data_year`` for the split). ``feature_matrix()`` does the
read-only DB read and calls it.

Decisions come from the M2 EDA notebook (`notebooks/01_eda.ipynb` §9):

* target ``is_high_emitter`` = ``ghg_emissions_intensity`` above the median for
  the same ``primary_property_type`` in the same ``data_year``; built on
  non-null-intensity, non-demolished rows.
* ``is_outlier`` = Tukey 3×IQR within ``(primary_property_type, data_year)`` —
  excluded from **training** only, not from valid/test.
* ``energy_star_score`` kept only with a ``has_energy_star`` indicator, never
  imputed.
* ``building_age``: ``year_built`` null / < 1850 / negative age → NaN.
* split: train ``data_year <= 2022``, valid 2023, test 2024.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.model_selection import GroupShuffleSplit
from sqlalchemy.engine import Engine

TARGET = "is_high_emitter"
_GROUP = ["primary_property_type", "data_year"]
_IQR_K = 3.0

NUMERIC_FEATURES = [
    "building_age",
    "log_gfa_total",
    "parking_share",
    "number_of_floors",
    "energy_star_score",
    "has_energy_star",
]
CATEGORICAL_FEATURES = [
    "primary_property_type",
    "council_district",
]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

_SPLIT_YEARS = {"train": (0, 2022), "valid": (2023, 2023), "test": (2024, 2024)}


def load_raw(engine: Engine | None = None) -> pd.DataFrame:
    """``energy_records`` joined to ``buildings``, read through the RO engine."""
    from db.engine import ro_engine  # local import keeps this module import DB-free

    eng = engine or ro_engine()
    with eng.connect() as conn:
        energy = pd.read_sql("SELECT * FROM energy_records", conn)
        buildings = pd.read_sql("SELECT * FROM buildings", conn)
    return energy.merge(buildings, on="ose_building_id", how="left", validate="many_to_one")


def _tukey_far_out(s: pd.Series, k: float = _IQR_K) -> pd.Series:
    q1, q3 = s.quantile(0.25), s.quantile(0.75)
    iqr = q3 - q1
    if not np.isfinite(iqr) or iqr == 0:
        return pd.Series(False, index=s.index)
    return (s < q1 - k * iqr) | (s > q3 + k * iqr)


def build_features(raw: pd.DataFrame) -> pd.DataFrame:
    """Joined ``energy``+``buildings`` frame → model frame. Pure, no I/O."""
    df = raw[raw["demolished"].fillna(0) != 1].copy()
    df = df[df["ghg_emissions_intensity"].notna()]

    grp = df.groupby(_GROUP, dropna=False)["ghg_emissions_intensity"]
    df[TARGET] = (df["ghg_emissions_intensity"] > grp.transform("median")).astype(int)
    df["is_outlier"] = grp.transform(_tukey_far_out).astype(bool)

    age = df["data_year"] - df["year_built"]
    bad_age = df["year_built"].isna() | (df["year_built"] < 1850) | (age < 0)
    df["building_age"] = age.mask(bad_age)

    df["log_gfa_total"] = np.log10(df["gfa_total"].where(df["gfa_total"] > 0))
    df["parking_share"] = (df["gfa_parking"] / df["gfa_total"]).clip(lower=0, upper=1)
    df["has_energy_star"] = df["energy_star_score"].notna().astype(int)

    # council_district is a code, not a quantity — carry it as a string category
    df["council_district"] = df["council_district"].astype("Int64").astype("string")
    for col in CATEGORICAL_FEATURES:
        df[col] = df[col].astype("category")

    keep = ["ose_building_id", "data_year", TARGET, "is_outlier", *FEATURES]
    return df[keep].reset_index(drop=True)


def feature_matrix(engine: Engine | None = None) -> pd.DataFrame:
    """Convenience: ``build_features(load_raw(engine))``."""
    return build_features(load_raw(engine))


def temporal_split(
    frame: pd.DataFrame, *, drop_train_outliers: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """``(train, valid, test)`` by ``data_year`` — the stated task (score this
    year's filings from prior years). Outliers are dropped from **train** only.

    Caveat surfaced in M3: a building recurs across years, so ~96% of test
    buildings also appear in train. The split does not isolate generalisation to
    *new* buildings — use ``building_disjoint_split`` for that.
    """
    parts: dict[str, pd.DataFrame] = {}
    for name, (lo, hi) in _SPLIT_YEARS.items():
        part = frame[frame["data_year"].between(lo, hi)]
        if name == "train" and drop_train_outliers:
            part = part[~part["is_outlier"]]
        parts[name] = part.reset_index(drop=True)
    return parts["train"], parts["valid"], parts["test"]


def building_disjoint_split(
    frame: pd.DataFrame, *, test_size: float = 0.25, seed: int = 0, drop_train_outliers: bool = True
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(train, test)`` with **no building in both** — estimates how the model
    does on a genuinely unseen building. All years pooled."""
    splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
    groups = frame["ose_building_id"]
    train_idx, test_idx = next(splitter.split(frame, frame[TARGET], groups=groups))
    train, test = frame.iloc[train_idx], frame.iloc[test_idx]
    if drop_train_outliers:
        train = train[~train["is_outlier"]]
    return train.reset_index(drop=True), test.reset_index(drop=True)
