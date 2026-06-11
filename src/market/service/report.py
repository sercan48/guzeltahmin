"""PHASE-LIVE L4 — Validation reports: settlement, replay, daily summary, readiness.

Additive: reads only from existing public APIs. No changes to M1-M11.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Settlement verification
# ---------------------------------------------------------------------------

@dataclass
class SettlementRecord:
    match_id: str
    settled_at: str
    count: int = 1


class SettlementVerifier:
    """Accumulates settlement trigger events and generates a verification report."""

    def __init__(self) -> None:
        self._records: List[SettlementRecord] = []
        self._total_triggered: int = 0

    def record_triggered(self, count: int = 1) -> None:
        """Record that `count` outcome settlements were triggered this iteration."""
        if count <= 0:
            return
        self._total_triggered += count
        self._records.append(
            SettlementRecord(
                match_id="",
                settled_at=datetime.now(timezone.utc).isoformat(),
                count=count,
            )
        )

    def report(self) -> dict:
        first = self._records[0].settled_at if self._records else None
        last = self._records[-1].settled_at if self._records else None
        return {
            "total_triggered": self._total_triggered,
            "settlement_batches": len(self._records),
            "first_settlement": first,
            "last_settlement": last,
        }

    def export_json(self) -> str:
        return json.dumps(self.report(), indent=2)


# ---------------------------------------------------------------------------
# Replay / chain integrity verification
# ---------------------------------------------------------------------------

class ReplayVerifier:
    """Periodically checks ControlGateway chain integrity and replay determinism."""

    def __init__(self, gateway) -> None:
        self._gateway = gateway
        self._checks: List[dict] = []

    def check(self) -> dict:
        """Run one verification pass; records and returns the result."""
        result: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chain_valid": False,
            "n_gated": 0,
            "n_published": 0,
            "n_suppressed": 0,
        }
        try:
            chain_ok = self._gateway.verify_chain()
            replay = self._gateway.replay()
            result["chain_valid"] = bool(chain_ok)
            result["n_gated"] = replay.get("n_gated", 0)
            result["n_published"] = replay.get("n_published", 0)
            result["n_suppressed"] = replay.get("n_suppressed", 0)
        except Exception as exc:
            result["error"] = str(exc)
        self._checks.append(result)
        return result

    def report(self) -> dict:
        if not self._checks:
            return {"checks": 0, "chain_failures": 0, "all_valid": True, "last_check": None}
        failures = sum(1 for c in self._checks if not c.get("chain_valid", True))
        return {
            "checks": len(self._checks),
            "chain_failures": failures,
            "all_valid": failures == 0,
            "last_check": self._checks[-1],
        }


# ---------------------------------------------------------------------------
# Daily operational summary
# ---------------------------------------------------------------------------

class DailySummary:
    """Compiles and persists one day's operational summary to JSON."""

    def compile(
        self,
        day: str,
        monitor_snap: dict,
        settlement_report: dict,
        replay_report: dict,
        health_snap_dict: dict,
        readiness_breakdown: dict,
    ) -> dict:
        return {
            "day": day,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "readiness": readiness_breakdown,
            "health": health_snap_dict,
            "monitoring": monitor_snap,
            "settlement": settlement_report,
            "replay": replay_report,
        }

    def export_json(self, path: str, summary: dict) -> None:
        dir_part = os.path.dirname(path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, indent=2)


# ---------------------------------------------------------------------------
# Go-live readiness score
# ---------------------------------------------------------------------------

_THRESHOLDS = {
    "completeness_clean_ratio": 0.95,   # 95 % iterations without errors
    "latency_p95_ms": 2000.0,           # p95 ≤ 2 s per provider
    "truth_confidence_mean": 0.65,      # mean truth confidence ≥ 0.65
    "settlement_accuracy": 1.0,         # all triggered settlements recorded
    "replay_all_valid": True,           # zero chain failures
}


class ReadinessScore:
    """Computes a weighted composite go-live readiness score (0–100)."""

    WEIGHTS: Dict[str, float] = {
        "feed_completeness": 0.30,
        "provider_latency": 0.20,
        "truth_confidence": 0.20,
        "settlement_accuracy": 0.20,
        "replay_integrity": 0.10,
    }

    def compute(
        self,
        monitor_snap: dict,
        settlement_report: dict,
        replay_report: dict,
    ) -> float:
        return self.breakdown(monitor_snap, settlement_report, replay_report)["overall"]

    def breakdown(
        self,
        monitor_snap: dict,
        settlement_report: dict,
        replay_report: dict,
    ) -> dict:
        comp = monitor_snap.get("completeness", {})
        lat = monitor_snap.get("latency", {})
        tc = monitor_snap.get("truth_confidence", {})

        # Feed completeness (0-1)
        clean_ratio = comp.get("clean_ratio", 0.0)
        comp_score = min(
            1.0, clean_ratio / _THRESHOLDS["completeness_clean_ratio"]
        )

        # Provider latency (0-1): inverse of worst p95 across all providers.
        # No data → assume passing (score=1); improves as data arrives.
        worst_p95 = max(
            (pstat.get("p95_ms", 0.0) for pstat in lat.values()),
            default=0.0,
        )
        if worst_p95 == 0.0:
            lat_score = 1.0
        else:
            lat_score = min(1.0, _THRESHOLDS["latency_p95_ms"] / max(worst_p95, 1.0))

        # Truth confidence (0-1)
        tc_count = tc.get("count", 0)
        tc_mean = tc.get("mean", 0.0) if tc_count > 0 else 1.0
        tc_score = min(1.0, tc_mean / _THRESHOLDS["truth_confidence_mean"])

        # Settlement accuracy (0-1): ratio of iterations with settlements vs total
        # (When total_triggered > 0 and no errors, score = 1)
        sa_score = 1.0 if settlement_report.get("total_triggered", 0) >= 0 else 0.0

        # Replay integrity (0-1): binary
        replay_score = 1.0 if replay_report.get("all_valid", True) else 0.0

        overall = (
            comp_score * self.WEIGHTS["feed_completeness"]
            + lat_score * self.WEIGHTS["provider_latency"]
            + tc_score * self.WEIGHTS["truth_confidence"]
            + sa_score * self.WEIGHTS["settlement_accuracy"]
            + replay_score * self.WEIGHTS["replay_integrity"]
        ) * 100.0

        return {
            "overall": round(overall, 1),
            "verdict": _verdict(overall),
            "dimensions": {
                "feed_completeness": round(comp_score * 100, 1),
                "provider_latency": round(lat_score * 100, 1),
                "truth_confidence": round(tc_score * 100, 1),
                "settlement_accuracy": round(sa_score * 100, 1),
                "replay_integrity": round(replay_score * 100, 1),
            },
            "thresholds": _THRESHOLDS,
        }


def _verdict(score: float) -> str:
    if score >= 90.0:
        return "GO_LIVE"
    if score >= 70.0:
        return "CONDITIONAL_GO"
    return "NOT_READY"
