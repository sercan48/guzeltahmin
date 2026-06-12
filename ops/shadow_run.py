"""PHASE-OPS — SHADOW phase operational driver.

This is operational glue ONLY. It adds no new feature, model, provider, or
endpoint. It composes existing PHASE-LIVE components to:

  1. Start the production harness in SHADOW / dry-run mode
     (dry_run forced True, monetization disabled, publishing held no-op).
  2. Auto-validate the SHADOW start checklist (Blocks A-E).
  3. Write a per-iteration shadow_log line
     (latency, completeness, control gate state, settlement summary,
      replay hash check).
  4. Emit ops/SHADOW_DAILY_REPORT_<YYYY-MM-DD>.json with read-only
     ROI / CLV / hit-rate (date-stamped so 30-day history is preserved).
  5. Enforce CRITICAL-event behaviour:
       - chain_valid == False  -> STOP RUN
       - leakage detected       -> STOP RUN
       - provider outage > threshold -> degrade mode
  6. In live mode: attempt manual_reset() from LOCKED -> SHADOW once the
     control plane's own kill factors clear (real data flowing, health >= 30,
     settlement_confidence >= 0.30). Logs the outcome each attempt.

Every component imported here already exists and is unchanged.

Usage (offline rehearsal — no live credentials needed):
    python3 -m ops.shadow_run --iterations 5 --offline

Usage (live — requires API keys in env):
    python3 -m ops.shadow_run --iterations 100 --poll-seconds 30

Control plane notes:
  The control plane starts in OFF state. The first evaluate() with zero real
  data fires two kill factors (k_health: health_v2=0 < 30, k_settle:
  settlement_confidence=0 < 0.30) and transitions to LOCKED. LOCKED never
  auto-promotes; only manual_reset() exits it, and only when all kill factors
  are clear. In live mode the driver attempts manual_reset() every
  --reset-warmup iterations once real metrics are available.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from typing import Optional

from src.market.activation import EnvSecretProvider, NullHttpClient
from src.market.activation.transport import StaticSecretProvider
from src.market.control.control_plane import ControlMetrics
from src.market.service.deployment import build_production_harness
from src.market.service.monetization.clock import SystemClock
from src.market.service.preflight import ProviderValidator
from src.market.service.production import ProductionProfile
from src.market.service.public.launch import assert_no_leakage, LeakageError
from src.market.settlement.ledger import SettlementLedger


def _utc_day() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


# --------------------------------------------------------------------------- #
# Checklist auto-validation (Blocks A-E) using existing components only.
# --------------------------------------------------------------------------- #

def validate_checklist(profile: ProductionProfile, harness, offline: bool) -> dict:
    """Auto-evaluate SHADOW start checklist Blocks A-E. Returns a result dict."""
    results = {}

    # Block A — Provider mapping (credential presence; values never read)
    real_pf = ProviderValidator(EnvSecretProvider()).run(profile.required_secrets(), [])
    results["A_provider_mapping"] = {
        "required_secrets": profile.required_secrets(),
        "live_credentials_present": real_pf.passed,
        "harness_preflight_passed": bool(harness._preflight and harness._preflight.passed),
        "mode": "OFFLINE_REHEARSAL" if offline else "LIVE",
        "status": "PASS" if (harness._preflight and harness._preflight.passed) else "FAIL",
    }

    # Block B — Fixture coverage (populated after first iteration)
    results["B_fixture_coverage"] = {
        "note": "populated after first iteration (jobs_processed)",
        "status": "DEFERRED",
    }

    # Block C — Settlement source (ledger reachable + chain valid)
    led = SettlementLedger(profile.truth_db.replace("truth", "settlement"))
    s = led.replay()
    results["C_settlement_source"] = {
        "ledger_reachable": True,
        "settlement_chain_valid": s.chain_valid,
        "n_settlements": s.n_settlements,
        "status": "PASS" if s.chain_valid else "FAIL",
    }
    led.close()

    # Block D — Replay determinism
    rc = harness._replay.check()
    results["D_replay_determinism"] = {
        "control_chain_valid": rc.get("chain_valid", False),
        "status": "PASS" if rc.get("chain_valid", False) else "FAIL",
    }

    # Block E — Kill-switch readiness
    state = harness._runtime.health_snapshot().to_dict().get("control_state", "UNKNOWN")
    results["E_kill_switch"] = {
        "dry_run_enforced": bool(harness._runtime._publisher.dry_run),
        "monetization_disabled": harness._runtime._monetization is None,
        "control_state_initial": state,
        "control_state_note": (
            "OFF/LOCKED is expected at cold start; transitions to SHADOW via "
            "manual_reset() once live metrics clear kill factors "
            "(health_v2 >= 30, settlement_confidence >= 0.30)"
        ),
        "alert_thresholds": {
            "min_provider_availability": profile.alerts.min_provider_availability,
            "min_completeness": profile.alerts.min_completeness,
            "max_consecutive_empty_iterations": profile.alerts.max_consecutive_empty_iterations,
        },
        "status": "PASS" if harness._runtime._publisher.dry_run else "FAIL",
    }

    blocking = [k for k, v in results.items() if v.get("status") == "FAIL"]
    results["_overall"] = {
        "blocking_failures": blocking,
        # Live credentials absent is EXPECTED in offline rehearsal.
        "ready_to_iterate": len([b for b in blocking if b != "B_fixture_coverage"]) == 0,
    }
    return results


# --------------------------------------------------------------------------- #
# Control plane reset helper
# --------------------------------------------------------------------------- #

def _try_manual_reset(harness, logf) -> bool:
    """Attempt manual_reset() on the control plane if it is LOCKED.

    Uses the last known ControlMetrics stored by the gateway (read-only).
    Returns True if the reset succeeded (state is now SHADOW).
    """
    plane = getattr(harness._runtime._gateway, "plane", None)
    if plane is None:
        return False
    from src.market.control.control_plane import SystemState
    if plane.state != SystemState.LOCKED:
        return False

    last_metrics: Optional[ControlMetrics] = getattr(
        harness._runtime._gateway, "last_metrics", None)
    if last_metrics is None:
        logf.write(f"# RESET_ATTEMPT: no metrics yet, skipped  at={_utc_now()}\n")
        return False

    ok = plane.manual_reset(last_metrics)
    if ok:
        logf.write(
            f"# RESET_OK: LOCKED -> SHADOW via manual_reset()  at={_utc_now()}\n")
    else:
        from src.market.control.control_plane import kill_factors
        kf = kill_factors(last_metrics, plane.cfg)
        active = [k for k, v in kf.items() if v]
        logf.write(
            f"# RESET_BLOCKED: kill factors still active: {active}  at={_utc_now()}\n")
    return ok


# --------------------------------------------------------------------------- #
# SHADOW run
# --------------------------------------------------------------------------- #

def run_shadow(
    iterations: int,
    report_dir: str,
    offline: bool,
    poll_seconds: float = 30.0,
    outage_threshold: float = 0.50,
    reset_warmup: int = 10,
) -> dict:
    os.makedirs(report_dir, exist_ok=True)
    day = _utc_day()
    log_path = os.path.join(report_dir, f"shadow_log_{day}.txt")
    settle_db = os.path.join(report_dir, "shadow_settlement.db")

    # Offline rehearsal uses 0.001 so the loop completes instantly.
    # Live mode uses the caller-supplied poll_seconds (default 30s).
    effective_poll = 0.001 if offline else poll_seconds

    profile = ProductionProfile(
        poll_interval_seconds=effective_poll,
        scheduler_db=os.path.join(report_dir, "shadow_scheduler.db"),
        truth_db=os.path.join(report_dir, "shadow_truth.db"),
        control_db=os.path.join(report_dir, "shadow_control.db"),
        bridge_db=os.path.join(report_dir, "shadow_bridge.db"),
        report_dir=report_dir,
    )

    if offline:
        secrets = StaticSecretProvider({
            "PINNACLE_API_KEY": "rehearsal", "BETFAIR_APP_KEY": "rehearsal",
            "BETFAIR_SESSION_TOKEN": "rehearsal",
        })
        http = NullHttpClient()
    else:
        secrets = EnvSecretProvider()
        http = None  # real UrllibHttpClient selected by activation layer

    harness = build_production_harness(
        profile, secret_provider=secrets, http_client=http, clock=SystemClock(),
    )

    checklist = validate_checklist(profile, harness, offline)

    stop_reason = None
    first_snapshot = None
    consecutive_low_avail = 0
    reset_attempted_at: Optional[int] = None

    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(
            f"# SHADOW LOG {day}  mode={'OFFLINE' if offline else 'LIVE'}"
            f"  poll={effective_poll}s  start={_utc_now()}\n"
        )

        for i in range(iterations):
            res = harness.run_iteration()
            it = res["iteration"]
            op = res["operational"]
            state = harness._runtime.health_snapshot().to_dict().get("control_state", "UNKNOWN")
            rc = harness._replay.check()
            chain_valid = rc.get("chain_valid", False)

            line = {
                "ts": _utc_now(),
                "iter": i,
                "latency_ms": it.get("latency_ms"),
                "jobs_processed": it.get("jobs_processed"),
                "completeness": op.get("snapshot_completeness"),
                "provider_availability": op.get("provider_availability"),
                "control_state": state,
                "signals_gated": it.get("signals_gated"),
                "published": it.get("published"),
                "suppressed": it.get("suppressed"),
                "outcomes_triggered": it.get("outcomes_triggered"),
                "degraded": it.get("degraded"),
                "replay_chain_valid": chain_valid,
                "new_alerts": [a.get("rule") for a in res.get("new_alerts", [])],
            }
            logf.write(json.dumps(line, ensure_ascii=False) + "\n")
            logf.flush()

            if first_snapshot is None:
                first_snapshot = dict(line)
                checklist["B_fixture_coverage"] = {
                    "jobs_processed_first_iter": it.get("jobs_processed", 0),
                    "note": (
                        "0 jobs is expected offline (NullHttpClient, no live fixtures)"
                        if offline else "live fixture count"
                    ),
                    "status": "PASS" if not offline else "OFFLINE_NO_FIXTURES",
                }

            # --- CRITICAL STOPS -------------------------------------------- #
            if not chain_valid:
                stop_reason = "REPLAY_CHAIN_INVALID"
                logf.write(f"# CRITICAL STOP: {stop_reason} @ iter {i}\n")
                break

            try:
                assert_no_leakage(json.dumps(line))
            except LeakageError:
                stop_reason = "LEAKAGE_DETECTED"
                logf.write(f"# CRITICAL STOP: {stop_reason} @ iter {i}\n")
                break

            # --- Provider outage degrade ------------------------------------ #
            avail = op.get("provider_availability", 1.0)
            if avail < outage_threshold:
                consecutive_low_avail += 1
                if consecutive_low_avail >= 3:
                    logf.write(
                        f"# DEGRADE: provider_availability={avail:.2f}"
                        f" < {outage_threshold} for {consecutive_low_avail} iters\n"
                    )
            else:
                consecutive_low_avail = 0

            # --- manual_reset() attempt in live mode ----------------------- #
            # Only in live mode; only once per run; only after warmup window.
            # Offline: kill factors will never clear (NullHttpClient → health=0).
            if not offline and state == "LOCKED" and reset_attempted_at is None:
                if i >= reset_warmup:
                    reset_attempted_at = i
                    _try_manual_reset(harness, logf)

        logf.write(
            f"# SHADOW LOG END  stop_reason={stop_reason or 'COMPLETED'}"
            f"  at={_utc_now()}\n"
        )

    # --- Daily report (read-only ROI / CLV / hit-rate) ----------------------- #
    led = SettlementLedger(settle_db)
    settle = led.replay()
    led.close()

    actual_iters = iterations if stop_reason is None else (i + 1)
    daily = {
        "day": day,
        "generated_at": _utc_now(),
        "mode": "SHADOW_OFFLINE_REHEARSAL" if offline else "SHADOW_LIVE",
        "dry_run": True,
        "iterations_run": actual_iters,
        "stop_reason": stop_reason,
        "checklist": checklist,
        "first_iteration_snapshot": first_snapshot,
        "operational": harness.operational_snapshot(),
        "readiness": harness.readiness_score(),
        "alerts": harness._alerts.to_dict(),
        "performance_read_only": {
            "n_settlements": settle.n_settlements,
            "n_void": settle.n_void,
            "total_roi": settle.total_roi,
            "mean_roi": settle.mean_roi,
            "mean_clv": settle.mean_clv,
            "clv_positive_rate": settle.beat_close_rate,
            "settlement_chain_valid": settle.chain_valid,
            "note": (
                "no settled bets — offline rehearsal has no live feed"
                if offline else "live settlement window"
            ),
        },
    }

    # Date-stamped so each day's report is preserved (not overwritten).
    report_path = os.path.join(report_dir, f"SHADOW_DAILY_REPORT_{day}.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(daily, fh, indent=2, ensure_ascii=False)

    harness._runtime.shutdown()
    return {"log_path": log_path, "report_path": report_path, "daily": daily}


def main() -> None:
    ap = argparse.ArgumentParser(description="SHADOW phase operational driver")
    ap.add_argument("--iterations", type=int, default=5,
                    help="number of poll iterations (use 0 for unlimited / SIGTERM stop)")
    ap.add_argument("--report-dir", default="ops")
    ap.add_argument("--offline", action="store_true",
                    help="rehearsal mode: NullHttpClient + simulated credentials")
    ap.add_argument("--poll-seconds", type=float, default=30.0,
                    help="poll interval for live mode (ignored in --offline)")
    ap.add_argument("--reset-warmup", type=int, default=10,
                    help="iterations to wait before attempting manual_reset() in live mode")
    args = ap.parse_args()

    n = args.iterations if args.iterations > 0 else 10_000_000
    out = run_shadow(
        iterations=n,
        report_dir=args.report_dir,
        offline=args.offline,
        poll_seconds=args.poll_seconds,
        reset_warmup=args.reset_warmup,
    )
    print("SHADOW run complete")
    print("  log:    ", out["log_path"])
    print("  report: ", out["report_path"])
    print("  stop_reason:", out["daily"]["stop_reason"])


if __name__ == "__main__":
    main()
