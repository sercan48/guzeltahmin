"""PHASE-LIVE L6 — Production daily report compiler.

Compiles and persists a daily operational report combining operational
metrics, readiness, health, monitoring, settlement, replay, alerts, and the
startup pre-flight result.

Pure stdlib JSON. No external deps.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional


class ProductionDailyReport:
    """Compiles + writes one day's production operational report to JSON."""

    def compile(
        self,
        *,
        day: str,
        operational: dict,
        readiness: dict,
        health: dict,
        monitoring: dict,
        settlement: dict,
        replay: dict,
        alerts: dict,
        preflight: Optional[dict] = None,
    ) -> dict:
        return {
            "day": day,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "mode": "DRY_RUN",
            "preflight": preflight,
            "operational": operational,
            "readiness": readiness,
            "health": health,
            "monitoring": monitoring,
            "settlement": settlement,
            "replay": replay,
            "alerts": alerts,
        }

    def export_json(self, path: str, report: dict) -> None:
        dir_part = os.path.dirname(path)
        if dir_part:
            os.makedirs(dir_part, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
