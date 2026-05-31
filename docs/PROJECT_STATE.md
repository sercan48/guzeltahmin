# Project State & System Maturity Snapshot

This document registers module maturity scores, active technical debts, calibration metrics, and production readiness checks.

---

## 1. Module Maturity Dashboard

| Subsystem | Maturity (0-10) | Status | Key Focus |
| --------- | --------------- | ------ | --------- |
| **Data Ingestion** | 6/10 | Production | Integrate schema validators and API retries. |
| **Feature Engineering** | 8/10 | Production | Ensure robust summer league contextual mappings. |
| **Model Stack** | 8/10 | Production | Maintain XGB, LGBM, CatBoost, and Poisson stacking. |
| **Probability Calibration** | 9/10 | Production | ECE-minimizer calibrator selection is active. |
| **Decision Selection Layer** | 9/10 | Production | Double-gate filters and market multipliers are integrated. |
| **CLV / Value Engine** | 8/10 | Production | Implied margin cleaning and line tracking are validated. |
| **Adaptive Learning** | 7/10 | Production | SHAP-CLV weight tuning is active. |
| **Drift Monitoring** | 8/10 | Production | 3-level alert logic and DB logging are active. |
| **Threshold Optimization** | 9/10 | Production | Optuna weekly search with drawdown limits is active. |
| **Infra & DevOps** | 4/10 | Sandbox | Needs Docker containerization and connection pools. |

---

## 2. Quantitative & Analytical Status

### Calibration Status
- **Methodology:** Platt scaling, Isotonic regression, and Beta calibration are evaluated per league in a 3-fold cross-validation benchmarker.
- **Active Calibrators:** League-specific calibrator files are loaded from `models/ensemble/calibrator_{league_code}.pkl` on demand, falling back to global calibrations.

### Feature Coverage
- Includes 32 base statistics + 3 odds-derived features = 35 columns.
- Incorporates specific MLS, Brazil, and Norway context columns: `travel_distance_km`, `is_artificial_pitch`, `cup_rotation_fatigue`, `dp_presence`, and `extreme_humidity`.

### Drift Detection Status
- **Active Monitoring:** 7-day rolling CLV averages and variance ratios are checked daily.
- **Alert Levels:** Level 1 (Log + warnings on Streamlit) and Level 3 (retraining required recommendation logs) are operational.

---

## 3. Known Technical Debt Log

1. **Tight Database Coupling:** DB queries are executed directly inside evaluation code using raw SQL strings. A Repository Pattern wrapper is needed to decouple the layers.
2. **Configuration Fragmentation:** Decision configurations (SHAP weights, biases) are stored in flat JSON files, while threshold states are written in SQLite tables.
3. **No SQLite Connection Pool:** SQLite is vulnerable to concurrent write locks when FastAPI endpoints and Streamlit run simultaneously.

---

## 4. Production Readiness Summary

- **Production Readiness Score:** **74 / 100**
- **Risk Assessment:** **Medium Risk**
  *The core mathematical predictive pipeline (ML models, calibration curves, Optuna threshold optimizer) is highly mature and ready. The system requires FastAPI security middleware, Slowapi rate-limiting, and Dockerization to be ready for live account executions.*
