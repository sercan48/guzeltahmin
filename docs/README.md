# MIW — Market Intelligence Warehouse

Design & architecture documentation for the MIW football prediction system. These documents describe the system design across all phases; they do not contain source code.

## Documentation Index

| Document | Scope |
|---|---|
| `MIW_ARCHITECTURE.md` | Overall system architecture |
| `MIW_DATABASE_LAYER.md` | Database / storage layer |
| `MIW_INGESTION_ARCHITECTURE.md` | Data ingestion pipeline |
| `MIW_COLLECTOR_BACKFILL.md` | Collectors & historical backfill |
| `MIW_FEATURE_LAYER.md` | Feature layer (see note below) |
| `MIW_CLV_INTELLIGENCE.md` | Phase 6 — CLV Intelligence & Market Learning |
| `MIW_DECISION_ENGINE_V3.md` | Phase 7 — Market-Aware Calibration & Decision Engine |
| `MIW_PORTFOLIO_ENGINE.md` | Phase 8 — Portfolio Intelligence & Capital Allocation |
| `MIW_HISTORICAL_ODDS_WAREHOUSE_PLAN.md` | Phase 9 — Historical Odds Warehouse plan |
| `MIW_REALWORLD_ALIGNMENT.md` | Phase 10 — Real-World Alignment Layer |
| `MIW_PAPER_TRADING_VALIDATION.md` | Phase 11 — Paper Trading & Execution Validation |
| `MIW_GOVERNANCE_DEPLOYMENT.md` | Phase 12 — Governance & Live Deployment Framework |
| `MIW_EXECUTION_ROI_ALIGNMENT.md` | Faz 13 — Realistic Execution & ROI Alignment Engine (design) |
| `MIW_SHADOW_LIVE_SIMULATION.md` | Faz 14 — End-to-End Live Simulation & Shadow Verification Layer (design) |
| `MIW_R1_PAL_CORE.md` | R1 — PAL Core (Provider Abstraction Layer) implementation |
| `MIW_R1_1_PROVIDER_INTEGRATION.md` | Phase 1.1 / R1.1 — First Live Provider Integration & Snapshot Validation |
| `MIW_R1_2_MARKET_MEASUREMENT.md` | R1.2 — Live Market Measurement Layer (CLV + Odds Drift Core) — **runnable code** in `src/market/` |
| `MIW_R1_3_EDGE_DETECTION_KERNEL.md` | R1.3 — Edge Detection Kernel (model-vs-market edge, EQS, tiers) — **runnable code** in `src/market/edge/` |
| `MIW_SYSTEM_AUDIT.md` | Master audit — understanding, gaps & roadmap |

## Note on `MIW_FEATURE_LAYER.md`

`MIW_FEATURE_LAYER.md` is the canonical feature-layer document. It contains:

- Market Intelligence Features
- Steam Move Signals
- Reverse Line Movement (RLM)
- Bookmaker Trust Metrics
- Market Regime Features
- Feature Leakage Controls

(Previously referred to informally as "MIW_MARKET_FEATURES"; the file name is `MIW_FEATURE_LAYER.md`.)
