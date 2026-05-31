"""Weather Multiplier — adjusts match probabilities based on environmental factors.

Rules:
- Rain/Snow: Increases unpredictability. Decreases Home Advantage. Favors Draw.
- High Wind (> 10 m/s): Disrupts passing games. Lowers goal expectation, increases Draw probability slightly.
- Extreme Temps (< 0 or > 35): Increases fatigue, can increase late goals or favor the physically stronger team.
"""

def apply_weather_multiplier(probs: dict, weather: dict) -> dict:
    """Adjusts H/D/A probabilities based on weather conditions.
    
    Args:
        probs: Dict with 'H', 'D', 'A' probability floats (summing to ~1.0)
        weather: Dict from weather_client
        
    Returns:
        Adjusted probs dict.
    """
    if not weather or weather.get("condition") == "Unknown":
        return probs

    h = probs.get("H", 0.33)
    d = probs.get("D", 0.33)
    a = probs.get("A", 0.33)

    is_raining = weather.get("is_raining", False)
    is_snowing = weather.get("is_snowing", False)
    wind = weather.get("wind_speed", 0.0)
    
    # 1. Rain and Snow increase unpredictability (pushes probs slightly towards the mean)
    # They specifically boost Draw chances because games become scrappy
    if is_snowing:
        d_boost = 0.05
        h -= (d_boost / 2)
        a -= (d_boost / 2)
        d += d_boost
    elif is_raining:
        d_boost = 0.03
        h -= (d_boost / 2)
        a -= (d_boost / 2)
        d += d_boost

    # 2. High wind heavily affects structured home attacks, favoring a scrappy away/draw performance.
    if wind >= 10.0:  # > 36 km/h
        h -= 0.02
        a += 0.01
        d += 0.01

    # Ensure constraints (no negative probs, sum to 1.0)
    h = max(0.01, h)
    d = max(0.01, d)
    a = max(0.01, a)
    
    total = h + d + a
    
    return {
        "H": round(h / total, 3),
        "D": round(d / total, 3),
        "A": round(a / total, 3)
    }
