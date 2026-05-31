# Güzel Tahmin — Quantitative Sports Prediction & Decision Engine

An institutional-grade, multi-layered predictive quantitative model and bet selection engine for sports forecasting. The system integrates machine learning models, point-in-time feature engineering, dynamic probability calibration, joint league-market threshold optimizations, and a Closing Line Value (CLV) learning feedback loop.

---

## Repository Structure

```
.
├── README.md                           # Main documentation entrypoint
├── requirements.txt                    # Project python dependencies
├── main.py                             # Unified runtime execution entrypoint
├── api.py                              # FastAPI backend REST API services
├── bot.py                              # Telegram publishing and automation bot
├── worker.py                           # Background task scheduler and update workers
├── config/                             # Global configuration files
│   ├── settings.py                     # Environment variables and path registries
│   ├── constants.py                    # Static dimensions, features list, and label maps
│   └── leagues.py                      # League emojis, classifications, and parameters
├── docs/                               # System documentation
│   ├── ARCHITECTURE.md                 # System overview and end-to-end data flows
│   ├── FEATURES.md                     # Feature engineering point-in-time definitions
│   ├── MODEL_STACK.md                  # Stacking ensemble, meta-learner, and calibration
│   ├── CLV_ENGINE.md                   # Closing Line Value trackers and edge classification
│   ├── THRESHOLD_SYSTEM.md             # Optuna threshold joint optimizations and rollbacks
│   ├── DEPLOYMENT.md                   # Setup guides and production configurations
│   ├── KNOWN_LIMITATIONS.md            # Critical limitations and quants assumptions
│   └── PROJECT_STATE.md                # System maturity dashboard and risk reports
├── scripts/                            # Operational runners
│   ├── daily_threshold_runner.py       # Daily micro-adjustments and rollback runner
│   ├── close_odds_updater.py           # Kickoff odds reconciler and CLV recorder
│   ├── init_db.py                      # Database schema build and ingestor
│   └── verify_db.py                    # Database validation check script
├── src/                                # Source modules
│   ├── db/                             # Core persistence layer
│   │   ├── base.py                     # Database connection adapters
│   │   └── migrations.py               # Versioned migrations (up to v11)
│   ├── features/                       # Raw feature engineering calculators
│   │   ├── team_strength.py            # Elo strength, decay, and standings factors
│   │   ├── form_calculator.py          # Rolling form momentum and h2h records
│   │   ├── xg_features.py              # Expected goals overperformance metrics
│   │   ├── player_impact.py            # Squad ratings and valuation aggregators
│   │   ├── referee_impact.py           # Referee strictness and card risk metrics
│   │   ├── fixture_congestion.py       # Fixture density and travel distance fatigue
│   │   └── weather_multiplier.py       # Temperature and humidity multipliers
│   ├── model/                          # Machine learning engine
│   │   ├── ensemble.py                 # Stacked XGB/LGBM/CatBoost + Poisson Meta-learner
│   │   ├── calibration_benchmarker.py   # ECE-minimizing probability calibrators
│   │   ├── value_clv_engine.py         # Implied margin cleaners and value bet engines
│   │   ├── adaptive_learning.py        # SHAP-driven feedback weight tuning
│   │   ├── adaptive_thresholds.py      # Optuna joint optimizer service
│   │   └── predictor.py                # Single match inference pipeline
│   └── evaluator/                      # Market selection engine
│       ├── market_builder.py           # Double-gate bet selection and scoring
│       └── historical_replay_engine.py # Backtest validation simulator
└── tests/                              # Unit and integration test suites
```

---

## Core Technologies

- **Machine Learning:** XGBoost, LightGBM, CatBoost, Scikit-Learn, Optuna, SHAP
- **API Backend:** FastAPI, Uvicorn, Pydantic
- **Dashboard:** Streamlit, Pandas, Plotly
- **Database:** SQLite (persisted database engine)
- **Integration:** Telegram Bot API (Python-telegram-bot)

---

## Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/your-username/guzel-tahmin.git
   cd guzel-tahmin
   ```

2. **Setup virtual environment:**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # On Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```

3. **Configure Environment variables:**
   Copy the template environment file:
   ```bash
   cp .env.example .env
   ```
   Fill in your `TELEGRAM_BOT_TOKEN`, `TELEGRAM_FREE_CHANNEL_ID`, and database path settings inside `.env`.

4. **Initialize database & ingest data:**
   ```bash
   python scripts/init_db.py
   ```

5. **Run the test suite:**
   ```bash
   python -m pytest tests/
   ```

---

## Execution Guide

### API Server
Starts the FastAPI service:
```bash
uvicorn api:app --host 0.0.0.0 --port 8000 --reload
```

### Streamlit Panel
Launches the monitoring and management dashboard:
```bash
streamlit run app/streamlit_app.py
```

### Telegram Bot
Starts the bot listener:
```bash
python bot.py
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
