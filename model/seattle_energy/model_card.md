# Model card — Seattle high-emitter classifier

_Placeholder. Drafted for real at milestone M3; this is a deliverable, not an
afterthought (architecture doc section 4.4)._

- **Task:** binary classification — `is_high_emitter`, defined as
  `ghg_emissions_intensity` above the median for the same `primary_property_type`
  in the same `data_year` (normalising within type + year keeps the label
  balanced and forces the model past "big old building").
- **Data window:** train `data_year <= 2022`, validate 2023, test 2024 — mirrors
  the real task and stops a building's other years leaking across the split.
- **Features:** building age, `log(gfa_total)`, parking share,
  `primary_property_type`, `council_district`. `energy_star_score` only with a
  `has_energy_star` indicator; rows are never dropped on it.
- **Model:** `HistGradientBoostingClassifier` in an sklearn `Pipeline`;
  `LogisticRegression` interpretable baseline.
- **Metrics:** ROC-AUC, PR-AUC, confusion matrix, permutation importance. _TBD._
- **Known limitations:** _TBD — expect a modest signal; say so honestly._
- **Intended use:** portfolio demonstration and the `predict` tool. Not for
  compliance or enforcement decisions.
