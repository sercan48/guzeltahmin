# MIW Faz 15 — Real Data Calibration & Sharp Anchor Alignment

> **Status:** Design — pre-implementation. **DO NOT WRITE CODE.** Pure
> architecture + mathematical specification, consistent with Faz 1–14.
> **Dependencies:** MIW_PORTFOLIO_ENGINE (F8) · MIW_REALWORLD_ALIGNMENT (F10) ·
> MIW_PAPER_TRADING_VALIDATION (F11) · MIW_GOVERNANCE_DEPLOYMENT (F12) ·
> MIW_EXECUTION_ROI_ALIGNMENT (F13) · MIW_SHADOW_LIVE_SIMULATION (F14) ·
> MIW_R1_2_MARKET_MEASUREMENT · MIW_R1_3_EDGE_DETECTION_KERNEL.

---

## 0. Problem Statement

The system is **structurally complete** (F1–F14, R1.2–R1.3) but every quantity
it trusts is anchored to the *wrong reference*: the edge kernel measures
`p_model` vs a **bookmaker-average** de-vigged price, CLV is computed against a
**bookmaker-average** close, and execution parameters are **assumed**, not fit.
On free / synthetic / low-liquidity odds these references are biased — so
"profitability" is *simulated*, not *real*.

Faz 15 installs the **truth layer**: it defines the sharp market as the
probability anchor, calibrates model probabilities and CLV to it, fits execution
parameters from real replay, strips free-API / synthetic / low-liquidity bias,
and converts the system from a *simulated-profit reporter* into a *real-market
profitability predictor*. It is a **calibration/correction boundary** — it does
not retrain the base model; it learns a calibration map and trust weights on top.

---

## 1. Full Architecture

```
                       FAZ 15 — REAL DATA CALIBRATION & SHARP ANCHOR ALIGNMENT
  ┌────────────────────────────────────────────────────────────────────────────────────────────┐
  │  C1 Sharp Market Anchor                                                                       │
  │     P_sharp = devig(Shin/power/logit) over efficiency-weighted sharp books                    │
  │     hierarchy: Pinnacle > Betfair(exchange) > top books > soft/free                           │
  │                       │                                                                        │
  │   C5 Market Quality Weighting ──▶ trust T_b per book/API ──┐                                  │
  │                       │                                     │ (weights P_sharp & consensus)   │
  │                       ▼                                     ▼                                  │
  │  C2 Calibration Drift Detection      P_model ⟷ P_sharp ⟷ P_closing   (per-league bias)        │
  │                       │                                                                        │
  │       ┌───────────────┼───────────────────────────┬───────────────────────────┐              │
  │       ▼               ▼                           ▼                           ▼              │
  │  C3 CLV Truth     C4 Execution Param Fit      C7 Bias Removal Engine      C6 Long-Term         │
  │     Correction       (κ,λ,P_fill from F11/F14)   (free API · low-liq ·        Stability        │
  │     CLV_sharp                                     synthetic inflation)        Validation        │
  │       │               │                           │                           (rolling 300–1000)│
  │       └───────────────┴──────────────┬────────────┴───────────────────────────┘              │
  │                                       ▼                                                        │
  │  C8 Outputs:  P_calibrated  ·  CLV_sharp  ·  ROI_true       C9 Integration contracts          │
  └────────────────────────────────────────────────────────────────────────────────────────────┘
```

| Component | Role |
|---|---|
| C1 Sharp anchor | Define `P_sharp` baseline + de-vig methodology + efficiency hierarchy |
| C2 Drift detection | Quantify `P_model` vs `P_sharp` vs `P_closing` bias, per league |
| C3 CLV truth correction | CLV against sharp close, not bookmaker average |
| C4 Execution fitting | Fit F13's `κ, λ, P(fill)` from F11/F14 replay |
| C5 Quality weighting | Trust score per book/API; dynamic reweighting |
| C6 Stability validation | Rolling 300–1000 match, regime-aware calibration stability |
| C7 Bias removal | Strip free-API, low-liquidity, synthetic-inflation bias |
| C8 Outputs | `P_calibrated`, `CLV_sharp`, `ROI_true` |
| C9 Integration | Contracts with F10/F11/F13/F14 |

