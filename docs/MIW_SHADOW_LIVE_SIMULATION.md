# MIW Faz 14 — End-to-End Live Simulation & Shadow Verification Layer

> **Status:** Design — pre-implementation. **DO NOT WRITE CODE.** Pure
> architecture + mathematical specification, consistent with Faz 1–13.
> **Dependencies:** MIW_PORTFOLIO_ENGINE (F8) · MIW_REALWORLD_ALIGNMENT (F10) ·
> MIW_PAPER_TRADING_VALIDATION (F11) · MIW_GOVERNANCE_DEPLOYMENT (F12) ·
> MIW_EXECUTION_ROI_ALIGNMENT (F13) · MIW_R1_2_MARKET_MEASUREMENT ·
> MIW_R1_3_EDGE_DETECTION_KERNEL.

---

## 0. Problem Statement

Every layer has been validated *in isolation*: the edge kernel scores edges
(R1.3), F13 discounts them to executable ROI, F11 paper-trades, F10 checks CLV.
But there is **no single harness that runs the entire chain end-to-end under
realistic live conditions** — real-time ticks, latency, delayed/rejected fills,
liquidity shocks — and proves that theoretical → paper → live performance does
not silently collapse somewhere along the pipe.

Faz 14 is the **shadow-to-live verification system**: it replays (or live-shadows)
the full pipeline, simulates execution physics from F13, and measures the gap
between what each stage *claims* and what is *realized*, producing a hard
`LIVE_READY` gate. It is a **verification harness, not a new strategy** — it
changes nothing upstream; it observes, stresses, and scores.

---

## 1. Architecture

```
                         FAZ 14 — SHADOW-TO-LIVE VERIFICATION SYSTEM
  ┌──────────────────────────────────────────────────────────────────────────────────────────┐
  │  S1 End-to-End Shadow Pipeline  (deterministic replay of the full chain)                   │
  │     ingestion → feature → model → edge(R1.3) → decision → execution(F13) → CLV(F10) → port.│
  │                          │                                                                 │
  │                          ▼                                                                 │
  │  S2 Live Simulation Engine   tick-by-tick odds replay · stochastic latency · delayed fills │
  │                          │                                                                 │
  │        ┌─────────────────┼───────────────────────────┬───────────────────────────┐        │
  │        ▼                 ▼                           ▼                           ▼        │
  │  S3 CLV Stability   S4 Execution Stress       S5 Pipeline Consistency      S8 Portfolio    │
  │     Validator(F10)     (liq. collapse, odds      Checker (theo vs paper       Stability     │
  │                        shock, rejection)         vs live-sim divergence)      Test          │
  │        │                 │                           │                           │        │
  │        └─────────────────┴──────────────┬────────────┴───────────────────────────┘        │
  │                                          ▼                                                  │
  │  S6 Convergence Metrics: CR · SPG · PTI · LSDI      S7 Failure-Mode Replay (worst 10%)      │
  │                                          │                                                  │
  │                                          ▼                                                  │
  │  S9 GO-LIVE GATE  →  LIVE_READY ∈ {true,false}   (+ S10 integration contracts)              │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
```

| Stage | Role |
|---|---|
| S1 Shadow pipeline | Deterministic, replayable run of the entire chain on recorded data |
| S2 Live sim engine | Real-time tick emulation: latency, delayed/partial/rejected fills |
| S3 CLV stability | CLV drift live-vs-paper; sharp-anchor consistency (F10) |
| S4 Execution stress | Liquidity collapse, odds shock, bookmaker rejection scenarios |
| S5 Consistency checker | Divergence among theoretical / paper / live-sim pipelines |
| S6 Convergence metrics | CR, SPG, PTI, LSDI |
| S7 Failure replay | Re-run worst-decile scenarios; locate edge collapse |
| S8 Portfolio stability | Volatility under regime change, correlation breakdown, exposure stress |
| S9 Go-live gate | Strict `LIVE_READY` conditions |
| S10 Integration | Contracts with F10, F11, F13, R1.3 |

**Three parallel pipelines** are run on the *same* event stream and compared:
```
P_theo  : theoretical — quoted odds, full fill, no latency/impact      (R1.3 + ROI_theoretical)
P_paper : paper — F11 fills, real-time but frictionless decisioning     (F11)
P_live  : simulated-live — F13 execution physics + S2 latency/ticks     (F13 + S2)
```
Verification = measuring and bounding the gaps `P_theo → P_paper → P_live`.

---

## 2. Simulation Design

### 2.1 (S1) Deterministic Shadow Replay
Replays the recorded snapshot stream through the exact production code path with
a **fixed seed** and **point-in-time** guarantees (no look-ahead). Same input ⇒
byte-identical decisions, so any divergence is attributable, not noise. This is
the regression backbone: a code change that alters historical decisions is
flagged immediately.

