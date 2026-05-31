"""Autonomous Data Agent — LLM-powered semantic web extraction.

Inspired by Agent Reach architecture: uses an LLM to semantically extract
structured data from web pages instead of fragile CSS/XPath scraping.

Runs as a nightly cron job (async). Extracts:
  1. Injury/suspension lists per team
  2. Squad market values

Results are cached in the `team_status` DB table. The predictor reads ONLY
from this table — zero live internet calls during prediction.

Usage:
    python -m src.agents.data_agent              # Run once (all teams)
    python -m src.agents.data_agent --league T1   # Single league
"""

import os
import json
import math
import asyncio
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger("data_agent")

# ── Constants ────────────────────────────────────────────────────────
AGENT_CACHE_TTL_HOURS = 18  # Don't re-fetch if data is newer than this
MAX_RETRIES = 2

# Sources for semantic extraction (no HTML scraping — LLM reads the page)
INJURY_SOURCES = {
    "transfermarkt": "https://www.transfermarkt.com/{team_slug}/ausfaelle/verein/{tm_id}",
    "premierinjuries": "https://www.premierinjuries.com/injury-table.php",
}

SQUAD_VALUE_SOURCES = {
    "transfermarkt": "https://www.transfermarkt.com/{team_slug}/startseite/verein/{tm_id}",
}


# ── DB Schema for team_status ────────────────────────────────────────
TEAM_STATUS_DDL = """
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
"""

TEAM_STATUS_INDEX = (
    "CREATE INDEX IF NOT EXISTS idx_team_status_team ON team_status(team_id)"
)


def ensure_team_status_table(db) -> None:
    """Create team_status table if it doesn't exist."""
    if not db.table_exists("team_status"):
        db.execute(TEAM_STATUS_DDL)
        db.execute(TEAM_STATUS_INDEX)
        logger.info("[Agent] team_status table created.")


# ── LLM Extraction Engine ───────────────────────────────────────────
class SemanticExtractor:
    """Uses Gemini LLM to semantically parse web content into structured JSON."""

    def __init__(self):
        self.model = None
        self._init_llm()

    def _init_llm(self):
        try:
            import google.generativeai as genai
            api_key = os.getenv("GEMINI_API_KEY", "")
            if api_key:
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel("gemini-1.5-flash")
                logger.info("[Agent] Gemini LLM initialized.")
            else:
                logger.warning("[Agent] GEMINI_API_KEY missing. Agent will use fallback.")
        except ImportError:
            logger.warning("[Agent] google-generativeai not installed.")

    def extract_injuries(self, raw_text: str, team_name: str) -> dict:
        """Semantically extract injury list from raw page text."""
        if not self.model:
            return self._fallback_injuries(team_name)

        prompt = f"""Analyze this football team page content for "{team_name}".
Extract ALL currently injured or suspended players.

Return ONLY valid JSON (no markdown fences):
{{
  "injured": [
    {{"name": "Player Name", "injury": "Knee ACL", "return_date": "2026-03-15", "severity": "major"}},
  ],
  "suspended": [
    {{"name": "Player Name", "reason": "Red card", "matches_remaining": 2}}
  ],
  "squad_value_eur_millions": 450.0
}}

If no injuries found, return empty arrays.
If squad value is not on the page, set it to 0.

Page content:
{raw_text[:8000]}
"""
        try:
            response = self.model.generate_content(prompt)
            text = response.text.strip()
            # Clean markdown fences
            for prefix in ("```json", "```"):
                if text.startswith(prefix):
                    text = text[len(prefix):]
            if text.endswith("```"):
                text = text[:-3]
            return json.loads(text.strip())
        except Exception as e:
            logger.error(f"[Agent] LLM extraction failed for {team_name}: {e}")
            return self._fallback_injuries(team_name)

    @staticmethod
    def _fallback_injuries(team_name: str) -> dict:
        return {"injured": [], "suspended": [], "squad_value_eur_millions": 0}


