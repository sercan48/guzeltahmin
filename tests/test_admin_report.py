import unittest
from unittest.mock import MagicMock, patch
from app.bot.admin import _generate_detailed_report

class TestAdminReport(unittest.TestCase):
    @patch('app.bot.admin.get_backend')
    def test_generate_detailed_report_empty(self, mock_get_backend):
        # Setup mock db to return empty list of predictions
        mock_db = MagicMock()
        mock_db.fetchall.return_value = []
        mock_db.fetchone.return_value = {'c': 0}
        mock_get_backend.return_value = mock_db
        
        report = _generate_detailed_report()
        self.assertIn("Sistemde henüz sonuçlanmış maç tahmini bulunmamaktadır.", report)
        mock_db.connect.assert_called_once()
        mock_db.close.assert_called_once()

    @patch('app.bot.admin.get_backend')
    def test_generate_detailed_report_with_data(self, mock_get_backend):
        # Setup mock predictions with correct and incorrect results
        mock_db = MagicMock()
        mock_db.fetchone.return_value = {'c': 0}
        mock_db.fetchall.return_value = [
            # Main wins, Value bet wins
            {
                'predicted_result': 'H',
                'actual_result': 'H',
                'home_win_prob': 0.60,
                'draw_prob': 0.20,
                'away_win_prob': 0.20,
                'home_odds': 2.00,
                'draw_odds': 3.50,
                'away_odds': 3.50
            },
            # Main loses, Value bet loses
            {
                'predicted_result': 'A',
                'actual_result': 'H',
                'home_win_prob': 0.20,
                'draw_prob': 0.20,
                'away_win_prob': 0.60,
                'home_odds': 3.50,
                'draw_odds': 3.50,
                'away_odds': 2.00
            }
        ]
        mock_get_backend.return_value = mock_db
        
        report = _generate_detailed_report()
        self.assertIn("DETAYLI BAŞARI VE ROI RAPORU", report)
        self.assertIn("Toplam Maç Sayısı: *2*", report)
        self.assertIn("*Ana Tahmin Başarısı (1X2)*: `%50.0` (1/2)", report)
        self.assertIn("AI DEĞERLİ (VALUE) TAHMİNLER", report)
        
if __name__ == '__main__':
    unittest.main()