### 2.2 (S2) Live Simulation Engine
Converts the snapshot warehouse into a **tick stream** and injects realism:
```
tick(t)         : next odds update arrives at event time t (from R1.2 series)
decision_lag    : L_dec ~ pipeline compute latency  (fixed + jitter)
network_lag     : L_net ~ Exp(μ_net) or LogNormal    (stochastic latency injection)
execution_lag   : L_exe = time from decision to order hitting the book
o_at_fill       : the odds *actually live* at  t_decision + L_dec + L_net + L_exe
                  (the line may have moved — captured via R1.2 drift between ticks)
```
Delayed execution emulation: the order is matched against the book state at the
*lagged* timestamp, not the decision timestamp — so latency directly produces
slippage via F13's model. Partial/rejected fills follow F13's `P(fill)`.

### 2.3 (S4) Execution Stress Scenarios
| Scenario | Injection | Tests |
|---|---|---|
| Liquidity collapse | `D → α·D` (α≪1) mid-fill | fill-rate & impact resilience |
| Sudden odds shift | step `Δp` jump between ticks (steam/news) | adverse selection, stop logic |
| Bookmaker rejection | reject order w.p. `r_rej` (limits, voids) | dependence on a single venue |
| Latency spike | `L_net → k·L_net` | timing-edge decay sensitivity |

### 2.4 (S7) Failure-Mode Replay
Rank all replayed decisions by realized PnL; isolate the **worst 10%** and
re-run them with full instrumentation to locate *where* the edge collapsed:
ingestion gap? stale `p_market`? fill failure? CLV reversal? adverse line move?
Output: an attribution histogram of collapse causes.

---

## 3. Validation Metrics (S6)

Let per-decision realized ROI in each pipeline be `R^theo_i, R^paper_i,
R^live_i`, over `N` decisions.

### 3.1 Convergence Rate (CR)
Agreement rate between paper and live-sim decisions (same selection, same
fill/no-fill, ROI within tolerance `ε`):
```
CR = (1/N) Σ_i  1[ |R^live_i − R^paper_i| ≤ ε  AND  fill_live_i == fill_paper_i ]     ∈ [0,1]
```
Complemented by a *mean-convergence* check: the rolling |E[R^live] − E[R^paper]|
must decay below `ε_μ` and stay there as `N` grows (stability, not luck).

### 3.2 Shadow-to-Paper Gap (SPG)
Signed performance loss from paper to live-sim:
```
SPG = ROI_paper − ROI_liveSim          (>0 ⇒ live conditions degrade performance)
report mean ± bootstrap CI; SPG is the headline "cost of going live".
```

### 3.3 Paper-to-Theoretical Inflation (PTI)
How inflated the theoretical number is over paper (ties to F13's
`Inflation_factor`):
```
PTI = ROI_theoretical / ROI_paper          ( ≥ 1; 1.0 = no inflation )
```

### 3.4 Live Simulation Degradation Index (LSDI)
Composite degradation of live-sim vs paper across four orthogonal axes:
```
LSDI = w1·(1 − EEI_live/EEI_paper)
     + w2·(1 − MRR_live/MRR_paper)            (EEI, MRR from F13)
     + w3·(1 − fill_live/fill_paper)
     + w4·max(0, DD_live/DD_paper − 1)        (drawdown inflation)
     ∈ [0, ~1];   0 = no degradation, higher = worse.   weights governance-set.
```

### 3.5 (S3) CLV Stability
```
CLV_drift   = | CLV_live − CLV_paper |                          (must be ≤ τ_clv)
sharp_anchor_consistency = corr( CLV_live , sharp_anchor_CLV )  (F10; must be ≥ ρ_sharp)
CLV is "stable" iff CLV_drift ≤ τ_clv  AND  consistency ≥ ρ_sharp  under S4 stress.
```

### 3.6 (S8) Portfolio Stability
```
σ_regime      = portfolio volatility measured within each market regime (R1.2)
Δσ_regime     = max_regime σ − min_regime σ                    (regime sensitivity)
ρ_breakdown   = drop in diversification when correlations → 1 under stress
exposure_VaR  = worst-case exposure under the stress scenarios (S4)
Stable iff Δσ_regime, ρ_breakdown, exposure_VaR within governance bands.
```

---

## 4. Failure Analysis

| Failure | Symptom in Faz 14 | Likely root cause | Gate response |
|---|---|---|---|
| Latency-driven slippage | High SPG, low CR, EEI_live ≪ EEI_paper | execution lag eats timing edge | tighten limits / earlier entry; fail gate if persistent |
| CLV reversal under live | CLV_drift > τ_clv, consistency↓ | edge was stale/illusory; fake sharp | block; recheck F10 sharp-anchor |
| Liquidity trap exposed | fill_live ≪ fill_paper in S4 | high edge on thin tier | demote tier; cap stake (F13) |
| Single-venue fragility | rejection scenario tanks ROI | over-reliance on one book | require multi-venue redundancy |
| Regime fragility | Δσ_regime large; ρ_breakdown high | correlated book at scale | reduce exposure; throttle correlated stake |
| Backtest mirage | PTI ≫ 1 with negative SPG cushion | theoretical never reachable | report PTI; size on paper/live, never theo |
| Replay non-determinism | S1 divergence on identical input | hidden state / look-ahead leak | hard fail — fix before any go-live |

