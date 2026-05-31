import sys
import json
from pathlib import Path
sys.path.insert(0, str(Path('.').absolute()))

from src.db.base import get_backend
from src.model.predictor import predict_match

def run():
    db = get_backend()
    db.connect()

    artifact_path = r'C:\Users\WIN\.gemini\antigravity\brain\49cc3e5d-4f2b-44fb-8305-f25da8802c86\gelecek_tahminler_mayis.json'
    with open(artifact_path, 'r', encoding='utf-8') as f:
        matches = json.load(f)

    print(f"| Tarih | Lig | Ev Sahibi | Deplasman | Eski Tahmin | Yeni Tahmin | Yeni Güven (%) |")
    print(f"|---|---|---|---|---|---|---|")
    for m in matches:
        date = m['date']
        h_name = m['home']
        a_name = m['away']
        league = m['league']
        old_pred = m['prediction']
        
        # Match teams might have slight name differences, so try a simple search if exact fails
        db_match = db.fetchone("SELECT id, home_team_id, away_team_id, season FROM matches WHERE date LIKE ? AND league_code = ? LIMIT 1", (f"{date}%", league))
        
        if not db_match:
            print(f"| {date} | {league} | {h_name} | {a_name} | {old_pred} | Bulunamadı | - |")
            continue
            
        try:
            pred = predict_match(db, db_match['home_team_id'], db_match['away_team_id'], league, db_match['season'])
            new_pred = pred['predicted_result']
            new_conf = pred['confidence_score'] * 10
            print(f"| {date} | {league} | {h_name} | {a_name} | {old_pred} | {new_pred} | {new_conf:.1f} |")
        except Exception as e:
            print(f"| {date} | {league} | {h_name} | {a_name} | {old_pred} | HATA: {str(e)[:20]} | - |")

    db.close()

if __name__ == '__main__':
    run()
