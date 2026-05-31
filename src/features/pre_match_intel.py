"""Pre-Match Intelligence Layer — Real-time prediction evolution.

Provides multi-stage prediction updates as match approaches:
  7 days  → Base model prediction (features only)
  48h     → + Opening odds integrated
  24h     → + Injury/lineup updates
  6h      → + Odds movement detection (steam moves)
  1h      → + Confirmed lineup + final odds = FINAL PREDICTION
"""

import logging
from datetime import datetime, timedelta
from dataclasses import dataclass, field

from src.db.base import get_backend

logger = logging.getLogger(__name__)


@dataclass
class OddsMovement:
    """Track odds movement direction and magnitude."""
    opening_home: float = 0
    opening_draw: float = 0
    opening_away: float = 0
    current_home: float = 0
    current_draw: float = 0
    current_away: float = 0

    @property
    def home_drift(self) -> float:
        """Positive = odds lengthened (less likely), Negative = shortened."""
        if not self.opening_home:
            return 0
        return (self.current_home - self.opening_home) / self.opening_home

    @property
    def away_drift(self) -> float:
        if not self.opening_away:
            return 0
        return (self.current_away - self.opening_away) / self.opening_away

    @property
    def steam_move(self) -> str:
        """Detect significant money movement."""
        if self.home_drift < -0.08:
            return "HOME_STEAM"
        elif self.away_drift < -0.08:
            return "AWAY_STEAM"
        elif self.home_drift > 0.10:
            return "HOME_DRIFT_OUT"
        elif self.away_drift > 0.10:
            return "AWAY_DRIFT_OUT"
        return "STABLE"

    @property
    def direction_label(self) -> str:
        labels = {
            "HOME_STEAM": "Ev sahibine para akisi",
            "AWAY_STEAM": "Deplasmana para akisi",
            "HOME_DRIFT_OUT": "Ev sahibinden uzaklasma",
            "AWAY_DRIFT_OUT": "Deplasmandan uzaklasma",
            "STABLE": "Stabil",
        }
        return labels.get(self.steam_move, "Bilinmiyor")


@dataclass
class PredictionSnapshot:
    """Single point-in-time prediction."""
    timestamp: datetime
    stage: str
    h_prob: float
    d_prob: float
    a_prob: float
    confidence: float
    trigger: str = ""

    @property
    def predicted_result(self) -> str:
        probs = {"H": self.h_prob, "D": self.d_prob, "A": self.a_prob}
        return max(probs, key=probs.get)


@dataclass
class PreMatchIntel:
    """Full pre-match intelligence for a match."""
    match_id: int
    home_team: str
    away_team: str
    match_date: datetime
    snapshots: list = field(default_factory=list)
    odds_movement: OddsMovement = field(default_factory=OddsMovement)
    lineup_confirmed: bool = False
    key_absences: list = field(default_factory=list)
    checklist: list = field(default_factory=list)

    def add_snapshot(self, stage: str, h_prob: float, d_prob: float,
                     a_prob: float, confidence: float, trigger: str = ""):
        self.snapshots.append(PredictionSnapshot(
            timestamp=datetime.now(),
            stage=stage, h_prob=h_prob, d_prob=d_prob,
            a_prob=a_prob, confidence=confidence, trigger=trigger,
        ))

    @property
    def latest(self) -> PredictionSnapshot:
        return self.snapshots[-1] if self.snapshots else None

    @property
    def prediction_evolved(self) -> bool:
        """Did the predicted result change across snapshots?"""
        if len(self.snapshots) < 2:
            return False
        return self.snapshots[0].predicted_result != self.snapshots[-1].predicted_result

    @property
    def confidence_trend(self) -> str:
        if len(self.snapshots) < 2:
            return "STABLE"
        delta = self.snapshots[-1].confidence - self.snapshots[0].confidence
        if delta > 5:
            return "RISING"
        elif delta < -5:
            return "FALLING"
        return "STABLE"


