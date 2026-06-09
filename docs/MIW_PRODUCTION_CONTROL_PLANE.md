# Phase 17 — Live Orchestration & Production Control Plane

> **Status:** Design — pre-implementation. **No ML redesign · no model retraining
> · no new feature engineering.** Pure production-orchestration layer. Consistent
> with Faz 1–16.
> **Operates (does not modify):** R1.2 measurement · R1.3 edge · F13 execution ·
> F14 shadow · F15 calibration · F16 truth warehouse · R1.1 PAL · existing bot
> code in `app/` (`telegram_bot.py`, `bot/predictions.py`, `bot/formatters.py`,
> `bot/admin.py`, `bot/subscribers.py`) and `telegram_vip_automation/`.

---

## 1. Executive Summary

Every analytical layer exists, but nothing *runs the system continuously and
safely*. Phase 17 is the **control plane**: an event-driven orchestrator that
walks each match through its lifecycle, fires snapshot collection (T-72h →
close), runs the existing pipeline (truth → measurement → edge → calibration →
execution) to produce signals, ships them to Telegram/API with an explainability
payload, and wraps the whole thing in a **risk-control layer** (kill switch,
drawdown throttling, exposure caps, liquidity suppression) plus a **staged
deployment ladder** (paper → micro → live) with automatic rollback.

It is an **operator, not a brain**: it schedules, gates, monitors, and degrades
gracefully — it never changes a model, a feature, or a calibration. Its single
headline output is the `LIVE_SYSTEM_HEALTH_SCORE`, which composes the honest
sub-scores of F13–F16 with live operational health (drift, latency, fill) and
arms the kill switch when the system is unfit to trade.

---

## 2. Architecture (Control Plane)

```
                              PHASE 17 — PRODUCTION CONTROL PLANE
  ┌──────────────────────────────────────────────────────────────────────────────────────────┐
  │  O1 Real-Time Orchestration Engine (event-driven, match-lifecycle)                          │
  │      fixture calendar → snapshot triggers (T-72h/48/24/6/1h/CLOSE) → job queue              │
  │            │                                                                                │
  │            ▼                                                                                │
  │  O8 Market Lifecycle State Machine   PREMATCH → ACTIVE → LOCKED → CLOSED → SETTLED          │
  │            │                                                                                │
  │            ▼                                                                                │
  │  PIPELINE INVOCATION (read-only over analytics):                                            │
  │     F16 Truth Store ─▶ R1.2 measurement ─▶ R1.3 edge ─▶ F15 calibrated ─▶ F13 executable    │
  │            │                                                                                │
  │   ┌────────┼───────────────┬───────────────────┬───────────────────┬────────────────┐      │
  │   ▼        ▼               ▼                   ▼                   ▼                ▼      │
  │  O2 Signal  O3 Risk        O4 Deployment      O5 Observability    O6 Scheduling    O9 Safety │
  │  Production  Control Plane   Ladder             & Monitoring        & Reliability     Layer   │
  │  (picks)     (kill/throttle) (paper→micro→live) (logs/metrics/anom) (retry/lock/idem)        │
  │   │                                                                                          │
  │   ▼                                                                                          │
  │  O7 Telegram / External Output Engine (structured pick + explainability)                     │
  │                                                                                              │
  │  O10 LIVE_SYSTEM_HEALTH_SCORE  ◀── composes F16/F15/F14/F13 + drift/latency/fill             │
  └──────────────────────────────────────────────────────────────────────────────────────────┘
```

**Principles.** (1) *Read-only over analytics* — the control plane invokes
existing engines, never edits them. (2) *Event-sourced* — every state change is
an immutable event (replayable, auditable). (3) *Fail-safe default* — on
uncertainty the system suppresses signals and degrades, it does not emit. (4)
*Single source of truth* — signals are computed only from the F16 Truth Store.

| Component | Role |
|---|---|
| O1 Orchestration | Event-driven scheduling of the match lifecycle + snapshot triggers |
| O2 Signal production | Continuous pick generation, batch vs stream, output routing |
| O3 Risk control plane | Kill switch, drawdown throttle, exposure caps, liquidity suppression |
| O4 Deployment ladder | Paper → micro → live staging with gates + rollback |
| O5 Observability | Event logs, metrics (CLV/ROI/drift/fill), anomaly + silent-failure detection |
| O6 Scheduling/reliability | Retry, idempotency, distributed locks, crash recovery |
| O7 Output engine | Telegram/API structured picks + explainability/transparency |
| O8 State machine | Per-match PREMATCH→ACTIVE→LOCKED→CLOSED→SETTLED |
| O9 Safety layer | No-bet zones, low-confidence suppression, outage degradation, truth-lag |
| O10 Health score | `LIVE_SYSTEM_HEALTH_SCORE` |

