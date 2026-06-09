# MIW R1.3 — Edge Detection Kernel

> Real, runnable code. Isolated to `src/market/edge/` +
> `tests/test_r1_3_edge.py`. **The prediction models are not called, retrained,
> or modified** — model probabilities are *injected*. No threshold optimization,
> no portfolio sizing, no Kelly, no feedback learning. This layer only
> **measures and classifies** edge. Every number below is the real output of
> `python3 -m src.market.edge.run_r1_3_edge` on the bundled 5-match fixture.

Built on R1.2 (drift / CLV / efficiency / integrity) and the R1.1 PAL contract.

---

## 1. Architecture

```
 R1.2 MeasurementResult            INJECTED (not computed here)
 ├─ efficiency: consensus_prob,    ├─ model_probability  p_model
 │  mean_overround, consensus,     └─ SegmentMeta: calibration_quality,
 │  sharp_proxy                                    historical clv_alignment
 └─ drift: prob_drift_total, prob_velocity
            │
            ▼
 ┌───────────────────────────── EdgeDetectionKernel (src/market/edge/) ─────────────────────────────┐
 │ (1) ModelMarketComparator   gap, gap_zscore, gap_percentile                                       │
 │ (2) EdgeMetricEngine        raw → calibrated → market_adj → drift_adj → sharp_adj  (discount cascade) │
 │ (3) EdgeConfidenceEngine    edge_confidence_score ∈ [0,1]  (5 weighted components)                │
 │ (4) MarketAgreementEngine   class A / B / C / D  (exact thresholds)                               │
 │ (6) EdgeQualityScorer       EQS ∈ [0,100]                                                         │
 │ (7) SignalClassifier        Tier S / A / B / C / Reject  (exact rules)                            │
 └───────────────────────────────────────────────────────────────────────────────────────────────┘
            │                                            ▲
            ▼                                            │ (offline, evaluation-only)
 EdgeResult per (match, market, selection)     (5) HistoricalValidator: ROI · CLV · Brier · ECE
```

| Module | File | Task |
|---|---|---|
| Config (all thresholds) | `config.py` | — |
| Comparator | `comparator.py` | 1 |
| Edge metrics (cascade) | `metrics.py` | 2 |
| Confidence engine | `confidence.py` | 3 |
| Agreement framework | `agreement.py` | 4 |
| Historical validation | `validation.py` | 5 |
| Edge Quality Score | `eqs.py` | 6 |
| Signal classification | `classification.py` | 7 |
| Orchestrator | `pipeline.py` | — |
| Runner + fixture probs | `run_r1_3_edge.py` | — |

---

## 2. Mathematical Definitions

Per `(match, market, selection)`. `p_model` injected; `p_market` = de-vigged
consensus from R1.2.

### Task 1 — Comparator
```
gap g = p_model − p_market
z     = (g − μ_g) / σ_g          (μ_g, σ_g over the batch population, or injected historical)
percentile = empirical rank of g in the population  ∈ [0,1]
```

### Task 2 — Edge metric cascade (each factor ∈ [0,1], discount-only)
```
raw_edge             = p_model / p_market − 1                         (EV at fair odds)
calibrated_edge      = raw_edge × f_cal           f_cal = calibration_quality
market_adjusted_edge = calibrated_edge × f_mkt    f_mkt = 1 / mean_overround
drift_adjusted_edge  = market_adjusted × f_drift  f_drift = (1 − contradiction) × stability
        contradiction = clip(max(0, −sign(g)·prob_drift_total)/0.08, 0, 1)
        stability     = clip(1 − |prob_velocity|/0.05, 0.5, 1)
sharp_adjusted_edge  = drift_adjusted × f_sharp   f_sharp = 1 − clip(max(0,−sign(g)·sharp_proxy)/0.03,0,1)
```
`sharp_adjusted_edge` is the **headline edge**. Confirming signals never inflate
magnitude — they raise *confidence* (Task 3).

### Task 3 — Edge Confidence  ∈ [0,1]
```
ECS = 0.25·c_cal + 0.15·c_eff + 0.25·c_disag + 0.15·c_drift + 0.20·c_clv
  c_cal   = calibration_quality
  c_eff   = market_consensus_score
  c_disag = exp(−½·((|z| − 1.5)/1.0)²)     (peaks at a meaningful, non-absurd gap)
  c_drift = f_drift
  c_clv   = historical clv_alignment
```

### Task 4 — Agreement classes (exact, top-down)
```
align = sign(g) · prob_drift_total
D CONFLICT  if |z| ≥ 2.5  OR (|g| > 0.02 AND align < −0.02)
A AGREE     elif |g| ≤ 0.02 OR |z| < 1.0
B MODEL>MKT elif g > 0
C MKT>MODEL else
```

### Task 6 — Edge Quality Score  ∈ [0,100]
```
edge_n = clip(sharp_adjusted_edge / 0.10, 0, 1)        (gated: edge ≤ 0 ⇒ EQS = 0)
EQS = 100 · (0.45·edge_n + 0.40·ECS + 0.15·clv_alignment)
```

