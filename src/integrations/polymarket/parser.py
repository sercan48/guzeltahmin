"""
src/integrations/polymarket/parser.py — Polymarket API Response Parser

Converts raw Gamma API dicts into typed MarketInfo / MarketSnapshot objects.
Handles both binary (Yes/No) and categorical (Home/Draw/Away) market structures.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from src.integrations.base import MarketInfo, MarketSnapshot, OutcomeSnapshot

logger = logging.getLogger(__name__)

# Outcome label → canonical role mapping (case-insensitive)
_HOME_LABELS: frozenset[str] = frozenset({
    "home", "home win", "home team", "home wins", "1", "yes",
})
_DRAW_LABELS: frozenset[str] = frozenset({
    "draw", "draw/tie", "tie", "x",
})
_AWAY_LABELS: frozenset[str] = frozenset({
    "away", "away win", "away team", "away wins", "2", "no",
})


def classify_outcome(
    label: str,
    all_outcomes: list[str],
    home_team: str | None = None,
    away_team: str | None = None,
) -> str | None:
    """
    Map a raw Polymarket outcome label to HOME / DRAW / AWAY.

    Strategy (in order):
    1. Direct keyword match (e.g., "Home", "Draw", "Away", "Yes", "No")
    2. Team name containment match
    3. Positional fallback for 2-way markets (idx 0 = HOME, idx 1 = AWAY)
    4. Positional fallback for 3-way markets (idx 0/1/2 = HOME/DRAW/AWAY)
    """
    norm = label.lower().strip()

    if norm in _HOME_LABELS:
        return "HOME"
    if norm in _DRAW_LABELS:
        return "DRAW"
    if norm in _AWAY_LABELS:
        return "AWAY"

    if home_team and home_team.lower() in norm:
        return "HOME"
    if home_team and norm in home_team.lower():
        return "HOME"
    if away_team and away_team.lower() in norm:
        return "AWAY"
    if away_team and norm in away_team.lower():
        return "AWAY"

    # Positional fallbacks
    try:
        idx = all_outcomes.index(label)
    except ValueError:
        return None

    if len(all_outcomes) == 2:
        return "HOME" if idx == 0 else "AWAY"
    if len(all_outcomes) == 3:
        return ("HOME", "DRAW", "AWAY")[idx]

    return None


def _parse_json_field(raw: str | list | None) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


def _safe_float(val: object) -> float | None:
    try:
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_market_info(raw: dict) -> MarketInfo:
    """Convert a raw Gamma API market dict to MarketInfo."""
    active = raw.get("active", False)
    closed = raw.get("closed", False)
    resolved = raw.get("resolved", False)

    if resolved:
        status = "resolved"
    elif closed:
        status = "closed"
    elif active:
        status = "active"
    else:
        status = "unknown"

    return MarketInfo(
        provider="polymarket",
        event_id=str(raw.get("eventId") or raw.get("event_id") or ""),
        market_id=str(raw.get("id") or ""),
        question=str(raw.get("question") or raw.get("description") or ""),
        slug=str(raw.get("slug") or ""),
        status=status,
        matched_home="",
        matched_away="",
        match_date="",
    )


def parse_snapshot(
    raw: dict,
    clob_data: dict[str, tuple[float | None, float | None]] | None = None,
    source_type: str = "pre_match",
    home_team: str | None = None,
    away_team: str | None = None,
) -> MarketSnapshot | None:
    """
    Parse a Gamma API market dict into a MarketSnapshot.

    Args:
        raw:         Raw dict from Gamma API /markets/{id}
        clob_data:   {token_id: (best_bid, best_ask)} from ClobClient (optional)
        source_type: "pre_match" | "closing"
        home_team:   Internal fixture home team name (helps outcome classification)
        away_team:   Internal fixture away team name

    Returns:
        MarketSnapshot, or None if the market cannot be parsed.
    """
    market_id = str(raw.get("id") or "")
    if not market_id:
        return None

    outcomes = _parse_json_field(raw.get("outcomes"))
    prices_raw = _parse_json_field(raw.get("outcomePrices"))
    token_ids = _parse_json_field(raw.get("clobTokenIds"))

    if not outcomes or not prices_raw or len(outcomes) != len(prices_raw):
        return None

    home_prob = draw_prob = away_prob = None
    outcome_snapshots: list[OutcomeSnapshot] = []

    for i, (label, price_val) in enumerate(zip(outcomes, prices_raw)):
        mid = _safe_float(price_val)
        if mid is None:
            continue

        # CLOB bid/ask
        best_bid: float | None = None
        best_ask: float | None = None
        spread: float | None = None
        if clob_data and i < len(token_ids):
            tid = token_ids[i]
            ba = clob_data.get(tid)
            if ba:
                best_bid, best_ask = ba
                if best_bid is not None and best_ask is not None:
                    spread = round(best_ask - best_bid, 6)

        role = classify_outcome(label, outcomes, home_team, away_team)
        outcome_snapshots.append(OutcomeSnapshot(
            label=label,
            role=role,
            mid_price=round(mid, 6),
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
        ))

        if role == "HOME":
            home_prob = round(mid, 6)
        elif role == "DRAW":
            draw_prob = round(mid, 6)
        elif role == "AWAY":
            away_prob = round(mid, 6)

    return MarketSnapshot(
        provider="polymarket",
        market_id=market_id,
        home_prob=home_prob,
        draw_prob=draw_prob,
        away_prob=away_prob,
        outcomes=outcome_snapshots,
        volume_24h=_safe_float(raw.get("volumeNum") or raw.get("volume")),
        liquidity=_safe_float(raw.get("liquidityNum") or raw.get("liquidity")),
        open_interest=None,
        timestamp=datetime.now(timezone.utc).isoformat(),
        is_closing=(source_type == "closing"),
        source_type=source_type,
    )
