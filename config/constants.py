"""Constants and weight coefficients for the prediction engine."""

# Team Strength Weights
SEASON_WEIGHT = 0.40
FORM_WEIGHT = 0.60
FORM_WINDOW = 5  # Last N matches

# Time Decay
TIME_DECAY_LAMBDA = 0.01  # ~70 day half-life

# Points
WIN_POINTS = 3
DRAW_POINTS = 1
LOSS_POINTS = 0

# Position-Based Player Impact Weights
POSITION_WEIGHTS = {
    "GK": {
        "primary": {"defending": 0.4},
        "secondary": {"physical": 0.3, "reflexes": 0.3},
        "team_weight": 0.12,
        "injury_multiplier": 2.0,
    },
    "DEF": {
        "primary": {"defending": 0.5},
        "secondary": {"physical": 0.3, "pace": 0.2},
        "team_weight": 0.25,
        "injury_multiplier": 1.3,
    },
    "MID": {
        "primary": {"passing": 0.4},
        "secondary": {"dribbling": 0.3, "shooting": 0.3},
        "team_weight": 0.35,
        "injury_multiplier": 1.0,
    },
    "FWD": {
        "primary": {"shooting": 0.5},
        "secondary": {"pace": 0.25, "dribbling": 0.25},
        "team_weight": 0.28,
        "injury_multiplier": 1.5,
    },
}

# Confidence Score Penalties
CONFIDENCE_PENALTIES = {
    "missing_player_data": -15,
    "missing_last5": -20,
    "low_h2h_data": -10,
    "missing_odds": -10,
    "uncertain_prediction": -15,
    "missing_xg": -8,
    "high_congestion": -5,
}

CONFIDENCE_BONUSES = {
    "rich_h2h_data": 5,
    "complete_data": 5,
    "xg_available": 8,
    "ensemble_agreement": 10,
}

# Value Bet Thresholds
VALUE_BET_THRESHOLDS = {
    "banko": {"margin": 5.0, "confidence": 75},
    "value": {"margin": 3.0, "confidence": 60},
}

# Feature Columns (40 features — 32 base + 5 summer features + 3 odds-derived)
FEATURE_COLUMNS = [
    "home_team_strength",
    "away_team_strength",
    "home_form_last5",
    "away_form_last5",
    "home_attack_rating",
    "home_defense_rating",
    "away_attack_rating",
    "away_defense_rating",
    "home_goals_scored_avg",
    "home_goals_conceded_avg",
    "away_goals_scored_avg",
    "away_goals_conceded_avg",
    "h2h_home_winrate",
    "h2h_goals_avg",
    "home_advantage_factor",
    "referee_strictness",
    "home_squad_value",
    "away_squad_value",
    "form_momentum_diff",
    "league_position_diff",
    "is_derby",
    "red_card_risk",
    "home_xg_efficiency",
    "away_xg_efficiency",
    # New features (8)
    "home_xg_avg",
    "away_xg_avg",
    "home_xg_overperformance",
    "away_xg_overperformance",
    "home_congestion_score",
    "away_congestion_score",
    "congestion_advantage",
    "clean_sheet_rate_diff",
    # Summer features (5) - natively learned by ML model
    "travel_distance_km",
    "is_artificial_pitch",
    "cup_rotation_fatigue",
    "dp_presence",
    "extreme_humidity",
    # Odds-derived features (3) — market intelligence
    "implied_home_prob",
    "implied_away_prob",
    "implied_draw_prob",
]

LABEL_MAP = {"H": 0, "D": 1, "A": 2}
LABEL_MAP_INV = {v: k for k, v in LABEL_MAP.items()}

# Model Types
MODEL_TYPES = ["xgboost", "lightgbm", "poisson", "ensemble"]

# Metric Weights for composite score
METRIC_WEIGHTS = {
    "accuracy": 0.30,
    "roi": 0.30,
    "brier_score": 0.20,
    "log_loss": 0.10,
    "yield_pct": 0.10,
}

# Football-Data.co.uk Column Mapping
CSV_COLUMN_MAP = {
    "Div": "league_code",
    "League": "league_code",
    "Date": "date",
    "HomeTeam": "home_team",
    "Home": "home_team",
    "AwayTeam": "away_team",
    "Away": "away_team",
    "FTHG": "ft_home_goals",
    "HG": "ft_home_goals",
    "FTAG": "ft_away_goals",
    "AG": "ft_away_goals",
    "FTR": "ft_result",
    "Res": "ft_result",
    "HTHG": "ht_home_goals",
    "HTAG": "ht_away_goals",
    "HTR": "ht_result",
    "HS": "home_shots",
    "AS": "away_shots",
    "HST": "home_shots_target",
    "AST": "away_shots_target",
    "HC": "home_corners",
    "AC": "away_corners",
    "HF": "home_fouls",
    "AF": "away_fouls",
    "HY": "home_yellows",
    "AY": "away_yellows",
    "HR": "home_reds",
    "AR": "away_reds",
    "Referee": "referee",
    "B365H": "b365_home",
    "B365D": "b365_draw",
    "B365A": "b365_away",
    "B365CH": "b365_home",
    "B365CD": "b365_draw",
    "B365CA": "b365_away",
    "PSH": "pin_home",
    "PSD": "pin_draw",
    "PSA": "pin_away",
    "PSCH": "pin_home",
    "PSCD": "pin_draw",
    "PSCA": "pin_away",
    "BbAv>2.5": "avg_over25",
    "BbAv<2.5": "avg_under25",
}

# Fuzzy Matching
FUZZY_MATCH_THRESHOLD = 80

# Subscription Plans
SUBSCRIPTION_PLANS = {
    "free": {"name": "Ücretsiz", "daily_predictions": 3, "coupon_limit": 1},
    "premium": {"name": "Premium", "daily_predictions": -1, "coupon_limit": -1},
    "vip": {"name": "VIP", "daily_predictions": -1, "coupon_limit": -1, "priority_support": True},
}
