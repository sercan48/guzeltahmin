import sys
import os
import json
import urllib.request
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.db.base import get_backend
from config.leagues import ACTIVE_LEAGUES

def send_telegram_message(token, chat_id, text):
    """Send HTML message via Telegram Bot API using urllib."""
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        print(f"[TG ERROR] Failed to send to {chat_id}: {e}")
        return None

def main():
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    free_channel = os.getenv("TELEGRAM_FREE_CHANNEL_ID", "@guzeltahmin")
    premium_channel = os.getenv("TELEGRAM_CHANNEL_ID", "-1003977338555")

    if not token:
        print("TELEGRAM_BOT_TOKEN is missing in .env")
        return

    db = get_backend()
    db.connect()

    query = """
        SELECT p.predicted_result, p.confidence_score, p.home_win_prob, p.draw_prob, p.away_win_prob,
               m.date, m.league_code, t1.name as home_team, t2.name as away_team
        FROM predictions p
        JOIN matches m ON p.match_id = m.id
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE DATE(m.date) BETWEEN '2026-05-29' AND '2026-05-31'
        ORDER BY m.date ASC, p.confidence_score DESC
    """
    preds = db.fetchall(query)
    db.close()

    if not preds:
        print("No predictions found for this weekend in the DB.")
        return

    print(f"Loaded {len(preds)} predictions from database.")

    # Group predictions by Date
    by_date = {}
    for p in preds:
        m_date = p["date"][:10]  # YYYY-MM-DD
        by_date.setdefault(m_date, []).append(p)

    # Build the bulletin message
    lines = [
        "🤖 <b>GÜZEL TAHMİN — HAFTA SONU BÜLTENİ</b> 🤖",
        f"📅 Tarih: 29-30-31 Mayıs 2026",
        "=====================================",
        "Yeni Log-Odds bazlı sakatlık penalizasyonlu ve kalibre edilmiş Stacking Ensemble modelimizin tahminleri:",
        ""
    ]

    for m_date, date_preds in sorted(by_date.items()):
        dt = datetime.strptime(m_date, "%Y-%m-%d").strftime("%d.%m.%Y")
        lines.append(f"📅 <b>{dt}</b>")
        lines.append("-------------------------------------")
        
        for p in date_preds:
            # Map predictions to human readable labels
            tag = p["predicted_result"]
            
            # Map tag if standard code
            label_map = {
                "1": "Ev Sahibi (1)",
                "X": "Beraberlik (X)",
                "2": "Deplasman (2)",
                "1X": "1X Çifte Şans",
                "X2": "X2 Çifte Şans",
                "12": "12 Çifte Şans",
                "O25": "2.5 Gol Üst",
                "U25": "2.5 Gol Alt",
                "BTTS_Y": "KG Var",
                "BTTS_N": "KG Yok"
            }
            # Also handle potential "MS X" style saved results
            clean_tag = tag.replace("MS ", "").strip()
            pred_label = label_map.get(clean_tag, tag)
            
            h_pct = p["home_win_prob"] * 100
            d_pct = p["draw_prob"] * 100
            a_pct = p["away_win_prob"] * 100
            conf = p["confidence_score"]
            
            league_name = ACTIVE_LEAGUES.get(p["league_code"], type("", (), {"name": p["league_code"]})).name

            lines.append(f"⚽ <b>{p['home_team']} vs {p['away_team']}</b>")
            lines.append(f"🏆 Lig: {league_name}")
            lines.append(f"🎯 <b>Öneri: {pred_label}</b> (Güven: %{conf:.1f})")
            lines.append(f"📊 İhtimaller: H: %{h_pct:.1f} | D: %{d_pct:.1f} | A: %{a_pct:.1f}")
            lines.append("")
        
        lines.append("")

    lines.append("=====================================")
    lines.append("<i>Model: Stacking Ensemble AI (XGBoost + LightGBM + Poisson) + Platt/Isotonic Calibration + Log-Odds Agent Penalty Layer.</i>")
    lines.append("⚠️ <i>Bahis tavsiyesi değildir. Sorumluluk kullanıcıya aittir.</i>")

    full_message = "\n".join(lines)

    # Send to premium channel
    print(f"Sending bülten to Premium Channel ({premium_channel})...")
    resp_prem = send_telegram_message(token, premium_channel, full_message)
    if resp_prem and resp_prem.get("ok"):
        print("Successfully posted to Premium Channel!")
    
    # Send to free channel
    print(f"Sending bülten to Free Channel ({free_channel})...")
    resp_free = send_telegram_message(token, free_channel, full_message)
    if resp_free and resp_free.get("ok"):
        print("Successfully posted to Free Channel!")

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    main()
