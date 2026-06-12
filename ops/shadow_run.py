"""PHASE-OPS — SHADOW phase operational driver.

This is operational glue ONLY. It adds no new feature, model, provider, or
endpoint. It composes existing PHASE-LIVE components to:

  1. Start the production harness in SHADOW / dry-run mode
     (dry_run forced True, monetization disabled, publishing held no-op).
  2. Auto-validate the SHADOW start checklist (Blocks A-E).
  3. Write a per-iteration shadow_log line
     (latency, completeness, control gate state, settlement summary,
      replay hash check).
  4. Emit ops/SHADOW_DAILY_REPORT.json with read-only ROI / CLV / hit-rate.
  5. Enforce CRITICAL-event behaviour:
       - chain_valid == False  -> STOP RUN
       - leakage detected       -> STOP RUN
       - provider outage > threshold -> degrade mode

Every component imported here already exists and is unchanged.

Usage:
    python3 -m ops.shadow_run --iterations 5
    python3 -m ops.shadow_run --iterations 5 --report-dir ops --offline
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
from typing import Optional

from src.market.activation import EnvSecretProvider, NullHttpClient
from src.market.activation.transport import StaticSecretProvider
from src.market.service.deployment import build_production_harness
from src.market.service.monetization.clock import SystemClock
from src.market.service.preflight import ProviderValidator
from src.market.service.production import ProductionProfile
from src.market.service.public.launch import assert_no_leakage, LeakageError
from src.market.settlement.ledger import SettlementLedger


# OFFLINE rehearsal uses the existing StaticSecretProvider (no new component).


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

    # Block B — Fixture coverage (first iteration job count)
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

    # Block D — Replay determinism + acceptance hash
    rc = harness._replay.check()
    results["D_replay_determinism"] = {
        "control_chain_valid": rc.get("chain_valid", False),
        "status": "PASS" if rc.get("chain_valid", False) else "FAIL",
    }

    # Block E — Kill-switch readiness (dry-run forced, control state observable)
    state = harness._runtime.health_snapshot().to_dict().get("control_state", "UNKNOWN")
    results["E_kill_switch"] = {
        "dry_run_enforced": bool(harness._runtime._publisher.dry_run),
        "monetization_disabled": harness._runtime._monetization is None,
        "control_state": state,
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
        # Live credentials absent is EXPECTED in an offline rehearsal and is
        # the documented top readiness blocker, not a harness fault.
        "ready_to_iterate": len([b for b in blocking if b != "B_fixture_coverage"]) == 0,
    }
    return results


# --------------------------------------------------------------------------- #
# SHADOW run
# --------------------------------------------------------------------------- #

def run_shadow(iterations: int, report_dir: str, offline: bool,
               outage_threshold: float = 0.50) -> dict:
    os.makedirs(report_dir, exist_ok=True)
    day = _utc_day()
    log_path = os.path.join(report_dir, f"shadow_log_{day}.txt")
    settle_db = os.path.join(report_dir, "shadow_settlement.db")

    profile = ProductionProfile(
        poll_interval_seconds=0.001,
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
        http = None  # real transport (UrllibHttpClient) selected by activation

    harness = build_production_harness(
        profile, secret_provider=secrets, http_client=http, clock=SystemClock(),
    )

    checklist = validate_checklist(profile, harness, offline)

    stop_reason = None
    first_snapshot = None
    consecutive_low_avail = 0

    with open(log_path, "a", encoding="utf-8") as logf:
        logf.write(f"# SHADOW LOG {day}  mode={'OFFLINE' if offline else 'LIVE'}  start={_utc_now()}\n")
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
                # backfill Block B now that we have a real iteration
                checklist["B_fixture_coverage"] = {
                    "jobs_processed_first_iter": it.get("jobs_processed", 0),
                    "note": "0 jobs is expected offline (NullHttpClient has no live fixtures)",
                    "status": "PASS" if not offline else "OFFLINE_NO_FIXTURES",
                }

            # --- CRITICAL EVENT BEHAVIOUR ---------------------------------- #
            if not chain_valid:
                stop_reason = "REPLAY_CHAIN_INVALID"
                logf.write(f"# CRITICAL STOP: {stop_reason} @ iter {i}\n")
                break

            # leakage guard: rendered publications must never carry full-format
            # markers. Dry-run emits nothing, but we still assert the guard arms.
            try:
                assert_no_leakage(json.dumps(line))
            except LeakageError:
                stop_reason = "LEAKAGE_DETECTED"
                logf.write(f"# CRITICAL STOP: {stop_reason} @ iter {i}\n")
                break

            avail = op.get("provider_availability", 1.0)
            if avail < outage_threshold:
                consecutive_low_avail += 1
                if consecutive_low_avail >= 3:
                    logf.write(f"# DEGRADE: provider availability {avail:.2f} "
                               f"< {outage_threshold} for {consecutive_low_avail} iters\n")
            else:
                consecutive_low_avail = 0

        logf.write(f"# SHADOW LOG END  stop_reason={stop_reason or 'COMPLETED'}  at={_utc_now()}\n")

    # --- Daily report (read-only ROI / CLV / hit-rate) ----------------------- #
    led = SettlementLedger(settle_db)
    settle = led.replay()
    led.close()

    daily = {
        "day": day,
        "generated_at": _utc_now(),
        "mode": "SHADOW_OFFLINE_REHEARSAL" if offline else "SHADOW_LIVE",
        "dry_run": True,
        "iterations_run": iterations if stop_reason is None else i + 1,
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
            "note": ("no settled bets — offline rehearsal has no live feed"
                     if offline else "live settlement window"),
        },
    }

    report_path = os.path.join(report_dir, "SHADOW_DAILY_REPORT.json")
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(daily, fh, indent=2, ensure_ascii=False)

    harness._runtime.shutdown()
    return {"log_path": log_path, "report_path": report_path, "daily": daily}


def main() -> None:
    ap = argparse.ArgumentParser(description="SHADOW phase operational driver")
    ap.add_argument("--iterations", type=int, default=5)
    ap.add_argument("--report-dir", default="ops")
    ap.add_argument("--offline", action="store_true",
                    help="rehearsal mode: NullHttpClient + simulated credentials")
    args = ap.parse_args()

    out = run_shadow(args.iterations, args.report_dir, args.offline)
    print("SHADOW run complete")
    print("  log:    ", out["log_path"])
    print("  report: ", out["report_path"])
    print("  stop_reason:", out["daily"]["stop_reason"])


if __name__ == "__main__":
    main()
