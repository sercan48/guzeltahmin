# M8 — Settlement & Outcome Ground Truth Engine

> **Status:** Design — pre-implementation. **No code.** Architecture + math +
> data-flow only, consistent with the design docs. **Does not touch M1–M7** — it
> is an additive layer on top. No prediction logic, no ML; outcome verification
> and settlement only.
> **Dependencies:** M2 Truth Store · M4 Lifecycle (CLOSED→SETTLED) · M5
> Orchestrator (PaperSignals + SETTLEMENT_COMPLETED) · M6 Telegram · M7 Shadow ·
> F10 CLV · F13 ROI decomposition · F15/F16 sharp anchor.

---

## 0. Purpose

Everything upstream *predicts and measures forward*; nothing yet *closes the
loop backward* from the real result. M8 turns the system from a "prediction
engine" into a **closed-loop measurement system**: it ingests the match outcome,
locks the canonical close, and finalizes realized CLV / ROI / error — so every
signal is graded against ground truth and the whole stack becomes
self-measuring.

Closed loop: `signal → (kickoff) → match result → truth closure → CLV/ROI
realization → performance finalization → feedback to monitoring`.

---

## 1. Architecture

```
            M4: CLOSED ───────────────────────────────────────────▶ SETTLED
                 │  match_finished      outcome_ingest    truth_close    metric_finalize
                 ▼        │                   │                │              │
   ┌─────────────────────┼───────────────────┼────────────────┼──────────────┼─────────────┐
   │  S8 Outcome         S9 Truth Closure    S10 Settlement   S11 Performance S12 Integration│
   │  Ingestion           (lock + ledger)     Confidence &     Finalization    & Feedback     │
   │  (provider-agnostic) (immutable, replay)  Math            (metrics/rollup)(M5/M6/M7)      │
   └──────────────────────────────────────────────────────────────────────────────────────────┘
                 │                                                          │
                 ▼                                                          ▼
        M2 Truth Store (extended): closing_truth (locked) + match_outcomes + settlement_ledger + realized_metrics
```

**Settlement pipeline components (S8–S12):**

| Comp | Role |
|---|---|
| **S8 Outcome Ingestion** | provider-agnostic result feeds → canonical score → market outcomes; OT/void/cancel handling |
| **S9 Truth Closure** | lock the canonical close at kickoff; write the immutable, replay-safe settlement ledger |
| **S10 Settlement Math & Confidence** | CLV_realized, ROI_realized, error decomposition, settlement_confidence |
| **S11 Performance Finalization** | per-match metrics, rolling CLV accuracy, league/regime + sharp/soft splits |
| **S12 Integration & Feedback** | grade M5 PaperSignals, reconcile M7 shadow, push M6 final-result message |

**Event flow** (maps onto the existing M4 lifecycle — no M4 change):
```
MATCH_FINISHED (M4 data event, CLOSED)
   → S8 outcome_ingest        → match_outcomes row (provenance-tagged)
RESULT_CONFIRMED (M4 guard)
   → S9 truth_close           → lock closing_truth, append settlement_ledger
SETTLEMENT_COMPLETED (M4 → SETTLED)
   → S10 settle               → CLV_realized / ROI_realized / errors / confidence
   → S11 finalize             → realized_metrics + rollups
   → S12 feedback             → M5/M6/M7
```

### M2 Truth Store extension (additive tables)
| Table | Purpose |
|---|---|
| `closing_truth` (locked) | the canonical sharp close, **frozen** at kickoff; `locked=true`, immutable |
| `match_outcomes` | canonical result per match (score, derived market outcomes, provenance, confidence) |
| `settlement_ledger` | append-only, hash-chained settlement records (one per match·market·selection) |
| `realized_metrics` | per-signal/per-match finalized CLV/ROI/error + rollup keys |

All append-only, point-in-time, provenance-tagged — same discipline as M2.

---

## 2. Mathematical Model

Notation: signal entry odds `o_entry` (the truth odds at emission, M3/M5);
locked canonical close `o_close = o_close_truth` (S9); model prob `p_model`;
sharp close prob `p_close = 1/o_close`; binary outcome `y ∈ {0,1}` (selection
won).

