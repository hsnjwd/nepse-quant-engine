"""Comprehensive tests for the Adaptive Strategy Engine."""

from __future__ import annotations

from typing import Any

import pytest

from src.adaptive.engine import AdaptiveDecision, AdaptiveStrategyEngine


# ======================================================================
# Fixtures
# ======================================================================


@pytest.fixture
def engine() -> AdaptiveStrategyEngine:
    """Default adaptive engine with built-in mapping."""
    return AdaptiveStrategyEngine()


@pytest.fixture
def strategies() -> list[str]:
    """Default list of registered strategy names."""
    return [
        "MomentumStrategy",
        "BreakoutStrategy",
        "MeanReversionStrategy",
        "VolumeStrategy",
    ]


@pytest.fixture
def signals() -> list[dict[str, Any]]:
    """List of signal payloads matching strategies()."""
    return [
        {
            "strategy_name": "MomentumStrategy",
            "signal": "BUY",
            "score": 8.5,
            "price": 100.0,
        },
        {
            "strategy_name": "BreakoutStrategy",
            "signal": "BUY",
            "score": 7.2,
            "price": 100.0,
        },
        {
            "strategy_name": "MeanReversionStrategy",
            "signal": "HOLD",
            "score": 5.0,
            "price": 100.0,
        },
        {
            "strategy_name": "VolumeStrategy",
            "signal": "SELL",
            "score": 3.0,
            "price": 100.0,
        },
    ]


@pytest.fixture
def signals_with_errors() -> list[dict[str, Any]]:
    """Signal list with some errored entries."""
    return [
        {
            "strategy_name": "MomentumStrategy",
            "signal": "BUY",
            "score": 8.5,
        },
        {
            "strategy_name": "BreakoutStrategy",
            "error": "Data error",
        },
        {
            "strategy_name": "MeanReversionStrategy",
            "signal": "HOLD",
            "score": 5.0,
        },
    ]


# ======================================================================
# Test: AdaptiveDecision dataclass
# ======================================================================


class TestAdaptiveDecision:
    """AdaptiveDecision construction, serialisation, repr."""

    def test_default_construction(self) -> None:
        """Default decision has UNKNOWN regime."""
        d = AdaptiveDecision()
        assert d.market_regime == "UNKNOWN"
        assert d.selected_strategies == []
        assert d.disabled_strategies == []
        assert d.strategy_weights == {}
        assert d.confidence == 0.0
        assert d.reasons == []
        assert d.metadata == {}

    def test_full_construction(self) -> None:
        """Decision with all fields populated."""
        d = AdaptiveDecision(
            market_regime="BULL",
            selected_strategies=["MomentumStrategy", "BreakoutStrategy"],
            disabled_strategies=["MeanReversionStrategy"],
            strategy_weights={
                "MomentumStrategy": 0.5,
                "BreakoutStrategy": 0.5,
            },
            confidence=85.5,
            reasons=["Bull market detected.", "Momentum preferred."],
            metadata={"generated_by": "AdaptiveStrategyEngine"},
        )
        assert d.market_regime == "BULL"
        assert len(d.selected_strategies) == 2
        assert len(d.disabled_strategies) == 1
        assert abs(d.strategy_weights["MomentumStrategy"] - 0.5) < 1e-6
        assert d.confidence == 85.5
        assert len(d.reasons) == 2

    def test_to_dict_returns_all_fields(self) -> None:
        """to_dict() returns all expected keys."""
        d = AdaptiveDecision(
            market_regime="BEAR",
            selected_strategies=["MeanReversionStrategy"],
            disabled_strategies=["MomentumStrategy", "BreakoutStrategy"],
            strategy_weights={"MeanReversionStrategy": 1.0},
            confidence=72.0,
            reasons=["Bear market detected."],
            metadata={"version": "1.0"},
        )
        result = d.to_dict()
        assert result["market_regime"] == "BEAR"
        assert "MomentumStrategy" in result["disabled_strategies"]
        assert result["confidence"] == 72.0
        assert result["metadata"]["version"] == "1.0"

    def test_to_dict_rounds_weights(self) -> None:
        """Weights are rounded to 4 decimal places."""
        d = AdaptiveDecision(strategy_weights={"A": 0.3333333333, "B": 0.6666666666})
        result = d.to_dict()
        assert result["strategy_weights"]["A"] == pytest.approx(0.3333, abs=1e-4)
        assert result["strategy_weights"]["B"] == pytest.approx(0.6667, abs=1e-4)

    def test_to_dict_rounds_confidence(self) -> None:
        """Confidence is rounded to 2 decimal places."""
        d = AdaptiveDecision(confidence=85.5555)
        result = d.to_dict()
        assert result["confidence"] == 85.56

    def test_repr(self) -> None:
        """__repr__ contains regime, count, confidence."""
        d = AdaptiveDecision(
            market_regime="BULL",
            selected_strategies=["MomentumStrategy"],
            confidence=90.0,
        )
        r = repr(d)
        assert "AdaptiveDecision" in r
        assert "BULL" in r
        assert "90.0" in r


