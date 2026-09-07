"""Train the Seattle high-emitter classifier (architecture doc §4.4, milestone M3).

    python -m model.seattle_energy.train

``HistGradientBoostingClassifier`` (native NaN + categoricals) is the model;
``LogisticRegression`` is the interpretable baseline. Two evaluations, both
reported because they answer different questions:

* **temporal** — train ``data_year <= 2022``, valid 2023, test 2024. The stated
  task. But ~96% of test buildings also appear in train, so it does not isolate
  generalisation to *new* buildings (M3 finding).
* **building-disjoint** — a random split with no building on both sides. Estimates
  performance on a genuinely unseen building.

Writes to ``artifacts/``:
    model-YYYYMMDD.joblib                 HGB refit on all years (gitignored)
    metrics-YYYYMMDD.json                 both evaluations, both models
    permutation-importance-YYYYMMDD.png
and regenerates ``model_card.md``.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from string import Template

import joblib
import pandas as pd
import plotly.express as px
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, confusion_matrix, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from model.seattle_energy.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    TARGET,
    building_disjoint_split,
    feature_matrix,
    temporal_split,
)

_HERE = Path(__file__).parent
ARTIFACTS = _HERE / "artifacts"
CARD = _HERE / "model_card.md"
CARD_TEMPLATE = _HERE / "model_card.template.md"
ACCENT = "#b4491f"
TODAY = dt.date.today().isoformat()


def _baseline() -> Pipeline:
    pre = ColumnTransformer(
        [
            (
                "num",
                Pipeline(
                    [("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())]
                ),
                NUMERIC_FEATURES,
            ),
            ("cat", OneHotEncoder(handle_unknown="ignore", min_frequency=25), CATEGORICAL_FEATURES),
        ]
    )
    return Pipeline([("pre", pre), ("clf", LogisticRegression(max_iter=2000))])


def _hgb() -> Pipeline:
    return Pipeline(
        [("clf", HistGradientBoostingClassifier(categorical_features="from_dtype", random_state=0))]
    )


def _metrics(model: Pipeline, x: pd.DataFrame, y: pd.Series) -> dict:
    proba = model.predict_proba(x)[:, 1]
    pred = (proba >= 0.5).astype(int)
    tn, fp, fn, tp = confusion_matrix(y, pred).ravel()
    return {
        "n": int(len(y)),
        "positive_rate": round(float(y.mean()), 4),
        "roc_auc": round(float(roc_auc_score(y, proba)), 4),
        "pr_auc": round(float(average_precision_score(y, proba)), 4),
        "accuracy": round(float((pred == y).mean()), 4),
        "confusion": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
    }


def _fit_eval(
    make: callable, x_tr: pd.DataFrame, y_tr: pd.Series, evals: dict
) -> tuple[Pipeline, dict]:
    model = make()
    model.fit(x_tr, y_tr)
    return model, {name: _metrics(model, x, y) for name, (x, y) in evals.items()}


def _importance(model: Pipeline, x: pd.DataFrame, y: pd.Series) -> pd.DataFrame:
    perm = permutation_importance(
        model, x, y, scoring="roc_auc", n_repeats=10, random_state=0, n_jobs=-1
    )
    imp = (
        pd.DataFrame(
            {"feature": FEATURES, "importance": perm.importances_mean, "std": perm.importances_std}
        )
        .sort_values("importance")
        .reset_index(drop=True)
    )
    fig = px.bar(
        imp,
        x="importance",
        y="feature",
        error_x="std",
        orientation="h",
        title="Permutation importance — ROC-AUC drop when shuffled (temporal test set)",
        labels={"importance": "Mean ROC-AUC drop", "feature": ""},
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(template="plotly_white", height=380, margin=dict(t=60, r=30, b=50, l=150))
    ARTIFACTS.mkdir(exist_ok=True)
    fig.write_image(ARTIFACTS / f"permutation-importance-{TODAY}.png", scale=2)
    return imp


def _write_card(payload: dict, imp: pd.DataFrame) -> None:
    t_hgb = payload["temporal"]["hgb"]
    t_test, t_seen = t_hgb["test"], t_hgb["test_seen_buildings"]
    t_new = t_hgb["test_new_buildings"]
    d_test = payload["building_disjoint"]["hgb"]["test"]
    base = payload["temporal"]["logreg_baseline"]["test"]
    ranked = imp.sort_values("importance", ascending=False)
    es_imp = float(imp.loc[imp["feature"] == "energy_star_score", "importance"].iloc[0])

    values = {
        "date": TODAY,
        "temporal_test_auc": f"{t_test['roc_auc']:.3f}",
        "temporal_test_auc_2dp": f"{t_test['roc_auc']:.2f}",
        "temporal_seen_auc": f"{t_seen['roc_auc']:.3f}",
        "temporal_new_auc": f"{t_new['roc_auc']:.3f}",
        "temporal_new_auc_2dp": f"{t_new['roc_auc']:.2f}",
        "seen_pct": f"{payload['temporal']['test_seen_pct']:.0%}",
        "new_n": t_new["n"],
        "disjoint_test_auc": f"{d_test['roc_auc']:.3f}",
        "disjoint_test_auc_2dp": f"{d_test['roc_auc']:.2f}",
        "baseline_auc": f"{base['roc_auc']:.3f}",
        "baseline_pr": f"{base['pr_auc']:.3f}",
        "numeric_features": ", ".join(f"`{f}`" for f in NUMERIC_FEATURES),
        "categorical_features": ", ".join(f"`{f}`" for f in CATEGORICAL_FEATURES),
        "top_features": " · ".join(
            f"`{r.feature}` ({r.importance:.3f})" for r in ranked.head(4).itertuples()
        ),
        "energy_star_importance": f"{es_imp:.2f}",
    }
    text = Template(CARD_TEMPLATE.read_text(encoding="utf-8")).substitute(values)
    CARD.write_text(text, encoding="utf-8")


def main() -> int:
    frame = feature_matrix()

    # --- temporal: the stated task ---
    tr, va, te = temporal_split(frame)
    seen = set(tr["ose_building_id"])
    te_seen = te[te["ose_building_id"].isin(seen)]
    te_new = te[~te["ose_building_id"].isin(seen)]
    seen_pct = len(te_seen) / len(te)
    print(
        f"temporal   train {len(tr):>6,} (pos {tr[TARGET].mean():.3f})   "
        f"valid {len(va):>5,}   test {len(te):>5,} ({seen_pct:.0%} seen, {len(te_new)} new)"
    )
    t_evals = {
        "valid": (va[FEATURES], va[TARGET]),
        "test": (te[FEATURES], te[TARGET]),
        "test_seen_buildings": (te_seen[FEATURES], te_seen[TARGET]),
        "test_new_buildings": (te_new[FEATURES], te_new[TARGET]),
    }
    temporal: dict = {
        "split": "train<=2022, valid 2023, test 2024",
        "test_seen_pct": round(seen_pct, 4),
    }
    for name, make in {"logreg_baseline": _baseline, "hgb": _hgb}.items():
        model, m = _fit_eval(make, tr[FEATURES], tr[TARGET], t_evals)
        temporal[name] = m
        if name == "hgb":
            temporal_hgb = model
        new_auc = m["test_new_buildings"]["roc_auc"]
        print(f"  {name:<16} test ROC-AUC {m['test']['roc_auc']:.3f}  (new {new_auc:.3f})")

    # --- building-disjoint: generalisation to unseen buildings ---
    dtr, dte = building_disjoint_split(frame)
    print(f"disjoint   train {len(dtr):>6,}   test {len(dte):>6,}   (0 shared buildings)")
    d_evals = {"test": (dte[FEATURES], dte[TARGET])}
    disjoint: dict = {"split": "GroupShuffleSplit on ose_building_id, 25% test, seed 0"}
    for name, make in {"logreg_baseline": _baseline, "hgb": _hgb}.items():
        _, m = _fit_eval(make, dtr[FEATURES], dtr[TARGET], d_evals)
        disjoint[name] = m
        print(f"  {name:<16} test ROC-AUC {m['test']['roc_auc']:.3f}")

    imp = _importance(temporal_hgb, te[FEATURES], te[TARGET])

    # --- ship: refit on everything ---
    full = frame[~frame["is_outlier"]]
    shipped = _hgb()
    shipped.fit(full[FEATURES], full[TARGET])
    ARTIFACTS.mkdir(exist_ok=True)
    joblib.dump(shipped, ARTIFACTS / f"model-{TODAY}.joblib")

    ranked = imp.sort_values("importance", ascending=False)
    payload = {
        "date": TODAY,
        "features": FEATURES,
        "temporal": temporal,
        "building_disjoint": disjoint,
        "permutation_importance": ranked.to_dict("records"),
        "shipped_artifact": f"model-{TODAY}.joblib — HGB refit on 2015-2024, outliers dropped",
    }
    metrics_path = ARTIFACTS / f"metrics-{TODAY}.json"
    metrics_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _write_card(payload, imp)
    print(f"wrote artifacts/ and model_card.md for {TODAY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
