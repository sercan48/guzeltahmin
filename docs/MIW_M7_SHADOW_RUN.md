# M7 — Shadow Run & Continuous Simulation Layer

> **Status:** Implemented (`src/market/shadow/`, `tests/test_shadow.py`).
> Simulation + orchestration only — **no prediction logic, no ML training, no
> paper/live execution; M1–M6 unchanged.** Pure-stdlib, network-free,
> deterministic; 19 tests (136 total in suite).

---

## 1. Executive Summary

M7 gives the system **production-runtime behaviour without going live**: a
24/7-style shadow runner replays simulated `T-72h → CLOSE` match timelines
through the existing M5 orchestrator, overlays stochastic noise + drift
injection, and continuously monitors runtime health — silent failures,
shadow↔paper divergence, and a composite System Health score. It drives the real
pipeline (truth → measurement → edge → paper signal) but never executes a bet
and changes no upstream module. Model probabilities are an **injected simulation
stub** (clearly labelled), so no prediction logic lives here.

---

## 2. Architecture

```
TimelineSimulator ──Triggers──▶ M5 PipelineOrchestrator (unchanged) ──▶ PaperSignals
   seed + noise                         │                                   │
   + drift injection                    ▼                                   ▼
   (liq shock / sharp /           per-tick WindowStat ◀──────────── collect signals/blocks/failures
    api delay)                          │
                 ┌────────────────┬─────┴───────────────┬────────────────────┐
                 ▼                ▼                     ▼                    ▼
        SilentFailureDetector  ShadowPaperDivergence  SystemHealthKernel   Drift Heatmap
                 │                (SPG/CR/regime)       (v1 composite)        │
                 └────────────────┴─────────┬───────────┴────────────────────┘
                                            ▼
                          ShadowReport: stability_score (0–100) + health + flags + heatmap
```

| Module | Role |
|---|---|
| `timeline.py` | T-72h→CLOSE trigger stream; deterministic seed + noise + drift injection |
| `monitors.py` | `SilentFailureDetector`, `ShadowPaperDivergence`, `SystemHealthKernel` v1 |
| `runner.py` | `ShadowRunner` drives M5; builds report, drift heatmap, stability score; stress scenarios |

**Event stream:** `snapshot_tick` → `ODDS_UPDATED` triggers; `state_transition`
is produced by the M4 lifecycle inside the orchestrator (observed, not driven).

---

## 3. Mathematical Definitions

Per-window (tick) stats: `n_triggers, n_signals, n_blocked, n_failures`,
`edge_values`, `tiers`, `truth_conf`. `edge_mean` / `truth_conf_mean` are window
means.

### Silent-failure detectors
```
no_signal_bug        : total_triggers >= k  AND  total_signals < 1
silent_clv_collapse  : truth_conf_mean[first] > floor > truth_conf_mean[last]
edge_stagnation      : Var(all edge_values) < eps_var   (signals present but frozen)
orchestrator_dead_zone: traffic across the run, but no signal/block/failure ANYWHERE
```

### Shadow–Paper divergence
Paper baseline = same seed, **no drift, zero noise**; shadow = drift + noise.
```
gap_i = | edge_mean_shadow[i] - edge_mean_paper[i] |
SPG   = mean_i gap_i                         (Shadow–Paper Gap)
CR    = (# windows with gap_i <= epsilon) / N    (Consistency Rate)
regime_drift = stdev_i(gap_i)                (distributional drift over time)
```

### System Health Kernel v1
```
pipeline_uptime = 1 - failures/triggers
signal_density  = min(1, (signals/triggers) / density_ref)
clv_stability   = 1 - min(1, Var(edge_mean per window) / clv_var_ref)
edge_entropy    = H(tier mix) / log(#distinct tiers)        # 0 = stagnation, ~1 = diverse
HEALTH = 100 * (0.30·uptime + 0.25·density + 0.25·clv_stability + 0.20·edge_entropy)
alert if any subscore < 0.50
```