def build_pre_match_checklist(intel: PreMatchIntel) -> list:
    """Generate context-aware pre-match checklist."""
    items = []

    # Always check lineups
    items.append({
        "item": "Kadro aciklamasi",
        "timing": "Mac oncesi 1 saat",
        "status": "confirmed" if intel.lineup_confirmed else "pending",
        "impact": "high",
    })

    # Key absences
    if intel.key_absences:
        for player in intel.key_absences:
            items.append({
                "item": f"{player} durumu",
                "timing": "Mac gunu",
                "status": "pending",
                "impact": "high",
            })

    # Odds movement
    if intel.odds_movement.steam_move != "STABLE":
        items.append({
            "item": f"Oran hareketi: {intel.odds_movement.direction_label}",
            "timing": "Son 6 saat",
            "status": "alert",
            "impact": "medium",
        })

    # Weather (if outdoor venue)
    items.append({
        "item": "Hava durumu kontrolu",
        "timing": "Mac gunu",
        "status": "pending",
        "impact": "low",
    })

    # Value bet check
    items.append({
        "item": "Son oran kontrolu (value hala gecerli mi?)",
        "timing": "Mac oncesi 30dk",
        "status": "pending",
        "impact": "high",
    })

    return items


def detect_value_bet(model_prob: float, market_odds: float,
                     threshold: float = 0.03) -> dict:
    """Detect value bet opportunity.

    Value exists when model probability > implied market probability.
    """
    if market_odds <= 1.01:
        return {"is_value": False, "margin_pct": 0, "edge": "NONE"}

    market_prob = 1.0 / market_odds
    margin = model_prob - market_prob

    if margin > threshold:
        kelly = margin / (market_odds - 1) if market_odds > 1 else 0
        return {
            "is_value": True,
            "margin_pct": round(margin * 100, 1),
            "edge": "POSITIVE",
            "kelly_fraction": round(min(kelly, 0.05), 4),
            "recommended_stake": "bankroll %2-3",
        }
    elif margin < -threshold:
        return {
            "is_value": False,
            "margin_pct": round(margin * 100, 1),
            "edge": "NEGATIVE",
            "kelly_fraction": 0,
            "recommended_stake": "OYNAMA",
        }

    return {
        "is_value": False,
        "margin_pct": round(margin * 100, 1),
        "edge": "NEUTRAL",
        "kelly_fraction": 0,
        "recommended_stake": "dusuk stake",
    }


def generate_live_betting_guide(prediction: dict, odds_movement: OddsMovement) -> list:
    """Generate conditional live betting recommendations."""
    guides = []
    h_prob = prediction.get("h_prob", prediction.get("home_win_prob", 0.33))
    a_prob = prediction.get("a_prob", prediction.get("away_win_prob", 0.33))
    predicted = prediction.get("predicted_result", "H")

    # Pre-match
    if h_prob + prediction.get("d_prob", prediction.get("draw_prob", 0.33)) > 0.55:
        guides.append({
            "timing": "MAC ONCESI",
            "bet": f"Cifte Sans 1X",
            "condition": "Her kosulda gecerli",
            "stop_loss": None,
            "confidence": "YUKSEK",
        })

    # 15 min live
    if predicted == "H":
        guides.append({
            "timing": "CANLI 15dk",
            "bet": f"Ev Sahibi ML",
            "condition": "Skor 0-0 ve ev sahibi baskili ise",
            "stop_loss": "Deplasman 1-0 one gecerse STOP",
            "confidence": "ORTA",
        })
    elif predicted == "A":
        guides.append({
            "timing": "CANLI 15dk",
            "bet": f"Deplasman ML",
            "condition": "Skor 0-0 ve deplasman kontrol ediyorsa",
            "stop_loss": "Ev sahibi 1-0 one gecerse STOP",
            "confidence": "ORTA",
        })

    # 60 min live
    over25 = prediction.get("over25_prob", 0.5)
    if over25 < 0.45:
        guides.append({
            "timing": "CANLI 60dk",
            "bet": "Alt 2.5 gol",
            "condition": "Skor 0-0 veya 1-0 ise",
            "stop_loss": "2. gol atilirsa STOP",
            "confidence": "ORTA-YUKSEK",
        })
    elif over25 > 0.65:
        guides.append({
            "timing": "CANLI 45dk",
            "bet": "Ust 2.5 gol",
            "condition": "Ilk yaridaki tempo yuksekse",
            "stop_loss": "IY 0-0 ve tempo dusukse STOP",
            "confidence": "ORTA",
        })

    # What to AVOID
    avoid_reasons = []
    if odds_movement.steam_move == "HOME_DRIFT_OUT":
        avoid_reasons.append("Ev sahibi orani uzaklasiyor — piyasa guvenmiyor")
    if odds_movement.steam_move == "AWAY_DRIFT_OUT":
        avoid_reasons.append("Deplasman orani uzaklasiyor — piyasa guvenmiyor")

    if avoid_reasons:
        guides.append({
            "timing": "KACINILACAK",
            "bet": "Oran kayan yone oynama",
            "condition": " + ".join(avoid_reasons),
            "stop_loss": None,
            "confidence": "UYARI",
        })

    return guides


