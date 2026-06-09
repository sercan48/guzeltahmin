# MIW Phase 12 — System Validation, Model Governance & Live Deployment Framework

> Purpose: transform MIW from a research platform into a controlled, production-grade prediction business. This phase adds NO new prediction capability; it wraps the existing layers (Prediction → Calibration → Threshold → Market Intelligence → CLV → Portfolio → Real-World Alignment → Paper Trading) in a governance, validation, monitoring and risk-control shell. Phases 1–11 are UNCHANGED. No code — architecture and operations design only.

## 1. Executive Summary

MIW now has seven mature analytical layers. What is missing is the institutional discipline to run them together safely: who approved the live model, when it rolls back, which layer makes money, and how we know when the system breaks.

Phase 12 defines nine governance modules:
1. Model Governance — registry, versioning, approval workflow, champion/challenger, rollback, promotion; TRAINING→VALIDATION→SHADOW→PAPER→LIVE lifecycle.
2. Walk-Forward Validation — expanding / rolling / purged k-fold / embargo + anti-leakage framework.
3. Performance Attribution — which layer generates vs destroys profit.
4. Live Monitoring — 10 metrics + green/yellow/red alerting.
5. Model Decay Detection — edge erosion, calibration decay, regime shift + automatic responses.
6. Business KPIs — Business Health Score.
7. Live Deployment Readiness — 8-component production score + GO-LIVE CHECKLIST.
8. Failure Simulation — stress testing and resilience.
9. Continuous Improvement Loop — research backlog and experiment approval.

Current Production Readiness Score: 63/100 -> PRODUCTION_READY = NO (yet). The system is analytically strong but governance, monitoring and live-evidence layers are not yet operational. When this phase is implemented and the Phase 11 paper/shadow window closes, the score rises toward 80+.

## 2. Governance Architecture (Module 1)

### 2.1 Model Registry
Every trained artifact (model, calibrator, threshold set, ensemble weights) is stored as an immutable artifact. Registry key fields:
- model_id (UUID)
- semantic_version (MAJOR.MINOR.PATCH)
- artifact_hash (SHA-256 for integrity)
- training_data_snapshot (hash + date range)
- feature_set_version
- hyperparams
- train_metrics (IS: LogLoss, Brier, ECE, AUC)
- val_metrics (OOS: LogLoss, Brier, ECE, ROI, CLV)
- lifecycle_state (TRAINING / VALIDATION / SHADOW / PAPER / LIVE / RETIRED)
- approved_by (role + timestamp)
- parent_model_id (lineage)

Principle: no artifact is ever overwritten. New training = new version. The registry is the single source of truth.

### 2.2 Lifecycle States
| State | Meaning | Exit gate |
|---|---|---|
| TRAINING | Fitting on data | Converged, artifact hashed |
| VALIDATION | Walk-forward OOS test | LogLoss<floor AND ECE<threshold AND OOS-ROI>0 |
| SHADOW | Runs live, no bets | predicted~=actual, positive CLV, >=N matches |
| PAPER | Phase 11 paper trading | DRS>=75, all floors |
| LIVE | Real capital (champion) | — |
| RETIRED | Archived | — |

### 2.3 Champion / Challenger
- Champion: approved model running real capital.
- Challenger: parallel candidate(s) on the same live feed; places no real bets, decisions logged.
- Both receive the SAME signals; performance compared PAIRED (same matches, same period -> lower variance).
- Challenger that beats champion with statistical significance (e.g. >=4 weeks + paired bootstrap CLV/ROI edge, p<0.05) becomes a promotion candidate.

### 2.4 Approval Workflow
Researcher proposes -> Model Validation Lead runs WF + leakage audit -> OOS gates? -> CRO risk review -> SHADOW -> PAPER (Phase 11) -> DRS>=75 & evidence? -> Head of Trading LIVE promotion approval. Two-person rule: no model reaches LIVE on a single approval (Validation Lead + Head of Trading + CRO).

### 2.5 Promotion & Rollback
Promotion:
- VALIDATION->SHADOW: OOS LogLoss not worse than champion AND ECE<=0.03.
- SHADOW->PAPER: shadow CLV>=0 (significant), realized~=predicted within tolerance.
- PAPER->LIVE: Phase 11 DRS>=75 AND all component floors AND challenger>champion (paired).

