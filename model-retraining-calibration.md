# Plan — Model Retraining, Calibration & Market Optimization

This plan details the retraining, calibration, and market optimization sprint for the "Güzel Tahmin" platform, focusing on summer leagues (Norway, Brazil, Sweden, Finland).

## Project Type
- **BACKEND / ML PIPELINE**

## Success Criteria
- Expected Calibration Error (ECE) < 0.15 across summer leagues.
- Brier Score < 0.30, Log Loss < 1.0.
- Replay sample $\ge 500$ completed matches.
- Multi-objective decision quality optimizer under coverage constraints $\ge 15\%$.
- No data leakage.

## Tech Stack
- Python 3.11+, SQLite.
- scikit-learn, XGBoost, LightGBM, CatBoost.
- SHAP, Optuna (if needed for hyperparameter search).

## Directory Structure
- [constants.py](file:///c:/Users/WIN/Desktop/G%C3%BCzel%20Tahmin/config/constants.py) (Add features to FEATURE_COLUMNS)
- [predictor.py](file:///c:/Users/WIN/Desktop/G%C3%BCzel%20Tahmin/src/model/predictor.py) (Build feature vectors and run inference)
- [ensemble.py](file:///c:/Users/WIN/Desktop/G%C3%BCzel%20Tahmin/src/model/ensemble.py) (Base model training and league residual layer)
- `src/model/calibration_lab.py` [NEW] (Platt, Isotonic, Beta, Temperature Scaling)
- `src/model/shap_explainer.py` [NEW] (SHAP explainability card engine)
- `scripts/audit_feature_importance.py` [NEW] (Phase 1 audit)
- `scripts/run_calibration_search.py` [NEW] (Phase 4 calibration selection)
- `scripts/optimize_thresholds.py` [NEW] (Phase 5 decision threshold selection)
- `tests/test_retraining_calibration.py` [NEW] (Automated test suite)

## Task Breakdown

### Phase 1 — Feature Importance Audit
- **Task 1.1**: Run `scripts/audit_feature_importance.py` on the existing model to document feature utilization.

### Phase 2 — Feature Matrix Rebuild (dataset_v2)
- **Task 2.1**: Update `config/constants.py` and `scripts/build_features.py` to support new summer league features.
- **Task 2.2**: Run feature builder to output `features.csv` (dataset_v2).

### Phase 3 — Full Model Retraining
- **Task 3.1**: Install `catboost` and `shap`.
- **Task 3.2**: Update `src/model/ensemble.py` to train XGBoost, LightGBM, and CatBoost, and implement the League Residual Layer.
- **Task 3.3**: Run `scripts/train_ensemble.py`.

### Phase 4 — Probability Calibration Lab
- **Task 4.1**: Implement `src/model/calibration_lab.py`.
- **Task 4.2**: Implement and run `scripts/run_calibration_search.py`.

### Phase 5 — Threshold Optimization
- **Task 5.1**: Implement and run `scripts/optimize_thresholds.py` to optimize thresholds per league under coverage constraints.

### Phase 6 — Market Performance Backtest
- **Task 6.1**: Run backtests on Norway, Brazil, Sweden, and Finland using the new models and thresholds.

### Phase 7 — SHAP & Explainability
- **Task 7.1**: Implement `src/model/shap_explainer.py` and integrate it with `predictor.py`.

### Phase 8 — League Specialization Report
- **Task 8.1**: Compare Global + Residual vs League-Specific models.

### Phase 9 — Deployment Readiness
- **Task 9.1**: Validate all readiness criteria.

## Phase X: Verification
- [x] Run `pytest tests/test_retraining_calibration.py -v`
- [x] Mark Phase X as complete in this file