---

## 2. Sharp Anchor System (C1)

**De-vig methodologies.** Raw implied `q_i = 1/o_i`, overround `R = Σ q_i`.
```
Multiplicative :  p_i = q_i / R                                   (baseline; biased on longshots)
Power          :  p_i ∝ q_i^(1/k),   solve k s.t. Σ p_i = 1       (corrects favorite-longshot bias)
Shin           :  p_i = [ √(z² + 4(1−z)·q_i²/R) − z ] / (2(1−z)),  solve z s.t. Σ p_i = 1
                  z = implied insider-trade fraction (accounts for informed money)
Logit consensus:  logit(p_i) = Σ_b w_b · logit(p_{b,i}),  then renormalize   (multi-book blend)
```
Default: **Shin** for single-book de-vig (handles longshot bias + insider money),
then **logit consensus** to blend books.

**Efficiency hierarchy → blend weights** `η_b` (the sharper the book, the closer
its close is to truth):
```
η_Pinnacle ≈ 1.00   η_Betfair(exchange) ≈ 0.95   η_top_book ≈ 0.6–0.8   η_soft/free ≈ 0.1–0.3
P_sharp,i = renorm( Σ_b η_b·T_b · devig(o_{b,i}) / Σ_b η_b·T_b )
```
where `T_b` is the dynamic trust score (C5). Pinnacle/Betfair dominate because
their closing line is the empirical truth proxy in sports markets.

---

## 3. Calibration Math Framework

### 3.1 (C2) Calibration Drift Detection
Three references per selection: `p_model`, `P_sharp`, `P_closing` (sharp-anchored
close). Closing is the truth proxy (outcome-predictive). Define:
```
model_bias        = E[ p_model  − 1{win} ]                 (vs realized outcome)
sharp_bias        = E[ P_sharp  − 1{win} ]                 (reference; should ≈ 0)
model_vs_sharp    = E[ p_model − P_sharp ]                 (systematic deviation)
ECE_model = Σ_bin (n_b/N)|acc_b − conf_b|     (calibration error vs outcomes)
KL(model‖sharp) = Σ p_model · log(p_model / P_sharp)       (info divergence)
```
**Per-league bias** `β_league = E_league[ logit(p_model) − logit(P_sharp) ]` — a
signed, regime/league-stratified calibration offset feeding C8.

### 3.2 (C8) Calibrated Probability `P_calibrated`
A monotone calibration map `g(·)` learned on history (isotonic / Platt /
beta-calibration) so outputs match realized frequencies, **shrunk toward the
sharp anchor** by model trust `τ_m ∈ [0,1]`:
```
logit(P_calibrated) = τ_m · logit( g(p_model) ) + (1 − τ_m) · logit( P_sharp )
```
`τ_m` rises where the model has demonstrated calibrated, sharp-beating skill
(per league/regime via C6), and falls toward the sharp anchor otherwise. `g` is
fit walk-forward (no leakage); when the model is uninformative, `P_calibrated →
P_sharp` (never worse than the market).

### 3.3 (C3) CLV Truth Correction → `CLV_sharp`
Replace bookmaker-average close with the **sharp de-vigged** close
`o_close_sharp = 1 / P_sharp,close`:
```
CLV_sharp   = o_entry / o_close_sharp − 1
CLV_naive   = o_entry / o_close_bookavg − 1
CLV_inflation = CLV_naive − CLV_sharp        (the part created by inefficient-market vig/avg)
```
Inefficient or soft-book closes inflate naive CLV; anchoring to the sharp close
removes that illusion. Only `CLV_sharp` is used downstream (F10/F13).