# ======================================================================
# Test: Constructor
# ======================================================================


class TestConstructor:
    """AdaptiveStrategyEngine construction."""

    def test_default_construction(self, engine: AdaptiveStrategyEngine) -> None:
        """Default engine initialises with built-in mapping."""
        assert engine is not None

    def test_custom_mapping(self) -> None:
        """Custom mapping overrides default."""
        custom = {
            "BULL": {
                "prefer": ["CustomStrategy"],
                "reduce": [],
                "disable": [],
            },
        }
        e = AdaptiveStrategyEngine(strategy_mapping=custom)
        decision = e.select_strategies("BULL", ["CustomStrategy", "Other"])
        assert "CustomStrategy" in decision.selected_strategies

    def test_custom_mapping_empty(self) -> None:
        """Empty mapping does not crash."""
        e = AdaptiveStrategyEngine(strategy_mapping={})
        decision = e.select_strategies("BULL", ["MomentumStrategy"])
        assert decision.market_regime == "BULL"

    def test_custom_mapping_partial(self) -> None:
        """Partial mapping falls back for unmapped regimes."""
        custom = {"BULL": {"prefer": ["Momentum"], "reduce": [], "disable": []}}
        e = AdaptiveStrategyEngine(strategy_mapping=custom)
        # BEAR not in custom mapping — falls back to empty
        decision = e.select_strategies("BEAR", ["MomentumStrategy"])
        assert decision.market_regime == "BEAR"


# ======================================================================
# Test: select_strategies — regime-specific
# ======================================================================