Rollback (automatic, reverts to previous champion instantly):
- 14-day rolling CLV negative (significant), OR
- ECE>0.06 (calibration broke), OR
- Drawdown exceeds Phase 8 halt threshold, OR
- Data integrity violation (feature missing/corrupt).
Rollback without data loss: registry always keeps previous champion LIVE-ready (hot standby).

## 3. Validation Framework (Module 2)

| Method | How | When to use |
|---|---|---|
| Expanding Window | Train set grows over time, test moves forward | Low/medium data; long-term structural relations stable |
| Rolling Window | Fixed-width window moves forward (old data drops) | Fast regime shift; market efficiency changing — avoids staleness |
| Purged K-Fold | Training samples leaking into test fold removed | Overlapping labels/feature windows (form, rolling stats) |
| Embargo | Samples right after the test fold banned from training for a gap | Autocorrelation/serial dependence; closing-derived features can leak |

Recommended primary regime: Purged + Embargo nested walk-forward as the main validation; expanding for main trend, rolling for regime sensitivity, reported side by side.

Anti-Leakage Framework (ties Phase 10 leakage audit into governance):
- Temporal gate: every feature must prove t_feature < t_decision (no future info).
- Closing-line ban: nothing derived from closing can be a decision-time feature (only used as an outcome in CLV measurement).
- Purge + embargo mandatory on all WF folds; violation -> fold invalid.
- Leakage canary: add closing line as a feature; an abnormal OOS jump signals leakage.
- MI anomaly: feature-outcome mutual information higher than expected is flagged.
- Every registry record carries leakage_audit_passed: true/false; false blocks promotion.

## 4. Performance Attribution (Module 3)

Measure each layer's profit contribution via ablation + Shapley-style sequential addition over the same match universe:
| Layer | Measurement | Expected sign |
|---|---|---|
| Model (ensemble) | Raw prob -> ROI base | + |
| Calibration | Calibrated vs raw; LogLoss/ECE drop + EV accuracy | + |
| Thresholds (gate) | Gate on vs off ROI delta | + |
| Market Layer | Sharp/steam/RLM signal in vs out | + (usually) |
| CLV Layer | CLV-informed stake/selection vs neutral | + (long run) |
| Portfolio Layer | Kelly+exposure+correlation vs flat stake | + (risk-adjusted) |

Shapley decomposition:
PnL_total = phi_model + phi_calib + phi_thresh + phi_market + phi_CLV + phi_portfolio + eps

Each layer's risk-adjusted contribution (CLV-Sharpe per layer) is also reported to catch "makes money but blows up variance". Profit-generating = positive phi + positive risk-adjusted. Profit-destroying = negative phi (e.g. too-aggressive threshold kills bet count, or market layer adds noise) -> deactivation candidate into the backlog. Attribution is also produced by league x market x regime.

## 5. Monitoring Framework (Module 4)

| Metric | Green | Yellow | Red |
|---|---|---|---|
| Accuracy (calibrated) | expected +-1σ | 1–2σ | >2σ |
| ROI (rolling 30d) | > target | 0–target | < 0 |
| Yield | > 2% | 0–2% | < 0 |
| CLV | > 1.5% | 0–1.5% | < 0 |
| Sharp CLV | > 1% | 0–1% | < 0 |
| Drawdown | < 8% | 8–15% | > halt |
| Exposure (league/market) | within limit | 80–100% of limit | exceeded |
| Confidence Drift (PSI) | < 0.1 | 0.1–0.25 | > 0.25 |
| Calibration Drift (ECE) | < 0.03 | 0.03–0.06 | > 0.06 |
| Market Drift | efficiency stable | mild shift | regime break |

Alert logic:
- Green: normal, no action.
- Yellow: tighter monitoring + role notification; stake auto-throttled (de-risking).
- Red: auto-brake (new bets stop), CRO escalation, rollback assessment triggered.
All alerts logged with timestamp for decay analysis and post-mortems.

## 6. Risk Management Framework (Module 5)