---

## 3. Signal Generation System (O2)

```
trigger (snapshot tick or state transition)
  → read F16 Truth Store (point-in-time)  → R1.2 signals → R1.3 edge/EQS/tier
  → F15 P_calibrated  → F13 executable_edge + ROI_true + P(fill)
  → O3 risk gates + O9 safety gates
  → if PASS: emit pick;  else: suppress (logged with reason)
```
- **Batch vs streaming.** Pre-match horizons (T-72h…T-6h) run as **batched** jobs
  on the snapshot schedule (efficient, quota-friendly). The final window
  (T-1h→CLOSE) and any steam/RLM events run as **streaming** reactions to ticks
  (latency-sensitive). The orchestrator chooses mode by `time_to_kickoff` and
  market volatility (R1.2 regime).
- **Continuous production.** Picks are (re)evaluated on each trigger; a pick is
  emitted once when it first clears the gates, and **amended/withdrawn** if a
  later snapshot flips it (tier downgrade, conflict, fill failure). Idempotency
  (O6) ensures a re-run never double-posts.
- **Output routing.** Free channel vs VIP channel vs API consumers — mapped from
  tier (S/A → VIP, B → free teaser, C/Reject → suppressed) via the existing
  `app/bot/subscribers.py` + `telegram_vip_automation` segmentation.
- **Explainability payload** generated per pick (see §8).

---

## 4. Risk Engine — Live Safety Layer (O3 + O9)

| Control | Trigger | Action |
|---|---|---|
| **Global kill switch** | `LIVE_SYSTEM_HEALTH_SCORE < H_min`, manual admin, or critical anomaly | halt all emission; flatten to paper; alert admins |
| **Drawdown throttle** | rolling realized drawdown > `DD_k` tiers | scale exposure `×(1−f(DD))`; raise tier threshold; at extreme → kill |
| **Regime exposure caps** | R1.2 regime = volatile/inefficient | cap aggregate + per-market exposure; widen confidence floor |
| **Liquidity suppression** | F16 `MSI`/depth low or `P(fill) < p_min` | suppress the signal (unrealizable) — don't post unfillable edges |
| **No-bet zones (O9)** | derby/abnormal markets, pre-team-news windows, thin leagues | hard exclude regardless of edge |
| **Low-confidence suppression** | `edge_confidence_score < c_min` or `confidence(truth) < t_min` | suppress; never emit a low-trust pick |
| **API outage degradation** | provider/anchor down (R1.1 health) | degrade gracefully: serve last-good with staleness banner, or go silent — never emit on stale truth |
| **Truth-lag detection** | F16 `as_of` lag > `τ_lag` behind wall-clock | freeze emission until truth catches up |

Risk gates are **AND-composed with the signal gates** and evaluated *last*; a
single trip suppresses or kills. All suppressions are logged with a reason code
(O5) — silence is always explained.

---

## 5. Deployment Ladder (Paper → Micro → Live) (O4)

```
PAPER ──gate──▶ MICRO ──gate──▶ LIVE        (capital scales only as evidence accrues)
```
| Stage | Capital | Promotion gate (all must hold) | Rollback |
|---|---|---|---|
| **Paper** | 0 (shadow) | F11 paper ROI/CLV positive over N; F14 `LIVE_READY` true (CR/SPG/PTI/LSDI within bounds); F12 sign-off | n/a |
| **Micro** | tiny, capped | paper gate + live micro CLV ≥ 0 over N_micro; fill rate ≥ target; no kill events | auto-revert to Paper on gate breach |
| **Live** | scaled by F8 portfolio | micro gate sustained + `LIVE_SYSTEM_HEALTH_SCORE ≥ H_live` + drawdown within band | auto-revert to Micro/Paper on breach |

**Automatic rollback rules:** any of {health score below stage floor, drawdown
breach, CLV turning negative over the rolling window, fill rate collapse, F16
truth-integrity drop} demotes the system one rung **automatically** and alerts.
Promotion is slow and evidence-gated; demotion is fast and automatic
(asymmetric by design).

---

## 6. Observability & Monitoring (O5)

- **Event-sourced logs:** every trigger, state transition, pick, suppression,
  gate decision, and order attempt is an immutable, replayable event (enables
  deterministic post-mortem and F14-style replay).
