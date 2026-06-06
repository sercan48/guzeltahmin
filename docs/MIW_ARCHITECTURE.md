# Market Intelligence Warehouse (MIW) — Architecture Design

> **Version 1.0** — Next-Generation Market Signal Infrastructure  
> **Status:** Approved — Ready for Implementation  
> **Date:** 2026-06-05

---

## Executive Summary

The Market Intelligence Warehouse (MIW) captures, normalizes, and transforms **bookmaker behavior** and **market movement** into first-class predictive signals for the Güzel Tahmin engine. It sits *between* the existing Data Ingestion Layer and the Feature Engineering Layer, providing a dedicated subsystem for odds time-series intelligence, bookmaker consensus analysis, sharp-vs-soft money flow detection, and market microstructure features.

**Key Insight:** The current system captures odds at two points only — snapshot (prediction time) and close (kickoff). The MIW introduces **continuous odds time-series ingestion**, turning the market from a static comparator into a rich, temporal signal source.

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                          LAYER 6: CONSUMERS                             │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────────┐  │
│  │ FastAPI  │  │ Telegram │  │Streamlit │  │ Threshold Optimizer   │  │
│  │  REST    │  │   Bot    │  │Dashboard │  │ (Optuna + MIW Signals)│  │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘  └───────────┬───────────┘  │
├───────┼──────────────┼──────────────┼───────────────────┼──────────────┤
│       │         LAYER 5: DECISION ENGINE                │              │
│       │  ┌─────────────────────────────────────────┐    │              │
│       └──┤  BetSelector (Enhanced)                 ├────┘              │
│          │  + Market Regime Context                │                   │
│          │  + Bookmaker Consensus Signal           │                   │
│          │  + Steam Move Penalty/Bonus             │                   │
│          └──────────────┬──────────────────────────┘                   │
├─────────────────────────┼─────────────────────────────────────────────┤
│                    LAYER 4: ML CORE                                    │
│  ┌──────────────────────┴──────────────────────┐                      │
│  │  Stacking Ensemble (Existing)               │                      │
│  │  + MIW Feature Vector (12 new features)     │                      │
│  │  + Market Regime Classifier (New)           │                      │
│  └──────────────────────┬──────────────────────┘                      │
│                         │                                              │
│  ┌──────────────────────┴──────────────────────┐                      │
│  │  Calibration Layer (Enhanced)               │                      │
│  │  + Market-State-Conditional Calibration     │                      │
│  └──────────────────────┬──────────────────────┘                      │
├─────────────────────────┼─────────────────────────────────────────────┤
│                    LAYER 3: FEATURE ENGINEERING                        │
│  ┌──────────────────────┴──────────────────────┐                      │
│  │  Existing Features (40 columns)             │                      │
│  ├─────────────────────────────────────────────┤                      │
│  │  ★ MIW FEATURE BUILDER (NEW - 12 columns)  │                      │
│  │    ├── Odds Velocity Features               │                      │
│  │    ├── Bookmaker Consensus Features         │                      │
│  │    ├── Market Microstructure Features        │                      │
│  │    └── Regime Classification Features       │                      │
│  └──────────────────────┬──────────────────────┘                      │
├─────────────────────────┼─────────────────────────────────────────────┤
│               ★ LAYER 2: MARKET INTELLIGENCE WAREHOUSE ★              │
│                                                                        │
│  ┌─────────────┐  ┌──────────────┐  ┌──────────────┐  ┌───────────┐  │
│  │ Odds Time-  │  │  Bookmaker   │  │   Market     │  │  Signal   │  │
│  │ Series      │  │  Behavior    │  │  Microstruc- │  │  Genera-  │  │
│  │ Engine      │  │  Profiler    │  │  ture Engine │  │  tor      │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬───────┘  └─────┬─────┘  │
│         │                │                  │                │         │
│         └────────────────┴──────────────────┴────────────────┘         │
│                                  │                                     │
│  ┌───────────────────────────────┴────────────────────────────────┐    │
│  │              Market Data Store (TimescaleDB / DuckDB)         │    │
│  │  odds_timeseries │ bookmaker_profiles │ market_signals │ ...  │    │
│  └───────────────────────────────┬────────────────────────────────┘    │
├──────────────────────────────────┼────────────────────────────────────┤
│                    LAYER 1: DATA INGESTION                             │
│  ┌──────────────────────────────┐│┌──────────────────────────────┐    │
│  │  Existing Ingestors          │││ ★ MIW INGESTORS (NEW)       │    │
│  │  (Football-Data, Understat,  │││   ├── Odds Poller Service   │    │
│  │   API-Football, Kaggle)      │││   ├── Pinnacle Feed Parser  │    │
│  └──────────────────────────────┘│└── ├── Multi-Book Aggregator │    │
│                                  │    └── Historical Backfiller │    │
│                                  │                               │    │
│  ┌───────────────────────────────┴────────────────────────────────┐    │
│  │           Smart Polling Scheduler (Rate-Limit Aware)          │    │
│  └───────────────────────────────────────────────────────────────┘    │
├──────────────────────────────────────────────────────────────────────┤
│                    LAYER 0: EXTERNAL SOURCES                          │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────────┐    │
│  │ The Odds │  │ Pinnacle │  │ Betfair   │  │ Football-Data.uk │    │
│  │ API      │  │ Feed     │  │ Exchange  │  │ (Historical Odds)│    │
│  └──────────┘  └──────────┘  └───────────┘  └──────────────────┘    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 2. Data Flow Diagrams

### 2.1 Primary MIW Data Pipeline