# ── Web Fetcher (async) ─────────────────────────────────────────────
async def fetch_page_text(url: str, timeout: int = 15) -> str:
    """Fetch a URL and return cleaned text content.

    Uses httpx for async HTTP. Falls back to urllib if not available.
    Returns raw text — the LLM handles semantic parsing.
    """
    try:
        import httpx
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GuzelTahminBot/1.0)"},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            # Strip HTML tags for cleaner LLM input
            return _strip_html(resp.text)
    except ImportError:
        # Fallback to sync urllib
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; GuzelTahminBot/1.0)"
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _strip_html(resp.read().decode("utf-8", errors="ignore"))
    except Exception as e:
        logger.error(f"[Agent] Fetch failed for {url}: {e}")
        return ""


def _strip_html(html: str) -> str:
    """Remove HTML tags, keeping text content for LLM parsing."""
    import re
    # Remove script/style blocks
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    html = re.sub(r"<[^>]+>", " ", html)
    # Collapse whitespace
    html = re.sub(r"\s+", " ", html).strip()
    return html


# ── Agent Core ───────────────────────────────────────────────────────
class DataAgent:
    """Autonomous data collection agent.

    Architecture:
      1. Reads team list from DB
      2. For each team, checks cache freshness (team_status.fetched_at)
      3. If stale, fetches web data and uses LLM to extract structured info
      4. Writes results to team_status table
      5. predictor.py reads team_status at prediction time (zero internet)
    """

    def __init__(self):
        from src.db.base import get_backend
        self.db = get_backend()
        self.extractor = SemanticExtractor()

    async def run(self, league_filter: str = None, force: bool = False):
        """Main agent loop — fetch and cache team status data."""
        self.db.connect()
        ensure_team_status_table(self.db)

        # Get teams to process
        if league_filter:
            teams = self.db.fetchall(
                "SELECT id, name, league_code FROM teams WHERE league_code = ?",
                (league_filter,)
            )
        else:
            teams = self.db.fetchall("SELECT id, name, league_code FROM teams")

        logger.info(f"[Agent] Processing {len(teams)} teams...")
        processed = 0
        skipped = 0

        for team in teams:
            team_id = team["id"]
            team_name = team["name"]

            # Check cache freshness
            if not force and self._is_fresh(team_id):
                skipped += 1
                continue

            # Fetch and extract
            status = await self._fetch_team_status(team_name, team_id)
            self._save_status(team_id, status)
            processed += 1

        self.db.close()
        logger.info(f"[Agent] Done. Processed: {processed}, Skipped (fresh): {skipped}")
        return {"processed": processed, "skipped": skipped}

    def _is_fresh(self, team_id: int) -> bool:
        """Check if cached data is still valid."""
        row = self.db.fetchone(
            "SELECT fetched_at FROM team_status WHERE team_id = ?",
            (team_id,)
        )
        if not row or not row["fetched_at"]:
            return False
        try:
            fetched = datetime.fromisoformat(str(row["fetched_at"]))
            return (datetime.now() - fetched) < timedelta(hours=AGENT_CACHE_TTL_HOURS)
        except (ValueError, TypeError):
            return False

    async def _fetch_team_status(self, team_name: str, team_id: int) -> dict:
        """Fetch injury + value data for a single team."""
        # Try API-Football injuries first (structured, reliable)
        api_result = self._try_api_football(team_id)
        if api_result and api_result.get("injured"):
            return api_result

        # Fallback: LLM semantic extraction from web
        slug = team_name.lower().replace(" ", "-")
        search_url = f"https://www.transfermarkt.com/schnellsuche/ergebnis/schnellsuche?query={team_name.replace(' ', '+')}"

        raw_text = await fetch_page_text(search_url)
        if raw_text:
            extracted = self.extractor.extract_injuries(raw_text, team_name)
            return self._normalize_extraction(extracted, team_id)

        # Final fallback: empty status
        return {
            "injured_players": [],
            "suspended_players": [],
            "injury_count": 0,
            "suspension_count": 0,
            "squad_value_eur": 0.0,
            "key_absences": [],
            "power_loss_pct": 0.0,
            "source": "fallback",
        }

    def _try_api_football(self, team_id: int) -> Optional[dict]:
        """Try to get injury data from cached API-Football results."""
        try:
            rows = self.db.fetchall(
                """SELECT player_name, type FROM injuries_cache
                   WHERE team_id = ?
                   AND cached_at > datetime('now', '-48 hours')""",
                (team_id,)
            )
            if rows:
                injured = [{"name": r["player_name"], "type": r.get("type", "unknown")}
                           for r in rows]
                # Calculate power loss from players table
                squad_val = self.db.fetchone(
                    "SELECT squad_value FROM teams WHERE id = ?", (team_id,)
                )
                total_val = (squad_val["squad_value"] or 0) if squad_val else 0

                missing_val = 0
                key_abs = []
                for inj in injured:
                    player = self.db.fetchone(
                        "SELECT market_value, importance_score FROM players WHERE team_id = ? AND name LIKE ?",
                        (team_id, f"%{inj['name']}%")
                    )
                    if player:
                        missing_val += (player["market_value"] or 0)
                        if (player.get("importance_score") or 0) > 0.7:
                            key_abs.append(inj["name"])

                power_loss = (missing_val / total_val * 100) if total_val > 0 else 0

                return {
                    "injured_players": injured,
                    "suspended_players": [],
                    "injury_count": len(injured),
                    "suspension_count": 0,
                    "squad_value_eur": total_val,
                    "key_absences": key_abs,
                    "power_loss_pct": round(power_loss, 2),
                    "source": "api_football_cache",
                }
        except Exception as e:
            logger.debug(f"[Agent] API-Football cache miss for team {team_id}: {e}")
        return None

    def _normalize_extraction(self, extracted: dict, team_id: int) -> dict:
        """Normalize LLM extraction output to DB format."""
        injured = extracted.get("injured", [])
        suspended = extracted.get("suspended", [])

        # Determine key absences (major severity or high-value)
        key_abs = [p["name"] for p in injured
                   if p.get("severity") in ("major", "critical")]

        # Calculate power loss estimate
        squad_val = extracted.get("squad_value_eur_millions", 0) * 1_000_000
        power_loss = min(50.0, len(injured) * 3.5 + len(key_abs) * 5.0)

        return {
            "injured_players": injured,
            "suspended_players": suspended,
            "injury_count": len(injured),
            "suspension_count": len(suspended),
            "squad_value_eur": squad_val,
            "key_absences": key_abs,
            "power_loss_pct": round(power_loss, 2),
            "source": "llm_extraction",
        }

    def _save_status(self, team_id: int, status: dict):
        """Upsert team status into DB."""
        injured_json = json.dumps(status.get("injured_players", []), ensure_ascii=False)
        suspended_json = json.dumps(status.get("suspended_players", []), ensure_ascii=False)
        key_abs_json = json.dumps(status.get("key_absences", []), ensure_ascii=False)

        data_hash = hashlib.md5(
            (injured_json + suspended_json).encode()
        ).hexdigest()

        expires = (datetime.now() + timedelta(hours=AGENT_CACHE_TTL_HOURS)).isoformat()

        self.db.execute("""
            INSERT INTO team_status (
                team_id, injured_players, suspended_players,
                injury_count, suspension_count, squad_value_eur,
                key_absences, power_loss_pct, source, data_hash,
                fetched_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(team_id) DO UPDATE SET
                injured_players = excluded.injured_players,
                suspended_players = excluded.suspended_players,
                injury_count = excluded.injury_count,
                suspension_count = excluded.suspension_count,
                squad_value_eur = excluded.squad_value_eur,
                key_absences = excluded.key_absences,
                power_loss_pct = excluded.power_loss_pct,
                source = excluded.source,
                data_hash = excluded.data_hash,
                fetched_at = excluded.fetched_at,
                expires_at = excluded.expires_at
        """, (
            team_id, injured_json, suspended_json,
            status.get("injury_count", 0),
            status.get("suspension_count", 0),
            status.get("squad_value_eur", 0),
            key_abs_json,
            status.get("power_loss_pct", 0),
            status.get("source", "agent"),
            data_hash,
            datetime.now().isoformat(),
            expires,
        ))