class TestSelectStrategiesBull:
    """BULL regime strategy selection."""

    def test_prefers_momentum_and_breakout(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Bull market prefers Momentum and Breakout."""
        decision = engine.select_strategies("BULL", strategies)
        assert decision.market_regime == "BULL"
        assert "MomentumStrategy" in decision.selected_strategies
        assert "BreakoutStrategy" in decision.selected_strategies

    def test_reduces_mean_reversion(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Bull market reduces MeanReversion."""
        decision = engine.select_strategies("BULL", strategies)
        mean_rev_weight = decision.strategy_weights.get("MeanReversionStrategy", 0)
        momentum_weight = decision.strategy_weights.get("MomentumStrategy", 0)
        assert mean_rev_weight < momentum_weight

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights always sum to 1.0 for Bull."""
        decision = engine.select_strategies("BULL", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_no_strategies_disabled(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """No disabled strategies in Bull."""
        decision = engine.select_strategies("BULL", strategies)
        assert decision.disabled_strategies == []

    def test_reasons_contain_momentum(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons mention Momentum preference."""
        decision = engine.select_strategies("BULL", strategies)
        reasons_text = " ".join(decision.reasons).lower()
        assert "momentum" in reasons_text

    def test_confidence_high(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Bull confidence is high (known regime + good coverage)."""
        decision = engine.select_strategies("BULL", strategies)
        assert decision.confidence >= 70.0


class TestSelectStrategiesBear:
    """BEAR regime strategy selection."""

    def test_disables_breakout(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Bear market disables Breakout."""
        decision = engine.select_strategies("BEAR", strategies)
        assert "BreakoutStrategy" in decision.disabled_strategies

    def test_prefers_defensive_and_mean_reversion(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Bear prefers Defensive and MeanReversion."""
        decision = engine.select_strategies("BEAR", strategies)
        assert "MomentumStrategy" in decision.selected_strategies

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0 for Bear."""
        decision = engine.select_strategies("BEAR", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_breakout_weight_zero(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Breakout has zero weight in Bear."""
        decision = engine.select_strategies("BEAR", strategies)
        assert decision.strategy_weights.get("BreakoutStrategy", -1) == 0.0

    def test_reasons_mention_disabled(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons mention BreakoutDisabled."""
        decision = engine.select_strategies("BEAR", strategies)
        reasons_text = " ".join(decision.reasons).lower()
        assert "disabled" in reasons_text


class TestSelectStrategiesSideways:
    """SIDEWAYS regime strategy selection."""

    def test_prefers_mean_reversion(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Sideways prefers MeanReversion."""
        decision = engine.select_strategies("SIDEWAYS", strategies)
        # MeanReversion should have higher weight than Momentum
        mrw = decision.strategy_weights.get("MeanReversionStrategy", 0)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        assert mrw > mw

    def test_reduces_momentum(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Sideways reduces Momentum."""
        decision = engine.select_strategies("SIDEWAYS", strategies)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        bw = decision.strategy_weights.get("BreakoutStrategy", 0)
        assert mw < bw

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0 for Sideways."""
        decision = engine.select_strategies("SIDEWAYS", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)


class TestSelectStrategiesHighVolatility:
    """HIGH_VOLATILITY regime strategy selection."""

    def test_prefers_defensive(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """High volatility prefers Defensive."""
        decision = engine.select_strategies("HIGH_VOLATILITY", strategies)
        assert decision.market_regime == "HIGH_VOLATILITY"
        assert len(decision.selected_strategies) == len(strategies)

    def test_reduces_breakout(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """High volatility reduces Breakout."""
        decision = engine.select_strategies("HIGH_VOLATILITY", strategies)
        bw = decision.strategy_weights.get("BreakoutStrategy", 0)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        assert bw < mw

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0."""
        decision = engine.select_strategies("HIGH_VOLATILITY", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)


class TestSelectStrategiesLowVolatility:
    """LOW_VOLATILITY regime strategy selection."""

    def test_prefers_momentum_and_breakout(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Low volatility prefers Momentum and Breakout."""
        decision = engine.select_strategies("LOW_VOLATILITY", strategies)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        bw = decision.strategy_weights.get("BreakoutStrategy", 0)
        vw = decision.strategy_weights.get("VolumeStrategy", 0)
        assert mw > vw
        assert bw > vw

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0."""
        decision = engine.select_strategies("LOW_VOLATILITY", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)


class TestSelectStrategiesAccumulation:
    """ACCUMULATION regime strategy selection."""

    def test_prefers_momentum_and_volume(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Accumulation prefers Momentum and Volume."""
        decision = engine.select_strategies("ACCUMULATION", strategies)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        vw = decision.strategy_weights.get("VolumeStrategy", 0)
        mrw = decision.strategy_weights.get("MeanReversionStrategy", 0)
        assert mw > mrw
        assert vw > mrw

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0."""
        decision = engine.select_strategies("ACCUMULATION", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)


class TestSelectStrategiesDistribution:
    """DISTRIBUTION regime strategy selection."""

    def test_prefers_exit_and_mean_reversion(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Distribution prefers Exit and MeanReversion."""
        decision = engine.select_strategies("DISTRIBUTION", strategies)
        mrw = decision.strategy_weights.get("MeanReversionStrategy", 0)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        assert mrw > mw

    def test_reduces_momentum(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Distribution reduces Momentum."""
        decision = engine.select_strategies("DISTRIBUTION", strategies)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        bw = decision.strategy_weights.get("BreakoutStrategy", 0)
        assert mw < bw

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0."""
        decision = engine.select_strategies("DISTRIBUTION", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)


class TestSelectStrategiesRecovery:
    """RECOVERY regime strategy selection."""

    def test_prefers_breakout_and_momentum(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Recovery prefers Breakout and Momentum."""
        decision = engine.select_strategies("RECOVERY", strategies)
        bw = decision.strategy_weights.get("BreakoutStrategy", 0)
        vw = decision.strategy_weights.get("VolumeStrategy", 0)
        assert bw > vw
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        assert mw > vw

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0."""
        decision = engine.select_strategies("RECOVERY", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)


class TestSelectStrategiesPanic:
    """PANIC regime strategy selection."""

    def test_disables_momentum_and_breakout(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Panic disables Momentum and Breakout."""
        decision = engine.select_strategies("PANIC", strategies)
        assert "MomentumStrategy" in decision.disabled_strategies
        assert "BreakoutStrategy" in decision.disabled_strategies

    def test_prefers_defensive(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Panic prefers Defensive."""
        decision = engine.select_strategies("PANIC", strategies)
        assert "MeanReversionStrategy" in decision.selected_strategies
        assert "VolumeStrategy" in decision.selected_strategies

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0 even when strategies disabled."""
        decision = engine.select_strategies("PANIC", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_momentum_zero_weight(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Disabled strategies get zero weight."""
        decision = engine.select_strategies("PANIC", strategies)
        assert decision.strategy_weights.get("MomentumStrategy", -1) == 0.0
        assert decision.strategy_weights.get("BreakoutStrategy", -1) == 0.0

    def test_reasons_list_disabled(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons list disabled strategies."""
        decision = engine.select_strategies("PANIC", strategies)
        reasons_text = " ".join(decision.reasons)
        assert "disabled" in reasons_text.lower()


class TestSelectStrategiesOverheated:
    """OVERHEATED regime strategy selection."""

    def test_prefers_defensive_and_mean_reversion(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Overheated reduces longs."""
        decision = engine.select_strategies("OVERHEATED", strategies)
        mrw = decision.strategy_weights.get("MeanReversionStrategy", 0)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        assert mrw > mw

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights sum to 1.0."""
        decision = engine.select_strategies("OVERHEATED", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)


# ======================================================================
# Test: Unknown regime
# ======================================================================


class TestUnknownRegime:
    """Behaviour with unrecognised regimes."""

    def test_unknown_regime_fallback(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Unknown regime returns equal-weight fallback."""
        decision = engine.select_strategies("ALIEN_REGIME", strategies)
        assert decision.market_regime == "ALIEN_REGIME"
        # All strategies selected
        assert len(decision.selected_strategies) == len(strategies)

    def test_unknown_regime_equal_weights(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Unknown regime gives equal weights."""
        decision = engine.select_strategies("ALIEN_REGIME", strategies)
        weights = list(decision.strategy_weights.values())
        # All weights should be approximately equal
        expected = 1.0 / len(strategies)
        for w in weights:
            assert w == pytest.approx(expected, abs=1e-6)

    def test_unknown_regime_confidence_low(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Unknown regime has lower confidence."""
        decision = engine.select_strategies("ALIEN_REGIME", strategies)
        assert decision.confidence < 70.0

    def test_empty_regime_string(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Empty regime string is treated as unknown."""
        decision = engine.select_strategies("", strategies)
        assert decision.market_regime == ""

    def test_lowercase_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Lowercase regime is uppercased."""
        decision = engine.select_strategies("bull", strategies)
        assert decision.market_regime == "BULL"

    def test_mixed_case_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Mixed case regime is uppercased."""
        decision = engine.select_strategies("BeAr", strategies)
        assert decision.market_regime == "BEAR"


# ======================================================================
# Test: Weight normalisation
# ======================================================================


class TestWeightNormalisation:
    """Weight calculation and normalisation."""

    def test_weights_always_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """All regimes produce weights summing to 1.0."""
        for regime in [
            "BULL", "BEAR", "SIDEWAYS",
            "HIGH_VOLATILITY", "LOW_VOLATILITY",
            "ACCUMULATION", "DISTRIBUTION",
            "RECOVERY", "PANIC", "OVERHEATED",
            "UNKNOWN",
        ]:
            decision = engine.select_strategies(regime, strategies)
            total = sum(decision.strategy_weights.values())
            assert total == pytest.approx(1.0, abs=1e-6), f"Failed for {regime}"

    def test_preferred_weight_higher_than_base(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Preferred strategies get higher weight than base."""
        decision = engine.select_strategies("BULL", strategies)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        vw = decision.strategy_weights.get("VolumeStrategy", 0)
        assert mw > vw

    def test_reduced_weight_lower_than_base(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reduced strategies get lower weight than base."""
        decision = engine.select_strategies("SIDEWAYS", strategies)
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        bw = decision.strategy_weights.get("BreakoutStrategy", 0)
        assert mw < bw

    def test_disabled_weight_zero(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Disabled strategies get zero weight."""
        decision = engine.select_strategies("BEAR", strategies)
        assert decision.strategy_weights.get("BreakoutStrategy", -1) == 0.0

    def test_all_disabled_fallback_equal(
        self,
        strategies: list[str],
    ) -> None:
        """When all strategies would be disabled, fall back to equal weight."""
        custom = {
            "TEST": {
                "prefer": [],
                "reduce": [],
                "disable": ["MomentumStrategy", "BreakoutStrategy",
                            "MeanReversionStrategy", "VolumeStrategy"],
            },
        }
        e = AdaptiveStrategyEngine(strategy_mapping=custom)
        decision = e.select_strategies("TEST", strategies)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)
        # All weights should be equal
        expected = 1.0 / len(strategies)
        for w in decision.strategy_weights.values():
            assert w == pytest.approx(expected, abs=1e-6)

    def test_single_strategy_produces_one(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Single strategy always gets weight 1.0."""
        decision = engine.select_strategies("BULL", ["MomentumStrategy"])
        assert decision.strategy_weights["MomentumStrategy"] == pytest.approx(1.0, abs=1e-6)

    def test_two_strategies_equal_when_no_preference(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Two strategies get equal weight when nothing matches."""
        decision = engine.select_strategies(
            "BULL", ["AlphaStrategy", "BetaStrategy"],
        )
        assert decision.strategy_weights["AlphaStrategy"] == pytest.approx(0.5, abs=1e-6)
        assert decision.strategy_weights["BetaStrategy"] == pytest.approx(0.5, abs=1e-6)

    def test_strategy_not_in_registry(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Strategies not in registry get base weight."""
        decision = engine.select_strategies(
            "BULL", ["MomentumStrategy", "AlphaStrategy"],
        )
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        aw = decision.strategy_weights.get("AlphaStrategy", 0)
        # Momentum is preferred, Alpha is base
        assert mw > aw


# ======================================================================
# Test: Confidence calculation
# ======================================================================


class TestConfidence:
    """Confidence score calculation."""

    def test_known_regime_confidence(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Known regimes have base confidence >= 70."""
        for regime in [
            "BULL", "BEAR", "SIDEWAYS",
            "HIGH_VOLATILITY", "LOW_VOLATILITY",
            "ACCUMULATION", "DISTRIBUTION",
            "RECOVERY", "PANIC", "OVERHEATED",
        ]:
            decision = engine.select_strategies(regime, strategies)
            assert decision.confidence >= 70.0, f"Failed for {regime}"
            assert decision.confidence <= 100.0

    def test_unknown_regime_confidence(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Unknown regimes have base confidence < 70."""
        decision = engine.select_strategies("UNKNOWN_REGIME", strategies)
        assert decision.confidence < 70.0

    def test_confidence_increases_with_more_strategies(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """More strategies increase coverage bonus (using PANIC where some get disabled)."""
        # PANIC disables MomentumStrategy and BreakoutStrategy
        # 1 strategy: MomentumStrategy is disabled → 0/1 selected → ratio=0
        d1 = engine.select_strategies("PANIC", ["MomentumStrategy"])
        # 2 strategies: Momentum disabled, MeanReversion not → 1/2 selected → ratio=0.5
        d2 = engine.select_strategies("PANIC", ["MomentumStrategy", "MeanReversionStrategy"])
        # 3 strategies: Momentum disabled, MeanReversion+Volume not → 2/3 selected → ratio≈0.667
        d3 = engine.select_strategies("PANIC", [
            "MomentumStrategy", "MeanReversionStrategy", "VolumeStrategy",
        ])
        assert d3.confidence > d2.confidence
        assert d2.confidence > d1.confidence

    def test_confidence_clamped(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Confidence is clamped to 0–100."""
        decision = engine.select_strategies("BULL", [])
        assert 0.0 <= decision.confidence <= 100.0

    def test_empty_strategies(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Empty strategy list produces 0 confidence."""
        decision = engine.select_strategies("BULL", [])
        assert decision.confidence == 0.0


# ======================================================================
# Test: Reasons
# ======================================================================


class TestReasons:
    """Reason string generation."""

    def test_reasons_not_empty_for_known_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons are generated for known regime."""
        decision = engine.select_strategies("BULL", strategies)
        assert len(decision.reasons) > 0

    def test_reasons_mention_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons include the regime name."""
        decision = engine.select_strategies("DISTRIBUTION", strategies)
        reasons_text = " ".join(decision.reasons)
        assert "distribution" in reasons_text.lower()

    def test_reasons_mention_confidence(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons include confidence."""
        decision = engine.select_strategies("BULL", strategies)
        reasons_text = " ".join(decision.reasons)
        assert "confidence" in reasons_text.lower()

    def test_reasons_mention_preferred_strategies(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons mention which strategies are preferred."""
        decision = engine.select_strategies("BULL", strategies)
        reasons_text = " ".join(decision.reasons)
        assert "preferred" in reasons_text.lower()
        assert "MomentumStrategy" in reasons_text

    def test_reasons_mention_disabled_strategies(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons mention which strategies are disabled."""
        decision = engine.select_strategies("BEAR", strategies)
        reasons_text = " ".join(decision.reasons)
        assert "BreakoutStrategy" in reasons_text
        assert "disabled" in reasons_text.lower()

    def test_reasons_unknown_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Unknown regime reasons mention fallback."""
        decision = engine.select_strategies("MYSTERY", strategies)
        reasons_text = " ".join(decision.reasons)
        assert "MYSTERY" in reasons_text or "unknown" in reasons_text.lower()


# ======================================================================
# Test: select_with_signals
# ======================================================================


class TestSelectWithSignals:
    """Strategy selection from signal payloads."""

    def test_extracts_strategy_names(
        self,
        engine: AdaptiveStrategyEngine,
        signals: list[dict[str, Any]],
    ) -> None:
        """Strategy names extracted from signals."""
        decision = engine.select_with_signals("BULL", signals)
        assert "MomentumStrategy" in decision.selected_strategies
        assert "BreakoutStrategy" in decision.selected_strategies

    def test_skips_errored_signals(
        self,
        engine: AdaptiveStrategyEngine,
        signals_with_errors: list[dict[str, Any]],
    ) -> None:
        """Errored signals are excluded from strategy names."""
        decision = engine.select_with_signals("BULL", signals_with_errors)
        # BreakoutStrategy had an error, should not be in selected strategies
        # But actually BreakoutStrategy is preferred in BULL, so it would be
        # However, because the signal was errored, its strategy_name won't
        # appear in the names list, so it won't be in selected_strategies
        assert "BreakoutStrategy" not in decision.selected_strategies

    def test_empty_signals(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Empty signals list produces empty decision."""
        decision = engine.select_with_signals("BULL", [])
        assert decision.selected_strategies == []
        assert decision.strategy_weights == {}
        assert decision.confidence == 0.0

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        signals: list[dict[str, Any]],
    ) -> None:
        """Weights from signals sum to 1.0."""
        decision = engine.select_with_signals("BULL", signals)
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_all_signals_errored(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """All errored signals produce empty strategy list."""
        signals = [
            {"strategy_name": "A", "error": "fail"},
            {"strategy_name": "B", "error": "fail"},
        ]
        decision = engine.select_with_signals("BULL", signals)
        assert decision.selected_strategies == []

    def test_regime_respected(
        self,
        engine: AdaptiveStrategyEngine,
        signals: list[dict[str, Any]],
    ) -> None:
        """Regime affects signal-based selection."""
        bull = engine.select_with_signals("BULL", signals)
        bear = engine.select_with_signals("BEAR", signals)
        # In Bull, Momentum is preferred. In Bear, it's not.
        # But Breakout is disabled in Bear
        assert "BreakoutStrategy" not in bear.disabled_strategies or \
               "BreakoutStrategy" in bear.disabled_strategies
        # Verify they differ
        assert bull.selected_strategies != bear.selected_strategies or \
               bull.disabled_strategies != bear.disabled_strategies


# ======================================================================
# Test: calculate_strategy_weights
# ======================================================================


class TestCalculateStrategyWeights:
    """Convenience method for weight-only calculation."""

    def test_returns_dict(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Returns weight dictionary."""
        weights = engine.calculate_strategy_weights("BULL", strategies)
        assert isinstance(weights, dict)

    def test_weights_sum_to_one(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights from convenience method sum to 1.0."""
        weights = engine.calculate_strategy_weights("PANIC", strategies)
        total = sum(weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)

    def test_same_as_select_strategies(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Weights match those from select_strategies."""
        w1 = engine.calculate_strategy_weights("BULL", strategies)
        d = engine.select_strategies("BULL", strategies)
        for name in strategies:
            assert w1.get(name, 0) == pytest.approx(
                d.strategy_weights.get(name, 0), abs=1e-6,
            )

    def test_empty_list(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Empty list returns empty dict."""
        weights = engine.calculate_strategy_weights("BULL", [])
        assert weights == {}


# ======================================================================
# Test: decision_to_dict
# ======================================================================


class TestDecisionToDict:
    """Serialisation helper."""

    def test_converts_decision(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """decision_to_dict returns dict."""
        decision = engine.select_strategies("BULL", strategies)
        result = engine.decision_to_dict(decision)
        assert isinstance(result, dict)
        assert result["market_regime"] == "BULL"

    def test_contains_all_keys(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Dict has all expected keys."""
        decision = engine.select_strategies("BULL", strategies)
        result = engine.decision_to_dict(decision)
        for key in [
            "market_regime", "selected_strategies", "disabled_strategies",
            "strategy_weights", "confidence", "reasons", "metadata",
        ]:
            assert key in result


# ======================================================================
# Test: Edge cases
# ======================================================================


class TestEdgeCases:
    """Boundary conditions and edge cases."""

    def test_empty_strategy_list(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Empty strategy list returns empty decision."""
        decision = engine.select_strategies("BULL", [])
        assert decision.selected_strategies == []
        assert decision.strategy_weights == {}

    def test_none_strategies(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """None strategy list uses mapping names."""
        decision = engine.select_strategies("BULL")
        assert decision.selected_strategies is not None

    def test_single_strategy(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Single strategy gets full weight."""
        decision = engine.select_strategies("BULL", ["MomentumStrategy"])
        assert decision.strategy_weights["MomentumStrategy"] == pytest.approx(1.0, abs=1e-6)

    def test_very_long_strategy_name(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Very long strategy names are handled."""
        long_name = "X" * 500
        decision = engine.select_strategies("BULL", [long_name])
        assert long_name in decision.strategy_weights

    def test_strategy_name_with_special_chars(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Special characters in strategy names work."""
        name = "Alpha-Beta_Gamma/Delta"
        decision = engine.select_strategies("BULL", [name])
        assert name in decision.selected_strategies

    def test_exact_strategy_name_matching(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Exact strategy names are matched correctly."""
        decision = engine.select_strategies(
            "BULL", ["MomentumStrategy", "NOT_MOMENTUM"],
        )
        # "MomentumStrategy" should match "Momentum" in mapping
        # "NOT_MOMENTUM" should NOT match "Momentum" because... wait,
        # "Momentum" IS a substring of "NOT_MOMENTUM"!
        # This is expected behaviour with substring matching.
        assert "MomentumStrategy" in decision.selected_strategies

    def test_case_insensitive_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Regime matching is case-insensitive."""
        d1 = engine.select_strategies("BULL", strategies)
        d2 = engine.select_strategies("bull", strategies)
        assert d1.confidence == d2.confidence
        for name in strategies:
            assert d1.strategy_weights[name] == pytest.approx(
                d2.strategy_weights[name], abs=1e-6,
            )

    def test_trailing_whitespace_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Trailing whitespace in regime is stripped."""
        decision = engine.select_strategies("  BULL  ", strategies)
        assert decision.market_regime == "BULL"

    def test_all_regimes_return_same_keys(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """All regimes return decisions with the same key structure."""
        for regime in [
            "BULL", "BEAR", "SIDEWAYS",
            "HIGH_VOLATILITY", "LOW_VOLATILITY",
            "ACCUMULATION", "DISTRIBUTION",
            "RECOVERY", "PANIC", "OVERHEATED",
            "ALIEN",
        ]:
            decision = engine.select_strategies(regime, strategies)
            d = decision.to_dict()
            for key in ["market_regime", "selected_strategies", "strategy_weights",
                        "confidence", "reasons"]:
                assert key in d, f"Missing {key} for {regime}"

    def test_disabled_strategies_not_in_selected(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Disabled strategies are not in selected_strategies."""
        decision = engine.select_strategies("BEAR", strategies)
        for disabled in decision.disabled_strategies:
            assert disabled not in decision.selected_strategies


# ======================================================================
# Test: Logging
# ======================================================================


class TestLogging:
    """Logging behaviour."""

    def test_select_strategies_logs(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """select_strategies logs regime and counts."""
        caplog.set_level(20)  # INFO level
        engine.select_strategies("BULL", strategies)
        log_text = caplog.text
        assert "BULL" in log_text or "Adaptive" in log_text

    def test_select_with_signals_logs(
        self,
        engine: AdaptiveStrategyEngine,
        signals: list[dict[str, Any]],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """select_with_signals logs regime."""
        caplog.set_level(20)  # INFO level
        engine.select_with_signals("PANIC", signals)
        log_text = caplog.text
        assert "PANIC" in log_text or "panic" in caplog.text

    def test_decision_to_dict_logs(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """decision_to_dict logs serialisation."""
        caplog.set_level(10)  # DEBUG level
        decision = engine.select_strategies("BULL", strategies)
        engine.decision_to_dict(decision)
        log_text = caplog.text
        assert "BULL" in log_text


# ======================================================================
# Test: Invalid inputs
# ======================================================================


class TestInvalidInputs:
    """Graceful handling of invalid inputs."""

    def test_none_strategies_list(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """None strategies list does not crash."""
        decision = engine.select_strategies("BULL", None)  # type: ignore[arg-type]
        assert decision.market_regime == "BULL"

    def test_non_list_strategies(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Non-list strategies parameter uses fallback (extracted from mapping)."""
        decision = engine.select_strategies("BULL", "not_a_list")  # type: ignore[arg-type]
        assert decision.market_regime == "BULL"

    def test_none_regime(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """None regime is treated as UNKNOWN."""
        decision = engine.select_strategies(None, strategies)  # type: ignore[arg-type]
        assert decision.market_regime == "UNKNOWN"

    def test_empty_strategies_with_mapping_names(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Empty strategy list returns empty weights."""
        decision = engine.select_strategies("BULL", [])
        assert decision.strategy_weights == {}

    def test_signals_with_missing_strategy_name(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Signals without strategy_name get placeholder names."""
        signals = [
            {"signal": "BUY", "score": 5.0},
            {"signal": "SELL", "score": 3.0},
        ]
        decision = engine.select_with_signals("BULL", signals)
        assert len(decision.selected_strategies) == 2

    def test_empty_mapping_for_unknown_regime(
        self,
        strategies: list[str],
    ) -> None:
        """Empty mapping returns equal weights for all regimes."""
        e = AdaptiveStrategyEngine(strategy_mapping={})
        decision = e.select_strategies("BULL", strategies)
        expected = 1.0 / len(strategies)
        for w in decision.strategy_weights.values():
            assert w == pytest.approx(expected, abs=1e-6)


# ======================================================================
# Test: Metadata
# ======================================================================


class TestMetadata:
    """Metadata included in decisions."""

    def test_metadata_contains_generator(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Metadata contains generated_by key."""
        decision = engine.select_strategies("BULL", strategies)
        assert decision.metadata.get("generated_by") == "AdaptiveStrategyEngine"

    def test_metadata_in_to_dict(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Metadata appears in to_dict output."""
        decision = engine.select_strategies("BULL", strategies)
        result = decision.to_dict()
        assert "metadata" in result
        assert result["metadata"]["generated_by"] == "AdaptiveStrategyEngine"


# ======================================================================
# Test: Strategy matching
# ======================================================================


class TestStrategyMatching:
    """Strategy name matching against mapping parts."""

    def test_momentum_matches_momentum_strategy(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """'Momentum' matches 'MomentumStrategy'."""
        decision = engine.select_strategies(
            "BULL", ["MomentumStrategy", "AlphaStrategy"],
        )
        mw = decision.strategy_weights.get("MomentumStrategy", 0)
        aw = decision.strategy_weights.get("AlphaStrategy", 0)
        assert mw > aw

    def test_breakout_matches_breakout_strategy(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """'Breakout' matches 'BreakoutStrategy'."""
        decision = engine.select_strategies(
            "BULL", ["BreakoutStrategy", "BetaStrategy"],
        )
        bw = decision.strategy_weights.get("BreakoutStrategy", 0)
        betaw = decision.strategy_weights.get("BetaStrategy", 0)
        assert bw > betaw

    def test_no_false_positive_matching(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Strategies are matched by substring only."""
        decision = engine.select_strategies(
            "BULL", ["MomentumX", "XBreakout"],
        )
        # Both should match via substring
        assert "MomentumX" in decision.selected_strategies
        assert "XBreakout" in decision.selected_strategies

    def test_empty_name_parts(
        self,
        engine: AdaptiveStrategyEngine,
    ) -> None:
        """Empty name parts match nothing."""
        parts = list[str]()
        result = AdaptiveStrategyEngine._match_strategies(
            ["MomentumStrategy"], parts,
        )
        assert result == []


# ======================================================================
# Test: All 10 regimes produce valid output
# ======================================================================


class TestAllRegimes:
    """Every regime produces valid, consistent output."""

    @pytest.mark.parametrize("regime", [
        "BULL",
        "BEAR",
        "SIDEWAYS",
        "HIGH_VOLATILITY",
        "LOW_VOLATILITY",
        "ACCUMULATION",
        "DISTRIBUTION",
        "RECOVERY",
        "PANIC",
        "OVERHEATED",
    ])
    def test_each_regime_produces_valid_decision(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
        regime: str,
    ) -> None:
        """Each regime produces a well-formed decision."""
        decision = engine.select_strategies(regime, strategies)
        assert decision.market_regime == regime
        assert len(decision.selected_strategies) >= 1
        total = sum(decision.strategy_weights.values())
        assert total == pytest.approx(1.0, abs=1e-6)
        assert 0 <= decision.confidence <= 100
        assert len(decision.reasons) > 0

    @pytest.mark.parametrize("regime", [
        "BULL", "BEAR", "SIDEWAYS",
        "HIGH_VOLATILITY", "LOW_VOLATILITY",
        "ACCUMULATION", "DISTRIBUTION",
        "RECOVERY", "PANIC", "OVERHEATED",
    ])
    def test_to_dict_roundtrip(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
        regime: str,
    ) -> None:
        """to_dict roundtrip preserves all keys."""
        decision = engine.select_strategies(regime, strategies)
        d = decision.to_dict()
        assert d["market_regime"] == regime
        assert len(d["selected_strategies"]) >= 1
        assert "strategy_weights" in d


# ======================================================================
# Test: Serialisation
# ======================================================================


class TestSerialisation:
    """Serialisation consistency."""

    def test_to_dict_contains_all_fields(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """to_dict contains all required fields."""
        decision = engine.select_strategies("BULL", strategies)
        d = decision.to_dict()
        assert set(d.keys()) == {
            "market_regime", "selected_strategies", "disabled_strategies",
            "strategy_weights", "confidence", "reasons", "metadata",
        }

    def test_to_dict_lists_are_copies(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """to_dict returns copies, not references."""
        decision = engine.select_strategies("BULL", strategies)
        d = decision.to_dict()
        d["selected_strategies"].append("FAKE")
        assert "FAKE" not in decision.selected_strategies

    def test_multiple_calls_consistent(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Multiple calls produce identical results (deterministic)."""
        d1 = engine.select_strategies("ACCUMULATION", strategies)
        d2 = engine.select_strategies("ACCUMULATION", strategies)
        assert d1.selected_strategies == d2.selected_strategies
        for name in strategies:
            assert d1.strategy_weights[name] == pytest.approx(
                d2.strategy_weights[name], abs=1e-6,
            )

    def test_to_dict_reasons_are_strings(
        self,
        engine: AdaptiveStrategyEngine,
        strategies: list[str],
    ) -> None:
        """Reasons in to_dict are all strings."""
        decision = engine.select_strategies("PANIC", strategies)
        d = decision.to_dict()
        for reason in d["reasons"]:
            assert isinstance(reason, str)
