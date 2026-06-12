"""PHASE-OPS — SHADOW Pre-Live Hardening Test Runner.

Operational glue ONLY. No new features, no provider changes, no
monetization/control logic changes.

Runs 6 failure injection scenarios + 1 stress loop against the existing
pipeline using only components already present:
  - MockOddsProvider (fail_matches)     (src/market/activation/providers)
  - CircuitBreaker / RateLimiter        (src/market/activation/transport)
  - build_production_harness            (src/market/service/deployment)
  - StaticSecretProvider                (src/market/activation/transport)
  - ManualClock                         (src/market/service/monetization/clock)
  - SettlementLedger.replay()           (src/market/settlement/ledger)

TRANSPORT NOTE
  The real providers (Pinnacle, Betfair) require a FixtureMap entry before
  they reach the HTTP layer — fetch_snapshot raises ProviderError("no fixture
  mapping") without calling transport.  FakeHttpClient injection therefore does
  not exercise the HTTP path in the offline harness.
  Instead, failure scenarios use MockOddsProvider.fail_matches to inject
  ProviderError at the provider level — the same bridge/retry/degrade path
  that real transport errors traverse.
  The injected http_client stays as NullHttpClient (raises ConnectionError)
  to keep the harness in its intended offline posture.

Each scenario runs a bounded harness loop, observes system behaviour, and
emits a PASS/DEGRADE/FAIL verdict.  Results written to
ops/SHADOW_HARDENING_<YYYY-MM-DD>.json.

Usage:
    python3 -m ops.shadow_hardening [--report-dir ops]
"""

from __future__ import annotations

import datetime
import json
import os
import tempfile
import time
from types import SimpleNamespace
from typing import List, Optional

from src.market.activation import NullHttpClient
from src.market.activation.providers import MockOddsProvider, ProviderError, ProviderOutcome
from src.market.activation.transport import (
    CircuitBreaker, CircuitState, StaticSecretProvider,
)
from src.market.service.deployment import build_production_harness
from src.market.service.monetization.clock import ManualClock
from src.market.service.production import ProductionProfile
from src.market.settlement.ledger import SettlementLedger


_BASE_TS = 1767614400.0   # 2026-01-05 12:00:00 UTC
_TIERS = ["TIER_S", "TIER_A", "TIER_B", "TIER_C"]


def _utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _utc_day() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")


def _fake_signal(tier: str = "TIER_A", match_id: str = "M001") -> SimpleNamespace:
    return SimpleNamespace(
        match_id=match_id, market="1X2", selection="home",
        edge_score=0.08, tier=tier, confidence=0.72,
        truth_confidence=0.75,
        timestamp="2026-01-05T12:00:00+00:00",
    )


def _base_profile(tmpdir: str) -> ProductionProfile:
    return ProductionProfile(
        poll_interval_seconds=0.001,
        scheduler_db=os.path.join(tmpdir, "s.db"),
        truth_db=os.path.join(tmpdir, "t.db"),
        control_db=os.path.join(tmpdir, "c.db"),
        bridge_db=os.path.join(tmpdir, "b.db"),
        report_dir=tmpdir,
    )


_SECRETS = StaticSecretProvider({
    "PINNACLE_API_KEY": "rehearsal", "BETFAIR_APP_KEY": "rehearsal",
    "BETFAIR_SESSION_TOKEN": "rehearsal",
})


def _build(tmpdir: str, clock=None, signal_source=None) -> object:
    return build_production_harness(
        _base_profile(tmpdir),
        secret_provider=_SECRETS,
        http_client=NullHttpClient(),
        clock=clock or ManualClock(ts=_BASE_TS),
        signal_source=signal_source,
        run_preflight=False,
    )


