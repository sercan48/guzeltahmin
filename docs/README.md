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
| `MIW_SHARP_ANCHOR_CALIBRATION.md` | Faz 15 — Real Data Calibration & Sharp Anchor Alignment (design) |
| `MIW_SHARP_DATA_INFRASTRUCTURE.md` | Faz 16 — Sharp Market Data Infrastructure & Truth Layer (design) |
| `MIW_TRUTH_WAREHOUSE_BOOTSTRAP.md` | Phase 16 bootstrap / R2 — Sharp Closing Odds Ingestion & Truth Warehouse (design) |
| `MIW_PRODUCTION_CONTROL_PLANE.md` | Phase 17 — Live Orchestration & Production Control Plane (design) |
| `MIW_R1_PAL_CORE.md` | R1 — PAL Core (Provider Abstraction Layer) implementation |
| `MIW_R1_1_PROVIDER_INTEGRATION.md` | Phase 1.1 / R1.1 — First Live Provider Integration & Snapshot Validation |
| `MIW_R1_2_MARKET_MEASUREMENT.md` | R1.2 — Live Market Measurement Layer (CLV + Odds Drift Core) — **runnable code** in `src/market/` |
| `MIW_R1_3_EDGE_DETECTION_KERNEL.md` | R1.3 — Edge Detection Kernel (model-vs-market edge, EQS, tiers) — **runnable code** in `src/market/edge/` |
| `MIW_SYSTEM_AUDIT.md` | Master audit — understanding, gaps & roadmap |

## M-series — production buildout (design → production)

| Document | Scope |
|---|---|
| `MIW_M3_TRUTH_RULE_ENFORCEMENT.md` | M3 — Truth Rule Enforcement adapter (**code** `src/market/truth/`) |
| `MIW_M3_2_TRUTH_TO_EDGE.md` | M3.2 — Truth→Edge wiring, discount-only (**code**) |
| `MIW_M4_LIFECYCLE_STATE_MACHINE.md` | M4 — Market lifecycle state machine, event-sourced (**code** `src/market/orchestration/`) |
| `MIW_M5_ORCHESTRATOR_PAPER.md` | M5 — Pipeline orchestrator & paper activation (**code**) |
| `MIW_M6_TELEGRAM_OUTPUT.md` | M6 — Telegram output & explainability, presentation only (**code** `app/bot/paper_formatter.py`) |
| `MIW_M7_SHADOW_RUN.md` | M7 — Shadow run & continuous simulation (**code** `src/market/shadow/`) |
| `MIW_M8_SETTLEMENT.md` | M8 — Settlement & Outcome Ground Truth Engine (design) |

> M1 (canonicalization) and M2 (Truth Store) are code in `src/market/truth/`;
> their contracts are documented in `MIW_SHARP_DATA_INFRASTRUCTURE.md` (F16) and
> `MIW_TRUTH_WAREHOUSE_BOOTSTRAP.md`.

## Note on `MIW_FEATURE_LAYER.md`

`MIW_FEATURE_LAYER.md` is the canonical feature-layer document. It contains:

- Market Intelligence Features
- Steam Move Signals
- Reverse Line Movement (RLM)
- Bookmaker Trust Metrics
- Market Regime Features
- Feature Leakage Controls

(Previously referred to informally as "MIW_MARKET_FEATURES"; the file name is `MIW_FEATURE_LAYER.md`.)
