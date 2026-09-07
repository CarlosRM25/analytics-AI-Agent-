"""M3: the feature layer's transforms. Pure — no DB, no network."""

import numpy as np
import pandas as pd

from model.seattle_energy.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    TARGET,
    build_features,
    building_disjoint_split,
    temporal_split,
)


def _rows(intensities: list[float]) -> list[dict]:
    return [
        {"ose_building_id": str(i), "ghg_emissions_intensity": v} for i, v in enumerate(intensities)
    ]


def _raw(rows: list[dict]) -> pd.DataFrame:
    cols = {
        "ose_building_id": "b",
        "data_year": 2020,
        "year_built": 1990,
        "gfa_total": 100_000.0,
        "gfa_parking": 10_000.0,
        "number_of_floors": 5,
        "energy_star_score": 60,
        "council_district": 7,
        "primary_property_type": "Office",
        "ghg_emissions_intensity": 5.0,
        "demolished": 0,
    }
    return pd.DataFrame([{**cols, **r} for r in rows])


def test_target_is_median_split_within_type_and_year():
    out = build_features(_raw(_rows([1, 2, 3, 4]))).sort_values("ose_building_id")
    assert out[TARGET].tolist() == [0, 0, 1, 1]  # median 2.5


def test_demolished_and_null_intensity_rows_are_dropped():
    raw = _raw(
        [
            {"ose_building_id": "keep"},
            {"ose_building_id": "demo", "demolished": 1},
            {"ose_building_id": "nullint", "ghg_emissions_intensity": np.nan},
        ]
    )
    assert build_features(raw)["ose_building_id"].tolist() == ["keep"]


def test_is_outlier_flags_far_out_within_group():
    raw = _raw(_rows([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 200]))
    out = build_features(raw).set_index("ose_building_id")
    assert bool(out.loc["10", "is_outlier"]) is True  # the 200
    assert not out.loc["0", "is_outlier"]  # the 10


def test_building_age_hygiene():
    raw = _raw(
        [
            {"ose_building_id": "ok", "year_built": 1980, "data_year": 2020},
            {"ose_building_id": "none", "year_built": np.nan},
            {"ose_building_id": "ancient", "year_built": 1700},
            {"ose_building_id": "future", "year_built": 2030, "data_year": 2020},
        ]
    )
    age = build_features(raw).set_index("ose_building_id")["building_age"]
    assert age["ok"] == 40
    assert pd.isna(age["none"]) and pd.isna(age["ancient"]) and pd.isna(age["future"])


def test_has_energy_star_indicator():
    raw = _raw(
        [
            {"ose_building_id": "scored", "energy_star_score": 75},
            {"ose_building_id": "unscored", "energy_star_score": np.nan},
        ]
    )
    out = build_features(raw).set_index("ose_building_id")
    assert out.loc["scored", "has_energy_star"] == 1
    assert out.loc["unscored", "has_energy_star"] == 0


def test_council_district_is_string_category_not_float():
    out = build_features(_raw([{"council_district": 7}]))
    assert out["council_district"].dtype == "category"
    assert out["council_district"].iloc[0] == "7"


def test_output_columns_are_exactly_the_contract():
    out = build_features(_raw([{}]))
    assert list(out.columns) == ["ose_building_id", "data_year", TARGET, "is_outlier", *FEATURES]
    for col in CATEGORICAL_FEATURES:
        assert out[col].dtype == "category"


def test_temporal_split_routes_years_and_drops_train_outliers_only():
    # two rows per year 2021-2024, each year its own (type,year) group of 2 so
    # nothing is a statistical outlier; force the flag by hand afterwards.
    raw = _raw(
        [
            {"ose_building_id": f"{y}-{i}", "data_year": y, "ghg_emissions_intensity": v}
            for y in (2021, 2022, 2023, 2024)
            for i, v in enumerate([1.0, 9.0])
        ]
    )
    frame = build_features(raw)
    frame["is_outlier"] = frame["data_year"].isin([2022, 2024]) & frame[TARGET].eq(1)

    train, valid, test = temporal_split(frame)
    assert set(train["data_year"]) == {2021, 2022}
    assert set(valid["data_year"]) == {2023}
    assert set(test["data_year"]) == {2024}
    assert not train["is_outlier"].any()  # dropped from train
    assert test["is_outlier"].any()  # kept in test


def test_building_disjoint_split_shares_no_building():
    raw = _raw(
        [
            {"ose_building_id": f"b{b}", "data_year": y, "ghg_emissions_intensity": float(b + y)}
            for b in range(40)
            for y in (2019, 2020, 2021, 2022)
        ]
    )
    frame = build_features(raw)
    train, test = building_disjoint_split(frame, test_size=0.3, seed=0)
    assert set(train["ose_building_id"]).isdisjoint(set(test["ose_building_id"]))
    assert len(train) + len(test) <= len(frame)  # outliers may be dropped from train
    assert not train["is_outlier"].any()
