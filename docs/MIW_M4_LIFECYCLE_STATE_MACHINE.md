# M4 — Market Lifecycle State Machine

> **Status:** Implemented (`src/market/orchestration/`,
> `tests/test_lifecycle.py`). Orchestration only — **no prediction logic.**
> Deterministic, event-sourced, pure-stdlib (sqlite3), network-free. 21 tests
> (91 total in suite).

---

## 1. Executive Summary

M4 is the orchestration backbone: a deterministic, event-sourced state machine
that walks each match through its lifecycle so the pipeline (truth → measurement
→ edge) runs at the right time, idempotently, and recoverably. It adds **no
prediction logic** — it only sequences and gates. Identical event sequences
always reconstruct identical state + counters (replay == recovery).

---

## 2. State Diagram

```
            ODDS_UPDATED        MARKET_LOCKED      MATCH_STARTED      SETTLEMENT_COMPLETED
  ∅ ─MATCH_CREATED▶ PREMATCH ─────────▶ ACTIVE ─────────▶ LOCKED ─────────▶ CLOSED ─────────▶ SETTLED
                       │ │                  │                │   (guards: MATCH_FINISHED →
                       │ └─MARKET_LOCKED────┘                │    RESULT_CONFIRMED → SETTLEMENT)
                       │                                     │
                 MATCH_CANCELLED  ◀───{PREMATCH,ACTIVE,LOCKED}
                 MATCH_VOIDED     ◀───{PREMATCH,ACTIVE,LOCKED,CLOSED}
  Terminal: SETTLED, VOID, CANCELLED
```

| State | Purpose | Entry | Exit | Allowed actions | Forbidden |
|---|---|---|---|---|---|
| PREMATCH | created, pre-trading | MATCH_CREATED | first ODDS_UPDATED / lock / cancel | ingest_odds, generate_signal | settle |
| ACTIVE | trading: odds + signals | first ODDS_UPDATED | MARKET_LOCKED / void / cancel | ingest_odds, generate_signal | settle |
| LOCKED | market locked, close captured | MARKET_LOCKED | MATCH_STARTED / void / cancel | capture_close | signals, odds |
| CLOSED | in-play / awaiting settlement | MATCH_STARTED | SETTLEMENT_COMPLETED / void | record_result | signals, odds |
| SETTLED | result applied (terminal) | SETTLEMENT_COMPLETED | — | — | all |
| VOID | match voided (terminal) | MATCH_VOIDED | — | — | all |
| CANCELLED | called off (terminal) | MATCH_CANCELLED | — | — | all |

---

## 3. Transition Rules

State-changing transitions (everything else from a state is rejected as
INVALID):
```
(∅, MATCH_CREATED)               -> PREMATCH
(PREMATCH, ODDS_UPDATED)         -> ACTIVE          # first odds activate the market
(PREMATCH|ACTIVE, MARKET_LOCKED) -> LOCKED
(LOCKED, MATCH_STARTED)          -> CLOSED
(CLOSED, SETTLEMENT_COMPLETED)   -> SETTLED          # guarded
(PREMATCH|ACTIVE|LOCKED|CLOSED, MATCH_VOIDED)     -> VOID
(PREMATCH|ACTIVE|LOCKED, MATCH_CANCELLED)         -> CANCELLED
```
Data events (recorded, no state change): `ODDS_UPDATED`/`SIGNAL_GENERATED` in
ACTIVE, `SIGNAL_GENERATED` in PREMATCH, `MATCH_FINISHED`/`RESULT_CONFIRMED` in
CLOSED.

**Guards (ordering inside CLOSED):** `MATCH_FINISHED` requires `started`;
`RESULT_CONFIRMED` requires `finished`; `SETTLEMENT_COMPLETED` requires
`result_confirmed`. A settlement attempt before its prerequisites is INVALID.

Invalid examples (rejected, counted): SETTLEMENT from PREMATCH, SIGNAL in
LOCKED, ODDS in CLOSED, MATCH_STARTED from ACTIVE.

---

## 4. Event Model

`Event(match_id, type, idempotency_key, occurred_at, payload, seq, recorded_at)`.
Types: `MATCH_CREATED, ODDS_UPDATED, SIGNAL_GENERATED, MATCH_STARTED,
MARKET_LOCKED, MATCH_FINISHED, RESULT_CONFIRMED, SETTLEMENT_COMPLETED` (+
`MATCH_VOIDED, MATCH_CANCELLED`). `seq`/`recorded_at` are assigned by the store
on append.

**Event sourcing:** append-only `lifecycle_events` table (immutable audit). The
store records every event (including duplicates); idempotency/dedup is applied
deterministically by the aggregate on fold/replay, so reconstruction is exact.
`MatchLifecycle.replay(events)` folds events through the transition function →
deterministic state + counters.

---

## 5. Idempotency Strategy

- **Duplicate key:** an event whose `idempotency_key` was already seen → no-op,
  `duplicate_event_count++` (DUPLICATE). Safe under retries/replays.
- **Duplicate settlement:** re-delivering `SETTLEMENT_COMPLETED` in SETTLED (new
  key) → IDEMPOTENT no-op (checked before guards); same key → DUPLICATE. Never
  double-settles.
- **Duplicate signal generation:** same signal key → DUPLICATE; signals are also
  forbidden outside PREMATCH/ACTIVE.
- **Idempotent milestones:** re-sending `MARKET_LOCKED` in LOCKED, `MATCH_STARTED`
  in CLOSED, etc. → IDEMPOTENT (no extra transition).

Apply order: dedup → idempotent-self → guard → transition → data-event → invalid.

---

## 6. Recovery Strategy

- **Process restart:** a fresh `LifecycleService` on the same `EventStore`
  rebuilds every aggregate by replaying its events (`test_crash_recovery_*`).
- **Duplicate events:** deduped by key on fold/replay (deterministic counters).
- **Out-of-order / delayed updates:** events fold in store (append) order; state
  guards reject events illegal for the current state, so a late `ODDS_UPDATED`
  after LOCKED is counted INVALID and cannot mutate state
  (`test_out_of_order_late_odds_rejected`).

---

## 7. Validation Plan (implemented)

| Property | Tests |
|---|---|
| legal transitions | happy_path → SETTLED, state progression, void/cancel |
| illegal transitions | settlement-from-prematch, signal-in-locked, odds-in-closed, started-from-active |
| guards | settlement-needs-result, result-needs-finished |
| idempotency | duplicate key/settlement/signal, idempotent relock |
| replay determinism | replay twice equal; service == pure replay |
| crash recovery | rebuild from SQLite store identical |
| observability | counters (transition/invalid/duplicate), state_age |

---

## 8. Implementation Roadmap

| Step | Scope | Status |
|---|---|---|
| M4.0 | State machine + event model + guards | ✅ done |
| M4.1 | Append-only `EventStore` + `LifecycleService` (recovery) | ✅ done |
| M4.2 | Validation suite (transitions/idempotency/replay/recovery) | ✅ done |
| M4.3 | Wire snapshot triggers → events (T-72h…CLOSE) at the orchestrator (M5) | next |
| M4.4 | Drive truth/measurement/edge per state (gated by allowed_actions) | next (M5) |

> No prediction logic added. M4 only sequences and gates; the pipeline modules
> run inside the actions the state permits.
