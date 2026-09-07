"""Train the Seattle high-emitter classifier (milestone M3).

Target: ``is_high_emitter`` — ``ghg_emissions_intensity`` above the median for the
same ``primary_property_type`` in the same ``data_year``. Split: train
``data_year <= 2022``, validate 2023, test 2024. Pipeline: ColumnTransformer ->
HistGradientBoostingClassifier, with LogisticRegression as the interpretable
baseline. Writes ``artifacts/model-YYYYMMDD.joblib`` and updates ``model_card.md``.
"""

# TODO(M3): implement the sklearn Pipeline, metric logging, and artifact dump.
