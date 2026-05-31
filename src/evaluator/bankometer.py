"""Banko match filter — identify highest confidence predictions."""

from typing import Optional


def bankometer(
    predictions: list[dict],
    top_percentile: float = 0.20,
    min_confidence: int = 70,
    min_margin: float = 3.0,
) -> list[dict]:
    """Filter predictions to the most reliable subset.

    Strategy: Take the top 20% (by confidence × margin) as 'Banko' picks.
    These are the predictions the model is most confident about
    AND where value exists.

    Args:
        predictions: Full list of match predictions
        top_percentile: Top N% to keep (default 20%)
        min_confidence: Minimum confidence threshold
        min_margin: Minimum value margin %

    Returns:
        Filtered and ranked list of banko picks
    """
    qualified = [
        p for p in predictions
        if p.get("confidence_score", 0) >= min_confidence
        and p.get("value_margin", 0) >= min_margin
    ]

    if not qualified:
        return []

    # Score = confidence × margin (weighted composite)
    for p in qualified:
        conf = p.get("confidence_score", 50)
        margin = p.get("value_margin", 0)
        max_prob = max(
            p.get("home_win_prob", 0),
            p.get("draw_prob", 0),
            p.get("away_win_prob", 0),
        )
        p["banko_score"] = conf * 0.4 + margin * 0.3 + max_prob * 100 * 0.3

    qualified.sort(key=lambda x: x["banko_score"], reverse=True)

    # Take top percentile
    n = max(1, int(len(qualified) * top_percentile))
    return qualified[:n]


def banko_summary(banko_picks: list[dict]) -> str:
    """Generate human-readable summary of banko picks."""
    if not banko_picks:
        return "Banko maç bulunamadı."

    lines = [
        "=" * 50,
        f"  BANKO MAÇLAR ({len(banko_picks)} adet)",
        "=" * 50,
    ]

    for i, p in enumerate(banko_picks, 1):
        home = p.get("home_team", "?")
        away = p.get("away_team", "?")
        result = p.get("predicted_result", "?")
        conf = p.get("confidence_score", 0)
        margin = p.get("value_margin", 0)

        result_label = {"H": f"{home} Kazanır", "D": "Beraberlik", "A": f"{away} Kazanır"}.get(result, "?")

        lines.append(f"\n  #{i} {home} vs {away}")
        lines.append(f"     Tahmin: {result_label}")
        lines.append(f"     Güven: {conf}/100 | Marj: %{margin:.1f}")
        lines.append(f"     Banko Skor: {p.get('banko_score', 0):.1f}")

    return "\n".join(lines)