Structural caveat: Faz 14 inherits F13's dependency on **calibrated execution
parameters** and **real closing lines (R2/R3)**. Until those exist, live-sim is a
*model of* live, not live — its outputs are upper-confidence, not ground truth.

---

## 5. Go-Live Decision Framework (S9)

`LIVE_READY = true` requires **all** of the following to hold over a
governance-mandated sample (walk-forward, multiple regimes), with no single
stress scenario breaching:
```
1. CR              ≥ CR_min        (e.g. 0.85)        — paper and live-sim agree
2. SPG             ≤ SPG_max       (e.g. 1.5% ROI)    — live cost is bounded
3. PTI             ≤ PTI_max       (e.g. 1.6)         — theory not wildly inflated
4. LSDI            ≤ LSDI_max      (e.g. 0.25)        — degradation contained
5. CLV stable      : CLV_drift ≤ τ_clv AND consistency ≥ ρ_sharp under S4 stress
6. Stress pass     : S4 scenarios all within bands (no liquidity/rejection blow-up)
7. Portfolio stable: S8 metrics within governance bands
8. Determinism     : S1 replay bit-identical on re-run
9. Governance      : F12 sign-off + kill-switch + monitoring wired
```
Decision logic is **AND across all gates** — a single failure ⇒ `LIVE_READY =
false` with the specific failing gate reported. The gate is *staged*: pass →
limited-capital canary → re-measure live → scale. No direct full-capital launch.

---

## 6. Integration Rules (S10)

| Layer | Faz 14 reads | Faz 14 writes / asserts |
|---|---|---|
| **F10 CLV Alignment** | sharp-anchor CLV, expected/realized CLV | CLV_drift, sharp_anchor_consistency under stress |
| **F11 Paper Trading** | paper fills, paper ROI/EEI/MRR | the `P_paper` baseline; SPG = paper − live-sim |
| **F13 Execution Realism** | slippage, `P(fill)`, impact, ROI decomposition | applies F13 physics inside `P_live`; EEI/MRR_live |
| **R1.3 Edge Kernel** | edge, EQS, tier, executable_edge | re-scores under live conditions; tier survival rate |

Hard rules: (1) Faz 14 **observes**, never mutates upstream outputs. (2) `P_live`
must use F13's execution model — no frictionless shortcuts. (3) Determinism (S1)
is non-negotiable: any look-ahead/hidden-state leak is an automatic fail. (4)
Go-live is gated by F12 governance on top of the Faz 14 metrics.

---

## 7. Final System Readiness Score (0–100)

Meta-score of end-to-end production readiness given which verification
components are actually exercised on real data:
```
Readiness = 100 · ( w1·G_pipeline + w2·G_convergence + w3·G_clv + w4·G_stress + w5·G_governance )
  G_pipeline    = E2E shadow pipeline runs deterministically on real recorded data
  G_convergence = CR/SPG/PTI/LSDI measured and within gate on a real sample
  G_clv         = CLV stability validated under stress with real closing lines (R2/R3)
  G_stress      = S4 stress scenarios executed and passed
  G_governance  = F12 sign-off, kill-switch, live monitoring in place
  weights (governance): w1=0.20, w2=0.25, w3=0.25, w4=0.15, w5=0.15
```

**Current honest estimate ≈ 30 / 100.** Justification:
- `G_pipeline` (~0.5): the chain exists and R1.2/R1.3 run deterministically, but
  there is no unified harness wiring ingestion→portfolio yet.
- `G_convergence` (~0.2): metrics are *defined* but not yet *measured* — no paper
  vs live-sim sample exists.
- `G_clv` (~0.2): blocked on real closing lines (R2/R3); only provisional close
  (R1.2) available, so CLV stability is unverified live.
- `G_stress` (~0.2): stress scenarios specified, not executed.
- `G_governance` (~0.5): F12 framework designed; kill-switch/monitoring not wired.

This is deliberately lower than F13's realism (~38): readiness is the *product*
of many gates, and several (real CLV, calibrated execution, a built harness)
remain open. Sequencing to raise it: **R2/R3 closing lines → calibrate F13 from
F11 fills → build the S1 harness → measure CR/SPG/PTI/LSDI → run S4 stress → F12
sign-off.** When complete, the same formula should reach 75–90 and unlock the
staged canary launch.

---

### Summary
Faz 14 specifies the verification harness that runs the full pipeline three ways
— theoretical, paper, simulated-live — on the same event stream, injects live
physics (ticks, latency, delayed/rejected fills, F13 execution), stress-tests
liquidity/odds-shock/rejection, replays the worst decile to locate edge
collapse, and condenses everything into four convergence metrics (CR, SPG, PTI,
LSDI) plus CLV- and portfolio-stability checks. A strict AND-gated `LIVE_READY`
decision (staged canary, never direct full launch) sits on top, governed by F12.
It mutates nothing upstream — it only verifies. Implementation is blocked on the
same real dependencies as F13 (closing lines R2/R3, execution calibration from
F11) plus building the harness itself. Current end-to-end readiness ≈ **30/100**,
rising as those gates close.