- **Metrics (live dashboards + alerts):** CLV (vs F16 close), ROI_true, edge/tier
  distribution, calibration drift (F15), fill rate & slippage (F13), truth lag,
  provider health/latency, queue depth, job success rate.
- **Anomaly detection:** statistical bounds on each metric (e.g., fill rate or
  CLV outside rolling band → alert); regime-aware thresholds.
- **Silent-failure detection (critical):** heartbeats per job; *expected-output*
  monitors (e.g., "fixtures today but zero picks emitted", "snapshots due but
  none captured", "truth store not advancing") — the system must detect *doing
  nothing when it should be doing something*, not only loud errors.

---

## 7. Scheduling & Reliability (O6)

| Concern | Design |
|---|---|
| **Retry policy** | bounded exponential backoff per job class; dead-letter queue for poison jobs; CLOSE-window jobs get priority + reserved quota |
| **Idempotent execution** | every job keyed by `(match_id, snapshot_type, pipeline_version)`; re-runs upsert, never duplicate-post (safe under retries/crashes) |
| **Distributed locks** | per-match / per-snapshot lock so only one worker processes a unit; lease-based with expiry to survive worker death |
| **Crash recovery** | event-sourced state ⇒ on restart, rebuild the lifecycle from the event log and resume pending jobs; no lost or double work |
| **Backpressure** | queue depth caps; shed low-priority pre-match work to protect the latency-critical close window |

Reliability target: a worker crash or provider hiccup never produces a wrong
bet, a double post, or a missed close — at worst it degrades to a logged
suppression.

---

## 8. Telegram / External Output Design (O7)

Structured pick (rendered via `app/bot/formatters.py`), with a transparency
layer explaining *why the pick exists*:
```
┌────────────────────────────────────────────┐
│  ⚽ {Home} vs {Away}  —  {League}            │
│  PICK: {selection} @ {o_truth}   TIER: {S/A/B} │
│  ──────────────────────────────────────────  │
│  Confidence: {edge_confidence_score}  EQS {0–100}│
│  Why this pick (edge decomposition):         │
│    model {p_calibrated} vs market {p_truth}  │
│    raw edge {…} → sharp-adjusted {…}          │
│    market: line {SHORTENING/…}, sharp {±}     │
│    CLV(sharp): {CLV_truth}                    │
│  Provenance: truth {OBSERVED/RECON} conf {…}  │
│  Stage: {PAPER/MICRO/LIVE}                    │
└────────────────────────────────────────────┘
```
- **Confidence explanation:** the R1.3 `edge_confidence_score` components and the
  tier reason string, in plain language.
- **Edge decomposition:** the R1.3 cascade (raw → calibrated → market → drift →
  sharp) so the reader sees *how much* survived and *why*.
- **Transparency layer:** provenance + confidence from F16, stage from O4, and a
  one-line rationale. VIP gets full detail; free channel gets a teaser
  (`telegram_vip_automation` routing). No pick is posted without its explanation.

---

## 9. Market Lifecycle State Machine (O8)

```
            T-72h..T-1h           ~T-15m            kickoff            full-time        results in
  PREMATCH ───────────▶ ACTIVE ──────────▶ LOCKED ──────────▶ CLOSED ──────────▶ SETTLED
     │  snapshot triggers   │ streaming/      │ capture CLOSE   │ no new signals   │ grade CLV/ROI,
     │  + batched picks     │ final picks     │ (truth close),  │ in-play optional │ update P&L,
     │                      │                 │ stop pre-match  │                  │ feed F11/F15
     └─ guard: PREMATCH only emits if state machine + risk gates pass
```
| State | Meaning | Allowed actions |
|---|---|---|
| PREMATCH | listed, > ~15m to KO | batched snapshots + pick (re)evaluation |
| ACTIVE | final pre-match window | streaming snapshots, final picks/amendments |
| LOCKED | ~kickoff | capture CLOSE truth; freeze pre-match emission |
| CLOSED | in-play | no pre-match signals (optional live module out of scope) |
| SETTLED | result known | grade `CLV_truth`/ROI_true, update P&L, feed F11/F15 |

Transitions are event-driven and idempotent; each is an immutable event.
Settlement closes the loop by feeding realized outcomes back to paper trading
(F11) and calibration stability (F15) — **without retraining the model.**

---

## 10. System Health Score (O10)

```
LIVE_SYSTEM_HEALTH_SCORE = w1·DataIntegrity(F16)
                         + w2·CalibrationStability(F15)
                         + w3·LiveReadiness(F14)
                         + w4·ROIRealism(F13)
                         + w5·OperationalHealth
  OperationalHealth = mean( drift_stability, latency_health, fill_stability, truth_freshness )
  weights: w1=0.25, w2=0.20, w3=0.15, w4=0.15, w5=0.25     (all sub-scores 0–100)
```
Acts as the **master interlock**: it gates the deployment ladder (§5) and arms
the global kill switch (§4). Because it *includes* the upstream honest scores, it
cannot read high while the data/calibration layers are empty — by construction
the system refuses to "go live" on ungrounded inputs.

---

## 11. Failure Modes & Recovery

| Failure | Detection | Recovery |
|---|---|---|
| Worker crash mid-job | lock lease expiry, heartbeat miss | another worker resumes from event log (idempotent) |
| Provider/anchor outage | R1.1 health, truth-lag | graceful degradation → suppress/stale-banner; restore on recovery |
| Truth store stalls | `as_of` not advancing | freeze emission; alert; backfill (F16 cold path) |
| Silent no-output | expected-output monitor | alert + auto-investigate; never assume "all good" on silence |
| Duplicate/late post | idempotency key check | dedup; amend not repost |
| Drawdown spike | rolling DD monitor | throttle → demote stage → kill if extreme |
| Calibration drift (F15) | stability monitor | raise thresholds; demote; alert; (no retrain here) |
| Bad pick after settlement | `CLV_truth` grading | post-mortem via event replay; feed F11/F15 |
| Config/version skew | pipeline_version in keys | reject mismatched workers; rolling, versioned deploys |

Recovery doctrine: **demote fast, promote slow; suppress on doubt; explain every
silence; replay everything.**

---

## 12. Final Production Readiness Score (0–100)

```
ProductionReadiness = 100 · ( w1·P_controlplane + w2·P_health + w3·P_safety + w4·P_deploy + w5·P_obs )
  P_controlplane = orchestrator/state-machine/scheduler built & running
  P_health       = LIVE_SYSTEM_HEALTH_SCORE sustained above live floor
  P_safety       = risk/safety layer wired (kill switch, throttle, suppression) & tested
  P_deploy       = ladder + rollback operational with real gate evidence
  P_obs          = observability/anomaly/silent-failure monitoring live
  weights: w1=0.25, w2=0.25, w3=0.20, w4=0.15, w5=0.15
```

**Current honest estimate ≈ 20 / 100.** Justification:
- `P_controlplane` (~0.3): an event-driven scheduler exists in spirit (the
  current bot posts on cron/watchdog), but no match-lifecycle state machine or
  distributed-reliability layer.
- `P_health` (~0.15): bounded above by the upstream chain —
  `LIVE_SYSTEM_HEALTH_SCORE` is low because F16≈25/F15≈22/F13≈38/F14≈30, all
  gated on real sharp data.
- `P_safety` (~0.2): kill-switch/throttle concepts defined; not wired/tested.
- `P_deploy` (~0.2): paper exists (F11); micro/live ladder + auto-rollback not
  built.
- `P_obs` (~0.2): basic logging exists; event-sourcing + silent-failure
  detection not.

**The binding constraint is unchanged:** production readiness is *capped by* the
health score, which is *capped by* real sharp data (Phase-16 bootstrap). Build
order to lift it: **Phase-16 bootstrap (real closing odds) → F15/F13 fit on real
data → F14 `LIVE_READY` → build this control plane (O1–O9) → run the paper→micro
ladder.** With those, production readiness rises to 75–90 and the staged live
launch unlocks. This control plane is the *operator* that makes the graded,
de-risked launch possible — but it deliberately cannot and will not go live until
the grounded scores permit.

---

### Summary
Phase 17 is the production control plane that operates the finished MIW stack
continuously and safely: an event-driven, match-lifecycle orchestrator
(PREMATCH→…→SETTLED) firing T-72h→CLOSE snapshots; continuous signal generation
(batch pre-match, streaming near close) routed to Telegram/API with full
edge-decomposition explainability; a live risk engine (kill switch, drawdown
throttle, regime caps, liquidity/low-confidence/truth-lag suppression); a
paper→micro→live deployment ladder with evidence-gated promotion and automatic
rollback; event-sourced observability with silent-failure detection; an
idempotent, lock-based, crash-recoverable scheduler; and a master
`LIVE_SYSTEM_HEALTH_SCORE` that interlocks the whole thing. It changes no model,
feature, or calibration — it schedules, gates, monitors, and degrades. Current
production readiness ≈ **20/100**, capped by the same root dependency (real sharp
data, Phase-16 bootstrap); once grounded it rises to 75–90 and enables the staged
live launch.