```
                         ┌─────────────────────────┐
                         │   External Odds Sources  │
                         │ (Odds API, Pinnacle, FD) │
                         └────────────┬─────────────┘
                                      │
                                      ▼
                    ┌─────────────────────────────────┐
                    │    Smart Polling Scheduler       │
                    │ ┌─────────────────────────────┐  │
                    │ │ Adaptive Rate:              │  │
                    │ │  T-48h to T-24h: 15 min    │  │
                    │ │  T-24h to T-6h:  5 min     │  │
                    │ │  T-6h  to T-1h:  2 min     │  │
                    │ │  T-1h  to T-0:   60 sec    │  │
                    │ └─────────────────────────────┘  │
                    └─────────────┬───────────────────┘
                                  │
                    ┌─────────────▼───────────────────┐
                    │     Odds Normalization Engine    │
                    │  • Multi-bookmaker alignment    │
                    │  • Overround removal per book   │
                    │  • Currency/format standardize  │
                    │  • Timestamp normalization (UTC)│
                    └─────────────┬───────────────────┘
                                  │
                    ┌─────────────▼───────────────────┐
                    │     Market Data Store (Write)    │
                    │  ┌───────────────────────────┐   │
                    │  │    odds_timeseries        │   │
                    │  │    (hypertable / append)  │   │
                    │  └───────────────────────────┘   │
                    └─────────────┬───────────────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
              ▼                   ▼                   ▼
   ┌──────────────────┐ ┌────────────────┐ ┌──────────────────┐
   │ Odds Velocity    │ │ Bookmaker      │ │ Market           │
   │ Calculator       │ │ Behavior       │ │ Microstructure   │
   │                  │ │ Profiler       │ │ Engine           │
   │ • dP/dt slopes   │ │ • Sharp vs Soft│ │ • Spread width   │
   │ • Acceleration   │ │ • Lead/Lag map │ │ • Overround trend│
   │ • Momentum       │ │ • Consensus    │ │ • Liquidity proxy│
   └────────┬─────────┘ └───────┬────────┘ └────────┬─────────┘
            │                   │                    │
            └───────────────────┼────────────────────┘
                                │
                    ┌───────────▼─────────────────────┐
                    │      Signal Generator           │
                    │  • Steam Move Detection         │
                    │  • Reverse Line Movement (RLM)  │
                    │  • Market Regime Classification  │
                    │  • Bookmaker Disagreement Index  │
                    └───────────┬─────────────────────┘
                                │
                    ┌───────────▼─────────────────────┐
                    │      MIW Feature Vector          │
                    │  12 new features injected into   │
                    │  the existing feature matrix     │
                    └─────────────────────────────────┘
```

### 2.2 Feedback Integration Flow

```
  ┌──────────────┐         ┌──────────────────┐
  │  Match       │         │  MIW Signal       │
  │  Prediction  │────────▶│  Features Added   │
  │  Pipeline    │         │  to Feature Matrix│
  └──────┬───────┘         └──────────────────┘
         │
         ▼
  ┌──────────────┐         ┌──────────────────┐
  │  Kickoff     │────────▶│  CLV Engine       │
  │  (T=0)       │         │  (Existing)       │
  └──────┬───────┘         └──────┬───────────┘
         │                        │
         ▼                        ▼
  ┌──────────────┐         ┌──────────────────┐
  │  Post-Match  │────────▶│  MIW Signal       │
  │  Result      │         │  Accuracy Tracker │
  └──────────────┘         │  (NEW)            │
                           └──────┬───────────┘
                                  │
                    ┌─────────────▼───────────────┐
                    │  Signal Attribution Engine   │
                    │  "Which MIW signals were     │
                    │   predictive for this match?"│
                    └─────────────┬───────────────┘
                                  │
              ┌───────────────────┼───────────────┐
              ▼                   ▼               ▼
  ┌───────────────┐   ┌───────────────┐   ┌──────────────┐
  │ Signal Weight │   │ Regime Model  │   │ Polling      │
  │ Updater       │   │ Retrainer     │   │ Priority     │
  │ (Online)      │   │ (Offline)     │   │ Adjuster     │
  └───────────────┘   └───────────────┘   └──────────────┘
```

---

## 3. Component Breakdown

### 3.1 Subsystem Map (18 Components across 6 Subsystems)

| Subsystem | Component | Responsibility | New/Modify |
|-----------|-----------|---------------|------------|
| **Ingestion** | `OddsPollerService` | Continuous odds polling with adaptive intervals | NEW |
| **Ingestion** | `SmartPollingScheduler` | Rate-limit aware scheduling, priority queue management | NEW |
| **Ingestion** | `OddsNormalizationEngine` | Multi-bookmaker odds alignment, overround stripping | NEW |
| **Ingestion** | `HistoricalBackfiller` | Retroactive population of MIW tables from FD.co.uk CSV | NEW |
| **Core MIW** | `OddsTimeSeriesEngine` | Time-series storage, retrieval, interpolation | NEW |
| **Core MIW** | `BookmakerBehaviorProfiler` | Per-bookmaker movement patterns, sharp/soft classification | NEW |
| **Core MIW** | `MarketMicrostructureEngine` | Spread analysis, overround trends, implied liquidity | NEW |
| **Core MIW** | `SignalGenerator` | Steam moves, RLM, consensus signals, regime classification | NEW |
| **Features** | `MIWFeatureBuilder` | Transforms MIW signals into 12-column feature vector | NEW |
| **Features** | `feature_compiler` (existing) | Inject MIW features into unified feature matrix | MODIFY |
| **ML** | `MarketRegimeClassifier` | HMM/GMM-based market state classifier (Trending/Mean-Reverting/Chaotic) | NEW |
| **ML** | `ensemble.py` (existing) | Accept expanded 52-feature matrix | MODIFY |
| **Calibration** | `MarketConditionCalibrator` | Regime-conditional probability calibration | NEW |
| **Decision** | `market_builder.py` (existing) | Inject MIW consensus signal into decision score | MODIFY |
| **Learning** | `SignalAttributionEngine` | Post-match MIW signal accuracy tracking | NEW |
| **Learning** | `MIWAdaptiveLearner` | Online signal weight updates | NEW |
| **Monitoring** | `MIWDriftDetector` | Market data quality and signal degradation monitoring | NEW |
| **Monitoring** | `BookmakerHealthMonitor` | Per-source availability and accuracy tracking | NEW |

