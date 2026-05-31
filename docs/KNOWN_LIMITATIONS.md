# Known System Limitations

This document lists the technical bottlenecks, model biases, database constraints, and quantitative assumptions of the system.

---

## 1. Quantitative & Modeling Assumptions

### Flat Stake Optimization
The Optuna threshold search evaluates trials using a flat $1.0$ unit stake. Real-world operations utilize dynamic capital allocation (e.g. Kelly Criterion). Optimizing thresholds under flat stakes might lead to sub-optimal sizing boundaries.

### Backtest CLV Approximation
The backtest engine approximates past CLV distributions using normal distribution seeding rather than actual historical kickoff odds. This introduces slight variations between simulated historical results and real-world returns.

### Weather Heuristics
Extreme weather multipliers (rain, snow, high wind) are applied post-model using manual multipliers. These are not learned directly as features in the base XGBoost or LightGBM models.

---

## 2. Technical & Data Bottlenecks

### SQLite Transaction Locks
The database is built on SQLite. If many API endpoints, Streamlit views, and workers attempt concurrent write operations, SQLite locks the file (`database is locked`), causing task crashes. 
- *Remedy:* Require a migration to PostgreSQL or SQLAlchemy connection pools with retries for multi-user commercial use.

### Ingestion Scraper Outages
If the TransferMarkt squad values scraper or lineups feed suffers an outage, the features pipeline falls back to $0$ values. This degrades model accuracy by understating player absences (training-serving skew).

### Volatility in Low-Volume Weeks
The Level 3 Retraining Alert is triggered when the rolling 7-day CLV drops below $-5\%$. In low-volume weeks (e.g. international breaks), a single negative line movement can trigger false retraining alerts.