def format_prediction_evolution(intel: PreMatchIntel) -> str:
    """Format prediction evolution timeline for Telegram."""
    lines = [
        "TAHMIN EVRIMI",
        "=" * 28,
        f"{intel.home_team} vs {intel.away_team}",
        "",
    ]

    for snap in intel.snapshots:
        result = snap.predicted_result
        result_label = {"H": "Ev", "D": "Bere", "A": "Dep"}.get(result, "?")
        prob = max(snap.h_prob, snap.d_prob, snap.a_prob)
        marker = ">>>" if snap == intel.latest else "   "
        lines.append(
            f"{marker} {snap.stage}: {result_label} %{prob*100:.0f} "
            f"(G:{snap.confidence:.0f}/10)"
        )
        if snap.trigger:
            lines.append(f"      Sebep: {snap.trigger}")

    if intel.prediction_evolved:
        lines.append(f"\nTahmin degisti! {intel.snapshots[0].predicted_result} -> {intel.latest.predicted_result}")
    else:
        lines.append(f"\nTahmin sabit: {intel.latest.predicted_result}")

    trend = intel.confidence_trend
    trend_icons = {"RISING": "Yukseliyor", "FALLING": "Dusuyor", "STABLE": "Sabit"}
    lines.append(f"Guven trendi: {trend_icons.get(trend, trend)}")

    return "\n".join(lines)


def format_live_guide(guides: list) -> str:
    """Format live betting guide for Telegram."""
    lines = ["CANLI BAHIS REHBERI", "=" * 28, ""]

    for g in guides:
        timing = g["timing"]
        if g["confidence"] == "UYARI":
            icon = "[!]"
        elif g["confidence"] == "YUKSEK":
            icon = "[+]"
        else:
            icon = "[ ]"

        lines.append(f"{icon} {timing}: {g['bet']}")
        lines.append(f"    Kosul: {g['condition']}")
        if g.get("stop_loss"):
            lines.append(f"    STOP: {g['stop_loss']}")
        lines.append("")

    return "\n".join(lines)


def format_checklist(items: list) -> str:
    """Format pre-match checklist for Telegram."""
    lines = ["MAC ONCESI CHECKLIST", "=" * 28, ""]

    for item in items:
        status_icons = {
            "confirmed": "[x]",
            "pending": "[ ]",
            "alert": "[!]",
        }
        icon = status_icons.get(item["status"], "[ ]")
        impact = {"high": "***", "medium": "**", "low": "*"}.get(item["impact"], "")
        lines.append(f"{icon} {item['item']} {impact}")
        lines.append(f"    Zamanlama: {item['timing']}")

    return "\n".join(lines)