---

### 3.2 Component Detail — Core MIW Layer

#### 3.2.1 Odds Time-Series Engine

**Purpose:** Store and query odds as time-series data rather than point-in-time snapshots.

**Key Capabilities:**
- **Append-only writes:** Every odds observation is a new row with `(match_id, bookmaker, market, selection, odds, clean_prob, timestamp)`
- **Time-bucketed queries:** "Give me 1-minute OHLC for home odds on match 4521 from bookmaker Pinnacle"
- **Interpolation:** Linear interpolation for missing intervals (e.g., bookmaker lagged behind)
- **Retention policies:** Raw ticks retained 90 days; 5-minute aggregates retained indefinitely

**Internal Data Model:**
```
OddsObservation {
    match_id:       int
    bookmaker_id:   int
    market_type:    enum(1X2, OU25, BTTS, DC, DNB, AH)
    selection:      str         # "1", "X", "2", "Over", "Under", etc.
    raw_odds:       float       # Original odds value
    clean_prob:     float       # Margin-stripped probability
    overround:      float       # Bookmaker margin at capture time
    timestamp:      datetime    # UTC capture time
    time_to_kick:   int         # Seconds until kickoff (negative = post-kick)
    source_latency: int         # API response time in ms
}
```

#### 3.2.2 Bookmaker Behavior Profiler

**Purpose:** Model individual bookmaker characteristics to distinguish sharp vs. soft market signals.

**Key Concepts:**

| Metric | Definition | Signal Value |
|--------|-----------|-------------|
| **Reaction Speed** | Average time (seconds) after first-mover until this book adjusts | Lower = sharper |
| **Opening Accuracy** | Correlation between opening line and closing line | Higher = sharper |
| **Movement Amplitude** | Average total odds drift from open to close | Higher = more uncertain |
| **Lead/Lag Score** | Granger causality p-value vs. Pinnacle movements | Lower p = leader |
| **Overround Stability** | Variance of margin across matches | Lower = more systematic |

**Bookmaker Classification:**
- **Sharp (Market Makers):** Pinnacle, SBOBet — Lead price discovery
- **Mid-Tier:** Bet365, Unibet — Follow sharps within 30-120 seconds
- **Soft (Retail):** 1xBet, Bwin — Lag by 5-30 minutes, carry wider margins
- **Exchange:** Betfair — Volume-weighted pricing, purest probability signal

#### 3.2.3 Market Microstructure Engine

**Purpose:** Extract structural market features that indicate confidence, uncertainty, and information flow.

**Computed Metrics:**

| Feature | Formula | Interpretation |
|---------|---------|---------------|
| **Spread Width** | `max(book_odds) - min(book_odds)` across bookmakers | Narrow = consensus, Wide = uncertainty |
| **Overround Trend** | Δ(overround) over time | Decreasing = market converging |
| **Price Dispersion Index** | `σ(clean_probs)` across bookmakers | Low = agreement |
| **Implied Liquidity** | `1 / overround` (proxy) | Higher = more competitive market |
| **Information Asymmetry Index** | `|P_sharp - P_soft|` | High = insiders moving sharps |

#### 3.2.4 Signal Generator

**Purpose:** Produce discrete, interpretable signals from raw market data.

**Signal Catalog:**

| Signal ID | Name | Trigger Condition | Type |
|-----------|------|-------------------|------|
| `SIG_001` | **Steam Move** | Sharp book moves ≥ 3% prob in < 5 min | Event |
| `SIG_002` | **Reverse Line Movement (RLM)** | Odds shorten despite > 60% public money on opposite side | Event |
| `SIG_003` | **Market Consensus Shift** | ≥ 4 bookmakers align direction within 30 min window | Event |
| `SIG_004` | **Dead Heat Alert** | All books converge to < 2% spread in final hour | State |
| `SIG_005` | **Bookmaker Disagreement** | Sharp-soft probability gap exceeds 5% | State |
| `SIG_006` | **Late Money Surge** | Volume spike (Betfair) in final 30 min > 2x average | Event |
| `SIG_007` | **Opening Line Value (OLV)** | Model probability exceeds sharp opening by ≥ 3% | State |
| `SIG_008` | **Market Regime** | HMM state classification (Trending/Mean-Reverting/Chaotic) | State |

---

### 3.3 MIW Feature Vector (12 New Features)

These features are injected into the existing feature matrix, expanding from 40 to 52 columns.

| # | Feature Name | Derivation | Range |
|---|-------------|-----------|-------|
| 1 | `odds_velocity_home` | dP(home)/dt at prediction time (prob change per hour) | [-0.1, 0.1] |
| 2 | `odds_velocity_away` | dP(away)/dt at prediction time | [-0.1, 0.1] |
| 3 | `odds_acceleration_home` | d²P(home)/dt² (is the drift accelerating?) | [-0.05, 0.05] |
| 4 | `market_consensus_score` | Percentage of tracked bookmakers agreeing on favorite (0-1) | [0.0, 1.0] |
| 5 | `sharp_soft_divergence` | |P(sharp) - P(soft)| for predicted outcome | [0.0, 0.3] |
| 6 | `steam_move_count` | Number of steam moves detected in last 6 hours | [0, 10] |
| 7 | `rlm_indicator` | Binary: reverse line movement detected (0/1) | {0, 1} |
| 8 | `overround_trend` | Slope of overround over last 6 hours | [-0.02, 0.02] |
| 9 | `price_dispersion` | σ(clean_probs) across bookmakers for predicted outcome | [0.0, 0.1] |
| 10 | `time_weighted_drift` | Cumulative odds drift weighted by recency | [-0.2, 0.2] |
| 11 | `market_regime` | Encoded regime state (0=Stable, 1=Trending, 2=Volatile) | {0, 1, 2} |
| 12 | `opening_line_edge` | Model probability minus sharp opening probability | [-0.3, 0.3] |

