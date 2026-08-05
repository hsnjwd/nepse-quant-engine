# Plugin Guide

The plugin system (`src/plugins/` + `plugins/`) lets you extend the
platform without touching core modules. Plugins are discovered
automatically and registered in a thread-safe singleton registry.

## Plugin Types

| Base class | Category | Method to implement |
| --- | --- | --- |
| `IndicatorPlugin` | Custom indicators | `compute(df, **params)` |
| `ReportPlugin` | Custom report sections | `render(data) -> markdown` |
| `StrategyPlugin` | Custom strategies | `build() -> BaseStrategy` |
| `AlertPlugin` | Custom alert rules | `check(analysis) -> list[dict]` |
| `ExporterPlugin` | Custom export formats | `export(data, path) -> path` |
| `NotificationPlugin` | Notification providers | `send(message, **kwargs) -> bool` |
| `BrokerPlugin` | Broker adapters | `connect()`, `place_order(order)` |

Every plugin must define a unique `name`. Metadata (`version`, `author`,
`description`, `plugin_type`) is optional but recommended.

## Writing a Plugin

Drop a module anywhere under `plugins/` (project root) or inside
`src/plugins/builtin/`:

```python
# plugins/my_indicator.py
from src.plugins import IndicatorPlugin


class MyRSIPlugin(IndicatorPlugin):
    name = "my_rsi"
    version = "1.0.0"
    author = "You"
    description = "Adds a custom RSI variant."

    def initialize(self) -> None:
        return None

    def compute(self, df, **params):
        result = df.copy()
        result["MY_RSI"] = result["Close"].rolling(14).mean()
        return result
```

No registry changes needed — discovery finds it automatically.

## Discovery

```python
from src.plugins import load_plugins, registry

load_plugins()                 # discover + register
registry().metadata()          # list registered plugin metadata
```

- `discover_in_package("src.plugins.builtin")` scans a Python package.
- `discover_in_directory("plugins")` scans on-disk modules.
- `reload_plugins()` clears and re-registers everything.

## Registry API

```python
from src.plugins import PluginRegistry, PluginType

reg = PluginRegistry()
reg.register(plugin)
reg.get("my_rsi")
reg.has("my_rsi")
reg.plugins_of_type(PluginType.INDICATOR)
reg.unregister("my_rsi")
reg.clear()
```

Global helpers: `register_plugin`, `get_plugin`, `plugins_of_type`,
`plugin_metadata`, `registry()`.

## Built-in Plugins

`src/plugins/builtin/example_plugins.py` ships one plugin per category:

- `volume_ratio_indicator` — adds VOLUME_RATIO_20
- `market_pulse_report` — renders a market pulse section
- `rsi_extreme_alerts` — alerts on RSI < 30 / > 70
- `csv_exporter` — CSV exports
- `console_notifier` — logs notifications
- `momentum_strategy_plugin` — wraps MomentumStrategy

## Lifecycle

- `initialize()` is called once after instantiation.
- `shutdown()` is called on unregister/clear.
- A plugin whose `initialize()` raises is skipped with a warning.