### 3.4 (C4) Execution Parameter Fitting
Fit F13's parameters from F11 paper fills + F14 live-sim replay by minimizing
observed-vs-modelled error (walk-forward, point-in-time):
```
slippage:  min_{κ_size,κ_speed,κ_mkt,γ}  Σ_i ( slip_obs,i − E[slip](ρ_i, v_i, m_i) )²
fill:      logistic MLE for P(fill|q) →  η, q_50(D,v,Δt,m)
impact:    min_{λ,δ}  Σ_i ( Δp_obs,i − λ·ρ_i^δ )²
```
Outputs replace the *assumed* F13 constants with *fitted* ones; refit on a
rolling schedule and per liquidity tier.

---

## 4. Bias Correction System (C5 + C7)

### 4.1 Market Quality Weighting (C5)
Trust score per source, dynamic, in `[0,1]`:
```
T_b = σ( a·closing_accuracy_b − b·vig_b − c·staleness_b + d·fill_reliability_b − e·rounding_b )
  closing_accuracy_b = 1 − E| devig(o_b,close) − P_sharp,close |     (how close to truth)
  vig_b              = overround tightness        staleness_b = update lag
  rounding_b         = coarse-odds tell of a free/scraped feed
```
Consensus and `P_sharp` weight observations by `η_b·T_b`; free/soft feeds get
small weight automatically.

### 4.2 Bias Removal Engine (C7)
| Bias | Mechanism | Correction |
|---|---|---|
| **Free-API bias** | lagged, rounded, derivative odds → systematic offset `b_api` | estimate `b_api = E[devig(o_free) − P_sharp]`; subtract or down-weight `T_b→~0` |
| **Low-liquidity distortion** | thin markets → noisy/biased, easily moved prices | weight observations by liquidity/depth; exclude below `D_min` from `P_sharp` |
| **Synthetic backtest inflation** | synthetic odds compress vig / remove slippage → fake edge | apply F13/F14 correction; scale synthetic-derived ROI by `1/Inflation_factor`; never promote on synthetic-only |

All three are estimated as **measurable offsets/weights**, not hand-waved: each
produces a number that C6 monitors over time.

---

## 5. Validation Methodology (C6)

**Rolling window** of 300–1000 settled matches; compute per window:
`ECE_model`, `Brier_model − Brier_sharp`, mean `CLV_sharp`, `model_vs_sharp`,
fitted-param stability. Calibration is **valid** iff:
```
ECE_model ≤ τ_ece              (well-calibrated to outcomes)
Brier_model ≤ Brier_sharp      (sharper than, or equal to, the sharp anchor)
mean CLV_sharp ≥ 0             (genuinely beats the sharp close)
stability: Var_window(ECE) and Var_window(β_league) ≤ τ_stab   (not regime-luck)
```
**Regime-aware:** stratify by R1.2 market regime (efficient/inefficient ×
stable/volatile); calibration must hold *per regime*, not just on average — a
model calibrated only in calm markets fails the gate.

---

## 6. Calibration Layer Outputs (C8)

| Output | Definition | Consumed by |
|---|---|---|
| `P_calibrated` | sharp-anchored, outcome-calibrated probability (§3.2) | R1.3 edge kernel (replaces raw `p_model`) |
| `CLV_sharp` | CLV vs sharp de-vigged close (§3.3) | F10 CLV alignment, F13 `CLV_realized` |
| `ROI_true` | F13 execution-adjusted ROI computed with `P_calibrated` + `CLV_sharp` + **fitted** execution params | F13/F14/F8, governance |

`ROI_true` is the headline: theoretical ROI (R1.3) → execution-adjusted (F13) →
**real-data-calibrated** (F15). It is the number governance (F12) and the
go-live gate (F14) should ultimately trust.

---

## 7. Integration Rules (C9)

