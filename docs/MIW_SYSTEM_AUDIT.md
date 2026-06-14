# MIW Master Audit — System Understanding, Gap Analysis & Improvement Roadmap

> **Scope.** Audits the existing production system (MIW Phase 1-9 + CLV/portfolio engine) **without redesigning it**: architecture map, weak points, mathematical risks, highest-ROI improvements, and live failure modes. **No code.** Existing structures (model stack, ECE calibrator, double/multi-gate, SHAP-CLV loop, Phases 6-9) are preserved; this report proposes *corrective* layers on top.

---

## 1. Executive Summary

The system is architecturally mature (high design completeness), but risk concentrates in the **implementation and validation layer**. Three critical themes:

1. **CLV feedback can be circular** — if realized CLV is measured against the closing of the same soft book you bet into, the signal inflates and all feedback (threshold/calibration/trust/regime) drifts the wrong way. A **sharp anchor (Pinnacle/Betfair devig) reference is mandatory.**
2. **Sim-to-live gap** — displayed odds != obtainable odds; without modeling slippage, account limits, and latency, backtest ROI will not be found live.
3. **Leakage surfaces** — closing-derived features, calibration+threshold+Optuna tuning on the same data, forward-looking regime definition → metrics look good but live collapses.

**Final System Score: 74/100** (component breakdown below). Design is strong; production-validation and data integrity are medium.

---

## 2. Architecture Understanding

End-to-end flow:

```
Ingestion (PAL, 8-snapshot, closing)
   -> MIW Features (F07 steam, F08 RLM, F10 disagreement, movement, F17 trust)
   -> Model Stack (XGBoost + LightGBM + CatBoost + Poisson) -> meta-stacker
   -> League Residual Layer
   -> Calibration (Platt / Isotonic / Beta, ECE-opt)
   -> Market-Aware Calibration (Phase 7: logit-blend p_final)
   -> Decision (DS v3 + multi-gate)
   -> CLV Engine (Phase 6: expected & realized CLV)
   -> Portfolio (Phase 8: fractional Kelly + exposure + correlation)
   -> Feedback (SHAP-CLV; threshold / calibration / trust / regime update)
   ^_________________________ closed loop _________________________|
Service: Telegram + FastAPI + Streamlit | Storage: SQLite
```

| Layer | Status | Hidden bottleneck |
|---|---|---|
| Ingestion / Warehouse | Phase 9 design ready | SQLite single-writer lock; concurrent writes in closing window + free-tier rate limits |
| Feature Layer | Mature (F07/F08/F10/F17) | Risk of closing-derived features leaking into pre-match prediction |
| Model Stack | 4-model + meta | Meta-stacker trained on samples overlapping base models (stacking leakage) |
| Calibration | ECE-opt global | Not league/market conditional; tail miscalibration hidden by ECE |
| Decision (DS v3) | Phase 7 | Heterogeneous-unit terms (EV, e_clv, Edge, C) weighted without normalization |
| CLV Engine | Phase 6 | Reference closing source (sharp vs soft) -> circularity |
| Portfolio | Phase 8 | Sparse $\Sigma$ estimation; regime-conditional correlation spikes |
| Feedback | SHAP-CLV | Sign-only fixed step -> oscillation/non-convergence; noise chasing |

**Missing components:** execution/slippage model, obtainability filter (obtainable odds), model drift monitoring (PSI/PSI-CLV), backtest-live consistency harness, latency-aware closing validation, feature-level CLV attribution.

---

## 3. Weakness Map

| Area | Weakness | Severity |
|---|---|---|
| CLV feedback | Soft-book closing reference -> circular/inflated CLV; if you are the steam the signal is fake | High |
| Execution | Slippage/limit/latency not modeled; sim-to-live gap | High |
| Leakage | Closing-derived features; calibration+threshold+Optuna on same data; forward-looking regime | High |
| Calibration | Global, not time/segment conditional; isotonic overfits small samples | Medium-High |
| Risk double-counting | C_combined already includes calibration reliability, then variance penalty $\gamma$ + dampeners cut risk again | Medium |
| Devig inconsistency | Vig removal method (proportional vs Shin vs power) not consistent across efficiency/CLV/decision | Medium |
| Portfolio $\Sigma$ | Same-match cross-market and shared-feature error correlation under-estimated | Medium |
| Infrastructure | SQLite single-writer; live polling + closing concurrency | Medium |

---

## 4. Mathematical Risk Analysis

### 4.1 Mathematical inconsistencies
- **Space mixing:** CLV v2 is in probability space, calibration blend in logit space. Transforms must be consistent one-directionally; otherwise $p_{final}$ drifts.
- **Heterogeneous-unit weighting:** in DS v3, $\beta_1 EV + \beta_2 \hat e_{clv} + \beta_3 \text{Edge} + \beta_4 C$ are on different scales; without z-score/rank standardization first, $\beta$ calibration creates implicit mis-weighting.
- **Kelly sensitivity:** $f=\text{Edge}/(o-1)$ is hyper-sensitive to edge error; if $p_{final}$ is not truly calibrated, even fractional $\kappa$ cannot fully prevent over-betting. Plus risk double-counting (above).
- **Devig:** the overround-removal method systematically shifts market probability and hence CLV; a consistent Shin/power standard is recommended.
- **market_reliability** (0.30/0.25/0.25/0.20) and $w_{market}$ weights are heuristic; empirically unvalidated.

### 4.2 Leakage risks
- Closing/late-snapshot-derived features entering the pre-match model.
- Random CV instead of walk-forward; overlapping windows in the meta-stacker.
- Calibrator and threshold (Optuna) fit on the **same** data split -> test optimism.
- League Residual Layer fit on the full history (future information).
- CLV label leaking as both target and feature.