### Task 7 — Tier rules (exact, top-down)
```
REJECT  if sharp_adjusted_edge ≤ 0  OR class == D  OR ECS < 0.35  OR EQS < 40
TIER_S  if EQS ≥ 85 AND ECS ≥ 0.70 AND class == B AND f_drift ≥ 0.90 AND f_sharp ≥ 0.90
TIER_A  elif EQS ≥ 70
TIER_B  elif EQS ≥ 55
TIER_C  elif EQS ≥ 40
```

---

## 3. Signal Hierarchy

| Tier | Meaning | Gate |
|---|---|---|
| **S** | Elite: large, calibrated, market- & sharp-confirmed, *settled* line | EQS≥85, ECS≥0.70, class B, f_drift & f_sharp ≥0.90 |
| **A** | Strong | EQS≥70 |
| **B** | Moderate | EQS≥55 |
| **C** | Marginal | EQS≥40 |
| **Reject** | No backable edge, conflict, low trust, or low quality | see rules |

Only **class B (model stronger)** can reach Tier S. Class A (agree) and class C
(market stronger) almost always fail the positive-edge gate → Reject/low tier.
Class D (conflict) is always Reject.

---

## 4. Validation Framework (evaluation only — no retraining)

`HistoricalValidator` buckets *settled* predictions by tier (or agreement class)
and computes four orthogonal metrics at **flat unit stake** (no Kelly, no
sizing):

| Metric | Formula | Good tier shows |
|---|---|---|
| ROI | `mean(outcome·entry_odds − 1)` | > 0 |
| CLV | `mean(entry_odds/closing_odds − 1)` | > 0 |
| % beat close | `mean(entry_odds > closing_odds)` | high |
| Brier(model) | `mean((p_model − outcome)²)` | ≤ Brier(market) |
| ECE | `Σ_b (n_b/N)·|acc_b − conf_b|` | low |

**Real run (synthetic settled set, 5×40 bets):**
| Tier | n | ROI | CLV | beat | Brier(m/k) | ECE |
|---|---|---|---|---|---|---|
| TIER_S | 40 | 0.3125 | **+0.0769** | 1.00 | 0.234 / 0.257 | 0.005 |
| TIER_A | 40 | 0.2650 | +0.0455 | 1.00 | 0.248 / 0.261 | 0.000 |
| TIER_B | 40 | 0.2500 | +0.0081 | 1.00 | 0.250 / 0.260 | 0.010 |
| TIER_C | 40 | 0.2150 | −0.0182 | 0.00 | 0.248 / 0.254 | 0.010 |
| REJECT | 40 | 0.1600 | **−0.0645** | 0.00 | 0.240 / 0.243 | 0.000 |

CLV (and % beat close) rank-orders the tiers monotonically — exactly the CLV
thesis from Phase 6. (ROI here is dominated by the synthetic odds and is the
*weaker* separator; CLV is the leading indicator.) In every bucket
Brier(model) ≤ Brier(market), i.e. the injected model is sharper than the line.

---

## 5. Example Scenarios (real fixture output)

```
Match                       p_mdl  p_mkt    gap     z   raw_e  sharp_e   ECS    EQS  class               tier
Arsenal vs Chelsea          0.520  0.459  0.061  1.59   0.132    0.090  0.88   86.5  B MODEL_STRONGER     TIER_A
Liverpool vs Man City       0.460  0.383  0.077  2.01   0.200    0.003  0.69   36.8  D CONFLICT_ZONE      REJECT
Barcelona vs Real Madrid    0.378  0.372  0.006  0.15   0.015    0.010  0.68   39.6  A MODEL_MARKET_AGREE  REJECT
Juventus vs Milan           0.570  0.528  0.042  1.11   0.080    0.052  0.84   67.1  B MODEL_STRONGER     TIER_B
Bayern vs Dortmund          0.665  0.648  0.017  0.43   0.026    0.017  0.73   45.5  A MODEL_MARKET_AGREE  TIER_C
```

**Scenario A — Arsenal (TIER_A, the genuine edge).** Model 0.52 vs market 0.459
(gap +0.061, z 1.59). The line is *shortening* on HOME and sharp money confirms,
so the cascade barely discounts: raw 0.132 → sharp 0.090 (`f_cal 0.88, f_mkt
0.935, f_drift 0.826, f_sharp 1.0`). ECS 0.88, EQS 86.5. Misses Tier S only
because the line is still moving (`f_drift 0.826 < 0.90` — not yet *settled*).

**Scenario D — Liverpool (REJECT, the trap edge).** Raw edge looks the *biggest*
(0.200, z 2.01) — but the market is moving the **opposite** way (away money,
`prob_drift < 0`) while the model likes HOME. The cascade collapses it:
`f_drift 0.06, f_sharp 0.276` → sharp_adjusted 0.0025, EQS 36.8, class **D**,
**Reject**. This is the kernel's core value: a large naive edge that contradicts
market behaviour is correctly killed.

