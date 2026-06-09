"""M6 — Paper signal Telegram presentation & explainability layer.

PRESENTATION ONLY. This module formats already-computed signals into
human-readable Telegram messages and decides channel *routing as metadata*. It
does NOT:
  - change any prediction / R1.2 / R1.3 / Truth Store / orchestrator logic
  - add bankroll, Kelly, or stake sizing
  - send real Telegram messages or place real bets

It is a new, additive module; the existing ``app/bot/formatters.py`` is left
untouched. Paper mode only.

Inputs it can render:
  - ``TruthAdjustedEdge`` (M3.2): full explainability (raw edge, truth discount,
    final edge, provenance).
  - ``PaperSignal`` (M5): the published summary; renders the final edge with
    provenance shown as UNKNOWN (decomposition not carried on PaperSignal).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Dict, List, Optional, Tuple


class Channel(str, Enum):
    VIP = "VIP"
    STANDARD = "STANDARD"
    MONITORING = "MONITORING"
    NONE = "NONE"          # not published


# Tier -> channel routing (metadata only; no real sending here)
_TIER_ROUTING: Dict[str, Channel] = {
    "TIER_S": Channel.VIP,
    "TIER_A": Channel.VIP,
    "TIER_B": Channel.STANDARD,
    "TIER_C": Channel.MONITORING,
    "REJECT": Channel.NONE,
}

_STRONG_TRUTH_CONF = 0.70
_SHARP_PRESENT_DISCOUNT = 0.70   # sharp_consensus_discount proxy for "sharp present"


def route(tier: str) -> Channel:
    return _TIER_ROUTING.get(tier, Channel.NONE)


@dataclass
class SignalView:
    """Presentation view: everything the message needs, nothing it doesn't."""
    match_id: str
    market: str
    selection: str
    tier: str
    final_edge: float
    truth_confidence: float
    provenance: str
    timestamp: str
    confidence: float = 0.0
    raw_edge: Optional[float] = None
    truth_discount: Optional[float] = None
    sharp_discount: Optional[float] = None   # sharp_consensus_discount (for "why")

    @classmethod
    def from_adjusted(cls, adj, confidence: float = 0.0, timestamp: str = "") -> "SignalView":
        return cls(
            match_id=adj.match_id, market=adj.market, selection=adj.selection,
            tier=adj.tier_after, final_edge=adj.edge_after_truth,
            truth_confidence=adj.truth_confidence, provenance=adj.provenance,
            timestamp=timestamp, confidence=confidence,
            raw_edge=adj.edge_before_truth, truth_discount=adj.truth_discount,
            sharp_discount=adj.sharp_consensus_discount,
        )

    @classmethod
    def from_paper(cls, ps) -> "SignalView":
        return cls(
            match_id=ps.match_id, market=ps.market, selection=ps.selection,
            tier=ps.tier, final_edge=ps.edge_score, truth_confidence=ps.truth_confidence,
            provenance="UNKNOWN", timestamp=ps.timestamp, confidence=ps.confidence,
        )


@dataclass
class FormatterMetrics:
    formatted_messages: int = 0
    rejected_messages: int = 0
    routing_distribution: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _num(x: Optional[float], nd: int = 2) -> str:
    return "n/a" if x is None else f"{x:.{nd}f}"


class PaperSignalFormatter:
    """Formats signal views into Telegram text + routing metadata. No sending."""

    def __init__(self) -> None:
        self.metrics = FormatterMetrics()

    # -- routing + suppression ---------------------------------------------
    def format_and_route(self, view: SignalView) -> Tuple[Optional[str], Channel]:
        """Returns (message | None, channel). REJECT -> suppressed (no message)."""
        channel = route(view.tier)
        if channel == Channel.NONE:
            self.metrics.rejected_messages += 1
            return None, channel
        msg = self.format(view)
        self.metrics.formatted_messages += 1
        self.metrics.routing_distribution[channel.value] = \
            self.metrics.routing_distribution.get(channel.value, 0) + 1
        return msg, channel

    # -- message rendering --------------------------------------------------
    def format(self, view: SignalView) -> str:
        lines: List[str] = []
        lines.append(f"⚽ {view.match_id} — {view.market}")
        lines.append(f"PICK: {view.selection}")
        lines.append("")
        # explainability block (required fields)
        lines.append(f"Tier: {view.tier.replace('TIER_', '')}")
        lines.append(f"Edge: {_pct(view.final_edge)}")
        lines.append(f"Confidence: {_num(view.confidence)}")
        lines.append(f"Truth Confidence: {_num(view.truth_confidence)}")
        lines.append(f"Source: {view.provenance}")
        lines.append(f"Time: {view.timestamp}")
        lines.append("")
        # edge decomposition (M3.2)
        lines.append("Edge decomposition:")
        lines.append(f"  Raw Edge: {_pct(view.raw_edge)}")
        lines.append(f"  Truth Discount: {_num(view.truth_discount)}")
        lines.append(f"  Final Edge: {_pct(view.final_edge)}")
        # provenance warning
        warning = self._provenance_warning(view.provenance)
        if warning:
            lines.append("")
            lines.append(warning)
        # transparency
        lines.append("")
        lines.append("WHY THIS PICK")
        for reason in self._why(view):
            lines.append(f"• {reason}")
        return "\n".join(lines)

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _provenance_warning(provenance: str) -> Optional[str]:
        if provenance == "RECONSTRUCTED":
            return "⚠️ Reconstructed market truth. Use with caution."
        if provenance == "PARTIAL":
            return "⚠️ Partial market truth. Use with caution."
        return None

    def _why(self, view: SignalView) -> List[str]:
        reasons: List[str] = []
        if view.raw_edge is None or view.raw_edge > 0:
            reasons.append("Model probability exceeds market probability")
        if view.truth_confidence >= _STRONG_TRUTH_CONF:
            reasons.append("Strong truth confidence")
        if view.sharp_discount is not None and view.sharp_discount >= _SHARP_PRESENT_DISCOUNT:
            reasons.append("Sharp consensus present")
        # an emitted signal already cleared the lifecycle state gate
        reasons.append("No lifecycle restrictions active")
        return reasons