---

## 4. Storage Architecture

### 4.1 Dual-Database Strategy

```
┌─────────────────────────────────────┐    ┌──────────────────────────────────┐
│        SQLite (Existing)            │    │    Market Time-Series Store      │
│        Match/Prediction DB          │    │    (TimescaleDB or DuckDB)       │
│                                     │    │                                  │
│  matches          │ teams           │    │  odds_timeseries (hypertable)    │
│  predictions      │ players         │    │  market_signals                  │
│  odds (static)    │ referees        │    │  bookmaker_profiles              │
│  odds_snapshots   │ subscribers     │    │  signal_accuracy_log             │
│  closing_odds     │ user_coupons    │    │  market_regime_states            │
│  clv_feedback_log │ threshold_state │    │  miw_feature_cache               │
│  bot_activity_log │ ...             │    │  polling_schedule                │
│                                     │    │  bookmaker_health_log            │
│  Schema: v11 (current)              │    │                                  │
│  Access: SQLite3, Row-oriented      │    │  Access: psycopg2 / duckdb      │
│  Queries: Point lookups, small agg  │    │  Queries: Time-range, OHLC,     │
│                                     │    │           window functions       │
└──────────────────┬──────────────────┘    └────────────────┬─────────────────┘
                   │                                        │
                   │        ┌──────────────────┐            │
                   └────────┤  Unified Query    ├────────────┘
                            │  Adapter Layer    │
                            │  (Repository      │
                            │   Pattern)        │
                            └──────────────────┘
```

### 4.2 New MIW Tables (Schema v12–v14)

#### v12: Core Market Tables

```sql
-- Primary time-series table (TimescaleDB hypertable candidate)
CREATE TABLE odds_timeseries (
    id              BIGSERIAL PRIMARY KEY,
    match_id        INTEGER NOT NULL,
    bookmaker_id    SMALLINT NOT NULL,
    market_type     VARCHAR(10) NOT NULL,    -- '1X2', 'OU25', 'BTTS', 'AH'
    selection       VARCHAR(10) NOT NULL,    -- '1', 'X', '2', 'Over', 'Under'
    raw_odds        REAL NOT NULL,
    clean_prob      REAL NOT NULL,
    overround       REAL NOT NULL,
    time_to_kick    INTEGER NOT NULL,        -- seconds until kickoff
    source_latency  SMALLINT DEFAULT 0,
    captured_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_ots_match_time ON odds_timeseries(match_id, captured_at DESC);
CREATE INDEX idx_ots_book_market ON odds_timeseries(bookmaker_id, market_type);

-- Bookmaker registry
CREATE TABLE bookmakers (
    id              SMALLSERIAL PRIMARY KEY,
    name            VARCHAR(50) NOT NULL UNIQUE,
    category        VARCHAR(10) NOT NULL CHECK(category IN ('sharp','mid','soft','exchange')),
    api_source      VARCHAR(50),
    is_active       BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
```

#### v13: Signal & Profile Tables

```sql
-- Detected market signals (events + states)
CREATE TABLE market_signals (
    id              BIGSERIAL PRIMARY KEY,
    match_id        INTEGER NOT NULL,
    signal_type     VARCHAR(20) NOT NULL,
    signal_strength REAL NOT NULL,
    direction       VARCHAR(5),
    metadata_json   JSONB,
    detected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    time_to_kick    INTEGER NOT NULL
);
CREATE INDEX idx_signals_match ON market_signals(match_id);
CREATE INDEX idx_signals_type ON market_signals(signal_type);

-- Bookmaker behavior profiles (updated daily)
CREATE TABLE bookmaker_profiles (
    id              SERIAL PRIMARY KEY,
    bookmaker_id    SMALLINT NOT NULL REFERENCES bookmakers(id),
    league_code     VARCHAR(10),
    reaction_speed_sec   REAL,
    opening_accuracy     REAL,
    movement_amplitude   REAL,
    lead_lag_score       REAL,
    overround_avg        REAL,
    overround_std        REAL,
    sample_size          INTEGER,
    computed_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(bookmaker_id, league_code)
);

-- Market regime states per match
CREATE TABLE market_regime_states (
    id              SERIAL PRIMARY KEY,
    match_id        INTEGER NOT NULL,
    regime          SMALLINT NOT NULL,       -- 0=Stable, 1=Trending, 2=Volatile
    confidence      REAL NOT NULL,
    transition_prob REAL[],
    computed_at     TIMESTAMPTZ DEFAULT NOW()
);
```

#### v14: Learning & Monitoring Tables