Model Decay Detection:
| Decay type | Detection | Automatic response |
|---|---|---|
| Edge Erosion | Rolling EV/ROI and CLV trend negative | Cut stake -> trigger recalibration -> accelerate challenger |
| Calibration Decay | ECE/Brier rising (PSI drift) | Refit calibrator; if not fixed -> RETIRED |
| Regime Shift | Market efficiency/closing-predictiveness changed (Phase 10) | Re-tune regime filter; regime-specific weights |
| League Degradation | OOS drop in a league | Cut exposure / tighten league-conditional threshold |
| Market Degradation | CLV/ROI collapse in a market (e.g. BTTS) | Demote market to paper / close |

Decay thresholds: CUSUM / Page-Hinkley change-point detectors separate one-off noise from persistent shift. Yellow -> auto de-risk; Red -> auto-brake + mandatory human review. All automatic responses are reversible and logged; no automatic action permanently corrupts the registry.

## 7. Business KPI Framework (Module 6)

| KPI | Definition |
|---|---|
| Subscriber Value | Net return per subscriber / LTV proxy |
| Pick Quality | Avg CLV + calibrated accuracy of published picks |
| Monthly ROI | Monthly net return / deployed capital |
| Hit Rate | Winning pick rate (vs expected) |
| Closing Line Outperformance | % of picks beating closing (positive CLV) |
| Retention | Renewal / churn |

Business Health Score:
BHS = 100 * sigma(w1*z_CLV + w2*z_ROI + w3*z_retention + w4*z_pickQ + w5*z_hit)
CLV and Pick Quality carry the highest weight: short-term ROI can inflate on lucky variance, but positive CLV is the leading indicator of long-run profit. BHS unifies customer satisfaction (retention) with model edge (CLV) on one dashboard.

## 8. Live Deployment Framework (Module 7)

Production Readiness Score (8 components):
| Component | Weight | Floor | Current |
|---|---|---|---|
| Data Integrity | 0.15 | 70 | 72 |
| Model Reliability | 0.15 | 65 | 70 |
| Calibration | 0.12 | 65 | 68 |
| CLV Stability | 0.15 | 65 | 40 |
| Execution Quality | 0.13 | 60 | 35 |
| Portfolio Stability | 0.10 | 55 | 55 |
| Monitoring | 0.10 | 60 | 50 |
| Governance | 0.10 | 60 | 55 |

PRS = sum_k w_k * c_k; PRODUCTION_READY = YES iff PRS>=80 AND for all k: c_k>=floor_k.
Every component must contribute — one component below floor (currently Execution=35 and CLV Stability=40) forces PRODUCTION_READY = NO regardless of total.

GO-LIVE CHECKLIST:
- [ ] Data: all sources within SLA, quarantine clean, snapshot integrity verified
- [ ] Model: champion approved in registry, leakage audit passed, OOS gates passed
- [ ] Calibration: ECE<0.03, league/market-conditional calibrators current
- [ ] CLV: >=8–12 weeks positive sharp-anchor CLV evidence (Phase 11)
- [ ] Execution: fill/slippage model validated on real samples, paper~=live
- [ ] Portfolio: Kelly+exposure+correlation limits active, halt threshold defined
- [ ] Monitoring: 10-metric dashboard + green/yellow/red live
- [ ] Governance: champion/challenger running, rollback tested, two-signature approval complete
- [ ] Stress test: all Module-8 scenarios pass (graceful degradation)
- [ ] Business: BHS monitored, subscriber comms/reporting ready
No real capital is released until every box is checked.

## 9. Stress Testing Framework (Module 8)

| Scenario | Injected fault | Expected resilience |
|---|---|---|
| Missing Odds | No odds on some matches | Silently skip; no-bet alarm; no crash |
| Missing Injuries | Injury feature absent | Impute + lower confidence; close gate if needed |
| Scraper Failure | A source fully down | Fail over to fallback provider (Phase 9 stack); stale-data flag |
| Market Shock | Sudden large odds move | Trigger steam regime; lower fill prob; de-risk |
| API Outage | Live API unresponsive | Last valid snapshot + circuit breaker; stop new bets |
| Wrong Calibrator | Wrong calibrator loaded | Artifact hash mismatch -> load rejected; fall to champion |
| Wrong Threshold Set | Wrong thresholds | Version check fails -> revert to safe default |
| Sharp Market Drift | Sharp structure changes | Regime-shift detector -> regime filter + rollback review |

