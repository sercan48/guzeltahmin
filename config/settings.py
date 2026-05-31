"""Global settings and configuration."""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Paths
ROOT_DIR = Path(__file__).parent.parent
DATA_DIR = ROOT_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
FIFA_DIR = DATA_DIR / "fifa"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_DIR = ROOT_DIR / "models"
CACHE_DIR = DATA_DIR / "cache"

for d in [RAW_DIR, FIFA_DIR, PROCESSED_DIR, MODELS_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Database
DB_BACKEND = os.getenv("DB_BACKEND", "sqlite")
SQLITE_PATH = ROOT_DIR / os.getenv("SQLITE_PATH", "data/guzel_tahmin.db")
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

# API Keys
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
FOOTBALL_DATA_ORG_KEY = os.getenv("FOOTBALL_DATA_ORG_KEY", "")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "")  # Premium channel
TELEGRAM_FREE_CHANNEL_ID = os.getenv("TELEGRAM_FREE_CHANNEL_ID", "")  # Free daily pick
TELEGRAM_VIP_LINK = os.getenv("TELEGRAM_VIP_LINK", "https://t.me/+placeholder_vip_link")
EXTRA_RSS_URLS = [
    x.strip() for x in os.getenv("EXTRA_RSS_URLS", "").split(",") if x.strip()
]
TELEGRAM_ADMIN_IDS = [
    int(x.strip()) for x in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",") if x.strip()
]

# Feature Flags
LIVE_BETTING_ENABLED = False  # Premium feature — yakında duyurulacak
SELF_LEARNING_ENABLED = True  # ML self-improvement aktif

# Football-Data.co.uk base URL
FOOTBALL_DATA_BASE_URL = "https://www.football-data.co.uk/mmz4281"

# Model
MODEL_PATH = MODELS_DIR / "xgb_model.pkl"
ENSEMBLE_PATH = MODELS_DIR / "ensemble"
RANDOM_SEED = 42
TEST_SIZE = 0.2

# Seasons to load (6 seasons for training + backtesting)
SEASONS = ["2526", "2425", "2324", "2223", "2122", "2021"]
SEASON_LABELS = {
    "2526": "2025-2026",
    "2425": "2024-2025",
    "2324": "2023-2024",
    "2223": "2022-2023",
    "2122": "2021-2022",
    "2021": "2020-2021",
}

# Backtest config — minimum 5 seasons walk-forward
BACKTEST_SEASONS = ["2021", "2122", "2223", "2324", "2425"]
LIVE_SEASON = "2526"

# Walk-forward folds
WALK_FORWARD_FOLDS = [
    {"train": ["2021"], "test": "2122"},
    {"train": ["2021", "2122"], "test": "2223"},
    {"train": ["2021", "2122", "2223"], "test": "2324"},
    {"train": ["2021", "2122", "2223", "2324"], "test": "2425"},
]