```sql
-- Signal accuracy tracking (post-match)
CREATE TABLE signal_accuracy_log (
    id              SERIAL PRIMARY KEY,
    match_id        INTEGER NOT NULL,
    signal_type     VARCHAR(20) NOT NULL,
    signal_direction VARCHAR(5),
    was_correct     BOOLEAN,
    profit_if_followed REAL,
    actual_result   VARCHAR(5),
    evaluated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Cached MIW features per match
CREATE TABLE miw_feature_cache (
    match_id                INTEGER PRIMARY KEY,
    odds_velocity_home      REAL,
    odds_velocity_away      REAL,
    odds_acceleration_home  REAL,
    market_consensus_score  REAL,
    sharp_soft_divergence   REAL,
    steam_move_count        SMALLINT,
    rlm_indicator           SMALLINT,
    overround_trend         REAL,
    price_dispersion        REAL,
    time_weighted_drift     REAL,
    market_regime           SMALLINT,
    opening_line_edge       REAL,
    computed_at             TIMESTAMPTZ DEFAULT NOW()
);

-- Bookmaker health monitoring
CREATE TABLE bookmaker_health_log (
    id              SERIAL PRIMARY KEY,
    bookmaker_id    SMALLINT NOT NULL,
    endpoint        VARCHAR(100),
    status          VARCHAR(15) CHECK(status IN ('healthy','degraded','down','stale')),
    response_time   INTEGER,
    data_freshness  INTEGER,
    checked_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Polling schedule management
CREATE TABLE polling_schedule (
    id              SERIAL PRIMARY KEY,
    match_id        INTEGER NOT NULL,
    next_poll_at    TIMESTAMPTZ NOT NULL,
    interval_sec    INTEGER NOT NULL,
    priority        SMALLINT DEFAULT 5,
    is_active       BOOLEAN DEFAULT TRUE,
    polls_remaining INTEGER,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_polling_next ON polling_schedule(next_poll_at) WHERE is_active = TRUE;
```

### 4.3 Data Volume Estimates

| Table | Rows/Day (15 matches) | Rows/Month | Retention | Size/Month |
|-------|----------------------|------------|-----------|------------|
| `odds_timeseries` | ~21,600 | ~648,000 | 90d raw, ∞ agg | ~50 MB |
| `market_signals` | ~150 | ~4,500 | ∞ | ~1 MB |
| `bookmaker_profiles` | ~60 | ~1,800 | Latest only | ~0.1 MB |
| `market_regime_states` | ~45 | ~1,350 | ∞ | ~0.2 MB |
| `signal_accuracy_log` | ~150 | ~4,500 | ∞ | ~1 MB |
| `miw_feature_cache` | ~15 | ~450 | ∞ | ~0.05 MB |

**Total incremental storage:** ~52 MB/month → ~624 MB/year

---

## 5. Event Flow

### 5.1 Event Bus Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      MIW EVENT BUS                              │
│                  (In-Process Queue / Redis*)                    │
│                                                                 │
│  Event Types:                                                   │
│  ┌──────────────────┐  ┌──────────────────┐                    │
│  │ ODDS_TICK        │  │ SIGNAL_DETECTED  │                    │
│  │ match_id, book,  │  │ match_id, type,  │                    │
│  │ odds, timestamp  │  │ strength, dir    │                    │
│  └────────┬─────────┘  └────────┬─────────┘                    │
│           │                      │                              │
│  ┌────────┴─────────┐  ┌────────┴─────────┐                    │
│  │ REGIME_CHANGE    │  │ BOOKMAKER_ALERT  │                    │
│  │ match_id, old,   │  │ bookmaker_id,    │                    │
│  │ new, confidence  │  │ status, details  │                    │
│  └────────┬─────────┘  └────────┬─────────┘                    │
│           │                      │                              │
│  ┌────────┴─────────┐  ┌────────┴─────────┐                    │
│  │ MATCH_STARTED    │  │ MATCH_SETTLED    │                    │
│  │ match_id,        │  │ match_id, result,│                    │
│  │ final_odds       │  │ closing_odds     │                    │
│  └──────────────────┘  └──────────────────┘                    │
└─────────────────────────────────────────────────────────────────┘
```

### 5.2 Event Flow — Match Lifecycle

```
T-48h: Match enters system
    │
    ├──▶ POLLING_SCHEDULED { match_id, interval: 900s }
    │    └── OddsPollerService starts monitoring
    │
T-48h → T-24h: Slow polling (15 min)
    │
    ├──▶ ODDS_TICK { match_id, bookmaker_id, odds, clean_prob }
    │    ├── OddsTimeSeriesEngine.append()
    │    └── SmartPollingScheduler.maybe_upgrade_priority()
    │
T-24h: Polling accelerates
    │
    ├──▶ POLLING_UPGRADED { match_id, interval: 300s }
    │
T-24h → T-6h: Medium polling (5 min)
    │
    ├──▶ ODDS_TICK × N
    │    ├── OddsVelocityCalculator.update()
    │    ├── BookmakerBehaviorProfiler.track_reaction()
    │    └── MarketMicrostructureEngine.update_spreads()
    │
    ├──▶ SIGNAL_DETECTED { type: STEAM_MOVE, direction: HOME, strength: 0.82 }
    │    ├── SignalGenerator.log_to_db()
    │    └── MIWFeatureBuilder.invalidate_cache(match_id)
    │
T-6h → T-1h: Fast polling (2 min)
    │
    ├──▶ REGIME_CHANGE { match_id, old: STABLE, new: TRENDING, confidence: 0.91 }
    │    └── MarketRegimeClassifier.emit_state()
    │
T-1h → T-0: Real-time polling (60 sec)
    │
    ├──▶ Multiple ODDS_TICK events
    ├──▶ MIWFeatureBuilder.compute_final_features(match_id)
    │    └── FEATURES_READY { match_id, feature_vector }
    │
T-0: Kickoff
    │
    ├──▶ MATCH_STARTED { match_id, final_odds }
    │    ├── OddsPollerService.stop_polling(match_id)
    │    ├── CLV Engine computes closing line value
    │    └── MIW records final market state snapshot
    │