| Layer | Faz 15 reads | Faz 15 writes / asserts |
|---|---|---|
| **F10 CLV Alignment** | sharp-anchor definition, expected/realized CLV | replaces CLV reference with `CLV_sharp`; reconciles its sharp anchor with C1 |
| **F11 Paper Trading** | real fills, outcomes | fits execution params (C4); supplies the calibration training set |
| **F13 Execution Model** | slippage/impact/fill structure | provides *fitted* `κ, λ, P(fill)`; feeds `P_calibrated`/`CLV_sharp` into `ROI_true` |
| **F14 Shadow Simulation** | live-sim replay, CR/SPG/PTI/LSDI | calibration stability under shadow stress; calibrated inputs improve convergence |
| **R1.3 Edge Kernel** | `p_model` | substitutes `P_calibrated`; sets `calibration_quality` (its `f_cal`) from C2/C6 |

Hard rules: (1) **No retraining of the base model** — F15 learns a calibration
map + trust weights on top. (2) `P_calibrated` may never be *less* calibrated
than `P_sharp` (shrinkage guarantees the market floor). (3) All maps/params fit
walk-forward, point-in-time, no leakage. (4) Synthetic/free-only data can
calibrate *structure* but can never certify go-live (F12/F14 gate).

---

## 8. Final Calibration Quality Score (0–100)

```
CalibQuality = 100 · ( w1·A_anchor + w2·A_calib + w3·A_clv + w4·A_exec + w5·A_stability )
  A_anchor    = sharp-anchor data coverage (real Pinnacle/Betfair closes available)
  A_calib     = 1 − normalized ECE_model vs outcomes  AND  Brier_model ≤ Brier_sharp
  A_clv       = CLV_sharp computable on real closes (R2/R3) with mean ≥ 0
  A_exec      = fraction of F13 params actually *fitted* from real replay
  A_stability = rolling/regime stability within bands (C6)
  weights (governance): w1=0.25, w2=0.25, w3=0.20, w4=0.15, w5=0.15
```

**Current honest estimate ≈ 22 / 100.** Justification:
- `A_anchor` (~0.2): no real Pinnacle/Betfair closing feed ingested yet — the
  sharp anchor is *defined* but not *populated* (R1.1 ran on a recorded fixture).
- `A_calib` (~0.2): calibration map specified; not fit on real outcomes.
- `A_clv` (~0.2): `CLV_sharp` needs real closing lines (R2/R3); only provisional
  close (R1.2) exists.
- `A_exec` (~0.2): F13 params still assumed, not fitted (needs F11 fills).
- `A_stability` (~0.3): rolling/regime methodology defined; no long-run sample.

This is the **lowest** score in the F13–F15 sequence (F13≈38, F14≈30, F15≈22)
and intentionally so: it sits at the bottom of the dependency chain — calibration
is only as real as the **real data** feeding it. The unlock sequence is the
binding constraint for the whole program:
**R2/R3 real closing-line warehouse → ingest Pinnacle/Betfair → fit C2/C3/C4 on
real outcomes → C6 long-run stability → re-score F13/F14.** When real sharp data
lands and the maps are fit, this score should rise to 70–85, at which point
`ROI_true` becomes a credible *real-market* profitability predictor.

---

### Summary
Faz 15 is the truth layer that re-anchors the whole stack to the **sharp market**:
`P_sharp` via Shin/power/logit de-vig over an efficiency-weighted
(Pinnacle>Betfair>…) book set; calibration-drift detection of
`p_model`↔`P_sharp`↔`P_closing` with per-league bias; CLV re-defined against the
sharp close (`CLV_sharp`, stripping inefficient-market inflation); execution
parameters *fitted* from F11/F14 replay; trust-weighting and bias removal for
free-API/low-liquidity/synthetic distortion; rolling, regime-aware stability
validation. Outputs `P_calibrated`, `CLV_sharp`, `ROI_true` feed R1.3/F10/F13/F14
without retraining the base model. It is the bottom of the dependency chain —
current calibration quality ≈ **22/100**, gated entirely on ingesting **real
sharp closing-line data** (R2/R3). Once that exists, the system converts from
simulated profitability to a real-market profitability predictor.
