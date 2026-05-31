import unittest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.model.predictor import predict_match

class TestTemperatureParameter(unittest.TestCase):
    def setUp(self):
        self.config_path = Path(__file__).parent.parent / "data" / "admin_config.json"
        self.config_backup = None
        
        # Backup existing config if any
        if self.config_path.exists():
            with open(self.config_path, "r", encoding="utf-8") as f:
                self.config_backup = f.read()

    def tearDown(self):
        # Restore backup
        if self.config_backup is not None:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                f.write(self.config_backup)
        elif self.config_path.exists():
            self.config_path.unlink()

    @patch('src.model.predictor.build_match_features')
    @patch('src.model.predictor._load_ensemble')
    @patch('src.model.predictor._load_calibrator')
    @patch('src.agents.data_agent.get_team_status_from_db')
    @patch('src.agents.data_agent.apply_agent_penalty')
    def test_predict_match_loads_temperature_from_config(self, mock_apply_penalty, mock_get_status, mock_load_calibrator, mock_load_ensemble, mock_build_features):
        # Setup mocks
        mock_build_features.return_value = {
            "_home_name": "HomeTeam",
            "_away_name": "AwayTeam",
            "home_win_prob": 0.4,
            "draw_prob": 0.3,
            "away_win_prob": 0.3
        }
        
        mock_ensemble = MagicMock()
        mock_ensemble.predict_full.return_value = {
            "h_prob": 0.4, "d_prob": 0.3, "a_prob": 0.3,
            "xgb_probs": [0.4, 0.3, 0.3], "lgb_probs": [0.4, 0.3, 0.3], "poi_probs": [0.4, 0.3, 0.3],
            "over25_prob": 0.5, "btts_prob": 0.5, "top_scores": []
        }
        mock_load_ensemble.return_value = (mock_ensemble, "ensemble")
        mock_load_calibrator.return_value = None
        mock_get_status.return_value = {"injury_count": 0, "power_loss_pct": 0, "key_absences": []}
        
        mock_apply_penalty.return_value = (0.4, 0.3, 0.3)

        # Write test temperature parameter to config
        test_temp = 1.85
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"temperature": test_temp}, f)

        # Run prediction
        db_mock = MagicMock()
        predict_match(db_mock, 1, 2, "T1", "2025-2026")

        # Verify apply_agent_penalty was called with the temperature from the JSON file (1.85)
        mock_apply_penalty.assert_called_once()
        called_args, called_kwargs = mock_apply_penalty.call_args
        self.assertEqual(called_kwargs.get("temperature"), test_temp)

if __name__ == '__main__':
    unittest.main()
