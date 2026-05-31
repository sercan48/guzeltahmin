# System Architecture & Data Flow

This document details the system design, core execution layers, database structures, and runtime services of the **Güzel Tahmin** quantitative engine.

---

## 1. High-Level System Architecture

The application is structured as a decoupled, multi-layered service architecture:

```
                  +-----------------------------------+
                  |      External API / Data Sources   |
                  |  (Football-Data, TransferMarkt,   |
                  |        RSS Feed, Weather)         |
                  +-----------------+-----------------+
                                    |
                                    v
+-------------------+     +---------+---------+     +-------------------+
|   Worker Service  |     |   Data Ingestors  |     | Telegram Bot Serv.|
|  (Cron Tasks &    |     |  (Lineups, Odds,  |     | (Subscription &   |
|   Rollbacks)      |     |   Results Ingest) |     |  Card Dispatcher) |
+---------+---------+     +---------+---------+     +---------+---------+
          |                         |                         |
          |                         v                         |
          |               +---------+---------+               |
          +-------------->|     Database      |<--------------+
                          |   (SQLite v11)    |
                          +---------+---------+
                                    |
                                    v
                          +---------+---------+
                          |  Feature Engine   |
                          | (Point-in-Time)   |
                          +---------+---------+
                                    |
                                    v
                          +---------+---------+
                          | Stacking Ensemble |
                          |   (Model Stack)   |
                          +---------+---------+
                                    |
                                    v
                          +---------+---------+
                          | Calibration Layer |
                          | (Platt/Iso/Beta)  |
                          +---------+---------+
                                    |
                                    v
                          +---------+---------+
                          |  Decision Engine  |
                          | (BetSelector)     |
                          +---------+---------+
                                    |
                                    v
                          +---------+---------+
                          |   FastAPI REST    |
                          |   (API Service)   |
                          +---------+---------+
                                    |
                                    v
                          +---------+---------+
                          |    Streamlit      |
                          |   (UI Dashboard)  |
                          +-------------------+
```

---

## 2. Core Execution Layers

### I. Data Layer (SQLite)
Maintains versioned tables for matches, teams, players, odds snapshots, closing odds, bot activities, and adaptive threshold states. Applied schemas are migrated up to **v11**.

### II. Feature Engineering Layer
Assembles match features point-in-time, enforcing strict historical boundaries to prevent chronological data leakage. Incorporates team Elo strength, goals, Clean Sheet differentials, expected goals (xG) overperformance, weather, travel fatigue, and scheduling congestion.

### III. ML Core Stacking Engine
Uses a two-level classifier:
- **Level-0 Base Models:** XGBoost + LightGBM + CatBoost + Poisson goals generator.
- **Level-1 Stacking:** Meta-learner (Logistic Regression) + League-specific Residual Correctors for specialized leagues (e.g. MLS, Norway, Brazil).

### IV. Calibration Layer
League-aware probability calibration. Benchmarks Platt scaling, Isotonic Regression, and Beta calibration per league, activating the calibrator that minimizes Expected Calibration Error (ECE).

### V. Evaluator & Selection Engine (`BetSelector`)
Ranks outcomes and identifies recommendations using a double-gate filter:
1. **Edge Filter:** Margin-cleaned model probability must exceed bookmaker probabilities by $\ge 2\%$.
2. **Probability Threshold Filter:** Calibrated probability must exceed the league-specific, market-adjusted threshold.

---

## 3. Runtime Services

1. **API Service (`api.py`):** FASTApi REST app serving predictions, coupon calculations, CLV/edge analytics, active feature weights, and threshold rollback triggers.
2. **Bot Service (`bot.py`):** Telegram automation bot managing free/premium channels, formats HTML explainable pick cards, and logs logs.
3. **Worker Service (`worker.py`):** Background cron schedules running kickoff odds reconciliations, SHAP-CLV weight adaptations, and threshold check-and-rollbacks.
