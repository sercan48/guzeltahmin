# M10 — Real Data Activation & Live System Bootstrap Layer

> **Status:** Design — pre-implementation. **No code.** Architecture + runtime +
> ingestion + ops only. **Additive over M1–M9 — touches nothing.** No
> prediction, no ML/model changes.
> **Activates:** R1.1 PAL → M1 canonicalization → M2 Truth Store → M5
> orchestrator triggers → M8 settlement, all under M7/M9 governance. This is the
> ops/activation realization of the Phase-16 Truth-Warehouse bootstrap, extended
> to outcomes, scheduling, failover, and live ops.

---

## 0. Purpose

Every layer is built or designed, but it has only ever seen *simulated* data
(R1.1 ran on a recorded fixture; M7 runs on synthetic timelines). M10 is the
**activation keystone**: it wires real, licensed odds + outcome feeds into the
existing pipeline through a reliable, scheduled, failover-protected, audited
ingestion layer — turning the simulation into a real-data production engine
without changing a single upstream module.

```
REAL FEEDS ─▶ M10 Ingestion+Scheduler+Validation ─▶ M2 Truth Store ─▶ (M3→R1.2→R1.3→M5→M6)
   (odds + outcomes, licensed)         │                                │
                                       └── M5 triggers, M8 outcomes ─────┘   governed by M7/M9
```

**Compliance:** all sources are official/licensed and used under their Terms of
Service (Pinnacle/Betfair APIs, licensed result vendors). M10 designs *authorized*
collection only — no scraping-evasion, no ToS circumvention.

---

## 1. Real Data Ingestion

- **Odds feeds (pre-match + closing):** scheduled snapshots at
  `T-72h, 48h, 24h, 12h, 6h, 3h, 1h, CLOSE` (kickoff-relative), per the Phase-16
  schedule. Each provider's quote is fetched, normalized to decimal (M1), and
  written to M2's raw ledger; CLOSE gets a reserved, tightened cadence.
- **Outcome / settlement feeds:** post-match result from ≥2 independent licensed
  vendors → M8 S8 canonical score → market outcomes.
- **Dedup / idempotency:** every snapshot keyed
  `(match, provider, market, selection, scheduled_tick)`; the scheduler emits M5
  triggers with stable keys `match:tickN` (and `match:created/started`), so M2
  ingest and M5 lifecycle are both idempotent — re-fetch/replay never
  double-counts (inherits M2/M4 dedup).
- **Retry / backoff:** bounded exponential backoff with jitter,
  `delay_n = min(base · 2^n + U(0,j), cap)`, capped attempts; transient errors
  retried, poison requests dead-lettered. CLOSE-window jobs get priority +
  reserved quota so a retry storm earlier never starves the close.

---

## 2. Data Reliability

```
Latency budget (per tick): fetch + parse + canonicalize + store ≤ B(tick)
   B is generous pre-match, TIGHT at CLOSE (the close must land before kickoff).
Completeness score (per match):
   C = w_t·(captured_ticks/expected_ticks)
     + w_p·(providers_present/providers_expected)
     + w_m·(markets_present/markets_expected)      ∈ [0,1]   (Σw = 1)
   sharp-anchor weighting: a tick missing Pinnacle/Betfair penalizes more.
```
| Concern | Strategy |
|---|---|
| API failure modes | timeout, 5xx, quota-exceeded, schema drift → classify, retry/failover/quarantine accordingly |
| Partial / missing data | de-vig only on full markets (M2); missing tick lowers `C`; reconstruction tagged (Phase-16 S5, no hallucination) |
| Rate limits | token-bucket per provider; daily quota budgeted so CLOSE is always affordable |
| Completeness gating | `C < C_min` ⇒ match flagged low-completeness; M9 may suppress its signals |

---

## 3. Scheduler System

- **Event-driven + cron hybrid:** cron-like timers fire kickoff-relative snapshot
  ticks; event-driven hooks (fixture-calendar updates, provider push, steam
  alerts) inject extra captures. The scheduler computes each match's tick
  timetable from its kickoff.
- **Deterministic execution clock:** a logical clock records the *intended*
  scheduled time of every job alongside wall-clock; replays use the recorded
  schedule so a re-run reproduces the same trigger sequence (replay-safe).
- **M5 integration:** each fired tick becomes an M5 `Trigger`
  (`MATCH_CREATED` / `ODDS_UPDATED` / `MATCH_STARTED` / … ) handed to
  `orchestrator.handle_trigger`. The scheduler is the *actuator* that feeds the
  already-built orchestrator; it adds no pipeline logic.
- **Replay-safe execution:** idempotent trigger keys + the deterministic clock ⇒
  the same fixture replayed yields the same lifecycle, truth, and signals.

---

## 4. Validation Pipeline

| Check | Logic |
|---|---|
| **Truth vs observed reconciliation** | compare M2 canonical truth to raw per-book observed (M3 HYBRID divergence); flag if `max_abs_gap > τ` |
| **Closing integrity** | the locked close (M8 S9) must equal the last pre-KO canonical snapshot; no post-lock mutation; provenance OBSERVED vs RECONSTRUCTED recorded |
| **Anomaly detection** | impossible jumps / stale / duplicate (R1.2 integrity); missing snapshots via completeness `C`; spike = `|Δp| > J(Δt)` |
| **Provider disagreement** | cross-book dispersion `σ_truth` (M2); large disagreement ⇒ down-weight outlier (trust `w_i`), flag, possibly quarantine the tick |

