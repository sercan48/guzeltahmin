"""FastAPI Backend for Güzel Tahmin Admin Panel."""

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Add project root to sys.path to resolve src and config modules
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.db.base import get_backend
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_FREE_CHANNEL_ID, SQLITE_PATH
from app.bot.predictions import _calculate_yesterday_performance, _calculate_performance_for_period
from app.bot.formatters import format_performance_report

# Initialize logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("admin_api")

app = FastAPI(title="Güzel Tahmin Admin API", version="1.0.0")

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # For local development ease
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CONFIG_PATH = ROOT_DIR / "data" / "admin_config.json"

# ── Pydantic Schemas ─────────────────────────────────────────────────
class ConfigSchema(BaseModel):
    temperature: float = 1.15
    live_betting_enabled: bool = False
    self_learning_enabled: bool = True

class SubscriberSchema(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    plan: str = "free"  # free, premium, vip
    is_active: int = 1
    end_date: Optional[str] = None

class TeamStatusSchema(BaseModel):
    injured_players: List[Dict[str, Any]] = []
    suspended_players: List[Dict[str, Any]] = []
    injury_count: int = 0
    suspension_count: int = 0
    squad_value_eur: float = 0.0
    key_absences: List[str] = []
    power_loss_pct: float = 0.0


# ── Config Helpers ──────────────────────────────────────────────────
def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading config: {e}")
    
    # Fallback default
    return {
        "temperature": 1.15,
        "live_betting_enabled": False,
        "self_learning_enabled": True
    }

def save_config(cfg: dict):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error saving config: {e}")
        raise HTTPException(status_code=500, detail=f"Config saved failed: {e}")


# ── Background Task Runner ───────────────────────────────────────────
def run_script_in_background(script_name: str):
    logger.info(f"Triggering script in background: {script_name}")
    try:
        script_path = ROOT_DIR / "scripts" / script_name
        if not script_path.exists():
            # Check src/agents/ if not in scripts
            script_path = ROOT_DIR / "src" / "agents" / script_name
            if not script_path.exists():
                logger.error(f"Script not found: {script_name}")
                return
        
        # Determine executable
        python_exe = sys.executable
        
        # Run subprocess
        proc = subprocess.Popen(
            [python_exe, "-m" if script_name.startswith("src.") else str(script_path)],
            cwd=str(ROOT_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = proc.communicate()
        logger.info(f"Script {script_name} output:\n{stdout}")
        if proc.returncode != 0:
            logger.error(f"Script {script_name} failed with code {proc.returncode}. Error:\n{stderr}")
    except Exception as e:
        logger.error(f"Exception running script {script_name}: {e}")


# ── Endpoints ────────────────────────────────────────────────────────

@app.get("/api/dashboard")
def get_dashboard_stats():
    db = get_backend()
    db.connect()
    try:
        # User counts
        total_users = db.fetchone("SELECT COUNT(*) as c FROM subscribers")["c"]
        premium_users = db.fetchone("SELECT COUNT(*) as c FROM subscribers WHERE is_active = 1 AND plan = 'premium'")["c"]
        vip_users = db.fetchone("SELECT COUNT(*) as c FROM subscribers WHERE is_active = 1 AND plan = 'vip'")["c"]
        
        # Performance
        daily_perf = _calculate_yesterday_performance(db)
        weekly_perf = _calculate_performance_for_period(db, 7)
        
        # Bot activity
        activity = db.fetchall("SELECT * FROM bot_activity_log ORDER BY timestamp DESC LIMIT 5")
        
        # Data source logs
        source_logs = db.fetchall("SELECT * FROM data_source_logs ORDER BY timestamp DESC LIMIT 5")
        
        # Config
        config = load_config()
        
        return {
            "stats": {
                "total_users": total_users,
                "premium_users": premium_users,
                "vip_users": vip_users,
                "yesterday_accuracy": round(daily_perf.get("accuracy", 0.0), 1),
                "yesterday_roi": round(daily_perf.get("roi", 0.0), 1),
                "yesterday_correct": daily_perf.get("correct", 0),
                "yesterday_total": daily_perf.get("total", 0),
                "weekly_accuracy": round(weekly_perf.get("accuracy", 0.0), 1),
                "weekly_roi": round(weekly_perf.get("roi", 0.0), 1),
            },
            "recent_activity": activity,
            "source_logs": source_logs,
            "config": config,
            "database_path": str(SQLITE_PATH)
        }
    except Exception as e:
        logger.error(f"Dashboard query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def update_config(cfg: ConfigSchema):
    save_config(cfg.dict())
    return {"status": "success", "config": load_config()}


# ── Subscriber Management Endpoints ─────────────────────────────────

@app.get("/api/subscribers")
def get_subscribers():
    db = get_backend()
    db.connect()
    try:
        rows = db.fetchall("SELECT * FROM subscribers ORDER BY created_at DESC")
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/subscribers")
def add_subscriber(sub: SubscriberSchema):
    db = get_backend()
    db.connect()
    try:
        # Check if already exists
        exists = db.fetchone("SELECT id FROM subscribers WHERE telegram_id = ?", (sub.telegram_id,))
        if exists:
            raise HTTPException(status_code=400, detail="Subscriber already exists")
        
        db.execute("""
            INSERT INTO subscribers (telegram_id, username, full_name, plan, is_active, start_date, end_date)
            VALUES (?, ?, ?, ?, ?, datetime('now'), ?)
        """, (sub.telegram_id, sub.username, sub.full_name, sub.plan, sub.is_active, sub.end_date))
        return {"status": "success", "message": "Subscriber added"}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.put("/api/subscribers/{telegram_id}")
def update_subscriber(telegram_id: int, sub: SubscriberSchema):
    db = get_backend()
    db.connect()
    try:
        exists = db.fetchone("SELECT id FROM subscribers WHERE telegram_id = ?", (telegram_id,))
        if not exists:
            raise HTTPException(status_code=404, detail="Subscriber not found")
        
        db.execute("""
            UPDATE subscribers 
            SET username = ?, full_name = ?, plan = ?, is_active = ?, end_date = ?
            WHERE telegram_id = ?
        """, (sub.username, sub.full_name, sub.plan, sub.is_active, sub.end_date, telegram_id))
        return {"status": "success", "message": "Subscriber updated"}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.delete("/api/subscribers/{telegram_id}")
def delete_subscriber(telegram_id: int):
    db = get_backend()
    db.connect()
    try:
        exists = db.fetchone("SELECT id FROM subscribers WHERE telegram_id = ?", (telegram_id,))
        if not exists:
            raise HTTPException(status_code=404, detail="Subscriber not found")
        
        db.execute("DELETE FROM subscribers WHERE telegram_id = ?", (telegram_id,))
        return {"status": "success", "message": "Subscriber deleted"}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ── Predictions Endpoints ───────────────────────────────────────────

@app.get("/api/predictions")
def get_predictions():
    db = get_backend()
    db.connect()
    try:
        query = """
            SELECT p.*, m.date, m.league_code,
                   t1.name as home_team, t2.name as away_team,
                   o.home_odds, o.draw_odds, o.away_odds
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            LEFT JOIN odds o ON p.match_id = o.match_id
            WHERE DATE(m.date) >= DATE('now', '-1 day')
            ORDER BY m.date ASC
        """
        rows = db.fetchall(query)
        # Parse JSON columns if any, and clean output
        for r in rows:
            if r.get("top_1_pick"):
                r["picks"] = [
                    {"pick": r["top_1_pick"], "type": r["top_1_type"], "success": r["top_1_success"]},
                    {"pick": r["top_2_pick"], "type": r["top_2_type"], "success": r["top_2_success"]}
                ]
        return rows
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/analytics/clv")
def get_clv_analytics():
    db = get_backend()
    db.connect()
    try:
        # Fetch predictions with CLV tracking
        query = """
            SELECT p.clv_pct, p.clv_class, p.value_edge, p.value_class,
                   p.predicted_result, p.actual_result, p.prediction_odds, p.closing_odds,
                   m.league_code
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.clv_pct IS NOT NULL
        """
        rows = db.fetchall(query)
        if not rows:
            return {
                "overall": {
                    "average_clv": 0.0,
                    "positive_clv_rate": 0.0,
                    "negative_clv_rate": 0.0,
                    "neutral_clv_rate": 0.0,
                    "market_efficiency_score": 1.0,
                    "total_count": 0
                },
                "leagues": {}
            }
        
        total = len(rows)
        avg_clv = sum(r["clv_pct"] for r in rows) / total
        pos_clv = sum(1 for r in rows if r["clv_pct"] > 0)
        neg_clv = sum(1 for r in rows if r["clv_pct"] < 0)
        neut_clv = sum(1 for r in rows if r["clv_class"] == "NEUTRAL_CLV" or (-2.0 <= r["clv_pct"] <= 2.0))
        
        eff_score = round(neut_clv / total, 4)
        
        leagues_data = {}
        for r in rows:
            l = r["league_code"]
            if l not in leagues_data:
                leagues_data[l] = []
            leagues_data[l].append(r)
            
        league_report = {}
        for l, l_rows in leagues_data.items():
            l_total = len(l_rows)
            l_avg_clv = sum(x["clv_pct"] for x in l_rows) / l_total
            l_pos_clv = sum(1 for x in l_rows if x["clv_pct"] > 0)
            
            # Hit rate of predictions
            l_correct = sum(1 for x in l_rows if x["predicted_result"] == x["actual_result"] and x["actual_result"] is not None)
            l_total_evaluated = sum(1 for x in l_rows if x["actual_result"] is not None)
            l_hit_rate = (l_correct / l_total_evaluated * 100) if l_total_evaluated > 0 else 0.0
            
            # ROI for flat-stake bets with positive CLV
            pos_clv_rows = [x for x in l_rows if x["clv_pct"] > 0 and x["actual_result"] is not None]
            staked = len(pos_clv_rows)
            profit = 0.0
            for x in pos_clv_rows:
                odds = x["prediction_odds"] or 1.80
                if x["predicted_result"] == x["actual_result"]:
                    profit += (odds - 1.0)
                else:
                    profit -= 1.0
            l_clv_roi = (profit / staked * 100) if staked > 0 else 0.0
            
            league_report[l] = {
                "average_clv": round(l_avg_clv, 2),
                "positive_clv_rate": round(l_pos_clv / l_total * 100, 1),
                "hit_rate": round(l_hit_rate, 1),
                "clv_roi": round(l_clv_roi, 1),
                "total_count": l_total
            }
            
        return {
            "overall": {
                "average_clv": round(avg_clv, 2),
                "positive_clv_rate": round(pos_clv / total * 100, 1),
                "negative_clv_rate": round(neg_clv / total * 100, 1),
                "neutral_clv_rate": round(neut_clv / total * 100, 1),
                "market_efficiency_score": eff_score,
                "total_count": total
            },
            "leagues": league_report
        }
    except Exception as e:
        logger.error(f"CLV analytics query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/analytics/edge")
def get_edge_analytics():
    db = get_backend()
    db.connect()
    try:
        # Fetch predictions with edge details
        query = """
            SELECT p.value_edge, p.value_class, p.clv_pct, p.predicted_result, p.actual_result, p.prediction_odds,
                   m.league_code
            FROM predictions p
            JOIN matches m ON p.match_id = m.id
            WHERE p.value_edge IS NOT NULL
        """
        rows = db.fetchall(query)
        if not rows:
            return {
                "overall": {
                    "average_edge": 0.0,
                    "edge_distribution": {"NO_VALUE": 0, "LOW_VALUE": 0, "MEDIUM_VALUE": 0, "HIGH_VALUE": 0},
                    "total_count": 0
                },
                "leagues": {}
            }
            
        total = len(rows)
        avg_edge = sum(r["value_edge"] for r in rows) / total
        
        dist = {"NO_VALUE": 0, "LOW_VALUE": 0, "MEDIUM_VALUE": 0, "HIGH_VALUE": 0}
        for r in rows:
            vc = r["value_class"] or "NO_VALUE"
            if vc in dist:
                dist[vc] += 1
                
        leagues_data = {}
        for r in rows:
            l = r["league_code"]
            if l not in leagues_data:
                leagues_data[l] = []
            leagues_data[l].append(r)
            
        league_report = {}
        for l, l_rows in leagues_data.items():
            l_total = len(l_rows)
            l_avg_edge = sum(x["value_edge"] for x in l_rows) / l_total
            
            # positive clv rate for this league
            l_pos_clv = sum(1 for x in l_rows if x.get("clv_pct") is not None and x["clv_pct"] > 0)
            l_clv_pct = (l_pos_clv / l_total * 100) if l_total > 0 else 0.0
            
            # Hit rate of predictions when they have value
            value_bets = [x for x in l_rows if x["value_class"] != "NO_VALUE" and x["actual_result"] is not None]
            val_total = len(value_bets)
            val_correct = sum(1 for x in value_bets if x["predicted_result"] == x["actual_result"])
            val_hit_rate = (val_correct / val_total * 100) if val_total > 0 else 0.0
            
            # ROI for flat-stake bets with value_class != 'NO_VALUE'
            staked = len(value_bets)
            profit = 0.0
            for x in value_bets:
                odds = x["prediction_odds"] or 1.80
                if x["predicted_result"] == x["actual_result"]:
                    profit += (odds - 1.0)
                else:
                    profit -= 1.0
            val_roi = (profit / staked * 100) if staked > 0 else 0.0
            
            league_report[l] = {
                "average_edge": round(l_avg_edge, 4),
                "positive_clv_rate": round(l_clv_pct, 1),
                "value_hit_rate": round(val_hit_rate, 1),
                "roi": round(val_roi, 1),
                "total_count": l_total,
                "value_count": val_total
            }
            
        return {
            "overall": {
                "average_edge": round(avg_edge, 4),
                "edge_distribution": dist,
                "total_count": total
            },
            "leagues": league_report
        }
    except Exception as e:
        logger.error(f"Edge analytics query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/analytics/drift")
def get_drift_analytics():
    db = get_backend()
    db.connect()
    try:
        from src.model.adaptive_learning import AdaptiveLearningEngine
        engine = AdaptiveLearningEngine(db)
        state = engine.detect_drift()
        return state
    except Exception as e:
        logger.error(f"Drift analytics query failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.get("/api/analytics/weights")
def get_weights_analytics():
    try:
        from src.model.adaptive_learning import AdaptiveLearningEngine
        engine = AdaptiveLearningEngine(None)
        
        weights = engine._load_json(engine.weights_path)
        bias = engine._load_json(engine.bias_path)
        thresholds = engine._load_json(engine.thresholds_state_path)
        
        return {
            "feature_weights": weights,
            "market_biases": bias,
            "league_thresholds": thresholds
        }
    except Exception as e:
        logger.error(f"Weights analytics retrieval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


class RollbackSchema(BaseModel):
    league_id: str
    version: int


@app.get("/api/analytics/thresholds")
def get_thresholds_analytics():
    db = get_backend()
    db.connect()
    try:
        rows = db.fetchall("""
            SELECT league_id, market_type, threshold_value, roi_30d, clv_30d, coverage_30d, version, is_active, last_updated
            FROM threshold_state
            ORDER BY league_id ASC, version DESC, market_type ASC
        """)
        
        # Group by league_id and version
        grouped = {}
        for r in rows:
            l_id = r["league_id"]
            ver = r["version"]
            if l_id not in grouped:
                grouped[l_id] = {}
            if ver not in grouped[l_id]:
                grouped[l_id][ver] = {
                    "version": ver,
                    "is_active": r["is_active"],
                    "roi_30d": r["roi_30d"],
                    "clv_30d": r["clv_30d"],
                    "coverage_30d": r["coverage_30d"],
                    "last_updated": r["last_updated"],
                    "thresholds": {}
                }
            grouped[l_id][ver]["thresholds"][r["market_type"]] = r["threshold_value"]
            
        # Convert versions map to sorted list per league
        result = {}
        for l_id, versions_map in grouped.items():
            result[l_id] = sorted(versions_map.values(), key=lambda x: x["version"], reverse=True)
            
        return result
    except Exception as e:
        logger.error(f"Thresholds analytics retrieval failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/api/analytics/thresholds/rollback")
def post_thresholds_rollback(rollback: RollbackSchema):
    db = get_backend()
    db.connect()
    try:
        # Check if the requested version exists
        exists = db.fetchone("""
            SELECT COUNT(*) as cnt FROM threshold_state 
            WHERE league_id = ? AND version = ?
        """, (rollback.league_id, rollback.version))
        
        if not exists or exists["cnt"] == 0:
            raise HTTPException(status_code=404, detail=f"Threshold version v{rollback.version} for league {rollback.league_id} not found")
            
        # Deactivate all versions of this league
        db.execute("""
            UPDATE threshold_state 
            SET is_active = 0 
            WHERE league_id = ?
        """, (rollback.league_id,))
        
        # Activate target version
        db.execute("""
            UPDATE threshold_state 
            SET is_active = 1 
            WHERE league_id = ? AND version = ?
        """, (rollback.league_id, rollback.version))
        
        msg = f"Manually reverted thresholds for league {rollback.league_id} to version v{rollback.version}"
        logger.info(msg)
        
        # Log to bot activity log
        try:
            db.execute("""
                INSERT INTO bot_activity_log (telegram_id, command, details)
                VALUES (0, 'manual_rollback', ?)
            """, (msg,))
        except Exception:
            pass
            
        return {
            "status": "success",
            "message": f"Successfully rolled back {rollback.league_id} to version v{rollback.version}"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Threshold rollback failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


# ── AI Insight (Injury Status) Endpoints ────────────────────────────

@app.get("/api/teams")
def get_teams():
    db = get_backend()
    db.connect()
    try:
        teams = db.fetchall("SELECT id, name, league_code, country FROM teams ORDER BY name ASC")
        return teams
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.get("/api/team_status/{team_id}")
def get_team_status(team_id: int):
    db = get_backend()
    db.connect()
    try:
        row = db.fetchone("SELECT * FROM team_status WHERE team_id = ?", (team_id,))
        if not row:
            # Return empty skeleton
            return {
                "team_id": team_id,
                "injured_players": [],
                "suspended_players": [],
                "injury_count": 0,
                "suspension_count": 0,
                "squad_value_eur": 0.0,
                "key_absences": [],
                "power_loss_pct": 0.0
            }
        
        # Deserialize JSON fields
        try:
            injured = json.loads(row["injured_players"] or "[]")
        except:
            injured = []
            
        try:
            suspended = json.loads(row["suspended_players"] or "[]")
        except:
            suspended = []
            
        try:
            absences = json.loads(row["key_absences"] or "[]")
        except:
            absences = []
            
        return {
            "team_id": team_id,
            "injured_players": injured,
            "suspended_players": suspended,
            "injury_count": row.get("injury_count", 0),
            "suspension_count": row.get("suspension_count", 0),
            "squad_value_eur": row.get("squad_value_eur", 0.0),
            "key_absences": absences,
            "power_loss_pct": row.get("power_loss_pct", 0.0)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/team_status/{team_id}")
def save_team_status(team_id: int, status: TeamStatusSchema):
    db = get_backend()
    db.connect()
    try:
        injured_json = json.dumps(status.injured_players, ensure_ascii=False)
        suspended_json = json.dumps(status.suspended_players, ensure_ascii=False)
        key_abs_json = json.dumps(status.key_absences, ensure_ascii=False)
        
        import hashlib
        data_hash = hashlib.md5((injured_json + suspended_json).encode()).hexdigest()
        expires = (datetime.now() + range_delta(18)).isoformat() # Helper fallback
        
        # Check table
        from src.agents.data_agent import ensure_team_status_table
        ensure_team_status_table(db)
        
        db.execute("""
            INSERT INTO team_status (
                team_id, injured_players, suspended_players,
                injury_count, suspension_count, squad_value_eur,
                key_absences, power_loss_pct, source, data_hash,
                fetched_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'admin_manual', ?, datetime('now'), ?)
            ON CONFLICT(team_id) DO UPDATE SET
                injured_players = excluded.injured_players,
                suspended_players = excluded.suspended_players,
                injury_count = excluded.injury_count,
                suspension_count = excluded.suspension_count,
                squad_value_eur = excluded.squad_value_eur,
                key_absences = excluded.key_absences,
                power_loss_pct = excluded.power_loss_pct,
                source = 'admin_manual',
                data_hash = excluded.data_hash,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
        """, (
            team_id, injured_json, suspended_json,
            status.injury_count, status.suspension_count, status.squad_value_eur,
            key_abs_json, status.power_loss_pct, data_hash, expires
        ))
        
        return {"status": "success", "message": "Team injury status updated"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

def range_delta(hours: int):
    from datetime import timedelta
    return timedelta(hours=hours)


# ── Action / Script Triggers ────────────────────────────────────────

@app.post("/api/trigger/run_production")
def trigger_production_pipeline(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_script_in_background, "run_production_pipeline.py")
    return {"status": "success", "message": "Production prediction pipeline triggered."}

@app.post("/api/trigger/data_agent")
def trigger_data_agent(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_script_in_background, "src.agents.data_agent")
    return {"status": "success", "message": "Autonomous Data Agent triggered."}

@app.post("/api/trigger/predict_today")
def trigger_predict_today(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_script_in_background, "predict_today.py")
    return {"status": "success", "message": "Predict Today script triggered."}


# ── Telegram manual report send ──────────────────────────────────────

@app.post("/api/bot/send_report")
def trigger_telegram_report():
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(status_code=400, detail="TELEGRAM_BOT_TOKEN is not configured in .env")
    if not TELEGRAM_FREE_CHANNEL_ID:
        raise HTTPException(status_code=400, detail="TELEGRAM_FREE_CHANNEL_ID is not configured in .env")
        
    db = get_backend()
    db.connect()
    try:
        daily = _calculate_yesterday_performance(db)
        weekly = _calculate_performance_for_period(db, 7)
        report_text = format_performance_report(daily, weekly)
    except Exception as e:
        logger.error(f"Error calculating report stats: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to compile stats: {e}")
    finally:
        db.close()
        
    # Send report via Direct Telegram API call
    try:
        import requests
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        resp = requests.post(
            url,
            json={
                "chat_id": TELEGRAM_FREE_CHANNEL_ID,
                "text": report_text,
                "parse_mode": "Markdown"
            },
            timeout=10
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=400, 
                detail=f"Telegram API returned status {resp.status_code}: {resp.text}"
            )
        return {"status": "success", "message": "Performance report broadcasted successfully!"}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Telegram API connection failed: {e}")


# ── Database Backup & Check Expirations ──────────────────────────────

@app.get("/api/database/backups")
def get_backups():
    backup_dir = ROOT_DIR / "data" / "backups"
    if not backup_dir.exists():
        return []
    
    backups = []
    for f in backup_dir.glob("*.db"):
        stat = f.stat()
        backups.append({
            "name": f.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created_at": datetime.fromtimestamp(stat.st_mtime).isoformat().replace("T", " ")[:19]
        })
    # Sort by date descending
    backups.sort(key=lambda x: x["created_at"], reverse=True)
    return backups


@app.post("/api/database/backup")
def create_backup():
    import shutil
    source = Path(SQLITE_PATH)
    if not source.exists():
        raise HTTPException(status_code=404, detail=f"Database not found at {source}")
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = ROOT_DIR / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    destination = backup_dir / f"guzel_tahmin_backup_{timestamp}.db"
    try:
        shutil.copy2(source, destination)
        return {"status": "success", "message": f"Backup created: {destination.name}", "filename": destination.name}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Backup failed: {e}")


@app.post("/api/subscribers/check_expirations")
def check_expirations():
    db = get_backend()
    db.connect()
    try:
        # Find active subscribers that have expired
        now = datetime.now().isoformat()
        expired_users = db.fetchall(
            "SELECT telegram_id, username, end_date FROM subscribers WHERE is_active = 1 AND end_date < ?",
            (now,)
        )
        
        kicked_count = 0
        kicked_users = []
        
        for user in expired_users:
            tg_id = user["telegram_id"]
            
            # 1. Update in DB
            db.execute("UPDATE subscribers SET is_active = 0 WHERE telegram_id = ?", (tg_id,))
            
            # 2. Log activity
            db.execute("""
                INSERT INTO bot_activity_log (telegram_id, command, details)
                VALUES (?, '/kick_expired', ?)
            """, (tg_id, f"Abonelik süresi bittiği için otomatik pasifleştirildi ({user['end_date']})"))
            
            # 3. Attempt Telegram Kick (ban and then unban to kick from channel)
            telegram_kicked = False
            from config.settings import TELEGRAM_CHANNEL_ID
            if TELEGRAM_BOT_TOKEN and TELEGRAM_CHANNEL_ID and tg_id:
                try:
                    import requests
                    # Kick (Ban)
                    ban_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/banChatMember"
                    ban_resp = requests.post(
                        ban_url,
                        json={"chat_id": TELEGRAM_CHANNEL_ID, "user_id": tg_id},
                        timeout=5
                    )
                    
                    if ban_resp.status_code == 200:
                        # Unban so they can rejoin in future
                        unban_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/unbanChatMember"
                        requests.post(
                            unban_url,
                            json={"chat_id": TELEGRAM_CHANNEL_ID, "user_id": tg_id, "only_if_banned": True},
                            timeout=5
                        )
                        telegram_kicked = True
                except Exception as te:
                    logger.error(f"Telegram kick failed for {tg_id}: {te}")
            
            kicked_users.append({
                "telegram_id": tg_id,
                "username": user["username"],
                "end_date": user["end_date"],
                "telegram_kicked": telegram_kicked
            })
            kicked_count += 1
            
        return {
            "status": "success",
            "kicked_count": kicked_count,
            "kicked_users": kicked_users
        }
    except Exception as e:
        logger.error(f"Expiration check failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


@app.post("/api/trigger/db_sync")
def trigger_db_sync(background_tasks: BackgroundTasks):
    from app.bot.predictions import daily_db_sync_job
    background_tasks.add_task(daily_db_sync_job, None)
    return {"status": "success", "message": "Database sync triggered in background."}

