# MIW — Project Manifest

## PROJECT STATUS

**Current Phase:** Implementation Ready

**Completed Design Phases:** MIW 1–12

**Core Systems:**
- Prediction Engine
- Calibration Engine
- Threshold Optimization
- CLV Intelligence
- Market Intelligence Warehouse
- Portfolio Engine
- Paper Trading Engine
- Governance Layer

**Current Priority:**
- R1 — PAL Core
- R2 — Historical Odds Warehouse MVP
- R3 — Snapshot Collection Engine

**Read Order:**
1. MIW_ARCHITECTURE
2. MIW_DATABASE_LAYER
3. MIW_INGESTION_ARCHITECTURE
4. MIW_COLLECTOR_BACKFILL
5. MIW_FEATURE_LAYER
6. MIW_CLV_INTELLIGENCE
7. MIW_DECISION_ENGINE_V3
8. MIW_PORTFOLIO_ENGINE
9. MIW_REALWORLD_ALIGNMENT
10. MIW_PAPER_TRADING_VALIDATION
11. MIW_GOVERNANCE_DEPLOYMENT
12. MIW_SYSTEM_AUDIT
13. MIW_R1_PAL_CORE
14. MIW_R1_1_PROVIDER_INTEGRATION
15. MIW_R1_2_MARKET_MEASUREMENT
16. MIW_R1_3_EDGE_DETECTION_KERNEL
17. MIW_EXECUTION_ROI_ALIGNMENT

**Important Rules:**
- No target leakage
- Point-in-time data only
- Provider Abstraction Layer mandatory
- Sharp-anchor CLV mandatory
- Walk-forward validation mandatory
- Paper trading required before live deployment

---

Canonical list of documentation files in `docs/` and their correct file names.

## docs/ structure

```
docs/
├── MIW_ARCHITECTURE.md
├── MIW_DATABASE_LAYER.md
├── MIW_INGESTION_ARCHITECTURE.md
├── MIW_COLLECTOR_BACKFILL.md
├── MIW_FEATURE_LAYER.md
├── MIW_CLV_INTELLIGENCE.md
├── MIW_DECISION_ENGINE_V3.md
├── MIW_PORTFOLIO_ENGINE.md
├── MIW_HISTORICAL_ODDS_WAREHOUSE_PLAN.md
├── MIW_REALWORLD_ALIGNMENT.md
├── MIW_PAPER_TRADING_VALIDATION.md
├── MIW_GOVERNANCE_DEPLOYMENT.md
├── MIW_R1_PAL_CORE.md
├── MIW_R1_1_PROVIDER_INTEGRATION.md
├── MIW_R1_2_MARKET_MEASUREMENT.md
├── MIW_R1_3_EDGE_DETECTION_KERNEL.md
├── MIW_EXECUTION_ROI_ALIGNMENT.md
└── MIW_SYSTEM_AUDIT.md
```

## Name reference

| Referenced as | Actual file |
|---|---|
| Market Features / MIW_MARKET_FEATURES | `MIW_FEATURE_LAYER.md` |

`MIW_FEATURE_LAYER.md` covers: Market Intelligence Features, Steam Move Signals, Reverse Line Movement, Bookmaker Trust Metrics, Market Regime Features, Feature Leakage Controls.

## Phase map

| Phase | Document |
|---|---|
| Core | MIW_ARCHITECTURE, MIW_DATABASE_LAYER, MIW_INGESTION_ARCHITECTURE, MIW_COLLECTOR_BACKFILL, MIW_FEATURE_LAYER |
| 6 | MIW_CLV_INTELLIGENCE |
| 7 | MIW_DECISION_ENGINE_V3 |
| 8 | MIW_PORTFOLIO_ENGINE |
| 9 | MIW_HISTORICAL_ODDS_WAREHOUSE_PLAN |
| 10 | MIW_REALWORLD_ALIGNMENT |
| 11 | MIW_PAPER_TRADING_VALIDATION |
| 12 | MIW_GOVERNANCE_DEPLOYMENT |
| 13 | MIW_EXECUTION_ROI_ALIGNMENT |
| R1 | MIW_R1_PAL_CORE |
| R1.1 | MIW_R1_1_PROVIDER_INTEGRATION |
| R1.2 | MIW_R1_2_MARKET_MEASUREMENT |
| R1.3 | MIW_R1_3_EDGE_DETECTION_KERNEL |
| Audit | MIW_SYSTEM_AUDIT |

> Note: `MIW_HISTORICAL_ODDS_WAREHOUSE_PLAN.md` (Phase 9) is included in `docs/` but is omitted from the Read Order above by design; consult it together with R2 (Historical Odds Warehouse MVP).