### System Stability Score (0–100)
```
stability = clip( HEALTH - 10·(#silent_failures) - min(20, 100·SPG), 0, 100 )
```

---

## 4. Streaming Replay Engine

- **Deterministic seed:** `random.Random(seed)` drives all stochastic noise; same
  `(seed, drifts, config)` ⇒ identical trigger stream ⇒ identical report
  (latency excluded as wall-clock).
- **Stochastic noise overlay:** `noise_std` perturbs the HOME-prob path per tick.
- **Drift injection:** `LIQUIDITY_SHOCK` (vig widens), `SHARP_MOVE` (informed prob
  jump / steam), `API_DELAY` (a provider's snapshot drops → partial feed).
- **Historical + live mixed:** the timeline mixes a deterministic baseline path
  (historical) with the noise/drift overlay (live-like); paper baseline vs shadow
  isolates the live-condition impact.

---

## 5. Load / Stress Simulation

| Scenario | Builder | Models |
|---|---|---|
| Peak match day (EPL Saturday) | `peak_day_report(n_matches)` | many concurrent matches at one kickoff |
| Odds burst (multi-provider spike) | `odds_burst_report` | extra providers quoting simultaneously |
| Partial feed outage | `partial_outage_report` | `API_DELAY` drops a provider over several ticks |

---

## 6. Outputs

- **Shadow Health Report** — `ShadowReport.to_dict()`: stability score, health
  composite + subscores + alerts, divergence (SPG/CR/regime), run summary.
- **Drift Heatmap** — `drift_heatmap`: per-tick intensity rows for
  `LIQUIDITY_SHOCK / SHARP_MOVE / API_DELAY` + an `OBSERVED_IMPACT` row
  (|shadow − paper| edge per tick).
- **Silent Failure Log** — list of `Flag(type, window_index, detail)`.
- **System Stability Score** — single 0–100 number.

---

## 7. Failure Modes Covered

| Runtime failure | Detection |
|---|---|
| "no signal" bug | `no_signal_bug` (traffic but zero signals) |
| Silent CLV collapse | `silent_clv_collapse` (truth-conf decays below floor) |
| Edge stagnation | `edge_stagnation` (frozen edge variance) |
| Orchestrator dead zone | `orchestrator_dead_zone` (run-level no activity) |
| Live drift vs baseline | SPG / CR / regime_drift |
| Provider outage / partial feed | stress scenario + health degradation |

---

## 8. Test Report

| Area | Tests |
|---|---|
| timeline determinism / seed / API-delay drop | 3 |
| shadow run + signals/windows + replay determinism | 3 |
| health bounds, clean-run no-flags, drift heatmap | 3 |
| silent failures (no-signal, CLV collapse, stagnation, healthy) | 4 |
| divergence (zero / grows), entropy | 3 |
| stress (peak day, partial outage, odds burst) | 3 |

19 tests; full suite **136 green**; no existing module changed.

---

## 9. System Stability Score (current synthetic run)

On the bundled simulation (single match, sharp-move drift, 1% noise): HEALTH ≈
96.7 (uptime 1.0, density 1.0, clv_stability 0.91, edge_entropy 0.95), SPG ≈
0.02, CR ≈ 0.63, **stability ≈ 85/100**. This is *simulation* health, not live —
it validates the runtime machinery; real stability still depends on real sharp
data (Phase-16 bootstrap) feeding the Truth Store.

---

### Definition of Done
- [x] 24/7-style shadow runner over T-72h→CLOSE timelines driving M5 unchanged
- [x] Streaming replay: deterministic seed + stochastic noise + drift injection
- [x] Silent-failure detector (4 classes)
- [x] Shadow↔Paper divergence (SPG / CR / regime drift)
- [x] System Health Kernel v1 (uptime / density / CLV stability / edge entropy) + alerts
- [x] Load/stress simulation (peak day / odds burst / partial outage)
- [x] Outputs: Health Report, Drift Heatmap, Silent Failure Log, Stability Score (0–100)
- [x] Tests: deterministic replay + stress cases
- [x] No prediction/ML/execution; M1–M6 unchanged
