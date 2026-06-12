"""PHASE-LIVE L7 — Operational dashboard export.

Compiles daily JSON snapshots and maintains rolling trend series for
readiness, provider health, and signal volume.

Pure stdlib JSON. Additive: no changes to M1-M11 / L1-L6.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional


class DashboardExporter:
    """Builds daily dashboard snapshots and appends to in-memory trend series."""

    def __init__(self, max_trend_points: int = 90) -> None:
        self._max = max_trend_points
        self._readiness_trend: List[dict] = []
        self._provider_health_trend: List[dict] = []
        self._signal_volume_trend: List[dict] = []

    # ------------------------------------------------------------------ #

    def record_day(
        self,
        day: str,
        readiness_overall: float,
        provider_health: dict,
        signal_volume: int,
    ) -> None:
        """Append one day's data point to each trend series (bounded)."""
        self._append(self._readiness_trend,
                     {"day": day, "readiness": round(readiness_overall, 1)})
        self._append(self._provider_health_trend,
                     {"day": day, "provider_health": provider_health})
        self._append(self._signal_volume_trend,
                     {"day": day, "signal_volume": signal_volume})

    def compile_snapshot(
        self,
        *,
        day: str,
        public_delivery: dict,
        subscribers: dict,
        readiness: dict,
        operational: dict,
        alerts: dict,
    ) -> dict:
        return {
            "day": day,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "PUBLIC_DRY_RUN",
            "public_delivery": public_delivery,
            "subscribers": subscribers,
            "readiness": readiness,
            "operational": operational,
            "alerts": alerts,
            "trends": {
                "readiness": list(self._readiness_trend),
                "provider_health": list(self._provider_health_trend),
                "signal_volume": list(self._signal_volume_trend),
            },
        }

    def export_json(self, path: str, snapshot: dict) -> None:
        dir_part = os.path.dirname(path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2)

    # ------------------------------------------------------------------ #

    @property
    def readiness_trend(self) -> List[dict]:
        return list(self._readiness_trend)

    @property
    def signal_volume_trend(self) -> List[dict]:
        return list(self._signal_volume_trend)

    @property
    def provider_health_trend(self) -> List[dict]:
        return list(self._provider_health_trend)

    def _append(self, series: List[dict], point: dict) -> None:
        series.append(point)
        if len(series) > self._max:
            del series[0]