### 4.3 CLV feedback bias
- If the reference is soft-book/own-closing it is circular; a **sharp anchor** is mandatory.
- Survivorship: only settled bets with captured closing -> missed closings systematically excluded.
- In thin markets the closing is noisy -> high-variance CLV.

### 4.4 Portfolio correlation blind spots
- Same-match 1X2 vs O/U correlation; shared-feature model-error correlation; regime-conditional correlation ($\rho$ rises in volatile regimes, misleads if estimated in calm regime).

### 4.5 Regime misclassification
- Hard 2x2 matrix + hysteresis is lagging; label noise near boundaries; post-hoc/forward-looking regime computation is leakage. **Soft (probabilistic) regime weights** recommended.

---

## 5. Improvement Roadmap (Top 10, by ROI impact)

| # | Improvement | Why it matters | Expected impact | Complexity |
|---|---|---|---|---|
| 1 | Sharp-anchor CLV (Pinnacle/Betfair devig as ground truth) | Breaks feedback circularity; corrects the entire learning signal | CLV validity up up, ROI up (indirect, large) | Medium |
| 2 | Execution/slippage + obtainability filter | Closes sim-to-live gap; eliminates fake edge | Live ROI realism up up | Medium |
| 3 | League/market-conditional calibration + recalibration cadence | Segment miscalibration breaks Kelly | Calibration up, ROI up, accuracy up | Medium |
| 4 | Anti-leakage CV (nested walk-forward; separate calibration/threshold holdouts) | Prevents fake edge -> live collapse | True ROI visibility up up | Medium |
| 5 | Standard devig (Shin/power) across the whole module | Removes market-probability & CLV bias | CLV/efficiency accuracy up | Low-Medium |
| 6 | DS v3 term standardization (z-score/rank) first | Removes heterogeneous-unit mis-weighting | Decision quality up | Low |
| 7 | Ensemble uncertainty (conformal/quantile) -> C_pred & Kelly shrinkage | Cuts over-betting on uncertain predictions | Risk-adjusted ROI up | Medium |
| 8 | Magnitude-aware + regularized SHAP-CLV adaptation | Fixes oscillation/noise-chasing of the sign-only fixed step | Feedback stability up, drift down | Low-Medium |
| 9 | Soft (probabilistic) regime + leakage-free computation | Reduces boundary noise & misclassification | Regime-dependent decision quality up | Medium |
| 10 | Move closing-window write path off single-writer SQLite (WAL/DuckDB/Postgres) | Closing-capture reliability -> CLV integrity | Missing-closing down, feedback quality up | Medium |

---

## 6. Advanced Quant Improvements

| Area | Suggestion |
|---|---|
| Calibration realism | Per-segment temperature scaling + monotone Beta; time-decay weighting; **conformal** coverage guarantee; per-league reliability diagram |
| Closing line prediction | Quantile LightGBM (q10/q50/q90, pinball — present in Phase 6) + hazard/survival for movement timing; model closing as a distribution and use as prior |
| Market efficiency | Shin model (insider param) for true probability + overround; per-bookmaker bias estimation; define efficiency dynamically via *realized-CLV-predictability* |
| Bayesian updating | Hierarchical Bayes priors for league/team; Beta-Binomial conjugate online updates for trust/efficiency; Bayesian Model Averaging instead of point stacking |
| Ensemble uncertainty | Conformal intervals + quantile/NGBoost; epistemic vs aleatoric split; feed uncertainty into $C_{pred}$ and Kelly shrinkage |
| Portfolio risk decomposition | Marginal risk contribution (Euler / component VaR); factor decomposition (league/market/regime factors); regime-conditional $\Sigma$; optional risk-parity allocation |

---

## 7. Production Risk Report (Failure Mode Simulation)

| Scenario | How it fails | Early warning |
|---|---|---|
| Live conditions | Free-tier rate limit -> missed closing -> no CLV record -> feedback starvation; latency -> bet at unobtainable price | missing-closing rate, latency p95, credit counter |
| Misleading CLV | If you are the steam / soft-book closing inflates; thin-market closing noisy; high-vig market CLV+ but ROI- | CLV vs ROI divergence; sharp-anchor deviation |
| Good metrics / overfit | Optuna threshold tuned on test; calibration+threshold on same data; SHAP-CLV chases noise -> backtest shines, live drops | walk-forward vs in-sample gap; segment-ECE; live-sim ROI gap |
| Account/selection survivorship | Soft books limit/close -> computed stake unobtainable; only obtainable bets remain -> biased ROI | fill rate; rejected/limited bet log |
| Regime shift | In volatile regime correlation spikes, $\Sigma$ under-estimates -> simultaneous drawdown | realized vs predicted portfolio variance; regime transition frequency |

---

## 8. Final System Score

| Component | Score | Note |
|---|---|---|
| Architecture & design completeness | 86 | Phases 1-9 comprehensive, modular, provider-agnostic via PAL |
| Model & calibration | 76 | Stack+ECE strong; not segment/time conditional |
| CLV & feedback accuracy | 66 | Circularity risk without sharp anchor |
| Leakage & validation discipline | 64 | Walk-forward/holdout separation must be clarified |
| Portfolio & risk | 74 | Kelly+exposure solid; correlation/regime blind spots |
| Infrastructure & operations | 70 | SQLite single-writer; execution model missing |
| **Weighted total** | **74 / 100** | Design strong; production-validation medium |

> **Highest leverage:** #1 sharp-anchor CLV + #4 anti-leakage validation + #2 execution model. These three, with no other model change, will raise both the score and real live ROI the most — because all three pull "good-looking but fake" edge back toward reality.