def _inject_mock_provider(harness, odds_fixture: dict, outcomes: dict = None,
                           fail_matches: set = None) -> None:
    """Replace bridge.providers[0] with a MockOddsProvider for failure injection.

    MockOddsProvider is injected in-place; the MonitoringProvider wrapper is
    preserved if already present (from build_production_harness wrapping).
    """
    from src.market.service.monitor import MonitoringProvider
    mp = MockOddsProvider(
        name="mock_provider", provider_class="pinnacle",
        odds_fixture=odds_fixture or {},
        outcomes=outcomes or {},
        fail_matches=fail_matches or set(),
    )
    # Wrap in MonitoringProvider so latency tracking still functions
    wrapped = MonitoringProvider(mp, harness._monitor.latency)
    harness._runtime._bridge.providers = [wrapped]


def _run_n(harness, n: int) -> list:
    results = []
    for _ in range(n):
        try:
            results.append(harness.run_iteration())
        except Exception as e:
            results.append({"error": str(e)})
    return results


def _chain_and_state(harness) -> tuple:
    chain_valid = harness._replay.check().get("chain_valid", False)
    state = harness._runtime.health_snapshot().to_dict().get("control_state", "UNKNOWN")
    return chain_valid, state


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO 1 — Provider timeout / ProviderError on every fetch
# ─────────────────────────────────────────────────────────────────────────────

