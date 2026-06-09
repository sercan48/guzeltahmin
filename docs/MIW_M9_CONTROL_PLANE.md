# M9 — Production Control Plane & Live Safety Gate System

> **Status:** Design — pre-implementation. **No code.** Architecture + math +
> control logic only. **Additive over M1–M8 — touches nothing.** No prediction,
> no ML; runtime control, safety, and deployment gating only.
> **Reads:** M2 Truth Store, M4 lifecycle, M5 orchestrator/PaperSignals, M7
> shadow (health/divergence/silent-failure), M8 settlement (realized CLV/ROI/
> drawdown/settlement-confidence). Realizes the M-series equivalent of the F17
> Production Control Plane, grounded on the actually-built modules.

---

## 0. Purpose

The pipeline *runs* (M1–M8) but nothing *governs* it. M9 wraps the running
system in a control plane that decides **whether, how loudly, and at what
capital** the system may act — turning a "working pipeline" into a **controlled
financial decision system**. It never computes a prediction or an edge; it reads
the data plane's measured signals and emits control decisions (system state,
per-signal gate, exposure caps, kill).

---

## 1. Architecture

### Data plane vs control plane
```
DATA PLANE  (M1–M8, unchanged):  truth → measurement → edge → paper signal → settlement
                                      │ emits metrics (health, CLV, drawdown, lag, divergence)
                                      ▼
CONTROL PLANE (M9, additive):    reads metrics ─▶ decides {SYSTEM_STATE, gate, caps, kill}
                                      │ control decisions flow back as GATES, never as data
                                      ▼
                          governs what the data plane is allowed to publish / execute
```
The control plane has **no path that produces signals**; it can only *permit,
degrade, suppress, throttle, or halt*.

### Components C1–C10
| C | Component | Role |
|---|---|---|
| C1 | **SYSTEM_STATE Manager** | the global OFF/SHADOW/PAPER/MICRO/LIVE/LOCKED machine |
| C2 | **Gate Evaluator** | promotion/demotion gate conditions (numeric) |
| C3 | **Kill-Switch & Risk-Index Engine** | multi-factor halt + soft-throttle index |
| C4 | **Runtime Decision Gate** | per-signal ALLOW / DEGRADE / SUPPRESS |
| C5 | **Risk Engine** | drawdown guard, exposure caps, regime throttle |
| C6 | **Deployment Ladder Controller** | PAPER→MICRO→CONTROLLED→FULL LIVE staging |
| C7 | **Health Score v2 & Anomaly Detector** | composite health + anomaly flags |
| C8 | **Auto-Rollback Controller** | fast demotion on breach |
| C9 | **Fail-Safe / Degraded-Mode Controller** | outage / truth-failure / "no data = no trade" |
| C10 | **Integration Bus** | M5/M7/M8 hooks + immutable control audit log |

Relationship to the stack: **Shadow (M7)** is the evidence source for promotion;
**Settlement (M8)** is the realized-truth feedback; **Orchestrator (M5)** is the
actuator whose publish/execute step the gate (C4) sits in front of; **Truth
Store (M2)** freshness/provenance drives suppression.

---

## 2. Global Safety Model

### SYSTEM_STATE
```
OFF ──▶ SHADOW ──▶ PAPER ──▶ MICRO ──▶ LIVE
                                   ▲        │
   LOCKED ◀── (kill from ANY state) ───────┘     LOCKED ──(manual reset)──▶ SHADOW
```
| State | Meaning | Publish | Capital |
|---|---|---|---|
| OFF | nothing runs | no | none |
| SHADOW | M7 simulation only | silent | none |
| PAPER | M5 paper signals recorded + M6 to monitoring | monitoring only | none |
| MICRO | tiny controlled capital | yes (capped) | micro |
| LIVE | full (portfolio-scaled) | yes | full |
| LOCKED | safety halt | no | flatten |

### Transition rules (numeric gate conditions)
A promotion `s → s⁺` fires only if the gate `G(s⁺)` holds over a rolling window
of `N(s⁺)` settled matches (point-in-time, no look-ahead):
```
G(s⁺) =  HS_v2            ≥ H_min(s⁺)
     ∧  stability (M7)    ≥ S_min(s⁺)
     ∧  CR (shadow↔paper) ≥ CR_min(s⁺)   ∧  SPG ≤ SPG_max(s⁺)
     ∧  mean CLV_realized ≥ CLV_min(s⁺)  ∧  %beat_close ≥ beat_min(s⁺)     (M8)
     ∧  ROI_realized      ≥ ROI_min(s⁺)                                     (M8)
     ∧  max_drawdown      ≤ DD_max(s⁺)
     ∧  mean settlement_confidence ≥ SC_min(s⁺)                            (M8)
     ∧  data_coverage ≥ cov_min(s⁺)  ∧  truth_lag ≤ τ_lag
     ∧  KILL = false
```
Promotion is **slow** (sustained over `N`); demotion is **immediate** (one
breach). Thresholds tighten as the state escalates (e.g. `H_min`: SHADOW 50,
PAPER 60, MICRO 75, LIVE 85).

