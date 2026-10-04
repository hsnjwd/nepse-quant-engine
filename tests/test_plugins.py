"""Tests for the plugin system (src/plugins)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from src.plugins import (
    IndicatorPlugin,
    Plugin,
    PluginRegistry,
    PluginType,
    discover_in_directory,
    discover_in_package,
    get_plugin,
    load_plugins,
    plugin_metadata,
    plugins_of_type,
    register_plugin,
    registry,
    reload_plugins,
)


class _DummyPlugin(IndicatorPlugin):
    """Minimal indicator plugin for registry tests."""

    name = "dummy_indicator"

    def initialize(self) -> None:
        """No-op initialisation."""
        return None

    def compute(self, df: pd.DataFrame, **params: object) -> pd.DataFrame:
        """Return the frame unchanged."""
        return df


class TestPluginBase:
    def test_requires_name(self) -> None:
        class _Bad(Plugin):
            name = "base_plugin"

            def initialize(self) -> None:
                return None

        with pytest.raises(ValueError):
            _Bad()

    def test_metadata(self) -> None:
        plugin = _DummyPlugin()
        data = plugin.metadata()
        assert data["name"] == "dummy_indicator"
        assert data["type"] == "indicator"
        assert data["version"] == "1.0.0"

    def test_plugin_types(self) -> None:
        assert PluginType.INDICATOR.value == "indicator"
        assert PluginType.BROKER.value == "broker"
        assert PluginType.NOTIFICATION.value == "notification"

    def test_shutdown_noop(self) -> None:
        plugin = _DummyPlugin()
        plugin.shutdown()  # should not raise


class TestPluginRegistry:
    def test_register_count(self) -> None:
        reg = PluginRegistry()
        reg.register(_DummyPlugin())
        assert reg.count() == 1

    def test_register_duplicate(self) -> None:
        reg = PluginRegistry()
        reg.register(_DummyPlugin())
        with pytest.raises(ValueError):
            reg.register(_DummyPlugin())

    def test_register_non_plugin(self) -> None:
        reg = PluginRegistry()
        with pytest.raises(TypeError):
            reg.register("nope")  # type: ignore[arg-type]

    def test_get_and_has(self) -> None:
        reg = PluginRegistry()
        reg.register(_DummyPlugin())
        assert reg.has("dummy_indicator")
        assert reg.get("dummy_indicator").name == "dummy_indicator"
        with pytest.raises(KeyError):
            reg.get("missing")

    def test_unregister(self) -> None:
        reg = PluginRegistry()
        reg.register(_DummyPlugin())
        reg.unregister("dummy_indicator")
        assert reg.count() == 0
        with pytest.raises(KeyError):
            reg.unregister("dummy_indicator")

    def test_plugins_of_type(self) -> None:
        reg = PluginRegistry()
        reg.register(_DummyPlugin())
        assert len(reg.plugins_of_type(PluginType.INDICATOR)) == 1
        assert len(reg.plugins_of_type("indicator")) == 1
        assert reg.plugins_of_type(PluginType.BROKER) == []

    def test_clear(self) -> None:
        reg = PluginRegistry()
        reg.register(_DummyPlugin())
        reg.clear()
        assert reg.count() == 0

    def test_metadata_all(self) -> None:
        reg = PluginRegistry()
        reg.register(_DummyPlugin())
        assert len(reg.all()) == 1
        assert reg.metadata()[0]["name"] == "dummy_indicator"


class TestGlobalRegistry:
    def test_singleton(self) -> None:
        assert registry() is registry()

    def test_register_get(self) -> None:
        registry().clear()
        register_plugin(_DummyPlugin())
        assert get_plugin("dummy_indicator") is not None
        assert plugins_of_type(PluginType.INDICATOR)
        assert plugin_metadata()
        registry().clear()


class TestDiscovery:
    def test_discover_builtin(self) -> None:
        classes = discover_in_package("src.plugins.builtin")
        names = {c.name for c in classes}
        assert "volume_ratio_indicator" in names
        assert "market_pulse_report" in names
        assert "rsi_extreme_alerts" in names
        assert "csv_exporter" in names
        assert "console_notifier" in names
        assert "momentum_strategy_plugin" in names

    def test_discover_directory(self, tmp_path: Path) -> None:
        (tmp_path / "my_plugin.py").write_text(
            "from src.plugins import IndicatorPlugin\n"
            "class MyPlugin(IndicatorPlugin):\n"
            "    name = 'my_plugin'\n"
            "    def initialize(self):\n"
            "        return None\n"
            "    def compute(self, df, **params):\n"
            "        return df\n",
            encoding="utf-8",
        )
        classes = discover_in_directory(tmp_path)
        assert any(c.name == "my_plugin" for c in classes)

    def test_discover_missing_package(self) -> None:
        assert discover_in_package("no.such.package") == []

    def test_discover_missing_directory(self, tmp_path: Path) -> None:
        assert discover_in_directory(tmp_path / "missing") == []

    def test_load_plugins(self) -> None:
        registry().clear()
        plugins = load_plugins(register=True)
        assert plugins
        assert registry().count() > 0
        registry().clear()

    def test_reload_plugins(self) -> None:
        count = reload_plugins()
        assert count > 0


class TestBuiltinPlugins:
    def test_volume_ratio_indicator(self) -> None:
        from src.plugins.builtin.example_plugins import VolumeRatioIndicatorPlugin

        plugin = VolumeRatioIndicatorPlugin()
        plugin.initialize()
        df = pd.DataFrame({"Volume": [100, 200, 300, 400, 500]})
        result = plugin.compute(df, period=2)
        assert "VOLUME_RATIO_20" in result.columns

    def test_market_pulse_report(self) -> None:
        from src.plugins.builtin.example_plugins import MarketPulseReportPlugin

        plugin = MarketPulseReportPlugin()
        out = plugin.render({"index": 2000.0, "change_pct": 1.0, "regime": "BULL"})
        assert "Market Pulse" in out
        empty = plugin.render({})
        assert "No market data" in empty

    def test_price_alert(self) -> None:
        from src.plugins.builtin.example_plugins import PriceAlertPlugin

        plugin = PriceAlertPlugin()
        assert plugin.check({"rsi": 25.0})
        assert plugin.check({"rsi": 80.0})
        assert plugin.check({"rsi": 50.0}) == []

    def test_csv_exporter(self, tmp_path: Path) -> None:
        from src.plugins.builtin.example_plugins import CsvExporterPlugin

        plugin = CsvExporterPlugin()
        path = str(tmp_path / "out.csv")
        result = plugin.export([{"a": 1, "b": 2}], path)
        assert result == path
        assert Path(path).exists()

    def test_console_notification(self) -> None:
        from src.plugins.builtin.example_plugins import ConsoleNotificationPlugin

        plugin = ConsoleNotificationPlugin()
        assert plugin.send("hello") is True

    def test_strategy_plugin(self) -> None:
        from src.plugins.builtin.example_plugins import MovingAverageStrategyPlugin

        plugin = MovingAverageStrategyPlugin()
        strategy = plugin.build()
        assert strategy.name == "MomentumStrategy"
