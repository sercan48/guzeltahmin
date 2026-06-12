# PHASE-LIVE L6 — Production Dry-Run Deployment

Run the complete production stack continuously against **real** Pinnacle and
Betfair feeds while keeping **all publishing in dry-run mode** (no Telegram
messages are ever sent). Collects operational evidence over days/weeks for a
go-live decision.

## What L6 adds (all additive)

| File | Component |
|---|---|
| `src/market/service/production.py` | `ProductionProfile`, `AlertThresholds` |
| `src/market/service/preflight.py` | `ProviderValidator` — startup credential + reachability checks |
| `src/market/service/metrics.py` | `OperationalMetrics`, `AlertEngine` |
| `src/market/service/deployment.py` | `ProductionHarness`, `build_production_harness()` |
| `src/market/service/deployment_report.py` | `ProductionDailyReport` |

No changes to M1–M11 or L1–L5 behaviour. `dry_run` is **forced `True`** by
`ProductionProfile.to_runtime_config()` and cannot be overridden.

## Quick start

```bash
export PINNACLE_API_KEY="..."
export BETFAIR_APP_KEY="..."
export BETFAIR_SESSION_TOKEN="..."
```

```python
from src.market.service import ProductionProfile, build_production_harness

profile = ProductionProfile(
    poll_interval_seconds=30.0,
    report_dir="prod_reports",
)
harness = build_production_harness(profile)   # runs pre-flight checks
harness.run()        # blocks; SIGTERM / Ctrl-C for graceful shutdown
```

`build_production_harness()` runs pre-flight credential checks first. If a
required secret is missing it logs a warning (it does not crash) — inspect
`harness._preflight.to_dict()` for the result.

## Operational metrics (per `OperationalMetrics.snapshot()`)

| Metric | Meaning |
|---|---|
| `uptime_seconds` | Wall-clock since harness start (via injected Clock) |
| `iterations` / `total_jobs` | Loop cycles and jobs processed |
| `provider_availability` | Fraction of iterations with no errors |
| `snapshot_completeness` | Clean-iteration ratio from `FeedMonitor` |
| `consecutive_empty` | Iterations in a row with zero jobs (outage signal) |
| `settlements_total` / `signals_total` | Settlement + signal throughput |
| `degraded_iterations` | Iterations spent in degraded mode |

## Alert rules (`AlertEngine` against `AlertThresholds`)

| Rule | Severity | Trigger |
|---|---|---|
| `degraded_mode` | CRITICAL | Service entered degraded mode |
| `provider_availability` | WARNING | Availability below `min_provider_availability` (0.90) |
| `provider_outage` | WARNING | `consecutive_empty` ≥ `max_consecutive_empty_iterations` (120) |
| `completeness` | WARNING | Completeness below `min_completeness` (0.80) |
| `replay_integrity` | CRITICAL | Replay chain verification failed |

## Daily report

A JSON report is written to `<report_dir>/prod-<YYYY-MM-DD>.json` at each UTC
midnight rollover and on shutdown. It contains: `mode: "DRY_RUN"`, pre-flight
result, operational metrics, readiness score, health snapshot, monitoring
(latency/completeness/truth-confidence), settlement summary, replay report,
and the full alert log.

## Restart recovery

All pipeline state lives in append-only SQLite ledgers (`prod_scheduler.db`,
`prod_truth.db`, `prod_bridge.db`, `prod_control.db`, `prod_control.db.gate`).
On restart, `build_production_harness()` re-opens the same files; the scheduler
replays unobserved events, the truth store retains all snapshots, and the
control plane re-verifies its hash chain. Replay determinism is preserved.

## Definition of Done — verification

```bash
python3 -m unittest tests.test_service_deployment -v   # 60 offline tests
python3 -m unittest tests.test_m11_acceptance -v       # acceptance hash unchanged
```

Acceptance hash: `ab3844b895a887e3579a29e273261154743507bf157596bc4657aaa7b901abcd`