### 2.1 Realized CLV
```
CLV_realized = o_entry / o_close − 1            (back side; > 0 ⇒ beat the close)
```
Aggregate beat rate: `beat = mean( 1[o_entry > o_close] )`.

### 2.2 Realized ROI (paper, flat unit — no Kelly, no stake sizing)
```
ROI_realized = y · o_entry − 1                  (win: o_entry−1, loss: −1)
portfolio ROI_realized = (Σ_i y_i·o_entry_i − N) / N
```

### 2.3 Prediction-error decomposition
The model-vs-outcome error splits into three orthogonal sources:
```
(p_model − y) = (p_model − p_close)   ←  calibration_error  (model vs sharp truth)
              + (p_close − y)          ←  truth_error        (sharp close vs reality)
execution_error = (o_entry − o_close)/o_close  (price actually obtained vs the close;
                                                paper: realized slippage proxy, ideally 0)
```
Brier-style magnitude split (per bucket):
```
Brier_model  = mean( (p_model − y)^2 )
Brier_close  = mean( (p_close − y)^2 )          (reference: is the model beating the close?)
calibration_component = mean( (p_model − p_close)^2 )
truth_component       = mean( (p_close − y)^2 )
```
A good system shows `Brier_model ≤ Brier_close` (model adds skill over the sharp
close) **and** positive `CLV_realized` (it captured value before the close).

### 2.4 Settlement confidence
A `[0,1]` score on how trustworthy a settlement is:
```
settlement_confidence = w1·outcome_source_agreement     (multi-feed result consensus)
                      + w2·close_provenance_factor       (OBSERVED=1, RECONSTRUCTED<1; from S9)
                      + w3·completeness                  (no void/partial/abandon)
                      + w4·timeliness                    (result within deadline)
   (weights sum to 1; low confidence ⇒ quarantine, exclude from rollups until resolved)
```

---

## 3. Outcome Ingestion Layer (S8)

- **Provider-agnostic feeds:** results from N independent sources (official API,
  data vendors). The layer normalizes each into a canonical result; no source is
  trusted alone (cross-checked in S10).
- **Canonical score normalization:** raw score → canonical `(home_goals,
  away_goals, status, period)` → derived per-market outcomes (1X2 from
  full-time; O/U from total goals; DNB; etc.). One mapping, all markets.
- **OT / void / cancel handling:**
  - markets are settled on the **rules period** they reference (e.g., 1X2 =
    full-time, excluding ET/penalties unless the market says otherwise);
  - **void** (abandoned / not played to settlement rules) ⇒ no settlement, signal
    marked `VOID`, excluded from ROI/CLV;
  - **cancel** ⇒ all markets void; lifecycle → CANCELLED;
  - mis-period (ET counted on a FT market) ⇒ rejected by the normalizer.

---

## 4. Truth Closure Logic (S9)

- **Closing snapshot locking:** at kickoff (M4 LOCKED), the canonical close
  `o_close_truth` is **frozen** — copied into `closing_truth(locked=true)`. After
  lock, no recompute can change it; settlement always reads the locked value.
  Provenance (OBSERVED/RECONSTRUCTED) is carried.
- **Immutable settlement ledger:** append-only, **hash-chained**
  (`entry_hash = H(prev_hash ‖ payload)`) for tamper-evidence; versioned; never
  updated or deleted. One entry per `(match, market, selection)`.
- **Replay-safe closure:** settlement is **idempotent**, keyed by
  `(match, market, selection)` — re-running produces the same ledger entry (same
  hash). Deterministic reconstruction by folding the ledger (mirrors M4). A
  duplicate `SETTLEMENT_COMPLETED` is a no-op (M4 already guarantees this).

---

## 5. Performance Finalization Engine (S11)

- **Per-match final metrics:** for each settled signal — `CLV_realized`,
  `ROI_realized`, `prediction_error` components, win/loss, settlement_confidence.