T+2h: Match settles
    │
    ├──▶ MATCH_SETTLED { match_id, result, scores }
    │    ├── SignalAttributionEngine.evaluate_all_signals(match_id)
    │    ├── MIWAdaptiveLearner.update_weights(match_id)
    │    └── BookmakerBehaviorProfiler.update_accuracy(match_id)
```

---

## 6. Learning Flow

### 6.1 Online Learning (Per-Match, ~Minutes)

```
┌──────────────────────────────────────────────────────────────┐
│                   ONLINE LEARNING LOOP                       │
│                                                              │
│  Trigger: MATCH_SETTLED event                                │
│                                                              │
│  1. Retrieve all signals generated for this match            │
│  2. For each signal:                                         │
│     ├── Was signal direction correct? (HOME/DRAW/AWAY match) │
│     ├── Would following the signal have been profitable?     │
│     └── Record to signal_accuracy_log                        │
│  3. Update signal weights via EMA:                           │
│     w_new = α × accuracy_this_match + (1-α) × w_old         │
│     where α = 0.05 (slow adaptation to prevent overfit)      │
│  4. Update bookmaker_profiles for each participating book    │
│  5. If signal was STEAM_MOVE and was correct:                │
│     └── Increase steam_move weight in MIWFeatureBuilder      │
│                                                              │
│  Latency: < 5 seconds post-settlement                        │
│  State: signal_weights.json (12 weights, one per feature)    │
└──────────────────────────────────────────────────────────────┘
```

### 6.2 Offline Learning (Weekly, ~Hours)

```
┌──────────────────────────────────────────────────────────────┐
│                   OFFLINE LEARNING LOOP                       │
│                                                              │
│  Trigger: Weekly cron (Sunday 03:00 UTC)                     │
│                                                              │
│  1. MARKET REGIME CLASSIFIER RETRAIN                         │
│     ├── Pull last 90 days of odds_timeseries                 │
│     ├── Compute per-match volatility features                │
│     ├── Fit HMM with 3 states (Stable/Trending/Volatile)    │
│     ├── Validate via silhouette score + match outcome corr   │
│     └── Deploy new regime model to models/miw/               │
│                                                              │
│  2. BOOKMAKER PROFILE REFRESH                                │
│     ├── Recompute lead/lag scores (Granger causality test)   │
│     ├── Update reaction speeds and opening accuracy          │
│     └── Reclassify bookmakers if profile shifts              │
│                                                              │
│  3. SIGNAL EFFICACY AUDIT                                    │
│     ├── Compute rolling 30-day accuracy per signal type      │
│     ├── Compute rolling 30-day ROI per signal type           │
│     ├── If signal accuracy < 45% for 30 days:               │
│     │   └── Flag for deprecation or parameter tuning         │
│     └── If signal accuracy > 60% consistently:              │
│         └── Increase feature weight cap from 1.0 to 1.5     │
│                                                              │
│  4. FEATURE IMPORTANCE VALIDATION                            │
│     ├── Run SHAP on last 30 days with MIW features           │
│     ├── Compare MIW feature importance vs base features      │
│     └── Alert if MIW features contribute < 5% total SHAP    │
│                                                              │
│  Latency: 15-30 minutes                                      │
│  Output: Updated models, profiles, weights, audit report     │
└──────────────────────────────────────────────────────────────┘
```

### 6.3 Meta-Learning (Monthly, Strategy Evaluation)

```
┌──────────────────────────────────────────────────────────────┐
│                   META-LEARNING LOOP                          │
│                                                              │
│  Trigger: Monthly cron (1st of month, 04:00 UTC)             │
│                                                              │
│  1. MIW VALUE ASSESSMENT                                     │
│     ├── Compare model performance WITH vs WITHOUT MIW feats  │
│     ├── Compute:                                             │
│     │   • ΔROI = ROI(with_MIW) - ROI(without_MIW)           │
│     │   • ΔAccuracy = Acc(with_MIW) - Acc(without_MIW)      │
│     │   • ΔCLV = CLV(with_MIW) - CLV(without_MIW)           │
│     └── If ΔROI < -2%: Auto-disable MIW features (safety)   │
│                                                              │
│  2. SIGNAL LIFECYCLE MANAGEMENT                              │
│     ├── Promote experimental signals to production           │
│     ├── Retire signals with < 50% accuracy for 60+ days     │
│     └── Introduce new candidate signals from research queue  │
│                                                              │
│  3. POLLING COST OPTIMIZATION                                │
│     ├── Analyze API call counts vs. signal value per match   │
│     ├── Identify low-value polling windows (no signal delta) │
│     └── Adjust SmartPollingScheduler interval boundaries     │
│                                                              │
│  Output: Monthly MIW health report, feature toggle updates   │
└──────────────────────────────────────────────────────────────┘
```

---

## 7. Feedback Loop Design

### 7.1 Four Closed-Loop Circuits

```
   ┌─────────────────────────────────────────────────────────────────┐
   │                    FEEDBACK LOOP TOPOLOGY                       │
   │                                                                 │
   │  LOOP 1: SIGNAL → PREDICTION → RESULT → SIGNAL WEIGHTS         │
   │  ┌──────┐    ┌──────────┐    ┌──────┐    ┌──────────────┐      │
   │  │Signal│───▶│Prediction│───▶│Result│───▶│Weight Update │──┐   │
   │  │Gen.  │    │Pipeline  │    │      │    │(Online EMA)  │  │   │
   │  └──┬───┘    └──────────┘    └──────┘    └──────────────┘  │   │
   │     └──────────────────────────────────────────────────────┘   │
   │                                                                 │
   │  LOOP 2: ODDS DATA → REGIME CLASSIFIER → FEATURE → MODEL       │
   │  ┌──────┐    ┌──────────┐    ┌───────┐    ┌──────────────┐     │
   │  │Odds  │───▶│Regime    │───▶│Feature│───▶│ML Ensemble   │──┐  │
   │  │Stream│    │Classifier│    │Builder│    │(+Calibrator) │  │  │
   │  └──┬───┘    └──────────┘    └───────┘    └──────────────┘  │  │
   │     │                                                        │  │
   │     └── Retrained weekly with new regime labels ◀────────────┘  │
   │                                                                 │
   │  LOOP 3: BOOKMAKER PROFILE → SHARP/SOFT WEIGHTING → CLV        │
   │  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐      │
   │  │Bookmaker │───▶│Sharp/Soft    │───▶│Weighted Market   │──┐   │
   │  │Profiler  │    │Classification│    │Consensus Signal  │  │   │
   │  └──┬───────┘    └──────────────┘    └──────────────────┘  │   │
   │     │                                                       │   │
   │     └── Updated via post-match lead/lag validation ◀────────┘   │
   │                                                                 │
   │  LOOP 4: POLLING COST → SIGNAL VALUE → POLLING PRIORITY         │
   │  ┌──────────┐    ┌──────────────┐    ┌──────────────────┐      │
   │  │Polling   │───▶│Signal Value  │───▶│Priority Queue    │──┐   │
   │  │Scheduler │    │Per API Call  │    │Adjustment        │  │   │
   │  └──┬───────┘    └──────────────┘    └──────────────────┘  │   │
   │     └── Monthly cost optimization review ◀──────────────────┘   │
   └─────────────────────────────────────────────────────────────────┘
