"""Referee impact analysis — card frequency and strictness scoring."""


def referee_strictness(db, referee_name: str, league_code: str = None) -> dict:
    """Get referee strictness metrics.

    Returns:
        avg_yellows, avg_reds, strictness_score (0-1),
        match_count, total_cards_per_game
    """
    if league_code:
        ref = db.fetchone(
            "SELECT * FROM referees WHERE name=? AND league_code=?",
            (referee_name, league_code),
        )
    else:
        ref = db.fetchone(
            "SELECT * FROM referees WHERE name=?",
            (referee_name,),
        )

    if not ref:
        return {
            "avg_yellows": 4.0,  # Global average
            "avg_reds": 0.15,
            "strictness_score": 0.5,
            "match_count": 0,
            "cards_per_game": 4.15,
        }

    cards_pg = ref["avg_yellows"] + ref["avg_reds"] * 3  # Red = 3x impact
    # Normalize strictness: avg ~4 cards → 0.5, 8+ → 1.0, 0 → 0.0
    strictness = min(cards_pg / 8.0, 1.0)

    return {
        "avg_yellows": round(ref["avg_yellows"], 2),
        "avg_reds": round(ref["avg_reds"], 2),
        "strictness_score": round(strictness, 3),
        "match_count": ref["match_count"],
        "cards_per_game": round(cards_pg, 2),
    }


def calculate_all_strictness(db):
    """Recalculate strictness_score for all referees in database."""
    referees = db.fetchall("SELECT id, avg_yellows, avg_reds FROM referees")

    for ref in referees:
        cards = ref["avg_yellows"] + ref["avg_reds"] * 3
        strictness = min(cards / 8.0, 1.0)
        db.execute(
            "UPDATE referees SET strictness_score=? WHERE id=?",
            (round(strictness, 3), ref["id"]),
        )

    print(f"[OK] Strictness scores updated for {len(referees)} referees.")


def match_tension_factor(
    is_derby: bool = False,
    league_position_diff: int = 0,
    referee_strictness_score: float = 0.5,
) -> float:
    """Estimate how 'tense' a match will be.

    High tension = more cards, more disruption for technical teams.

    Returns:
        Tension factor 0.0 (calm) to 1.0 (explosive)
    """
    tension = 0.3  # Base tension

    if is_derby:
        tension += 0.3

    # Close league positions = more competitive
    if abs(league_position_diff) <= 3:
        tension += 0.15

    # Strict referee amplifies tension
    tension += referee_strictness_score * 0.25

    return min(tension, 1.0)