# ── Predictor Integration Layer ──────────────────────────────────────
def get_team_status_from_db(db, team_id: int) -> dict:
    """Read cached team status for prediction — ZERO internet calls.

    This is the function predictor.py imports.
    Returns injury count, power loss, key absences — all from DB cache.
    """
    ensure_team_status_table(db)
    row = db.fetchone(
        "SELECT * FROM team_status WHERE team_id = ?", (team_id,)
    )
    if not row:
        return {
            "injury_count": 0,
            "suspension_count": 0,
            "power_loss_pct": 0.0,
            "key_absences": [],
            "squad_value_eur": 0.0,
        }

    try:
        key_abs = json.loads(row.get("key_absences") or "[]")
    except (json.JSONDecodeError, TypeError):
        key_abs = []

    return {
        "injury_count": row.get("injury_count", 0) or 0,
        "suspension_count": row.get("suspension_count", 0) or 0,
        "power_loss_pct": row.get("power_loss_pct", 0.0) or 0.0,
        "key_absences": key_abs,
        "squad_value_eur": row.get("squad_value_eur", 0.0) or 0.0,
    }


def apply_agent_penalty(h_prob: float, d_prob: float, a_prob: float,
                        home_status: dict, away_status: dict,
                        temperature: float = 1.15) -> tuple[float, float, float]:
    """Apply injury/availability penalty to raw probabilities using log-odds space with temperature scaling.

    Logic:
      - Converts raw calibrated probabilities to log-probabilities (z = ln(p)).
      - Applies shifts in log-odds space (representing multiplicative scaling).
      - Re-normalizes using Softmax with a Temperature (T) parameter for numerical stability.
      - Uses epsilon protection to prevent ln(0) errors.
    """
    home_loss = home_status.get("power_loss_pct", 0)
    away_loss = away_status.get("power_loss_pct", 0)
    home_key = len(home_status.get("key_absences", []))
    away_key = len(away_status.get("key_absences", []))

    # Net advantage: positive = away benefits from home injuries
    net_penalty = (home_loss - away_loss) / 100.0
    key_penalty = (home_key - away_key) * 0.02

    total_shift = net_penalty * 0.3 + key_penalty

    # Cap the shift to prevent wild swings
    total_shift = max(-0.12, min(0.12, total_shift))

    # Epsilon protection for log-odds conversion
    eps = 1e-15
    h_prob = max(eps, min(1.0 - eps, h_prob))
    d_prob = max(eps, min(1.0 - eps, d_prob))
    a_prob = max(eps, min(1.0 - eps, a_prob))

    # 1. Convert to log-odds (log-probabilities)
    z_h = math.log(h_prob)
    z_d = math.log(d_prob)
    z_a = math.log(a_prob)

    # 2. Apply shift to log-odds space (scaled by 2.5 for realistic probability change)
    # A positive total_shift means Home team is weakened:
    # - Decreases Home log-odds
    # - Increases Draw log-odds (+0.4 weight)
    # - Increases Away log-odds (+0.6 weight)
    scaling_factor = 2.5
    adj_shift = total_shift * scaling_factor

    if abs(total_shift) >= 0.005:
        if adj_shift > 0:
            z_h -= adj_shift
            z_d += adj_shift * 0.4
            z_a += adj_shift * 0.6
        else:
            shift = abs(adj_shift)
            z_a -= shift
            z_d += shift * 0.4
            z_h += shift * 0.6

    # 3. Softmax activation with Temperature scaling (with numerical stability)
    max_z = max(z_h, z_d, z_a)
    exp_h = math.exp((z_h - max_z) / temperature)
    exp_d = math.exp((z_d - max_z) / temperature)
    exp_a = math.exp((z_a - max_z) / temperature)

    total_exp = exp_h + exp_d + exp_a
    if total_exp > 0:
        h_prob = exp_h / total_exp
        d_prob = exp_d / total_exp
        a_prob = exp_a / total_exp

    return (
        round(max(0.01, h_prob), 4),
        round(max(0.01, d_prob), 4),
        round(max(0.01, a_prob), 4),
    )


# ── CLI Entry Point ──────────────────────────────────────────────────
async def main():
    import argparse
    parser = argparse.ArgumentParser(description="Güzel Tahmin Data Agent")
    parser.add_argument("--league", type=str, default=None, help="Filter by league code")
    parser.add_argument("--force", action="store_true", help="Ignore cache, re-fetch all")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(message)s")

    agent = DataAgent()
    result = await agent.run(league_filter=args.league, force=args.force)
    print(f"\n[Agent] Result: {result}")


if __name__ == "__main__":
    asyncio.run(main())
