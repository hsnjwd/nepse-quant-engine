"""Alert center — manages all alert types with priority, muting, and notification history.

Alert types:
- Price Alerts: notify when price crosses thresholds
- Volume Alerts: notify on volume spikes
- RSI Alerts: notify when RSI enters overbought/oversold
- MACD Alerts: notify on MACD crossovers
- Breakout Alerts: notify on support/resistance breakouts
- Regime Change Alerts: notify on market regime transitions
- Portfolio Alerts: notify on portfolio P&L milestones
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

ALERTS_FILE = Path.home() / ".nepse" / "alerts.json"

# Priority levels
PRIORITY_CRITICAL = 5
PRIORITY_HIGH = 4
PRIORITY_MEDIUM = 3
PRIORITY_LOW = 2
PRIORITY_INFO = 1


@dataclass
class AlertRule:
    """Configuration for an alert rule."""

    id: str = ""
    symbol: str = ""
    alert_type: str = ""  # price | volume | rsi | macd | breakout | regime | portfolio
    operator: str = ""    # > | < | == | crosses_above | crosses_below
    threshold: float = 0.0
    enabled: bool = True
    muted: bool = False
    priority: int = PRIORITY_MEDIUM
    message_template: str = ""
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class AlertEvent:
    """A single triggered alert event."""

    id: str = ""
    rule_id: str = ""
    symbol: str = ""
    alert_type: str = ""
    message: str = ""
    priority: int = PRIORITY_MEDIUM
    current_value: float = 0.0
    threshold: float = 0.0
    read: bool = False
    timestamp: datetime = field(default_factory=datetime.now)


class AlertCenter:
    """Central alert manager with rule-based triggering and persistent history.

    Usage::

        center = AlertCenter()
        center.add_rule("NABIL", "price", ">", 550.0, priority=5)
        alerts = center.check_price_alerts({"NABIL": 560.0})
        history = center.get_history()
    """

    def __init__(self) -> None:
        self._rules: list[AlertRule] = []
        self._events: list[AlertEvent] = []
        self._event_counter = 0
        self._load()

    # ── Rule management ──────────────────────────────────────────

    def add_rule(
        self,
        symbol: str,
        alert_type: str,
        operator: str,
        threshold: float,
        priority: int = PRIORITY_MEDIUM,
        message_template: str = "",
    ) -> AlertRule:
        """Add a new alert rule."""
        rule = AlertRule(
            id=f"R{len(self._rules) + 1:04d}",
            symbol=symbol.upper(),
            alert_type=alert_type,
            operator=operator,
            threshold=threshold,
            priority=priority,
            message_template=message_template or self._default_message(alert_type, symbol, operator, threshold),
            enabled=True,
            muted=False,
        )
        self._rules.append(rule)
        self._save()
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        """Remove an alert rule by ID."""
        for rule in self._rules:
            if rule.id == rule_id:
                self._rules.remove(rule)
                self._save()
                return True
        return False

    def enable_rule(self, rule_id: str) -> bool:
        """Enable a disabled rule."""
        rule = self._get_rule(rule_id)
        if rule:
            rule.enabled = True
            self._save()
            return True
        return False

    def disable_rule(self, rule_id: str) -> bool:
        """Disable a rule without removing it."""
        rule = self._get_rule(rule_id)
        if rule:
            rule.enabled = False
            self._save()
            return True
        return False

    def mute_rule(self, rule_id: str) -> bool:
        """Mute notifications for a rule."""
        rule = self._get_rule(rule_id)
        if rule:
            rule.muted = True
            self._save()
            return True
        return False

    def unmute_rule(self, rule_id: str) -> bool:
        """Unmute notifications for a rule."""
        rule = self._get_rule(rule_id)
        if rule:
            rule.muted = False
            self._save()
            return True
        return False

    def get_rules(self, symbol: str | None = None) -> list[AlertRule]:
        """Get all rules, optionally filtered by symbol."""
        if symbol:
            return [r for r in self._rules if r.symbol == symbol.upper()]
        return list(self._rules)

    def _get_rule(self, rule_id: str) -> AlertRule | None:
        for r in self._rules:
            if r.id == rule_id:
                return r
        return None

    # ── Alert checking ───────────────────────────────────────────

    def check_price_alerts(self, prices: dict[str, float]) -> list[AlertEvent]:
        """Check price threshold rules against *prices*."""
        new_events: list[AlertEvent] = []
        for rule in self._rules:
            if not rule.enabled or rule.alert_type != "price":
                continue
            price = prices.get(rule.symbol)
            if price is None:
                continue
            if self._evaluate(price, rule.operator, rule.threshold):
                event = self._create_event(rule, price)
                new_events.append(event)
        return new_events

    def check_rsi_alerts(self, rsi_values: dict[str, float]) -> list[AlertEvent]:
        """Check RSI rules (e.g. overbought > 70, oversold < 30)."""
        return self._check_threshold("rsi", rsi_values)

    def check_macd_alerts(self, macd_crossovers: dict[str, str]) -> list[AlertEvent]:
        """Check MACD crossover rules."""
        new_events: list[AlertEvent] = []
        for rule in self._rules:
            if not rule.enabled or rule.alert_type != "macd":
                continue
            crossover = macd_crossovers.get(rule.symbol)
            if crossover and crossover == rule.operator:
                event = self._create_event(rule, 1.0)
                new_events.append(event)
        return new_events

    def check_volume_alerts(self, volume_data: dict[str, dict[str, float]]) -> list[AlertEvent]:
        """Check volume spike rules.

        *volume_data* is ``{symbol: {"current": ..., "average": ..., "ratio": ...}}``
        """
        new_events: list[AlertEvent] = []
        for rule in self._rules:
            if not rule.enabled or rule.alert_type != "volume":
                continue
            vd = volume_data.get(rule.symbol)
            if not vd:
                continue
            ratio = vd.get("ratio", 0)
            if self._evaluate(ratio, rule.operator, rule.threshold):
                event = self._create_event(rule, ratio)
                new_events.append(event)
        return new_events

    def check_breakout_alerts(
        self,
        prices: dict[str, float],
        supports: dict[str, float],
        resistances: dict[str, float],
    ) -> list[AlertEvent]:
        """Check breakout rules (price breaks support or resistance)."""
        new_events: list[AlertEvent] = []
        for rule in self._rules:
            if not rule.enabled or rule.alert_type != "breakout":
                continue
            price = prices.get(rule.symbol)
            if price is None:
                continue
            support = supports.get(rule.symbol)
            resistance = resistances.get(rule.symbol)
            if rule.operator == "breaks_above" and resistance and price >= resistance:
                event = self._create_event(rule, price)
                new_events.append(event)
            elif rule.operator == "breaks_below" and support and price <= support:
                event = self._create_event(rule, price)
                new_events.append(event)
        return new_events

    def check_regime_alerts(self, regimes: dict[str, str]) -> list[AlertEvent]:
        """Check for regime change alerts (tracked internally)."""
        new_events: list[AlertEvent] = []
        for rule in self._rules:
            if not rule.enabled or rule.alert_type != "regime":
                continue
            current = regimes.get(rule.symbol)
            if current and current == rule.operator:
                event = self._create_event(rule, 1.0)
                new_events.append(event)
        return new_events

    def check_portfolio_alerts(self, portfolio_value: float, threshold_pct: float = 5.0) -> list[AlertEvent]:
        """Check portfolio-level alerts (e.g. P&L milestone)."""
        new_events: list[AlertEvent] = []
        for rule in self._rules:
            if not rule.enabled or rule.alert_type != "portfolio":
                continue
            if self._evaluate(portfolio_value, rule.operator, rule.threshold):
                event = self._create_event(rule, portfolio_value)
                new_events.append(event)
        return new_events

    def _check_threshold(self, alert_type: str, values: dict[str, float]) -> list[AlertEvent]:
        new_events: list[AlertEvent] = []
        for rule in self._rules:
            if not rule.enabled or rule.alert_type != alert_type:
                continue
            val = values.get(rule.symbol)
            if val is None:
                continue
            if self._evaluate(val, rule.operator, rule.threshold):
                event = self._create_event(rule, val)
                new_events.append(event)
        return new_events

    def _evaluate(self, value: float, operator: str, threshold: float) -> bool:
        if operator == ">":
            return value > threshold
        if operator == "<":
            return value < threshold
        if operator == ">=":
            return value >= threshold
        if operator == "<=":
            return value <= threshold
        if operator == "==":
            return abs(value - threshold) < 0.001
        return False

    def _create_event(self, rule: AlertRule, current_value: float) -> AlertEvent:
        self._event_counter += 1
        message = rule.message_template.replace("{value}", f"{current_value:.2f}")
        event = AlertEvent(
            id=f"E{self._event_counter:06d}",
            rule_id=rule.id,
            symbol=rule.symbol,
            alert_type=rule.alert_type,
            message=message,
            priority=rule.priority,
            current_value=current_value,
            threshold=rule.threshold,
        )
        self._events.insert(0, event)
        self._save_events()
        logger.info("[AlertCenter] %s: %s", event.id, event.message)
        return event

    # ── Event history ────────────────────────────────────────────

    def get_history(
        self,
        limit: int = 100,
        alert_type: str | None = None,
        symbol: str | None = None,
    ) -> list[AlertEvent]:
        """Get alert history with optional filters."""
        events = self._events
        if alert_type:
            events = [e for e in events if e.alert_type == alert_type]
        if symbol:
            events = [e for e in events if e.symbol == symbol.upper()]
        return events[:limit]

    def mark_read(self, event_id: str) -> bool:
        """Mark an event as read."""
        for event in self._events:
            if event.id == event_id:
                event.read = True
                self._save_events()
                return True
        return False

    def mark_all_read(self) -> None:
        """Mark all events as read."""
        for event in self._events:
            event.read = True
        self._save_events()

    def delete_event(self, event_id: str) -> bool:
        """Delete a single event."""
        for event in self._events:
            if event.id == event_id:
                self._events.remove(event)
                self._save_events()
                return True
        return False

    def clear_history(self) -> None:
        """Clear all alert events."""
        self._events.clear()
        self._save_events()

    def get_unread_count(self) -> int:
        """Return the number of unread events."""
        return sum(1 for e in self._events if not e.read)

    # ── Persistence ──────────────────────────────────────────────

    def _default_message(self, alert_type: str, symbol: str, operator: str, threshold: float) -> str:
        templates = {
            "price": f"{symbol} price {operator} {threshold}",
            "volume": f"{symbol} volume spike {operator} {threshold:.1f}x",
            "rsi": f"{symbol} RSI {operator} {threshold}",
            "macd": f"{symbol} MACD {operator}",
            "breakout": f"{symbol} breakout {operator} {threshold}",
            "regime": f"{symbol} regime changed to {operator}",
            "portfolio": f"Portfolio {operator} {threshold}",
        }
        return templates.get(alert_type, f"{symbol}: {alert_type} {operator} {threshold}")

    def _save(self) -> None:
        try:
            ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "rules": [
                    {
                        "id": r.id, "symbol": r.symbol, "alert_type": r.alert_type,
                        "operator": r.operator, "threshold": r.threshold,
                        "enabled": r.enabled, "muted": r.muted, "priority": r.priority,
                        "message_template": r.message_template,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in self._rules
                ],
            }
            with open(ALERTS_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("[AlertCenter] Save error: %s", exc)

    def _save_events(self) -> None:
        try:
            ALERTS_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Use separate file for events to avoid conflicts with rules
            events_file = ALERTS_FILE.parent / "alerts_events.json"
            data = [
                {
                    "id": e.id, "rule_id": e.rule_id, "symbol": e.symbol,
                    "alert_type": e.alert_type, "message": e.message,
                    "priority": e.priority, "current_value": e.current_value,
                    "threshold": e.threshold, "read": e.read,
                    "timestamp": e.timestamp.isoformat(),
                }
                for e in self._events
            ]
            with open(events_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("[AlertCenter] Events save error: %s", exc)

    def _load(self) -> None:
        try:
            if ALERTS_FILE.exists():
                with open(ALERTS_FILE) as f:
                    data = json.load(f)
                for rd in data.get("rules", []):
                    self._rules.append(AlertRule(
                        id=rd["id"], symbol=rd["symbol"], alert_type=rd["alert_type"],
                        operator=rd["operator"], threshold=rd["threshold"],
                        enabled=rd.get("enabled", True), muted=rd.get("muted", False),
                        priority=rd.get("priority", PRIORITY_MEDIUM),
                        message_template=rd.get("message_template", ""),
                        created_at=datetime.fromisoformat(rd["created_at"]) if rd.get("created_at") else datetime.now(),
                    ))
            events_file = ALERTS_FILE.parent / "alerts_events.json"
            if events_file.exists():
                with open(events_file) as f:
                    data = json.load(f)
                for ed in data:
                    self._events.append(AlertEvent(
                        id=ed["id"], rule_id=ed.get("rule_id", ""), symbol=ed["symbol"],
                        alert_type=ed["alert_type"], message=ed["message"],
                        priority=ed.get("priority", PRIORITY_MEDIUM),
                        current_value=ed.get("current_value", 0),
                        threshold=ed.get("threshold", 0),
                        read=ed.get("read", False),
                        timestamp=datetime.fromisoformat(ed["timestamp"]) if ed.get("timestamp") else datetime.now(),
                    ))
                self._event_counter = max(int(e.id[1:]) for e in self._events) if self._events else 0
        except Exception as exc:
            logger.warning("[AlertCenter] Load error: %s", exc)