Resilience measurement: graceful degradation — every fault ends in safe-skip or safe-stop, never a silent wrong bet. Resilience Score per scenario {pass / partial / fail}; production requires all critical scenarios = pass. Chaos drills injected periodically in shadow/paper.

## 10. Continuous Improvement Framework (Module 9)

Research backlog: each idea is a record {hypothesis, expected edge, metric, effort, risk}, ranked by ICE/RICE (Impact x Confidence / Effort).
Experiment -> production pipeline: Hypothesis -> Offline experiment (WF + leakage audit) -> OOS edge significant? -> Challenger in SHADOW -> PAPER (Phase 11) -> beats champion (paired)? -> LIVE promotion (two-signature).
Approval principles:
- Pre-registration: hypothesis and success metric written BEFORE the experiment -> prevents p-hacking.
- No model goes from offline metrics directly to LIVE; SHADOW -> PAPER mandatory.
- Negative results are archived (institutional memory); the same failed idea is not retried.

## 11. Final Production Readiness Score

Production Readiness Score (current design stage): 63/100.
Components: Data 72, Model 70, Calibration 68, CLV Stability 40, Execution 35, Portfolio 55, Monitoring 50, Governance 55.
Weighted total ~= 63 but TWO components below floor (CLV Stability < 65, Execution < 60) => PRODUCTION_READY = NO.
Reason: analytical layers strong, but CLV and execution quality are not yet validated with live/paper evidence; monitoring and governance not operational. As the Phase 11 paper/shadow window (8–12 weeks) closes and Phase 12 governance+monitoring go live, the score rises toward 80+ and the checklist completes.

## 12. Top 20 Future Improvements (ranked by ROI)

1. Standardize sharp-anchor CLV across all markets — strongest long-run edge leader — Medium
2. Calibrate execution/slippage model on real fill data — closes paper->live gap — Medium
3. Fully automate Champion/Challenger — continuous improvement + safe promotion — Medium
4. League/market-conditional calibration — lower ECE, better EV accuracy — Medium
5. Automatic decay detector (CUSUM/Page-Hinkley) — early edge-erosion catch — Low
6. 10-metric live monitoring dashboard + alerts — mandatory production visibility — Medium
7. Make nested purged+embargo walk-forward the default — leakage-free realistic OOS — Low
8. Conformal/quantile uncertainty -> Kelly shrinkage — risk-adjusted stake accuracy — Medium
9. Automate performance-attribution dashboard — fast detection of profit-destroying layer — Medium
10. Fully automatic rollback + hot standby — limits disaster risk — Low
11. Upgrade devig to Shin/power — more accurate sharp probabilities — Low
12. Regime-conditional model weights — resilience to regime shift — Medium
13. Magnitude-aware SHAP-CLV feature adaptation — continuously improves feature quality — Medium
14. Stabilize portfolio correlation matrix with Ledoit-Wolf — better VaR/ES — Low
15. Stress-test automation (chaos drills) — production resilience evidence — Medium
16. Business Health Score dashboard + retention tracking — commercial sustainability — Low
17. SQLite -> single-writer queue / Postgres migration — fixes concurrent-write bottleneck — High
18. Extend closing-line predictor (quantile) — pre-CLV signal quality — Medium
19. Pre-registration experiment system — prevents p-hacking, builds trust — Low
20. Automatic model lineage + artifact hash verification — eliminates wrong-artifact faults — Low

---
Summary: Phase 12 is the shell that turns MIW from a research platform into a managed production business: who approves (governance), how it is validated (walk-forward + anti-leakage), where profit comes from (attribution), how it is monitored (monitoring + alerts), when it breaks (decay), commercial health (KPI/BHS), when it goes live (PRS + checklist), how it fails (stress testing) and how it improves (continuous improvement). No code written; previous phases unchanged. Current PRS 63/100 => PRODUCTION_READY = NO; targets 80+ once Phase 11 evidence window + Phase 12 operations complete.