- **Rolling-window CLV accuracy:** rolling mean `CLV_realized`, `% beat close`,
  rolling `Brier_model − Brier_close` over the last N matches (point-in-time, no
  look-ahead).
- **League / regime breakdown:** stratify all metrics by league and by R1.2
  market regime (efficient/inefficient × stable/volatile) — a model healthy only
  in calm regimes is exposed.
- **Sharp vs soft split:** partition by the truth's sharpness (M16 MSI / close
  provenance): does realized CLV/ROI hold where the close was sharp-anchored vs
  soft? This is the credibility test — value must persist against sharp closes.

---

## 6. Failure Modes

| Failure | Detection | Response |
|---|---|---|
| **Delayed result** | no outcome by deadline | settlement `PENDING`; bounded retry/poll; alert; never finalize on missing data |
| **Incorrect provider result** | cross-source disagreement (S8/S10) | `settlement_confidence ↓`, quarantine, require a second source before finalizing |
| **Partial settlement** | some markets settled, others pending | `PARTIAL` state; finalize per-market; no premature match finalize |
| **Duplicate closure** | ledger key / hash already present | idempotent no-op (S9 + M4) |
| **Mismatch truth vs paper** | locked `o_close` / entry differs from paper-recorded entry | reconciliation flag (ties to M7 SPG); quarantine the signal's metrics |
| **Void/cancel after entry** | status from S8 | mark `VOID`; exclude from ROI/CLV; refund-neutral in paper |

---

## 7. System Integration (S12)

- **M5 (orchestrator):** on `SETTLEMENT_COMPLETED`, M8 grades the orchestrator's
  `PaperSignal`s for that match (looks up entry odds it recorded), writes
  `realized_metrics`. The orchestrator is unchanged — M8 reads its outputs.
- **M7 (shadow):** after a shadow run, M8 **reconciles** simulated signals
  against settled outcomes, closing the loop on shadow predictions and feeding
  realized CLV back into the SystemHealthKernel (turning the simulated CLV proxy
  into measured CLV).
- **M6 (Telegram):** a **final-result message** is appended to the pick — e.g.
  `RESULT: HOME ✓ | CLV +3.1% | ROI +0.95u (paper) | settled OBSERVED conf 0.94`
  — presentation only, no betting.

---

## 8. Readiness Score Impact

M8 supplies the **missing backward half** of the loop. Structurally, after M8 the
system is a complete closed-loop measurement system (forward: M1–M7; backward:
M8). But realized CLV/ROI are only as real as the data feeding them.

```
ProductionReadiness (post-M8, design)  ≈ 30 / 100
  closed-loop completeness  : 1.0  (forward + backward designed/built)
  real outcome feed         : ~0.2 (result providers not wired)
  real sharp close (S9 lock): ~0.2 (gated on Phase-16 real Pinnacle/Betfair)
  finalization machinery     : design-ready
```
Interpretation: M8 raises the *structural* readiness (the loop now closes), but
the binding constraint is unchanged — **real closing-line + real result feeds**.
With M8 built on real data, realized CLV/ROI become trustworthy and readiness
moves toward 70–85; the system is then genuinely "measure, don't guess".

---

## 9. Roadmap (M8.1 → M8.5)

| Step | Scope |
|---|---|
| **M8.1** | Outcome Ingestion: provider-agnostic result adapters + canonical score normalization + OT/void/cancel |
| **M8.2** | Truth Closure: lock `closing_truth` at kickoff; immutable hash-chained `settlement_ledger`; replay-safe idempotent closure |
| **M8.3** | Settlement Math: `CLV_realized`, `ROI_realized`, prediction-error decomposition, `settlement_confidence` |
| **M8.4** | Performance Finalization: per-match metrics, rolling CLV accuracy, league/regime + sharp/soft splits |
| **M8.5** | Integration & Feedback: grade M5 signals, reconcile M7 shadow, M6 final-result message, readiness re-score |

> Each step is additive (no M1–M7 change), prediction-free, and outcome/settlement
> only. M8 is what converts the prediction engine into a closed-loop measurement
> system.
