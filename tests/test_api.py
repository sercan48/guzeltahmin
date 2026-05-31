import unittest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from app.api import app

class TestAdminAPI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch('app.api.load_config')
    def test_get_config(self, mock_load_config):
        mock_load_config.return_value = {
            "temperature": 1.15,
            "live_betting_enabled": False,
            "self_learning_enabled": True
        }
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["temperature"], 1.15)

    @patch('app.api.save_config')
    @patch('app.api.load_config')
    def test_post_config(self, mock_load_config, mock_save_config):
         mock_load_config.return_value = {
             "temperature": 1.35,
             "live_betting_enabled": True,
             "self_learning_enabled": True
         }
         payload = {
             "temperature": 1.35,
             "live_betting_enabled": True,
             "self_learning_enabled": True
         }
         response = self.client.post("/api/config", json=payload)
         self.assertEqual(response.status_code, 200)
         self.assertEqual(response.json()["config"]["temperature"], 1.35)
         mock_save_config.assert_called_once()

    @patch('app.api.get_backend')
    def test_get_subscribers(self, mock_get_backend):
        mock_db = MagicMock()
        mock_db.fetchall.return_value = [
            {"telegram_id": 12345, "username": "testuser", "plan": "vip", "is_active": 1}
        ]
        mock_get_backend.return_value = mock_db

        response = self.client.get("/api/subscribers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["username"], "testuser")
        mock_db.connect.assert_called_once()
        mock_db.close.assert_called_once()

    @patch('app.api.Path.glob')
    @patch('app.api.Path.exists')
    def test_get_backups(self, mock_exists, mock_glob):
        mock_exists.return_value = True
        
        mock_file = MagicMock()
        mock_file.name = "guzel_tahmin_backup_20260529_120000.db"
        mock_stat = MagicMock()
        mock_stat.st_size = 2 * 1024 * 1024  # 2MB
        mock_stat.st_mtime = 1780056000     # Epoch timestamp
        mock_file.stat.return_value = mock_stat
        
        mock_glob.return_value = [mock_file]
        
        response = self.client.get("/api/database/backups")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["name"], "guzel_tahmin_backup_20260529_120000.db")
        self.assertEqual(response.json()[0]["size_mb"], 2.0)

    @patch('shutil.copy2')
    @patch('app.api.Path.exists')
    def test_create_backup(self, mock_exists, mock_copy):
        mock_exists.return_value = True
        response = self.client.post("/api/database/backup")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        mock_copy.assert_called_once()

    @patch('app.api.get_backend')
    def test_check_expirations(self, mock_get_backend):
        mock_db = MagicMock()
        # Mock expired user in db fetchall
        mock_db.fetchall.return_value = [
            {"telegram_id": 99999, "username": "expired_user", "end_date": "2026-05-28 12:00:00"}
        ]
        mock_get_backend.return_value = mock_db
        
        # We don't configure bot token/channel in test environment, so no requests mock is needed,
        # but let's make sure it queries db and updates is_active
        response = self.client.post("/api/subscribers/check_expirations")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kicked_count"], 1)
        self.assertEqual(response.json()["kicked_users"][0]["username"], "expired_user")
        
        # Verify it updated database and logged activity
        mock_db.execute.assert_any_call("UPDATE subscribers SET is_active = 0 WHERE telegram_id = ?", (99999,))
        mock_db.close.assert_called_once()

    @patch('app.api.get_backend')
    def test_get_clv_analytics(self, mock_get_backend):
        mock_db = MagicMock()
        mock_db.fetchall.return_value = [
            {
                "clv_pct": 5.0, "clv_class": "POSITIVE_CLV", "value_edge": 0.04, "value_class": "LOW_VALUE",
                "predicted_result": "H", "actual_result": "H", "prediction_odds": 2.0, "closing_odds": 2.1,
                "league_code": "E0"
            }
        ]
        mock_get_backend.return_value = mock_db
        response = self.client.get("/api/analytics/clv")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["overall"]["average_clv"], 5.0)
        self.assertEqual(data["overall"]["total_count"], 1)
        self.assertIn("E0", data["leagues"])
        self.assertEqual(data["leagues"]["E0"]["average_clv"], 5.0)
        mock_db.close.assert_called_once()

    @patch('app.api.get_backend')
    def test_get_edge_analytics(self, mock_get_backend):
        mock_db = MagicMock()
        mock_db.fetchall.return_value = [
            {
                "clv_pct": 5.0, "value_edge": 0.04, "value_class": "LOW_VALUE",
                "predicted_result": "H", "actual_result": "H", "prediction_odds": 2.0,
                "league_code": "E0"
            }
        ]
        mock_get_backend.return_value = mock_db
        response = self.client.get("/api/analytics/edge")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["overall"]["average_edge"], 0.04)
        self.assertEqual(data["overall"]["edge_distribution"]["LOW_VALUE"], 1)
        mock_db.close.assert_called_once()

if __name__ == '__main__':
    unittest.main()
