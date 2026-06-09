"""MIW R1.2 — Live Market Measurement Layer (CLV + Odds Drift Core).

A *pure measurement* layer that converts raw odds snapshots (produced by the
R1.1 Provider Abstraction Layer) into market-microstructure intelligence
signals. It contains **no ML, no calibration, no thresholds, no decision
logic** — only deterministic transformations over point-in-time snapshots.

Public surface
--------------
- schema:               OddsRecord, MarketType, SnapshotType, Horizon
- timeseries:           MarketTimeSeriesBuilder
- drift_engine:         OddsDriftEngine
- clv_foundation:       CLVFoundation
- efficiency_signals:   MarketEfficiencyEngine
- integrity:            DataIntegrityLayer
- measurement_pipeline: MeasurementPipeline (orchestrator)

Design rules (inherited from PROJECT_MANIFEST):
- No target leakage. Every horizon bucket only uses data available *at or
  before* that horizon's cut-off time.
- Point-in-time only. Strict chronological ordering is enforced.
- Closing line is a *future placeholder*; CLV is reported as PENDING until a
  closing snapshot exists.
"""

from .schema import OddsRecord, MarketType, SnapshotType, Horizon, MarketKey

__all__ = [
    "OddsRecord",
    "MarketType",
    "SnapshotType",
    "Horizon",
    "MarketKey",
]
