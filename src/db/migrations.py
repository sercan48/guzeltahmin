"""Database schema creation and migration — v4."""

SCHEMA_VERSION = 11

TABLES = {
    "schema_version": """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "teams": """
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            aliases TEXT DEFAULT '[]',
            league_code TEXT NOT NULL,
            country TEXT,
            style_score REAL DEFAULT 0.5,
            squad_value REAL DEFAULT 0.0,
            avg_player_value REAL DEFAULT 0.0,
            tier INTEGER DEFAULT 3,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "players": """
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            team_id INTEGER REFERENCES teams(id),
            position TEXT CHECK(position IN ('GK', 'DEF', 'MID', 'FWD')),
            fifa_overall INTEGER DEFAULT 0,
            fifa_pace INTEGER DEFAULT 0,
            fifa_shooting INTEGER DEFAULT 0,
            fifa_passing INTEGER DEFAULT 0,
            fifa_dribbling INTEGER DEFAULT 0,
            fifa_defending INTEGER DEFAULT 0,
            fifa_physical INTEGER DEFAULT 0,
            market_value REAL DEFAULT 0.0,
            importance_score REAL DEFAULT 0.0,
            UNIQUE(name, team_id)
        )
    """,
    "matches": """
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE NOT NULL,
            league_code TEXT NOT NULL,
            season TEXT NOT NULL,
            season_label TEXT,
            home_team_id INTEGER NOT NULL REFERENCES teams(id),
            away_team_id INTEGER NOT NULL REFERENCES teams(id),
            ft_home_goals INTEGER,
            ft_away_goals INTEGER,
            ft_result TEXT CHECK(ft_result IN ('H', 'D', 'A')),
            ht_home_goals INTEGER,
            ht_away_goals INTEGER,
            home_shots INTEGER DEFAULT 0,
            away_shots INTEGER DEFAULT 0,
            home_shots_target INTEGER DEFAULT 0,
            away_shots_target INTEGER DEFAULT 0,
            home_corners INTEGER DEFAULT 0,
            away_corners INTEGER DEFAULT 0,
            home_fouls INTEGER DEFAULT 0,
            away_fouls INTEGER DEFAULT 0,
            home_yellows INTEGER DEFAULT 0,
            away_yellows INTEGER DEFAULT 0,
            home_reds INTEGER DEFAULT 0,
            away_reds INTEGER DEFAULT 0,
            referee TEXT,
            home_xg REAL,
            away_xg REAL,
            importance TEXT DEFAULT 'normal',
            UNIQUE(date, home_team_id, away_team_id)
        )
    """,
    "odds": """
        CREATE TABLE IF NOT EXISTS odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            bookmaker TEXT NOT NULL,
            home_odds REAL,
            draw_odds REAL,
            away_odds REAL,
            over25_odds REAL,
            under25_odds REAL,
            UNIQUE(match_id, bookmaker)
        )
    """,
    "predictions": """
        CREATE TABLE IF NOT EXISTS predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            analysis_date DATE,
            model_type TEXT DEFAULT 'ensemble',
            home_win_prob REAL,
            draw_prob REAL,
            away_win_prob REAL,
            confidence_score INTEGER DEFAULT 0,
            is_value_bet INTEGER DEFAULT 0,
            value_margin REAL DEFAULT 0.0,
            predicted_result TEXT,
            actual_result TEXT,
            top_1_pick TEXT,
            top_1_type TEXT,
            top_1_success INTEGER DEFAULT -1,
            top_2_pick TEXT,
            top_2_type TEXT,
            top_2_success INTEGER DEFAULT -1,
            was_posted INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, analysis_date, model_type)
        )
    """,
    "referees": """
        CREATE TABLE IF NOT EXISTS referees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            league_code TEXT,
            avg_yellows REAL DEFAULT 0.0,
            avg_reds REAL DEFAULT 0.0,
            avg_fouls REAL DEFAULT 0.0,
            strictness_score REAL DEFAULT 0.5,
            match_count INTEGER DEFAULT 0,
            UNIQUE(name, league_code)
        )
    """,
    # ─── v4 New Tables ────────────────────────────────────────
    "model_experiments": """
        CREATE TABLE IF NOT EXISTS model_experiments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_type TEXT NOT NULL,
            params_json TEXT,
            feature_set TEXT,
            train_seasons TEXT,
            test_season TEXT,
            accuracy REAL,
            brier_score REAL,
            log_loss REAL,
            roi REAL,
            yield_pct REAL,
            composite_score REAL,
            is_active INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "backtest_results": """
        CREATE TABLE IF NOT EXISTS backtest_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER REFERENCES model_experiments(id),
            match_id INTEGER REFERENCES matches(id),
            predicted_result TEXT,
            actual_result TEXT,
            home_prob REAL,
            draw_prob REAL,
            away_prob REAL,
            confidence REAL,
            bet_odds REAL,
            roi_contribution REAL,
            correct INTEGER
        )
    """,
    "match_xg": """
        CREATE TABLE IF NOT EXISTS match_xg (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER REFERENCES matches(id),
            home_xg REAL,
            away_xg REAL,
            home_shots INTEGER,
            away_shots INTEGER,
            home_shots_on_target INTEGER,
            away_shots_on_target INTEGER,
            source TEXT DEFAULT 'understat',
            UNIQUE(match_id, source)
        )
    """,
    "fixture_density": """
        CREATE TABLE IF NOT EXISTS fixture_density (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER REFERENCES teams(id),
            match_id INTEGER REFERENCES matches(id),
            days_since_last_match INTEGER,
            matches_last_14_days INTEGER,
            matches_last_30_days INTEGER,
            is_congested INTEGER DEFAULT 0,
            UNIQUE(team_id, match_id)
        )
    """,
    "data_source_logs": """
        CREATE TABLE IF NOT EXISTS data_source_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_name TEXT NOT NULL,
            endpoint TEXT,
            status TEXT CHECK(status IN ('success', 'failed', 'rate_limited', 'timeout')),
            response_time_ms INTEGER,
            error_message TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "subscribers": """
        CREATE TABLE IF NOT EXISTS subscribers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            full_name TEXT,
            plan TEXT DEFAULT 'free' CHECK(plan IN ('free', 'premium', 'vip')),
            start_date TIMESTAMP,
            end_date TIMESTAMP,
            is_active INTEGER DEFAULT 1,
            added_by TEXT,
            total_coupons INTEGER DEFAULT 0,
            successful_coupons INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "bot_activity_log": """
        CREATE TABLE IF NOT EXISTS bot_activity_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER,
            command TEXT,
            details TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "user_coupons": """
        CREATE TABLE IF NOT EXISTS user_coupons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            coupon_name TEXT,
            picks_json TEXT NOT NULL,
            total_odds REAL,
            strategy TEXT DEFAULT 'custom',
            status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'won', 'lost', 'partial')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "injuries_cache": """
        CREATE TABLE IF NOT EXISTS injuries_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fixture_id INTEGER,
            team_id INTEGER,
            player_name TEXT,
            type TEXT,
            cached_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    # ─── v7 Agent Cache Table ─────────────────────────────────────
    "team_status": """
        CREATE TABLE IF NOT EXISTS team_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team_id INTEGER NOT NULL REFERENCES teams(id),
            injured_players TEXT DEFAULT '[]',
            suspended_players TEXT DEFAULT '[]',
            injury_count INTEGER DEFAULT 0,
            suspension_count INTEGER DEFAULT 0,
            squad_value_eur REAL DEFAULT 0.0,
            key_absences TEXT DEFAULT '[]',
            power_loss_pct REAL DEFAULT 0.0,
            source TEXT DEFAULT 'agent',
            data_hash TEXT,
            fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            expires_at TIMESTAMP,
            UNIQUE(team_id)
        )
    """,
    "league_metadata": """
        CREATE TABLE IF NOT EXISTS league_metadata (
            league_code TEXT PRIMARY KEY,
            league_type TEXT NOT NULL CHECK(league_type IN ('EUROPE_STABLE', 'SUMMER_VOLATILE', 'HIGH_ROTATION')),
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "odds_snapshots": """
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            market_type TEXT NOT NULL,
            selection TEXT NOT NULL,
            bookmaker TEXT NOT NULL,
            odds REAL NOT NULL,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "closing_odds": """
        CREATE TABLE IF NOT EXISTS closing_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            market_type TEXT NOT NULL,
            selection TEXT NOT NULL,
            bookmaker TEXT NOT NULL,
            closing_odds REAL NOT NULL,
            captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, market_type, selection, bookmaker)
        )
    """,
    "clv_feedback_log": """
        CREATE TABLE IF NOT EXISTS clv_feedback_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id INTEGER NOT NULL REFERENCES matches(id),
            league_id TEXT NOT NULL,
            market_type TEXT NOT NULL,
            selection TEXT NOT NULL,
            model_probability REAL NOT NULL,
            market_open_odds REAL NOT NULL,
            market_close_odds REAL NOT NULL,
            clv_value REAL NOT NULL,
            result TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "threshold_state": """
        CREATE TABLE IF NOT EXISTS threshold_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            league_id TEXT NOT NULL,
            market_type TEXT NOT NULL,
            threshold_value REAL NOT NULL,
            roi_30d REAL NOT NULL,
            clv_30d REAL NOT NULL,
            coverage_30d REAL NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            is_active INTEGER NOT NULL DEFAULT 1,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(league_id, market_type, version)
        )
    """,
}

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_matches_date ON matches(date)",
    "CREATE INDEX IF NOT EXISTS idx_matches_league ON matches(league_code)",
    "CREATE INDEX IF NOT EXISTS idx_matches_season ON matches(season)",
    "CREATE INDEX IF NOT EXISTS idx_matches_home ON matches(home_team_id)",
    "CREATE INDEX IF NOT EXISTS idx_matches_away ON matches(away_team_id)",
    "CREATE INDEX IF NOT EXISTS idx_odds_match ON odds(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_players_team ON players(team_id)",
    "CREATE INDEX IF NOT EXISTS idx_predictions_match ON predictions(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_referees_name ON referees(name)",
    # v4 indexes
    "CREATE INDEX IF NOT EXISTS idx_experiments_type ON model_experiments(model_type)",
    "CREATE INDEX IF NOT EXISTS idx_experiments_active ON model_experiments(is_active)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_experiment ON backtest_results(experiment_id)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_match ON backtest_results(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_match_xg_match ON match_xg(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_fixture_density_team ON fixture_density(team_id)",
    "CREATE INDEX IF NOT EXISTS idx_subscribers_tg ON subscribers(telegram_id)",
    "CREATE INDEX IF NOT EXISTS idx_subscribers_plan ON subscribers(plan)",
    "CREATE INDEX IF NOT EXISTS idx_user_coupons_tg ON user_coupons(telegram_id)",
    "CREATE INDEX IF NOT EXISTS idx_bot_activity_tg ON bot_activity_log(telegram_id)",
    "CREATE INDEX IF NOT EXISTS idx_datasource_logs_ts ON data_source_logs(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_league_metadata_type ON league_metadata(league_type)",
    "CREATE INDEX IF NOT EXISTS idx_odds_snapshots_match ON odds_snapshots(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_closing_odds_match ON closing_odds(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_clv_feedback_match ON clv_feedback_log(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_clv_feedback_league ON clv_feedback_log(league_id)",
    "CREATE INDEX IF NOT EXISTS idx_threshold_state_league_market ON threshold_state(league_id, market_type)",
    "CREATE INDEX IF NOT EXISTS idx_threshold_state_active ON threshold_state(is_active)",
]


def run_migrations(db) -> None:
    """Create all tables and indexes. Safe to run multiple times."""
    for table_name, ddl in TABLES.items():
        db.execute(ddl)

    for idx_sql in INDEXES:
        db.execute(idx_sql)

    existing = db.fetchone(
        "SELECT version FROM schema_version ORDER BY version DESC LIMIT 1"
    )
    current_version = existing["version"] if existing else 0

    if current_version < 3:
        _migrate_v3(db)

    if current_version < 4:
        _migrate_v4(db)

    if current_version < 5:
        _migrate_v5(db)

    if current_version < 6:
        _migrate_v6(db)

    if current_version < 7:
        _migrate_v7(db)

    if current_version < 8:
        _migrate_v8(db)

    if current_version < 9:
        _migrate_v9(db)

    if current_version < 10:
        _migrate_v10(db)

    if current_version < 11:
        _migrate_v11(db)

    if current_version < SCHEMA_VERSION:
        db.execute(
            "INSERT OR REPLACE INTO schema_version (version) VALUES (?)",
            (SCHEMA_VERSION,),
        )

    print(f"[OK] Database schema v{SCHEMA_VERSION} applied.")


def _migrate_v3(db):
    """v3: Add accuracy tracking to predictions."""
    v3_cols = [
        "analysis_date DATE", "top_1_pick TEXT", "top_1_type TEXT",
        "top_1_success INTEGER DEFAULT -1", "top_2_pick TEXT",
        "top_2_type TEXT", "top_2_success INTEGER DEFAULT -1",
    ]
    for c in v3_cols:
        try:
            db.execute(f"ALTER TABLE predictions ADD COLUMN {c}")
        except Exception:
            pass


def _migrate_v4(db):
    """v4: Add xG, season_label, importance to matches; model_type to predictions."""
    match_cols = [
        "home_xg REAL", "away_xg REAL", "season_label TEXT",
        "importance TEXT DEFAULT 'normal'",
    ]
    for c in match_cols:
        try:
            db.execute(f"ALTER TABLE matches ADD COLUMN {c}")
        except Exception:
            pass

    try:
        db.execute("ALTER TABLE predictions ADD COLUMN model_type TEXT DEFAULT 'ensemble'")
    except Exception:
        pass

    print("[MIGRATE] v4 schema upgrades applied.")


def _migrate_v5(db):
    """v5: Add was_posted to predictions table."""
    try:
        db.execute("ALTER TABLE predictions ADD COLUMN was_posted INTEGER DEFAULT 0")
        print("[MIGRATE] v5: was_posted column added to predictions.")
    except Exception as e:
        print(f"[MIGRATE] v5 warning (might already exist): {e}")


def _migrate_v6(db):
    """v6: Recreate predictions table without CHECK constraints on predicted_result and actual_result."""
    try:
        # Check if predictions table exists before renaming
        if db.table_exists("predictions"):
            # Rename old table
            db.execute("ALTER TABLE predictions RENAME TO predictions_old")
            
            # Recreate table without CHECK constraints
            db.execute("""
                CREATE TABLE predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    match_id INTEGER NOT NULL REFERENCES matches(id),
                    analysis_date DATE,
                    model_type TEXT DEFAULT 'ensemble',
                    home_win_prob REAL,
                    draw_prob REAL,
                    away_win_prob REAL,
                    confidence_score INTEGER DEFAULT 0,
                    is_value_bet INTEGER DEFAULT 0,
                    value_margin REAL DEFAULT 0.0,
                    predicted_result TEXT,
                    actual_result TEXT,
                    top_1_pick TEXT,
                    top_1_type TEXT,
                    top_1_success INTEGER DEFAULT -1,
                    top_2_pick TEXT,
                    top_2_type TEXT,
                    top_2_success INTEGER DEFAULT -1,
                    was_posted INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(match_id, analysis_date, model_type)
                )
            """)
            
            # Copy data from predictions_old to predictions (mapping the columns we have)
            db.execute("""
                INSERT INTO predictions (
                    id, match_id, home_win_prob, draw_prob, away_win_prob, confidence_score,
                    is_value_bet, value_margin, predicted_result, actual_result, created_at,
                    analysis_date, top_1_pick, top_1_type, top_1_success, top_2_pick,
                    top_2_type, top_2_success, model_type, was_posted
                )
                SELECT 
                    id, match_id, home_win_prob, draw_prob, away_win_prob, confidence_score,
                    COALESCE(is_value_bet, 0), COALESCE(value_margin, 0.0), predicted_result, actual_result, created_at,
                    analysis_date, top_1_pick, top_1_type, top_1_success, top_2_pick,
                    top_2_type, top_2_success, model_type, was_posted
                FROM predictions_old
            """)
            
            # Drop old table
            db.execute("DROP TABLE predictions_old")
            print("[MIGRATE] v6: predictions table recreated successfully without CHECK constraints.")
        else:
            # Just create predictions directly if not exists
            db.execute(TABLES["predictions"])
            print("[MIGRATE] v6: predictions table created.")
    except Exception as e:
        print(f"[MIGRATE] v6 error: {e}")
        # Rollback attempt
        try:
            db.execute("DROP TABLE IF EXISTS predictions")
            db.execute("ALTER TABLE predictions_old RENAME TO predictions")
            print("[MIGRATE] v6: rollback successful.")
        except Exception as re:
            print(f"[MIGRATE] v6: rollback failed: {re}")


def _migrate_v7(db):
    """v7: Add team_status table for autonomous data agent cache."""
    try:
        if not db.table_exists("team_status"):
            db.execute(TABLES["team_status"])
            db.execute("CREATE INDEX IF NOT EXISTS idx_team_status_team ON team_status(team_id)")
            print("[MIGRATE] v7: team_status table created.")
        else:
            print("[MIGRATE] v7: team_status already exists.")
    except Exception as e:
        print(f"[MIGRATE] v7 error: {e}")


def _migrate_v8(db):
    """v8: Add league_metadata table for league type classification."""
    try:
        if not db.table_exists("league_metadata"):
            db.execute(TABLES["league_metadata"])
            db.execute("CREATE INDEX IF NOT EXISTS idx_league_metadata_type ON league_metadata(league_type)")
            print("[MIGRATE] v8: league_metadata table created.")
        else:
            print("[MIGRATE] v8: league_metadata already exists.")
    except Exception as e:
        print(f"[MIGRATE] v8 error: {e}")


def _migrate_v9(db):
    """v9: Add odds_snapshots and closing_odds tables, and predictions CLV columns."""
    try:
        if not db.table_exists("odds_snapshots"):
            db.execute(TABLES["odds_snapshots"])
            db.execute("CREATE INDEX IF NOT EXISTS idx_odds_snapshots_match ON odds_snapshots(match_id)")
            print("[MIGRATE] v9: odds_snapshots table created.")
        if not db.table_exists("closing_odds"):
            db.execute(TABLES["closing_odds"])
            db.execute("CREATE INDEX IF NOT EXISTS idx_closing_odds_match ON closing_odds(match_id)")
            print("[MIGRATE] v9: closing_odds table created.")
        
        # Alter predictions table to add CLV tracking columns
        predictions_cols = [
            "prediction_odds REAL", "closing_odds REAL", "clv_pct REAL",
            "value_edge REAL", "value_class TEXT", "clv_class TEXT"
        ]
        for c in predictions_cols:
            try:
                db.execute(f"ALTER TABLE predictions ADD COLUMN {c}")
            except Exception:
                pass
        print("[MIGRATE] v9 predictions columns added successfully.")
    except Exception as e:
        print(f"[MIGRATE] v9 error: {e}")


def _migrate_v10(db):
    """v10: Add clv_feedback_log table and indexes for CLV feedback loop."""
    try:
        if not db.table_exists("clv_feedback_log"):
            db.execute(TABLES["clv_feedback_log"])
            db.execute("CREATE INDEX IF NOT EXISTS idx_clv_feedback_match ON clv_feedback_log(match_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_clv_feedback_league ON clv_feedback_log(league_id)")
            print("[MIGRATE] v10: clv_feedback_log table created.")
        else:
            print("[MIGRATE] v10: clv_feedback_log already exists.")
    except Exception as e:
        print(f"[MIGRATE] v10 error: {e}")


def _migrate_v11(db):
    """v11: Add threshold_state table and indexes for adaptive threshold optimization."""
    try:
        if not db.table_exists("threshold_state"):
            db.execute(TABLES["threshold_state"])
            db.execute("CREATE INDEX IF NOT EXISTS idx_threshold_state_league_market ON threshold_state(league_id, market_type)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_threshold_state_active ON threshold_state(is_active)")
            print("[MIGRATE] v11: threshold_state table created.")
        else:
            print("[MIGRATE] v11: threshold_state already exists.")
    except Exception as e:
        print(f"[MIGRATE] v11 error: {e}")