### Kill-switch (multi-factor)
Binary factor indicators `k_i ∈ {0,1}`:
```
k_health   = HS_v2 < H_floor
k_dd       = DD_realized > DD_kill   ∨  DD_theoretical > DD_kill_theo
k_truthlag = truth_lag > τ_kill
k_drift    = SPG_live > drift_kill   ∨  CR < CR_floor
k_silent   = any CRITICAL silent-failure flag (M7)
k_settle   = rolling settlement_confidence < SC_floor   ∨  settlement mismatch rate > m_max
k_manual   = admin halt
KILL = k_health ∨ k_dd ∨ k_truthlag ∨ k_drift ∨ k_silent ∨ k_settle ∨ k_manual
```
On `KILL=true` ⇒ `SYSTEM_STATE := LOCKED`, flatten exposure, alert, freeze
publication. A **soft risk index** arms throttling before a hard kill:
```
RiskIndex = Σ_i w_i · k_i           (w_i severity weights)
RiskIndex ≥ θ_throttle  ⇒ DEGRADE (silent mode)
RiskIndex ≥ θ_kill  ∨  any hard k_i ⇒ LOCKED
```

---

## 3. Runtime Decision Gating (C4)

Per signal, the gate returns `ALLOW | DEGRADE | SUPPRESS`:
```
SUPPRESS  if  SYSTEM_STATE ∈ {OFF, LOCKED}
          ∨  truth_lag > τ_pub                       (stale truth)
          ∨  truth_confidence < c_min                (untrustworthy)
          ∨  liquidity_score / MSI < liq_min          (liquidity risk)
          ∨  no fresh OBSERVED truth                  ("no data = no trade")
DEGRADE   elif SYSTEM_STATE = SHADOW                   (silent mode)
          ∨  RiskIndex ≥ θ_throttle
          ∨  tier not cleared for current state        (e.g. only S/A in MICRO)
ALLOW     else                                         (publish/execute per state)
```
**Silent mode** (DEGRADE): the pipeline runs fully and signals are recorded and
monitored, but **nothing is published externally and no capital is committed** —
used in SHADOW and during throttle, so the system keeps measuring while
withholding action.

---

## 4. Risk Engine (control level, C5)