**Scenario A' — Barcelona (REJECT, no edge).** gap +0.006, z 0.15 → model and
market agree; nothing to bet. EQS 39.6 < 40 → Reject.

**Scenario B — Juventus (TIER_B).** gap +0.042, confirming drift+sharp, EQS 67.1
→ moderate, real, confirmed edge.

**Scenario C — Bayern (TIER_C).** Small gap on a heavy favourite (+0.017), edge
survives the cascade but is marginal → EQS 45.5, Tier C.

**Scenario S — constructed (TIER_S, reachable).** p_model 0.58 vs 0.50, a line
that shortened toward the model early then **settled** (`prob_velocity 0.002`),
sharp confirms, calibration 0.90: `f_drift 0.96, f_sharp 1.0`, raw 0.160 → sharp
0.134, ECS 0.926, EQS 94.1, class B → **TIER_S**. (Verified by
`test_tier_s_reachable_when_confirmed_and_settled`.)

---

## 6. Integration Plan

1. **Inputs.** `model_probability` comes from the existing predictor's output
   (read-only — no change to `src/model/`). `SegmentMeta.calibration_quality`
   reads existing calibration reports (e.g. `docs/feature_importance_report` /
   calibrator reliability); `clv_alignment` reads R1.2/warehouse CLV history.
2. **Wire-up.** `EdgeDetectionKernel().run(measurement_result, model_probs,
   segment_meta)` — `measurement_result` is the R1.2 `MeasurementResult`. Pure
   function call; no I/O.
3. **Persistence.** Store `EdgeResult` rows (`gap`, edge cascade, ECS, EQS,
   tier) in a warehouse `edge_signals` table for the validator and dashboards.
4. **Boundary.** The kernel emits tiers only. Stake sizing / Kelly / portfolio
   construction remain **out of scope** (future phase) and consume tiers
   downstream — the kernel never sizes.
5. **Config.** All thresholds live in `EdgeConfig`; surface them in
   governance/config, but treat tuning as a separate, gated exercise (this phase
   forbids threshold optimization).

---

## 7. Risk Analysis

- **Garbage-in (calibration).** EQS leans on `calibration_quality`; a wrong
  value inflates/deflates tiers. Mitigation: `f_cal` shrinks edge linearly, and
  the validator's ECE column audits realized calibration per tier.
- **Stale market reference.** `p_market` from a stale/illiquid line yields a
  fake gap. Mitigation: R1.2 integrity flags (gaps, frozen feed, jumps) should
  gate the kernel — **do not score selections on flagged series.**
- **z-score population.** Batch-derived μ/σ is unstable for tiny slates.
  Mitigation: inject historical `(μ_g, σ_g)` once available
  (`hist_gap_mean/std`).
- **Provisional close.** `clv_alignment` history is only meaningful once R2/R3
  supplies real closing lines; until then treat CLV inputs as directional.
- **Sharp-proxy is a proxy.** It uses early move + book confidence, not true
  sharp flow; a soft-book-led early move can mislead `f_sharp`. Mitigation: book
  confidence weighting + small `SH_REF` keep its influence bounded.
- **No outcome feedback (by design).** Tiers cannot self-correct in this phase;
  the validator is the only feedback channel and it is offline/manual.

---

## 8. Implementation Roadmap

| Step | Scope | Status |
|---|---|---|
| R1.3a | Comparator + edge cascade + confidence + agreement + EQS + tiers | ✅ done (this doc) |
| R1.3b | Historical validator (ROI/CLV/Brier/ECE) | ✅ done (evaluation-only) |
| R1.3c | Gate kernel on R1.2 integrity flags (skip flagged series) | next |
| R1.3d | Persist `edge_signals`; inject historical `(μ_g, σ_g)` + real `clv_alignment` | needs R2 warehouse |
| R1.3e | Real closing line (R2/R3) → validator on true CLV, recalibrate `clv_alignment` | needs R2/R3 |
| R1.4  | Stake/portfolio layer **consumes** tiers (Kelly, sizing) | future phase, out of scope here |

### Run it
```bash
python3 -m src.market.edge.run_r1_3_edge --pretty      # per-selection edges + validation
python3 -m unittest tests.test_r1_3_edge -v            # 16 invariants (no network, no ML)
```

---

**Summary:** a pure, runnable edge-detection kernel — comparator → 5-stage edge
cascade → confidence → agreement (A/B/C/D) → EQS (0–100) → tiers (S/A/B/C/Reject)
— plus an evaluation-only historical validator (ROI/CLV/Brier/ECE). On the
fixture it produces a clean spread (A / Reject-conflict / Reject-agree / B / C),
correctly kills the Liverpool "trap" edge where the market moves against the
model, and Tier S is reachable on a confirmed, settled line. 16 invariant tests
pass; prediction models, thresholds, and stake sizing were untouched. Next: gate
on integrity flags + wire real closing-line CLV from R2/R3.