```

### 7.2 Loop Details

#### Loop 1: Signal Attribution Feedback

**Goal:** Learn which market signals are predictive.

| Step | Actor | Action |
|------|-------|--------|
| 1 | SignalGenerator | Emits signals during pre-match window |
| 2 | MIWFeatureBuilder | Converts signals into feature vector |
| 3 | Ensemble Model | Uses features in prediction |
| 4 | Result Engine | Match settles, actual outcome known |
| 5 | SignalAttributionEngine | Evaluates each signal's directional accuracy |
| 6 | MIWAdaptiveLearner | Updates signal weights via EMA |
| 7 | MIWFeatureBuilder | Next match uses updated weights for feature scaling |

**Convergence Guard:** Signal weights are clamped in `[-2.0, 2.0]` and change rate is limited to `±0.1 per match` to prevent oscillation from single outlier events.

#### Loop 2: Regime-Conditional Calibration

**Goal:** Calibrate probabilities differently under different market states.

- In **Stable** regimes: Use tight Platt scaling (market is efficient, model should be conservative)
- In **Trending** regimes: Use wider isotonic regression (market is discovering information, model edge exists)
- In **Volatile** regimes: Apply uncertainty penalty (widen probability bands, raise thresholds)

#### Loop 3: Bookmaker Trust Adaptation

**Goal:** Weight bookmaker signals by their historical accuracy.

Each bookmaker receives a **trust score** τ ∈ [0, 1] that modulates their contribution to consensus features:

```
consensus_weighted = Σ(τ_b × P_b) / Σ(τ_b)
```

Trust scores update weekly based on `opening_accuracy` and `lead_lag_score`.

#### Loop 4: Polling Cost Optimization

**Goal:** Maximize signal information per API call.

```
Signal Value per Call = Signals Detected in Window / API Calls in Window
```

Polling windows with consistently low signal/call ratios get their intervals widened. Windows with high signal density get tighter polling.

---

## 8. Risk Analysis

### 8.1 Data Risks

| Risk | Severity | Probability | Mitigation |
|------|----------|------------|------------|
| **Odds API outage** | HIGH | Medium | Fallback chain: Odds API → FD.co.uk → Cached last known. Stale data flag. |
| **Bookmaker delisting** | MEDIUM | Low | If book count < 3, disable consensus features, fall back to single-book mode. |
| **Odds data lag** | HIGH | Medium | Discard observations with > 5 min source latency. |
| **Historical data gaps** | MEDIUM | High | Backfiller populates only Pinnacle + Bet365 from FD.co.uk. MIW features NULL for pre-MIW matches. |
| **Rate limiting** | MEDIUM | High | SmartPollingScheduler with circuit breaker: 3 consecutive rate-limits → exponential backoff. |

### 8.2 Model Risks

| Risk | Severity | Probability | Mitigation |
|------|----------|------------|------------|
| **Feature leakage** | CRITICAL | Medium | Strict `time_to_kick > 0` filter. `captured_at < match_kickoff` enforced. |
| **Overfitting to market noise** | HIGH | Medium | SHAP importance filtering. Monthly auto-disable if ΔROI < -2%. |
| **Regime model instability** | MEDIUM | Medium | 90-day window, min 200 matches. Fallback to "Stable" if silhouette < 0.6. |
| **Signal weight oscillation** | MEDIUM | Low | EMA α=0.05, ±0.1/match cap, hard clamp [-2.0, 2.0]. |
| **Concept drift** | HIGH | Medium | Quarterly bookmaker reclassification. |

### 8.3 Infrastructure Risks

| Risk | Severity | Probability | Mitigation |
|------|----------|------------|------------|
| **Database migration failure** | CRITICAL | Low | Dual-database: SQLite stays primary. MIW starts in DuckDB (embedded). |
| **Polling service crash** | HIGH | Medium | DB-backed polling state. Auto-resume on restart. Dead-letter queue. |
| **Storage growth** | LOW | Low | ~52 MB/month. 90-day raw retention + auto-aggregation. |
| **Write contention** | HIGH | High (SQLite) | MIW tables in separate database file. No cross-DB contention. |

### 8.4 Regulatory & Ethical Risks

| Risk | Severity | Probability | Mitigation |
|------|----------|------------|------------|
| **Odds API ToS violation** | HIGH | Medium | Verify continuous polling + storage is within provider terms. Internal consumption only. |
| **Bookmaker countermeasures** | MEDIUM | Low | MIW doesn't interact with bookmakers directly. Downstream usage monitoring. |

---

## 9. Production Rollout Strategy

### 9.1 Four-Phase Deployment

```
Phase 1                Phase 2               Phase 3              Phase 4
FOUNDATION             SHADOW MODE           PARALLEL RUN         FULL INTEGRATION
(Weeks 1-3)            (Weeks 4-6)           (Weeks 7-10)         (Weeks 11-14)
```

#### Phase 1: Foundation (Weeks 1–3)

| Task | Description | Success Criteria |
|------|-------------|-----------------|
| Schema deployment | Create MIW tables (v12) in DuckDB | Tables exist, migrations pass |
| Odds Poller MVP | Single-source poller (The Odds API) with 15-min interval | Storing odds ticks |
| Normalization engine | Overround cleaning and probability extraction | Clean probs sum to 1.0 ± 0.001 |
| Historical backfill | Populate from Football-Data.co.uk CSVs | 3+ seasons loaded |
| Bookmaker registry | Seed bookmakers table | Classification matches known sharp/soft |

#### Phase 2: Shadow Mode (Weeks 4–6)

| Task | Description | Success Criteria |
|------|-------------|-----------------|
| Smart Polling Scheduler | Adaptive interval based on time-to-kick | Intervals correctly accelerate |
| Signal Generator v1 | Steam move + RLM detection | > 0.5 precision on manual review |
| MIW Feature Builder v1 | Compute 12 features | All 12 populated for tracked matches |
| Shadow predictions | Run with and without MIW features | Paired predictions for A/B |
| Monitoring | Health monitors active | Alerts fire on simulated failures |

#### Phase 3: Parallel Run (Weeks 7–10)

| Task | Description | Success Criteria |
|------|-------------|-----------------|
| A/B comparison | Evaluate shadow predictions | MIW ≥ current performance |
| Regime Classifier | Train HMM on 90 days live data | > 0.55 silhouette score |
| Signal Attribution | Post-match signal evaluation | Accuracy tracking operational |
| Feedback loops 1-3 | Enable online updates | Weights updating, calibrator switching |
| Stress testing | Simulate outages, lag, volume | Graceful degradation verified |

#### Phase 4: Full Integration (Weeks 11–14)

| Task | Description | Success Criteria |
|------|-------------|-----------------|
| Feature expansion | 40 → 52 features in production | Model retrained |
| Decision engine update | MIW consensus in BetSelector | Scores incorporate market intelligence |
| Threshold re-optimization | Optuna search with 52 features | New thresholds optimized |
| Meta-learning loop | Monthly MIW value assessment | First monthly report generated |
| Documentation | Update all docs | Docs reflect MIW integration |

### 9.2 Rollback Strategy

```python
# config/settings.py
MIW_ENABLED = False              # Master toggle
MIW_FEATURES_IN_MODEL = False    # Use MIW features in ensemble
MIW_SIGNALS_IN_DECISION = False  # Use MIW signals in BetSelector
MIW_POLLING_ACTIVE = False       # Run odds poller service
```

### 9.3 Success Metrics

| Metric | Target | Window |
|--------|--------|--------|
| **ΔROI** | ≥ +1.5% | 30 days |
| **ΔCLV** | ≥ +0.5% avg | 30 days |
| **Steam Move Precision** | ≥ 55% | 60 days |
| **RLM Precision** | ≥ 52% | 60 days |
| **API Cost** | ≤ $50/month incremental | Monthly |
| **Feature Latency** | < 200ms P99 | Ongoing |
| **Data Freshness** | < 120 sec P95 | Ongoing |

---

## File Map

### New Files (17)

| File | Purpose |
|------|---------|
| `src/miw/__init__.py` | Package init |
| `src/miw/odds_timeseries.py` | Time-series engine |
| `src/miw/bookmaker_profiler.py` | Bookmaker behavior analysis |
| `src/miw/microstructure.py` | Market microstructure metrics |
| `src/miw/signal_generator.py` | Signal detection and emission |
| `src/miw/feature_builder.py` | MIW → Feature vector transform |
| `src/miw/regime_classifier.py` | HMM/GMM market regime model |
| `src/miw/signal_attribution.py` | Post-match signal evaluation |
| `src/miw/adaptive_learner.py` | Online signal weight updates |
| `src/miw/drift_detector.py` | MIW data quality monitoring |
| `src/miw/health_monitor.py` | Bookmaker source health |
| `src/ingestion/odds_poller.py` | Continuous odds polling service |
| `src/ingestion/polling_scheduler.py` | Smart scheduling |
| `src/ingestion/odds_normalizer.py` | Multi-book normalization |
| `src/ingestion/historical_backfiller.py` | Retroactive data population |
| `config/miw_settings.py` | MIW configuration and toggles |
| `docs/MIW_ARCHITECTURE.md` | This document |

### Modified Files (9)

| File | Change |
|------|--------|
| `src/db/migrations.py` | v12-v14 MIW migrations |
| `config/constants.py` | FEATURE_COLUMNS 40 → 52 |
| `config/data_sources.py` | MIW polling source configs |
| `src/model/ensemble.py` | Accept 52-feature matrix |
| `src/model/calibration_benchmarker.py` | Regime-conditional calibration |
| `src/evaluator/market_builder.py` | MIW consensus in decision score |
| `src/model/adaptive_learning.py` | MIW signal weights in feedback |
| `worker.py` | MIW polling + retrain crons |
| `config/settings.py` | MIW feature toggles |