```
Drawdown guard:
  DD_realized    = (peak_equity − equity) / peak_equity        (from M8 realized ROI path)
  DD_theoretical = open_exposure_worstcase / bankroll
  tiers: DD > DD_throttle ⇒ scale exposure ×(1 − f(DD));  DD > DD_kill ⇒ KILL

Exposure caps (aggregate stake limits, paper-accounted; no Kelly here):
  per-market   Σ stake_m   ≤ cap_market
  per-league   Σ stake_l   ≤ cap_league
  per-window   Σ stake_Δt  ≤ cap_window
  effective cap = cap_base × m(regime),  m(regime) ∈ (0,1]   (R1.2 regime throttle:
                                                              volatile/inefficient ⇒ lower)
```
The risk engine sets caps and throttle multipliers; it does **not** size stakes
(sizing is the portfolio layer's job and is out of M9 scope).

---

## 5. Live Deployment Ladders (C6)

Hard numeric entry/exit (illustrative; governance-tuned). Demotion is automatic
on any exit breach.

| Stage (state) | Capital | Entry (all, over N) | Exit (any) |
|---|---|---|---|
| **PAPER** (PAPER) | 0 | HS_v2≥60, CR≥0.80, SPG≤0.02, no kill | HS_v2<55 or any kill |
| **MICRO** (MICRO) | tiny cap | PAPER + N≥100 settled, CLV≥0, ROI≥0, %beat≥0.52, SC≥0.8, HS_v2≥75 | DD>DD_micro, CLV<0 rolling, HS_v2<70 |
| **CONTROLLED** (LIVE, capped) | small cap | MICRO sustained N≥200, ROI>0, DD≤DD_ctrl, HS_v2≥80, drift CR≥0.85 | DD breach, CLV turns neg, HS_v2<78 |
| **FULL LIVE** (LIVE) | portfolio-scaled | CONTROLLED sustained N≥400, ROI>0, HS_v2≥85, no kill in window | any kill, HS_v2<82, DD>DD_live |

Promotion requires the full sample `N`; a single exit condition demotes one rung
immediately (asymmetric by design).

---

## 6. Observability + Alerting (C7)

### SYSTEM HEALTH SCORE v2
Extends M7's v1 (runtime) with M8 realized dimensions:
```
HS_v2 = 100 · ( w1·(health_runtime/100)            # M7 v1: uptime/density/clv_stability/edge_entropy
              + w2·realized_clv_accuracy            # M8: sign/%-beat agreement of expected vs realized CLV
              + w3·mean_settlement_confidence        # M8
              + w4·(1 − shadow_live_drift_norm)      # M7/M8: SPG_live normalized
              + w5·data_freshness )                  # M2: 1 − clip(truth_lag/τ,0,1)
  weights e.g. {0.30, 0.25, 0.20, 0.15, 0.10}
```
### Anomaly detection + auto-rollback (C8)
Anomalies: silent-failure flags (M7), CLV divergence (expected_CLV vs
CLV_realized gap), shadow↔live drift (SPG_live↑ / CR↓), settlement-confidence
collapse, drawdown breach. Auto-rollback: on any stage exit condition →
**demote one rung + alert**; on a hard kill factor → **LOCKED**. Rollback is fast
and automatic; promotion is slow and gated.

---

## 7. Fail-Safe Design (C9)

| Condition | Behaviour |
|---|---|
| **Degraded mode** | publication SUPPRESSED, measurement continues (silent) |
| **Partial outage** | lower exposure caps, raise `c_min`/`liq_min`, prefer OBSERVED truth, widen confidence floor |
| **Truth source failure** | freeze publication entirely until truth is fresh again (SUPPRESS all) |
| **"No data = no trade"** | **hard invariant**: no fresh OBSERVED truth for a selection ⇒ gate returns SUPPRESS unconditionally — never act on stale/absent truth |
| **Settlement stalls (M8)** | hold ladder promotions; rollups exclude unsettled; alert |

These are enforced by C4/C9 *before* any publish/execute, so an outage can only
ever reduce action, never increase risk.

---

## 8. Integration

| Layer | M9 reads | M9 controls |
|---|---|---|
| **M7 Shadow** | health v1, SPG/CR, silent-failure flags | promotion evidence + kill factors (`k_drift`, `k_silent`) |
| **M8 Settlement** | CLV_realized, ROI_realized, drawdown, settlement_confidence | ladder gates, `HS_v2`, `k_dd`/`k_settle`, drawdown guard feedback loop |
| **M5 Orchestrator** | PaperSignals + truth meta | the publish/execute step is fronted by C4 (ALLOW/DEGRADE/SUPPRESS); M5 itself unchanged |
| **M2 Truth Store** | truth_lag, provenance, MSI/liquidity | `k_truthlag`, suppression, "no data = no trade" |

Control decisions are written to an **immutable control audit log** (C10) so
every state transition, kill, and suppression is replayable — mirroring M4/M8
event-sourcing discipline.

---

## 9. Final System State

```
ProductionReadiness (post-M9, design)  ≈ 35 / 100
  control completeness : 1.0   (state machine, kill, gates, ladders, fail-safe all designed)
  data plane           : built (M1–M7) + M8 designed
  real outcome feed     : ~0.2  (results not wired)
  real sharp close      : ~0.2  (Phase-16 bootstrap not done)
  live capital path      : not enabled by design until gates pass on real data
```
M9 completes the **control** half: the system can now refuse to act unsafely,
stage capital gradually, and halt on multi-factor risk. Readiness rises because
the system is now *governable*, but the binding constraint is unchanged — **real
closing-line + real result feeds**.

**LIVE opens iff ALL hold, on real data:**
1. PAPER→MICRO→CONTROLLED gates each passed over their required `N` settled
   matches on **real** outcomes + **real** sharp closes;
2. `HS_v2 ≥ 85` sustained, `CR ≥ 0.85`, `SPG ≤ 0.02`, `DD ≤ DD_live`;
3. `mean CLV_realized > 0`, `%beat_close ≥ threshold`, `ROI_realized > 0`,
   `Brier_model ≤ Brier_close` (M8);
4. no kill factor open; settlement_confidence ≥ floor; truth fresh & OBSERVED-heavy;
5. governance sign-off + control audit log green.

Until every one is true, the control plane holds the system at the highest state
its evidence supports — by construction, it cannot promote itself to LIVE on
simulated or ungrounded data.

---

### Summary
M9 is the additive control plane that governs M1–M8 without touching them: a
global SYSTEM_STATE machine (OFF→SHADOW→PAPER→MICRO→LIVE, +LOCKED) with numeric
promotion gates and a multi-factor kill-switch; per-signal runtime gating
(ALLOW/DEGRADE/SUPPRESS) with silent mode; a control-level risk engine
(drawdown/exposure/regime throttle); hard-thresholded deployment ladders;
SYSTEM HEALTH SCORE v2 fusing M7 runtime and M8 realized signals with auto-
rollback; and fail-safe "no data = no trade" enforcement. It converts a working
pipeline into a controlled financial decision system. Readiness ≈ 35/100 — the
control is complete; LIVE remains gated on real closing-line and result data.