Validation runs **before** a tick becomes part of canonical truth; failures are
quarantined in the raw ledger with a reason code, never propagated (Phase-16 S2).

---

## 5. Failover Design

- **Multi-provider routing:** primary → secondary → fallback chain (R1.1
  `ProviderRegistry.get_active()` + health checks). A provider swap is config-only;
  downstream sees only canonical truth, never which provider served it.
- **Degraded mode:** on partial outage, drop the failed provider, widen
  `σ_truth`/lower confidence, raise suppression floors (M9 C9), keep serving from
  the surviving sharp anchor.
- **No-data → no-signal (hard rule):** if no fresh OBSERVED truth exists for a
  selection, the M9 gate returns SUPPRESS unconditionally — the system never acts
  on stale or absent data.

---

## 6. Ops Control Loop

```
ingest → validate → truth → (signals) → settle
   │                                        │
   └──── metrics (completeness, latency, freshness, σ_truth) ──▶ M7 health + M9 gates
                                                                    │
                              auto-pause (→ SHADOW/LOCKED) if: HS_v2 < floor,
                              truth_lag > τ, completeness < C_min, kill factor;
                              auto-resume when truth fresh + healthy over a window.
```
M10 emits the data-health telemetry that M7's SystemHealthKernel and M9's
kill-switch/ladder consume; M9 decides pause/resume/throttle, M10 obeys
(stops/loosens ingestion-driven publication accordingly).

---

## 7. Deployment Flow

`shadow → paper → micro → live`, governed by M9's ladders. M10's role is to
supply the **real-data evidence** each numeric gate needs:
| Stage | M10 entry evidence | Exit / rollback |
|---|---|---|
| SHADOW | real feeds flowing, completeness `C ≥ 0.8`, latency within budget | feed outage / `C < 0.6` |
| PAPER | + truth vs observed reconciled (gap ≤ τ), N≥100 settled with real outcomes | reconciliation drift, settlement stalls |
| MICRO | + M9 gate (CLV≥0, ROI≥0, HS_v2≥75, SC≥0.8) on real data | DD breach, CLV<0 rolling |
| LIVE | + M9 FULL-LIVE gate sustained on real data | any kill factor (M9) |

Rollback is automatic and asymmetric (M9 C8): one breach demotes a rung.

---

## 8. Safety

- **API key isolation:** keys only from environment / secret manager, never in
  the repo or logs (R1.1 discipline); per-provider scoping; rotation supported.
- **Append-only audit:** every fetch, validation verdict, trigger, and control
  decision is logged immutably (M4/M8/M9 event-sourcing); replayable.
- **Corruption prevention:** schema validation on ingest; immutable raw ledger +
  hash-chained settlement ledger (M8 S9); idempotent writes; quarantine over
  overwrite; the canonical Truth Store is the only downstream read (M3 rule).

---

## 9. Output

```
LIVE Readiness (post-M10, design)  ≈ 40 / 100
  pipeline (forward) : built (M1–M7)          control (M9) : designed
  settlement (M8)    : designed                activation (M10) : designed
  real odds feed     : ~0.25  (licensed + scheduled — not yet wired)
  real outcome feed  : ~0.20  (vendors not wired)
  coded M8/M9/M10    : 0      (design only)
```

### Blocking gaps to go LIVE
1. **License + wire real Pinnacle/Betfair odds feeds** (pre-match + CLOSE) into M2.
2. **License + wire ≥2 outcome/result vendors** into M8 S8.
3. **Implement M8** (settlement, locked close, realized CLV/ROI, ledger).
4. **Implement M9** (SYSTEM_STATE, kill-switch, gates, ladders) as live control.
5. **Implement M10** scheduler + ingestion + validation + failover as a service.
6. **Accumulate real settled history** (N≥400 across stages) for the M9 ladder gates.
7. **Secret management + audit infra** deployed; governance sign-off.

Until 1–2 (real feeds) exist, everything else is unverifiable on reality — they
are the true critical path. With them plus coded M8–M10, the readiness formula
moves toward 75–90 and the M9 ladder can legitimately promote toward LIVE.

---

### Summary
M10 is the additive activation layer that connects the simulation to the real
world: licensed, scheduled, deduped, retry/backoff-protected odds + outcome
ingestion; a completeness/latency reliability model; an event+cron scheduler with
a deterministic clock feeding M5 triggers replay-safely; a validation pipeline
(truth↔observed, closing integrity, anomaly, provider disagreement); multi-
provider failover with "no data = no trade"; an ops control loop wired to M7/M9
for auto pause/resume; and key-isolation + append-only audit + corruption
prevention. It changes nothing upstream. LIVE readiness ≈ 40/100 — the activation
*plan* is complete; the blocking gaps are the real feeds and the coded M8–M10
services, which are now the entire critical path from simulation to a real-data
production engine.