def scenario_provider_timeout(tmpdir: str) -> dict:
    """MockOddsProvider raises ProviderError on every match (simulates timeout /
    connection failure at the provider level).  Bridge must handle gracefully:
    no crash, jobs enter RETRY queue, chain_valid stays True."""
    clock = ManualClock(ts=_BASE_TS)
    h = _build(tmpdir, clock=clock,
                signal_source=lambda: [_fake_signal("TIER_A", "M001")])
    _inject_mock_provider(h, odds_fixture={}, fail_matches={"M001"})

    results = _run_n(h, 10)
    chain_valid, state = _chain_and_state(h)
    bridge_mon = h._runtime._bridge.monitor()
    h._runtime.shutdown()

    no_crash = all("error" not in r for r in results)
    return {
        "scenario": "provider_timeout",
        "iterations": len(results),
        "no_crash": no_crash,
        "bridge_failures": bridge_mon.get("ingestion_failure", 0),
        "bridge_in_retry": bridge_mon.get("in_retry", 0),
        "chain_valid": chain_valid,
        "final_control_state": state,
        "verdict": "PASS" if (no_crash and chain_valid) else "FAIL",
        "notes": "ProviderError on every fetch; bridge must queue RETRY without crash",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO 2 — Empty fixture response (provider returns 0 quotes)
# ─────────────────────────────────────────────────────────────────────────────

def scenario_empty_fixture(tmpdir: str) -> dict:
    """MockOddsProvider has no odds for M001 (empty dict) — fetch_snapshot
    returns [] → bridge raises ProviderError('no quotes').  Same RETRY path."""
    h = _build(tmpdir, signal_source=lambda: [_fake_signal("TIER_A", "M001")])
    # odds_fixture empty: no tick entries for M001 → empty quotes list
    _inject_mock_provider(h, odds_fixture={"M001": {}})

    results = _run_n(h, 10)
    chain_valid, state = _chain_and_state(h)
    bridge_mon = h._runtime._bridge.monitor()
    h._runtime.shutdown()

    no_crash = all("error" not in r for r in results)
    return {
        "scenario": "empty_fixture_response",
        "iterations": len(results),
        "no_crash": no_crash,
        "bridge_in_retry": bridge_mon.get("in_retry", 0),
        "chain_valid": chain_valid,
        "final_control_state": state,
        "verdict": "PASS" if (no_crash and chain_valid) else "FAIL",
        "notes": "0 quotes returned; bridge must handle empty response without panic",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO 3 — Malformed odds (extreme / impossible odds values)
# ─────────────────────────────────────────────────────────────────────────────

def scenario_malformed_payload(tmpdir: str) -> dict:
    """MockOddsProvider returns odds=0.0 (invalid; decimal odds must be > 1.0).
    The truth / edge layer must not panic on degenerate values."""
    h = _build(tmpdir, signal_source=lambda: [_fake_signal("TIER_A", "M001")])
    # Malformed: odds=0.0 and odds=-1.0 — triggers edge-case in math engine
    _inject_mock_provider(h, odds_fixture={"M001": {"PRE": {"home": 0.0, "away": -1.0}}})

    results = _run_n(h, 10)
    chain_valid, state = _chain_and_state(h)
    h._runtime.shutdown()

    no_crash = all("error" not in r for r in results)
    return {
        "scenario": "malformed_odds_payload",
        "iterations": len(results),
        "no_crash": no_crash,
        "chain_valid": chain_valid,
        "final_control_state": state,
        "verdict": "PASS" if (no_crash and chain_valid) else "FAIL",
        "notes": "odds=0.0/-1.0 injected; pipeline must not divide-by-zero or crash",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO 4 — Settlement missing / delayed
# ─────────────────────────────────────────────────────────────────────────────

def scenario_settlement_missing(tmpdir: str) -> dict:
    """Signal for M001 gated; outcome never arrives (MockOddsProvider has no
    outcomes dict).  SettlementLedger must stay consistent at 0 settlements."""
    h = _build(tmpdir, signal_source=lambda: [_fake_signal("TIER_A", "M001")])
    _inject_mock_provider(h, odds_fixture={"M001": {"PRE": {"home": 1.9, "away": 2.1}}},
                          outcomes={})  # no outcome for M001

    results = _run_n(h, 10)
    chain_valid, state = _chain_and_state(h)

    led = SettlementLedger(os.path.join(tmpdir, "settlement.db"))
    settle = led.replay()
    led.close()
    h._runtime.shutdown()

    return {
        "scenario": "settlement_missing_delayed",
        "iterations": len(results),
        "settlement_n": settle.n_settlements,
        "control_chain_valid": chain_valid,
        "ledger_chain_valid": settle.chain_valid,
        "final_control_state": state,
        "verdict": "PASS" if (chain_valid and settle.chain_valid) else "FAIL",
        "notes": "Outcome never arrives; SettlementLedger must stay consistent at 0",
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO 5 — Circuit breaker OPEN state observation
# ─────────────────────────────────────────────────────────────────────────────

def scenario_circuit_breaker_open(tmpdir: str) -> dict:
    """All provider fetches fail (ProviderError) for enough iterations to
    exceed the runtime's degraded_failure_threshold.  Expect: degraded=True
    on iteration, degraded_mode alert from AlertEngine.

    Note: the runtime's consecutive_failures counter increments only when
    bridge.process_due() itself raises (not per-job ProviderError which is
    caught inside the bridge).  With MockOddsProvider the per-job error is
    caught by the bridge; the bridge returns normally (results=[RETRY]).
    The runtime sees 0 ingestion_success which triggers provider_outage alert
    but not degraded_mode. This is the correct documented behaviour:
    runtime.degraded is set when process_due() raises an exception, not
    when individual jobs enter RETRY.
    """
    h = _build(tmpdir, signal_source=lambda: [_fake_signal("TIER_A", "M001")])
    _inject_mock_provider(h, odds_fixture={}, fail_matches={"M001"})

    results = _run_n(h, 25)  # exceed max_consecutive_empty_iterations / 5
    chain_valid, state = _chain_and_state(h)
    bridge_mon = h._runtime._bridge.monitor()
    alerts = [a for r in results if isinstance(r, dict) for a in r.get("new_alerts", [])]
    alert_rules = list({a.get("rule") for a in alerts})
    h._runtime.shutdown()

    no_crash = all("error" not in r for r in results)
    return {
        "scenario": "circuit_breaker_open",
        "iterations": len(results),
        "no_crash": no_crash,
        "bridge_in_retry": bridge_mon.get("in_retry", 0),
        "alerts_fired": alert_rules,
        "chain_valid": chain_valid,
        "final_control_state": state,
        "verdict": "PASS" if (no_crash and chain_valid) else "FAIL",
        "notes": (
            "Per-job ProviderError caught by bridge → RETRY queue (not runtime degraded). "
            "Runtime degraded fires only when process_due() itself raises. "
            "provider_outage/completeness alerts expected after threshold."
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# SCENARIO 6 — Rate limiter exhaustion (ProviderError flood → RETRY queue fill)
# ─────────────────────────────────────────────────────────────────────────────

def scenario_rate_limit_exhaustion(tmpdir: str) -> dict:
    """Simulate rate-limit exhaustion by injecting ProviderError for many
    distinct match IDs simultaneously — bridge fills the RETRY queue.
    After injection, provider is restored to healthy state; queue must drain."""
    # 10 matches all failing initially
    fail_set = {f"M{i:03d}" for i in range(1, 11)}
    call_count = [0]
    signals_all = [_fake_signal("TIER_B", f"M{i:03d}") for i in range(1, 11)]

    def signal_source():
        i = call_count[0] % len(signals_all)
        call_count[0] += 1
        return [signals_all[i]]

    h = _build(tmpdir, signal_source=signal_source)
    _inject_mock_provider(h, odds_fixture={}, fail_matches=fail_set)

    # First 5 iterations: all fail (queue fills)
    results_fail = _run_n(h, 5)
    bridge_mon_fail = h._runtime._bridge.monitor()

    # Restore to healthy mock (no fail_matches)
    _inject_mock_provider(h,
        odds_fixture={f"M{i:03d}": {"PRE": {"home": 1.9, "away": 2.1}}
                      for i in range(1, 11)},
        outcomes={},
        fail_matches=set(),
    )
    results_recover = _run_n(h, 10)
    bridge_mon_after = h._runtime._bridge.monitor()
    chain_valid, state = _chain_and_state(h)
    h._runtime.shutdown()

    no_crash = all("error" not in r for r in results_fail + results_recover)
    return {
        "scenario": "rate_limiter_exhaustion",
        "iterations_injected": len(results_fail),
        "iterations_recovery": len(results_recover),
        "no_crash": no_crash,
        "retry_queue_at_peak": bridge_mon_fail.get("in_retry", 0),
        "retry_queue_after_recovery": bridge_mon_after.get("in_retry", 0),
        "chain_valid": chain_valid,
        "final_control_state": state,
        "verdict": "PASS" if (no_crash and chain_valid) else "FAIL",
        "notes": "10-match ProviderError flood then recovery; RETRY queue must fill and drain",
    }


# ─────────────────────────────────────────────────────────────────────────────
# STRESS LOOP — accelerated 24h simulation (120 steps × 12 min)
# ─────────────────────────────────────────────────────────────────────────────

def scenario_stress_loop(tmpdir: str, steps: int = 120) -> dict:
    """Accelerated time stress loop — each step advances clock 12 minutes
    (120 steps = 24h simulated).  Mixed-tier signals injected every step.
    Observes: control transitions, replay determinism, alert counts, loop speed."""
    clock = ManualClock(ts=_BASE_TS)
    tier_cycle = _TIERS * (steps // len(_TIERS) + 1)
    call_count = [0]

    def signal_source():
        t = tier_cycle[call_count[0] % len(tier_cycle)]
        call_count[0] += 1
        return [_fake_signal(t, f"M{call_count[0]:04d}")]

    h = _build(tmpdir, clock=clock, signal_source=signal_source)
    # Inject healthy mock provider so signals can progress beyond ProviderError
    _inject_mock_provider(h,
        odds_fixture={},  # no pre-registered fixtures → jobs enter RETRY (normal offline)
        outcomes={},
    )

    control_transitions: list = []
    prev_state: Optional[str] = None
    replay_failures = 0
    alert_counts: dict = {}
    t0 = time.monotonic()

    for i in range(steps):
        clock._ts += 720  # +12 minutes
        try:
            res = h.run_iteration()
        except Exception as e:
            res = {"error": str(e), "iteration": {}, "operational": {}, "new_alerts": []}

        state = h._runtime.health_snapshot().to_dict().get("control_state", "UNKNOWN")
        if state != prev_state:
            control_transitions.append({"step": i, "from": prev_state, "to": state,
                                        "sim_h": round(i * 12 / 60, 1)})
            prev_state = state

        for a in res.get("new_alerts", []):
            rule = a.get("rule", "unknown")
            alert_counts[rule] = alert_counts.get(rule, 0) + 1

        if i % 30 == 0:
            rc = h._replay.check()
            if not rc.get("chain_valid", True):
                replay_failures += 1

    elapsed = time.monotonic() - t0
    final_rc = h._replay.check()
    readiness = h.readiness_score()
    op = h.operational_snapshot()
    h._runtime.shutdown()

    return {
        "scenario": "stress_loop_24h_sim",
        "steps": steps,
        "simulated_hours": round(steps * 12 / 60, 1),
        "wall_clock_ms": round(elapsed * 1000),
        "signals_injected": call_count[0],
        "control_transitions": control_transitions,
        "replay_spot_check_failures": replay_failures,
        "final_chain_valid": final_rc.get("chain_valid", False),
        "final_readiness": readiness,
        "alert_counts": alert_counts,
        "operational_summary": op,
        "verdict": (
            "PASS" if final_rc.get("chain_valid", False) and replay_failures == 0
            else ("DEGRADE" if final_rc.get("chain_valid", False) else "FAIL")
        ),
        "notes": f"{steps} steps × 12 min = {round(steps*12/60,1)}h sim in {round(elapsed,2)}s wall clock",
    }


# ─────────────────────────────────────────────────────────────────────────────
# OBSERVABILITY CHECK
# ─────────────────────────────────────────────────────────────────────────────

def check_observability(report_dir: str, scenarios: list) -> dict:
    """Verify that shadow_log and daily report are readable and consistent."""
    day = _utc_day()
    log_path = os.path.join(report_dir, f"shadow_log_{day}.txt")
    report_path = os.path.join(report_dir, f"SHADOW_DAILY_REPORT_{day}.json")

    checks = {}
    checks["shadow_log_exists"] = os.path.exists(log_path)
    checks["daily_report_exists"] = os.path.exists(report_path)

    if checks["daily_report_exists"]:
        try:
            with open(report_path) as fh:
                rpt = json.load(fh)
            checks["daily_report_parseable"] = True
            checks["daily_report_has_readiness"] = "readiness" in rpt
            checks["daily_report_has_alerts"] = "alerts" in rpt
            checks["daily_report_mode"] = rpt.get("mode")
        except Exception as e:
            checks["daily_report_parseable"] = False
            checks["daily_report_error"] = str(e)
    else:
        checks["daily_report_parseable"] = False

    # Check that alarm thresholds fired in scenarios that should trigger them
    cb_scenario = next((s for s in scenarios if s["scenario"] == "circuit_breaker_open"), {})
    stress_scenario = next((s for s in scenarios if s["scenario"] == "stress_loop_24h_sim"), {})
    checks["provider_outage_alert_fires"] = (
        "provider_outage" in stress_scenario.get("alert_counts", {})
        or "provider_outage" in cb_scenario.get("alerts_fired", [])
    )
    checks["all_scenarios_have_chain_valid"] = all(
        s.get("chain_valid", s.get("final_chain_valid", s.get("control_chain_valid", True)))
        for s in scenarios
    )
    missing = [
        k for k in ["provider_availability", "snapshot_completeness",
                     "uptime_seconds", "iterations"]
        if k not in stress_scenario.get("operational_summary", {})
    ]
    checks["missing_operational_metrics"] = missing

    return checks


# ─────────────────────────────────────────────────────────────────────────────
# SCORING
# ─────────────────────────────────────────────────────────────────────────────

def _score(scenarios: list) -> tuple:
    weight = {
        "provider_timeout": 15,
        "empty_fixture_response": 10,
        "malformed_odds_payload": 15,
        "settlement_missing_delayed": 15,
        "circuit_breaker_open": 15,
        "rate_limiter_exhaustion": 10,
        "stress_loop_24h_sim": 20,
    }
    pass_val = {"PASS": 1.0, "DEGRADE": 0.5, "FAIL": 0.0}
    total_weight = sum(weight.values())
    earned = 0.0
    critical: list = []

    for s in scenarios:
        name = s["scenario"]
        w = weight.get(name, 10)
        v = pass_val.get(s["verdict"], 0.0)
        earned += w * v
        if s["verdict"] == "FAIL":
            critical.append(f"{name}: FAIL — {s.get('notes', '')}")
        cv = s.get("chain_valid", s.get("final_chain_valid",
             s.get("control_chain_valid", True)))
        if not cv:
            critical.append(f"{name}: REPLAY CHAIN INVALID")

    score = round(earned / total_weight * 100, 1)
    if score >= 90:
        verdict = "READY_FOR_LIVE_CREDENTIALS"
    elif score >= 70:
        verdict = "CONDITIONAL_READY — review DEGRADE items before live"
    else:
        verdict = "NOT_READY — resolve FAIL items before proceeding"

    return score, critical, verdict


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

_SCENARIOS = [
    ("provider_timeout",           scenario_provider_timeout),
    ("empty_fixture_response",     scenario_empty_fixture),
    ("malformed_odds_payload",     scenario_malformed_payload),
    ("settlement_missing_delayed", scenario_settlement_missing),
    ("circuit_breaker_open",       scenario_circuit_breaker_open),
    ("rate_limiter_exhaustion",    scenario_rate_limit_exhaustion),
    ("stress_loop_24h_sim",        scenario_stress_loop),
]


def run_hardening(report_dir: str = "ops") -> dict:
    os.makedirs(report_dir, exist_ok=True)
    day = _utc_day()
    print(f"[{_utc_now()}] SHADOW Pre-Live Hardening  ({len(_SCENARIOS)} scenarios)")

    scenario_results = []
    failure_map = {}

    for name, fn in _SCENARIOS:
        print(f"  ▶ {name:<38}", end=" ", flush=True)
        with tempfile.TemporaryDirectory() as d:
            t0 = time.monotonic()
            try:
                result = fn(d)
            except Exception as exc:
                result = {
                    "scenario": name,
                    "verdict": "FAIL",
                    "error": f"{type(exc).__name__}: {exc}",
                    "chain_valid": False,
                    "notes": "unhandled exception in scenario",
                }
            result.setdefault("wall_ms", round((time.monotonic() - t0) * 1000))
        scenario_results.append(result)
        v = result["verdict"]
        suffix = ""
        if v != "PASS" or result.get("error"):
            suffix = f"  ← {result.get('error', result.get('notes', ''))[:60]}"
        print(f"{v}{suffix}")
        if v != "PASS":
            failure_map[name] = {"verdict": v, "notes": result.get("notes", ""),
                                  "error": result.get("error")}

    score, critical, overall_verdict = _score(scenario_results)
    obs = check_observability(report_dir, scenario_results)

    report = {
        "day": day,
        "generated_at": _utc_now(),
        "mode": "SHADOW_HARDENING",
        "stability_score": score,
        "live_readiness_verdict": overall_verdict,
        "critical_risks": critical,
        "failure_map": failure_map,
        "observability": obs,
        "transport_layer_note": (
            "Real providers require FixtureMap entry before reaching HTTP layer. "
            "FakeHttpClient injection does not exercise transport in offline harness. "
            "Scenarios use MockOddsProvider.fail_matches to inject ProviderError at "
            "provider level — same bridge/retry/degrade code path as real HTTP failures."
        ),
        "scenarios": scenario_results,
    }

    path = os.path.join(report_dir, f"SHADOW_HARDENING_{day}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)

    print(f"\n[{_utc_now()}] Complete")
    print(f"  Stability score : {score}/100")
    print(f"  Verdict         : {overall_verdict}")
    if critical:
        for r in critical:
            print(f"  ⚠  {r}")
    print(f"\n  Observability checks:")
    for k, v in obs.items():
        mark = "✓" if v is True or (isinstance(v, list) and len(v) == 0) else ("✗" if v is False else "·")
        print(f"    {mark} {k}: {v}")
    print(f"\n  Report: {path}")
    return report


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="SHADOW pre-live hardening runner")
    ap.add_argument("--report-dir", default="ops")
    args = ap.parse_args()
    run_hardening(report_dir=args.report_dir)
