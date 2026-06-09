# M5 — Pipeline Orchestrator & Paper Activation

> **Status:** Implemented (`src/market/orchestration/orchestrator.py`,
> `tests/test_orchestrator.py`). Routing + execution control only — **no
> prediction logic, no betting, no stake sizing.** Paper mode only. Pure,
> network-free; 12 tests (103 total in suite).

---

## 1. Executive Summary

M5 connects M1–M4 into one routed, state-gated, idempotent flow that turns a
provider trigger into a `PaperSignal` candidate. The orchestrator **routes and
controls execution only**: model probabilities are *injected* via a provider
callback, and there is no bankroll, Kelly, or stake sizing anywhere.

---

## 2. Pipeline Architecture

```
Trigger ─▶ Lifecycle event(s) [M4] ─▶ (state gate) ─▶ Truth ingest+recompute [M2]
        ─▶ Truth Adapter [M3] ─▶ Measurement [R1.2] ─▶ Edge [R1.3]
        ─▶ Truth Adjustment [M3.2] ─▶ PaperSignal  (recorded; no betting)
                                   ▲
                 injected model probabilities (no prediction logic here)
```

---

## 3. Trigger System

| Trigger | Lifecycle event(s) | Pipeline action | Retry |
|---|---|---|---|
| MATCH_CREATED | MATCH_CREATED | register kickoff context | none |
| ODDS_UPDATED | ODDS_UPDATED | ingest → recompute truth → generate (if allowed) | bounded (idempotent) |
| SNAPSHOT_CAPTURED | ODDS_UPDATED | same as ODDS_UPDATED | bounded |
| MATCH_STARTED | MARKET_LOCKED, MATCH_STARTED | lock + close; stop signals | none |
| MATCH_FINISHED | MATCH_FINISHED | record | none |
| SETTLEMENT_COMPLETED | MATCH_FINISHED, RESULT_CONFIRMED, SETTLEMENT_COMPLETED | settle (paper) | none |

Lifecycle event idempotency keys are derived deterministically from the trigger
key (`<trigger_key>:<event_type>`), so re-firing a trigger is a no-op (M4 dedup).

---

## 4. Execution Model

State-gated signal generation (hard gate = M4 `can_generate_signal()`):

| State | Signals |
|---|---|
| PREMATCH | allowed |
| ACTIVE | **limited** — only tier ≥ `active_min_tier` emitted |
| LOCKED / CLOSED / SETTLED / VOID / CANCELLED | blocked |

- **Idempotent / replay-safe / duplicate-safe:** truth ingest + recompute are
  idempotent; each emitted signal raises a `SIGNAL_GENERATED` event keyed
  `signal:<m>:<mkt>:<sel>:<trigger_key>` — a duplicate trigger yields
  `duplicate_signals++` and emits no new `PaperSignal`.
- **Bounded retry:** the pipeline action retries `max_retries` times on transient
  failure (safe because idempotent).

---

## 5. Paper Trading Design

`PaperSignal` fields (exactly — enforced by test): `match_id, market, selection,
edge_score, tier, confidence, truth_confidence, timestamp`. **No bankroll, no
Kelly, no stake sizing, no real betting.** Signals are recorded in
`orchestrator.paper_signals`; that is the entire paper layer in M5.

---

## 6. Monitoring

`OrchestratorMetrics`: `signals_generated`, `signals_blocked`,
`duplicate_signals`, `pipeline_failures`, `execution_latency_last/total`,
`triggers_handled`. M4 aggregate snapshots add per-match
state/transition/invalid/duplicate counters.

---

## 7. Failure Modes

| Failure | Handling |
|---|---|
| Provider outage / no snapshots | truth empty → no signals (graceful, not a failure) |
| Partial truth data (missing selection) | de-vig skips that book; fewer/no signals; no crash |
| Duplicate triggers | deduped at lifecycle + signal level (`duplicate_signals`) |
| Delayed/out-of-order updates | state gate blocks late odds (M4 invalid + `signals_blocked`) |
| Missing kickoff context | generation returns `[]` gracefully |
| Pipeline exception | bounded retry, then `pipeline_failures++`, `failed=True`, no crash |

---

## 8. Validation Plan (implemented)

| Property | Tests |
|---|---|
| trigger routing | created→PREMATCH, odds→ACTIVE, started→LOCKED→CLOSED |
| state gating | signals in ACTIVE; blocked after kickoff |
| paper signal generation | exact fields (no stake/bankroll), populated, non-reject |
| replay determinism | identical signals + metrics across two runs |
| duplicate safety | re-sent trigger → no new signal, `duplicate_signals++` |
| failure handling | no-context graceful, partial-data graceful |
| monitoring | all metric fields tracked |

---

## 9. Implementation Roadmap

| Step | Scope | Status |
|---|---|---|
| M5.0 | Orchestrator: trigger→lifecycle→state-gated pipeline→PaperSignal | ✅ done |
| M5.1 | Paper activation (PaperSignal record) + metrics | ✅ done |
| M5.2 | Validation suite (gating/routing/replay/duplicate/paper) | ✅ done |
| M5.3 | Snapshot scheduler → triggers (T-72h…CLOSE wall-clock) | next |
| M5.4 | Paper settlement grading (CLV_truth/outcome) at SETTLEMENT | next |
| M6 | Telegram output of PaperSignal + truth/edge explainability | next |

> No betting logic, no stake sizing. Model probabilities are injected; the
> orchestrator only routes and gates. Paper mode only.
