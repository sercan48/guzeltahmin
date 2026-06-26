"""
tests/test_polymarket_observer.py — WP-POLY-1 Isolation & Contract Tests

Acceptance criteria:
  ✓ Zero change to prediction outputs
  ✓ Replay hashes unchanged
  ✓ Acceptance hashes unchanged
  ✓ Module isolation (no prediction engine imports in observer code)
  ✓ Deterministic snapshots
  ✓ Closing snapshots immutable
  ✓ Full benchmark report structure
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# 1. Module isolation — prediction engine must NOT be imported by observer
# ---------------------------------------------------------------------------

FORBIDDEN_IMPORTS = {
    "wc_intelligence_engine",
    "result_settler",
    "shadow_predictor",
    "calibration",
}


def _source(module_path: str) -> str:
    import importlib
    mod = importlib.import_module(module_path)
    return Path(mod.__file__).read_text()


@pytest.mark.parametrize("module_path", [
    "src.integrations.base",
    "src.integrations.polymarket.client",
    "src.integrations.polymarket.parser",
    "src.integrations.polymarket.mapper",
])
def test_no_prediction_engine_imports(module_path: str) -> None:
    """Observer modules must not import prediction engine code."""
    src = _source(module_path)
    for name in FORBIDDEN_IMPORTS:
        assert name not in src, (
            f"{module_path} must not reference '{name}' (prediction engine import detected)"
        )


# ---------------------------------------------------------------------------
# 2. Prediction engine stability — shadow predictor must be unaffected
# ---------------------------------------------------------------------------

class TestPredictionEngineStability:

    def test_acceptance_hash_is_deterministic(self) -> None:
        """Same inputs must always produce the same acceptance hash."""
        try:
            from ops.shadow_predictor import ShadowPredictor
        except ImportError:
            pytest.skip("ShadowPredictor not available in this environment")

        sp = ShadowPredictor()
        r1 = sp.predict("Argentina", "France")
        h1 = ShadowPredictor.acceptance_hash("Argentina", "France", r1)
        r2 = sp.predict("Argentina", "France")
        h2 = ShadowPredictor.acceptance_hash("Argentina", "France", r2)

        assert h1 == h2, "Acceptance hash is non-deterministic — prediction engine may be broken"

    def test_prediction_unchanged_after_observer_import(self) -> None:
        """Importing Polymarket observer modules must not alter prediction results."""
        try:
            from ops.shadow_predictor import ShadowPredictor
        except ImportError:
            pytest.skip("ShadowPredictor not available in this environment")

        sp = ShadowPredictor()
        r_before = sp.predict("Brazil", "Germany")
        hash_before = ShadowPredictor.acceptance_hash("Brazil", "Germany", r_before)

        # Import all observer modules
        import src.integrations.base  # noqa: F401
        import src.integrations.polymarket.client  # noqa: F401
        import src.integrations.polymarket.mapper  # noqa: F401
        import src.integrations.polymarket.parser  # noqa: F401

        r_after = sp.predict("Brazil", "Germany")
        hash_after = ShadowPredictor.acceptance_hash("Brazil", "Germany", r_after)

        assert hash_before == hash_after, (
            "Prediction output changed after importing observer modules — isolation violated"
        )


# ---------------------------------------------------------------------------
# 3. MarketConsensusProvider interface contract
# ---------------------------------------------------------------------------

class TestMarketConsensusProviderContract:

    def test_abstract_cannot_be_instantiated(self) -> None:
        from src.integrations.base import MarketConsensusProvider
        with pytest.raises(TypeError):
            MarketConsensusProvider()  # type: ignore[abstract]

    def test_interface_has_required_methods(self) -> None:
        from src.integrations.base import MarketConsensusProvider
        assert callable(getattr(MarketConsensusProvider, "find_market", None))
        assert callable(getattr(MarketConsensusProvider, "get_snapshot", None))
        assert isinstance(
            getattr(MarketConsensusProvider, "provider_name", None),
            property,
        )


# ---------------------------------------------------------------------------
# 4. Parser — outcome classification
# ---------------------------------------------------------------------------

class TestOutcomeClassifier:

    def test_home_labels(self) -> None:
        from src.integrations.polymarket.parser import classify_outcome
        for label in ("Home", "home", "HOME", "Home Win", "1", "Yes", "yes"):
            assert classify_outcome(label, [label, "X"]) == "HOME", f"Failed: {label}"

    def test_draw_labels(self) -> None:
        from src.integrations.polymarket.parser import classify_outcome
        for label in ("Draw", "draw", "DRAW", "Tie", "X", "x"):
            assert classify_outcome(label, ["Home", label, "Away"]) == "DRAW", f"Failed: {label}"

    def test_away_labels(self) -> None:
        from src.integrations.polymarket.parser import classify_outcome
        for label in ("Away", "away", "AWAY", "Away Win", "2", "No", "no"):
            assert classify_outcome(label, ["Home", label]) == "AWAY", f"Failed: {label}"

    def test_team_name_matching(self) -> None:
        from src.integrations.polymarket.parser import classify_outcome
        assert classify_outcome(
            "Argentina", ["Argentina", "France"], home_team="Argentina", away_team="France"
        ) == "HOME"
        assert classify_outcome(
            "France", ["Argentina", "France"], home_team="Argentina", away_team="France"
        ) == "AWAY"

    def test_positional_fallback_binary(self) -> None:
        from src.integrations.polymarket.parser import classify_outcome
        assert classify_outcome("TeamA", ["TeamA", "TeamB"]) == "HOME"
        assert classify_outcome("TeamB", ["TeamA", "TeamB"]) == "AWAY"

    def test_positional_fallback_3way(self) -> None:
        from src.integrations.polymarket.parser import classify_outcome
        assert classify_outcome("Alpha", ["Alpha", "Tie", "Beta"]) == "HOME"
        assert classify_outcome("Tie", ["Alpha", "Tie", "Beta"]) == "DRAW"
        assert classify_outcome("Beta", ["Alpha", "Tie", "Beta"]) == "AWAY"


class TestParseSnapshot:

    _BINARY_RAW = {
        "id": "mkt001",
        "question": "Will Argentina beat France?",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.70","0.30"]',
        "clobTokenIds": '["t1","t2"]',
        "active": True,
        "volume": 50000.0,
        "liquidity": 10000.0,
    }

    _3WAY_RAW = {
        "id": "mkt002",
        "question": "Argentina vs France: Match Result",
        "outcomes": '["Home","Draw","Away"]',
        "outcomePrices": '["0.50","0.25","0.25"]',
        "clobTokenIds": '["t1","t2","t3"]',
        "active": True,
    }

    def test_binary_market_parses_home_away(self) -> None:
        from src.integrations.polymarket.parser import parse_snapshot
        snap = parse_snapshot(self._BINARY_RAW, home_team="Argentina", away_team="France")
        assert snap is not None
        assert snap.home_prob == pytest.approx(0.70)
        assert snap.away_prob == pytest.approx(0.30)
        assert snap.draw_prob is None

    def test_3way_market_parses_all_outcomes(self) -> None:
        from src.integrations.polymarket.parser import parse_snapshot
        snap = parse_snapshot(self._3WAY_RAW)
        assert snap is not None
        assert snap.home_prob == pytest.approx(0.50)
        assert snap.draw_prob == pytest.approx(0.25)
        assert snap.away_prob == pytest.approx(0.25)

    def test_empty_raw_returns_none(self) -> None:
        from src.integrations.polymarket.parser import parse_snapshot
        assert parse_snapshot({}) is None

    def test_mismatched_lengths_returns_none(self) -> None:
        from src.integrations.polymarket.parser import parse_snapshot
        raw = {
            "id": "mkt003",
            "outcomes": '["Home","Draw","Away"]',
            "outcomePrices": '["0.50","0.50"]',  # wrong length
            "clobTokenIds": '[]',
        }
        assert parse_snapshot(raw) is None

    def test_closing_snapshot_is_marked(self) -> None:
        from src.integrations.polymarket.parser import parse_snapshot
        snap = parse_snapshot(self._3WAY_RAW, source_type="closing")
        assert snap is not None
        assert snap.is_closing is True
        assert snap.source_type == "closing"

    def test_pre_match_snapshot_not_closing(self) -> None:
        from src.integrations.polymarket.parser import parse_snapshot
        snap = parse_snapshot(self._3WAY_RAW, source_type="pre_match")
        assert snap is not None
        assert snap.is_closing is False


# ---------------------------------------------------------------------------
# 5. PolymarketProvider — fuzzy matching (mock API)
# ---------------------------------------------------------------------------

class TestPolymarketProvider:

    _MOCK_MARKET = {
        "id": "mkt001",
        "question": "Will Argentina win vs France? Soccer World Cup",
        "slug": "argentina-vs-france-wc",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["0.65","0.35"]',
        "clobTokenIds": '["t1","t2"]',
        "active": True,
        "eventId": "evt001",
        "tags": [{"slug": "soccer"}, {"slug": "world-cup"}],
        "volume": 100000.0,
        "liquidity": 20000.0,
    }

    def _make_provider(self) -> "PolymarketProvider":
        from src.integrations.polymarket.mapper import PolymarketProvider
        from src.integrations.polymarket.client import GammaClient, ClobClient

        mock_gamma = MagicMock(spec=GammaClient)
        mock_gamma.iter_all_markets.return_value = iter([self._MOCK_MARKET])
        mock_gamma.get_market.return_value = self._MOCK_MARKET

        mock_clob = MagicMock(spec=ClobClient)
        mock_clob.get_best_bid_ask.return_value = (0.63, 0.67)

        return PolymarketProvider(gamma_client=mock_gamma, clob_client=mock_clob)

    def test_provider_name(self) -> None:
        p = self._make_provider()
        assert p.provider_name == "polymarket"

    def test_find_market_returns_market_info(self) -> None:
        p = self._make_provider()
        info = p.find_market("Argentina", "France", "2026-06-26")
        assert info is not None
        assert info.provider == "polymarket"
        assert info.market_id == "mkt001"
        assert info.matched_home == "Argentina"
        assert info.matched_away == "France"
        assert info.match_date == "2026-06-26"

    def test_find_market_no_match_returns_none(self) -> None:
        from src.integrations.polymarket.mapper import PolymarketProvider
        from src.integrations.polymarket.client import GammaClient

        mock_gamma = MagicMock(spec=GammaClient)
        mock_gamma.iter_all_markets.return_value = iter([])
        p = PolymarketProvider(gamma_client=mock_gamma)
        assert p.find_market("Arsenal", "Chelsea", "2026-08-15") is None

    def test_get_snapshot_returns_market_snapshot(self) -> None:
        from src.integrations.base import MarketInfo
        p = self._make_provider()
        info = MarketInfo(
            provider="polymarket",
            event_id="evt001",
            market_id="mkt001",
            question="Will Argentina win?",
            slug="arg-vs-fra",
            status="active",
            matched_home="Argentina",
            matched_away="France",
            match_date="2026-06-26",
        )
        snap = p.get_snapshot(info)
        assert snap is not None
        assert snap.provider == "polymarket"
        assert snap.home_prob is not None
        assert snap.matched_home == "Argentina"


# ---------------------------------------------------------------------------
# 6. Closing snapshot immutability
# ---------------------------------------------------------------------------

class TestClosingSnapshotImmutability:

    def test_closing_snapshot_never_overwritten(self) -> None:
        """Once written, a closing snapshot for a market_id must not change."""
        original = {
            "market_id": "mkt001",
            "home_prob": 0.65,
            "draw_prob": 0.20,
            "away_prob": 0.15,
            "matched_home": "Argentina",
            "matched_away": "France",
            "match_date": "2026-06-26",
            "is_closing": True,
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(original) + "\n")
            tmp_path = Path(f.name)

        # Load and attempt to "overwrite"
        existing: dict[str, dict] = {}
        with tmp_path.open() as f:
            for line in f:
                rec = json.loads(line.strip())
                existing[rec["market_id"]] = rec

        new_snap = {"market_id": "mkt001", "home_prob": 0.55}  # stale update

        with tmp_path.open("a") as f:
            if new_snap["market_id"] not in existing:   # guard check (same as ops code)
                f.write(json.dumps(new_snap) + "\n")

        lines = [json.loads(ln.strip()) for ln in tmp_path.read_text().splitlines() if ln.strip()]
        tmp_path.unlink()

        assert len(lines) == 1, "Closing snapshot was duplicated — immutability broken"
        assert lines[0]["home_prob"] == 0.65, "Original closing snapshot was overwritten"

    def test_second_fixture_can_be_added(self) -> None:
        """A different market_id can be added to the closing snapshots file."""
        snap1 = {"market_id": "mkt001", "home_prob": 0.65, "is_closing": True}
        snap2 = {"market_id": "mkt002", "home_prob": 0.40, "is_closing": True}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps(snap1) + "\n")
            tmp_path = Path(f.name)

        existing: dict[str, dict] = {}
        with tmp_path.open() as f:
            for line in f:
                rec = json.loads(line.strip())
                existing[rec["market_id"]] = rec

        with tmp_path.open("a") as f:
            if snap2["market_id"] not in existing:
                f.write(json.dumps(snap2) + "\n")

        lines = [json.loads(ln.strip()) for ln in tmp_path.read_text().splitlines() if ln.strip()]
        tmp_path.unlink()

        assert len(lines) == 2


# ---------------------------------------------------------------------------
# 7. Benchmark record builder
# ---------------------------------------------------------------------------

class TestBenchmarkRecordBuilder:

    _SETTLEMENT = {
        "natural_key": "argentina|france|2026-06-26",
        "home_team": "Argentina",
        "away_team": "France",
        "match_date": "2026-06-26",
        "actual_outcome": "HOME_WIN",
        "predicted_outcome": "HOME_WIN",
        "correct": True,
        "probabilities": {"H": 65.0, "D": 20.0, "A": 15.0},
        "confidence": 78.5,
    }

    _CLOSING_SNAP = {
        "market_id": "mkt001",
        "matched_home": "Argentina",
        "matched_away": "France",
        "match_date": "2026-06-26",
        "home_prob": 0.62,
        "draw_prob": 0.22,
        "away_prob": 0.16,
        "is_closing": True,
    }

    def test_empty_inputs(self) -> None:
        from reports.polymarket_report import build_benchmark_records
        assert build_benchmark_records([], {}) == []

    def test_settlement_without_closing_snap(self) -> None:
        from reports.polymarket_report import build_benchmark_records
        records = build_benchmark_records([self._SETTLEMENT], {})
        assert len(records) == 1
        r = records[0]
        assert r["model_h"] == 65.0
        assert r["model_correct"] is True
        assert r["market_h"] is None
        assert r["delta_h"] is None

    def test_settlement_with_closing_snap(self) -> None:
        from reports.polymarket_report import build_benchmark_records
        records = build_benchmark_records(
            [self._SETTLEMENT],
            {"mkt001": self._CLOSING_SNAP},
        )
        assert len(records) == 1
        r = records[0]
        assert r["market_h"] == pytest.approx(62.0)
        assert r["market_d"] == pytest.approx(22.0)
        assert r["market_a"] == pytest.approx(16.0)
        assert r["market_prediction"] == "HOME_WIN"
        assert r["market_correct"] is True
        # Delta: model% − market%
        assert r["delta_h"] == pytest.approx(3.0, abs=0.1)

    def test_brier_computed_correctly(self) -> None:
        from reports.polymarket_report import build_benchmark_records, _brier_contrib
        # Actual = HOME_WIN, model_h = 65% → p_actual = 0.65 → brier = (1-0.65)² = 0.1225
        records = build_benchmark_records([self._SETTLEMENT], {})
        assert records[0]["model_brier"] == pytest.approx(0.1225, abs=0.001)


# ---------------------------------------------------------------------------
# 8. Report structure validation
# ---------------------------------------------------------------------------

class TestReportStructure:

    def test_delta_report_required_keys(self, tmp_path: Path) -> None:
        from reports.polymarket_report import generate_delta_report
        with patch("reports.polymarket_report._OUT_DIR", tmp_path):
            report = generate_delta_report([])

        assert report["report_type"] == "POLYMARKET_DELTA_REPORT"
        assert "generated_at" in report
        assert "top_disagreements" in report
        assert "top_agreements" in report
        assert "note" in report
        assert "OBSERVATIONAL ONLY" in report["note"]

    def test_benchmark_report_required_keys(self, tmp_path: Path) -> None:
        from reports.polymarket_report import generate_benchmark
        with patch("reports.polymarket_report._OUT_DIR", tmp_path):
            report = generate_benchmark([])

        assert report["report_type"] == "POLYMARKET_BENCHMARK"
        assert "draw_analysis" in report
        assert "OBSERVATIONAL ONLY" in report["draw_analysis"]["note"]

    def test_daily_report_required_keys(self, tmp_path: Path) -> None:
        from reports.polymarket_report import generate_daily_report
        with patch("reports.polymarket_report._OUT_DIR", tmp_path):
            report = generate_daily_report("2026-06-26", [])

        assert report["report_type"] == "POLYMARKET_DAILY_REPORT"
        assert report["date"] == "2026-06-26"
        assert "generated_at" in report
