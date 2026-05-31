"""Centralized Venue Registry for Summer League Teams.

Stores coordinate locations (latitude, longitude) and stadium pitch types
to compute travel distances and pitch advantages deterministically.
"""

# Static dictionary mapping team names to their coordinates and pitch types.
# Keys are normalized (case-insensitive, stripped of common suffixes).
VENUE_REGISTRY = {
    # --- NORWAY ELITESERIEN ---
    "bodo/glimt": {"lat": 67.280, "lon": 14.404, "pitch": "ARTIFICIAL"},
    "tromso": {"lat": 69.648, "lon": 18.955, "pitch": "ARTIFICIAL"},
    "molde": {"lat": 62.738, "lon": 7.161, "pitch": "ARTIFICIAL"},
    "aalesund": {"lat": 62.470, "lon": 6.160, "pitch": "ARTIFICIAL"},
    "kristiansund": {"lat": 63.111, "lon": 7.728, "pitch": "ARTIFICIAL"},
    "hamkam": {"lat": 60.795, "lon": 11.078, "pitch": "NATURAL"},
    "rosenborg": {"lat": 63.430, "lon": 10.395, "pitch": "NATURAL"},
    "brann": {"lat": 60.372, "lon": 5.358, "pitch": "NATURAL"},
    "lillestrom": {"lat": 59.956, "lon": 11.050, "pitch": "NATURAL"},
    "kfum oslo": {"lat": 59.897, "lon": 10.793, "pitch": "ARTIFICIAL"},
    "start": {"lat": 58.146, "lon": 8.013, "pitch": "ARTIFICIAL"},
    "valerenga": {"lat": 59.922, "lon": 10.807, "pitch": "ARTIFICIAL"},
    "sandefjord": {"lat": 59.139, "lon": 10.218, "pitch": "NATURAL"},
    "fredrikstad": {"lat": 59.211, "lon": 10.938, "pitch": "NATURAL"},
    "sarpsborg 08": {"lat": 59.284, "lon": 11.118, "pitch": "ARTIFICIAL"},
    "viking": {"lat": 58.962, "lon": 5.731, "pitch": "ARTIFICIAL"},

    # --- BRAZIL SERIE A ---
    "atletico-mg": {"lat": -19.923, "lon": -43.945, "pitch": "NATURAL"},
    "palmeiras": {"lat": -23.527, "lon": -46.679, "pitch": "ARTIFICIAL"},
    "coritiba": {"lat": -25.421, "lon": -49.260, "pitch": "NATURAL"},
    "bragantino": {"lat": -22.957, "lon": -46.541, "pitch": "NATURAL"},
    "internacional": {"lat": -30.065, "lon": -51.236, "pitch": "NATURAL"},
    "athletico-pr": {"lat": -25.448, "lon": -49.290, "pitch": "ARTIFICIAL"},
    "vitoria": {"lat": -12.936, "lon": -38.431, "pitch": "NATURAL"},
    "remo": {"lat": -1.455, "lon": -48.476, "pitch": "NATURAL"},
    "fluminense": {"lat": -22.912, "lon": -43.230, "pitch": "NATURAL"},
    "gremio": {"lat": -30.027, "lon": -51.163, "pitch": "NATURAL"},
    "chapecoense-sc": {"lat": -27.104, "lon": -52.628, "pitch": "NATURAL"},
    "santos": {"lat": -23.975, "lon": -46.343, "pitch": "NATURAL"},
    "corinthians": {"lat": -23.545, "lon": -46.474, "pitch": "NATURAL"},
    "bahia": {"lat": -12.978, "lon": -38.504, "pitch": "NATURAL"},
    "sao paulo": {"lat": -23.599, "lon": -46.720, "pitch": "NATURAL"},
    "flamengo rj": {"lat": -22.912, "lon": -43.230, "pitch": "NATURAL"},
    "flamengo": {"lat": -22.912, "lon": -43.230, "pitch": "NATURAL"},
    "mirassol": {"lat": -20.817, "lon": -49.516, "pitch": "NATURAL"},
    "vasco": {"lat": -22.891, "lon": -43.228, "pitch": "NATURAL"},
    "botafogo rj": {"lat": -22.893, "lon": -43.292, "pitch": "NATURAL"},
    "botafogo": {"lat": -22.893, "lon": -43.292, "pitch": "NATURAL"},
    "cruzeiro": {"lat": -19.965, "lon": -43.916, "pitch": "NATURAL"},

    # --- SWEDEN ALLSVENSKAN ---
    "malmo ff": {"lat": 55.585, "lon": 12.986, "pitch": "NATURAL"},
    "malmo": {"lat": 55.585, "lon": 12.986, "pitch": "NATURAL"},
    "djurgarden": {"lat": 59.343, "lon": 18.084, "pitch": "ARTIFICIAL"},
    "hammarby": {"lat": 59.343, "lon": 18.084, "pitch": "ARTIFICIAL"},
    "aik": {"lat": 59.370, "lon": 17.999, "pitch": "NATURAL"},
    "ifk goteborg": {"lat": 57.708, "lon": 11.979, "pitch": "NATURAL"},
    "goteborg": {"lat": 57.708, "lon": 11.979, "pitch": "NATURAL"},
    "elfsborg": {"lat": 57.726, "lon": 12.946, "pitch": "ARTIFICIAL"},
    "hacken": {"lat": 57.721, "lon": 11.939, "pitch": "ARTIFICIAL"},
    "norrkoping": {"lat": 58.584, "lon": 16.173, "pitch": "ARTIFICIAL"},
    "kalmar": {"lat": 56.678, "lon": 16.347, "pitch": "NATURAL"},
    "halmstad": {"lat": 56.685, "lon": 12.866, "pitch": "NATURAL"},
    "mjallby": {"lat": 56.021, "lon": 14.686, "pitch": "NATURAL"},
    "sirius": {"lat": 59.862, "lon": 17.653, "pitch": "ARTIFICIAL"},
    "brommapojkarna": {"lat": 59.341, "lon": 17.886, "pitch": "ARTIFICIAL"},
    "vesteras": {"lat": 59.622, "lon": 16.536, "pitch": "ARTIFICIAL"},
    "vasteras": {"lat": 59.622, "lon": 16.536, "pitch": "ARTIFICIAL"},
    "gais": {"lat": 57.708, "lon": 11.979, "pitch": "NATURAL"},
    "varnamo": {"lat": 57.182, "lon": 14.053, "pitch": "NATURAL"},

    # --- FINLAND VEIKKAUSLIIGA ---
    "hjk helsinki": {"lat": 60.187, "lon": 24.922, "pitch": "ARTIFICIAL"},
    "hjk": {"lat": 60.187, "lon": 24.922, "pitch": "ARTIFICIAL"},
    "kups": {"lat": 62.892, "lon": 27.695, "pitch": "ARTIFICIAL"},
    "sjk": {"lat": 62.785, "lon": 22.846, "pitch": "ARTIFICIAL"},
    "ilves": {"lat": 61.498, "lon": 23.774, "pitch": "ARTIFICIAL"},
    "haka": {"lat": 61.272, "lon": 24.029, "pitch": "ARTIFICIAL"},
    "inter turku": {"lat": 60.443, "lon": 22.289, "pitch": "ARTIFICIAL"},
    "vps": {"lat": 63.090, "lon": 21.626, "pitch": "ARTIFICIAL"},
    "lahti": {"lat": 60.983, "lon": 25.632, "pitch": "NATURAL"},
    "mariehamn": {"lat": 60.102, "lon": 19.944, "pitch": "NATURAL"},
    "ifk mariehamn": {"lat": 60.102, "lon": 19.944, "pitch": "NATURAL"},
    "ac oulu": {"lat": 65.012, "lon": 25.471, "pitch": "ARTIFICIAL"},
    "gnistan": {"lat": 60.236, "lon": 24.957, "pitch": "ARTIFICIAL"},
    "eif": {"lat": 59.974, "lon": 23.435, "pitch": "NATURAL"},
}

def get_team_venue(team_name: str) -> dict:
    """Normalize team name and lookup coordinates and pitch type.
    
    Returns:
        dict: {"lat": float, "lon": float, "pitch": str} or None if not found.
    """
    if not team_name:
        return None
    normalized = team_name.lower().strip()
    
    # Direct lookup
    if normalized in VENUE_REGISTRY:
        return VENUE_REGISTRY[normalized]
        
    # Strip common suffixes/prefixes and retry
    for suffix in [" fc", " sk", " fk", " as", " rj", " pr", " sp", " mg", " sc", " ff", " ifk"]:
        if normalized.endswith(suffix):
            normalized = normalized[:-len(suffix)].strip()
            if normalized in VENUE_REGISTRY:
                return VENUE_REGISTRY[normalized]
                
    # Search for partial match
    for k, v in VENUE_REGISTRY.items():
        if k in normalized or normalized in k:
            return v
            
    return None
