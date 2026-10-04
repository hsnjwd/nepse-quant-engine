"""Broker Manager page — connect, place orders, view positions."""

from __future__ import annotations

import time
from typing import Any

import pandas as pd
import streamlit as st

from src.ui.helpers import section_header, divider, fmt_rupees
from src.ui.components import kpi_card


# ── Capability metadata (Part 5) ──────────────────────────────────

BROKER_FEATURES: dict[str, dict[str, Any]] = {
    "Paper": {
        "account_type": "Simulation",
        "features": ["Market orders", "Positions", "Balance", "Cash management"],
        "order_limits": "Unlimited (simulated)",
        "requires_auth": False,
    },
    "Mock": {
        "account_type": "Simulation",
        "features": ["Market orders", "Positions", "Balance", "Deterministic fills"],
        "order_limits": "Unlimited (simulated)",
        "requires_auth": False,
    },
    "Future NEPSE": {
        "account_type": "NEPSE — placeholder",
        "features": ["Connectivity contract only (NotImplementedError)"],
        "order_limits": "Not available",
        "requires_auth": True,
    },
    "IBKR": {
        "account_type": "Brokerage",
        "features": ["Stub adapter — requires ib_insync/ibapi + credentials"],
        "order_limits": "Not available (stub)",
        "requires_auth": True,
    },
    "Alpaca": {
        "account_type": "Brokerage",
        "features": ["Stub adapter — requires alpaca-py + API key/secret"],
        "order_limits": "Not available (stub)",
        "requires_auth": True,
    },
    "Binance": {
        "account_type": "Exchange",
        "features": ["Stub adapter — requires python-binance + API key/secret"],
        "order_limits": "Not available (stub)",
        "requires_auth": True,
    },
}


def broker_capabilities(broker_type: str) -> dict[str, Any]:
    """Return capability metadata for a broker type (safe default)."""
    return BROKER_FEATURES.get(broker_type, {
        "account_type": "Unknown",
        "features": [],
        "order_limits": "Unknown",
        "requires_auth": False,
    })


def render() -> None:
    """Render the Broker Manager page."""
    section_header(
        "🏦 Broker Manager",
        "Connect to brokers and manage paper trading",
    )

    try:
        from src.brokers.base import Order, OrderSide
        from src.brokers.paper import PaperBroker
    except ImportError as exc:
        st.error(f"Broker module unavailable: {exc}")
        return

    tabs = st.tabs(["🔌 Connection", "📈 Trade", "📊 Positions"])

    # ── Tab 1: Connection ─────────────────────────────────────────
    with tabs[0]:
        col1, col2 = st.columns(2)
        with col1:
            broker_type = st.selectbox(
                "Broker",
                ["Paper", "Mock", "Future NEPSE", "IBKR", "Alpaca", "Binance"],
                key="bk_type",
            )
            cash = st.number_input("Starting cash (₹)", value=1_000_000.0, key="bk_cash")
        with col2:
            price_feed_symbol = st.text_input("Price feed symbol", value="NABIL", key="bk_feed_symbol")
            price_feed_value = st.number_input("Price feed value", value=500.0, key="bk_feed_value")

        if st.button("Connect Broker", type="primary", key="bk_connect"):
            def _feed(symbol: str) -> float:
                return price_feed_value if symbol.upper() == price_feed_symbol.upper() else 500.0

            # Stub brokers (IBKR/Alpaca/Binance/Future NEPSE) raise
            # NotImplementedError on connect() — register them without
            # calling connect() and show their metadata instead.
            is_stub = broker_type in ("IBKR", "Alpaca", "Binance", "Future NEPSE")
            try:
                if broker_type == "Mock":
                    from src.brokers.mock import MockBroker

                    broker = MockBroker(cash=cash, price_feed=_feed)
                elif broker_type in ("IBKR", "Alpaca", "Binance"):
                    from src.brokers.adapters import get_adapter

                    broker = get_adapter(broker_type.lower())
                elif broker_type == "Future NEPSE":
                    from src.brokers.future_nepse import FutureNepseBroker

                    broker = FutureNepseBroker()
                else:
                    broker = PaperBroker(cash=cash, price_feed=_feed)

                if not is_stub:
                    broker.connect()
                st.session_state["bk_broker"] = broker
                st.session_state["bk_broker_type"] = broker_type
                if is_stub:
                    # Clear any stale state from a previously connected broker.
                    st.session_state.pop("bk_positions", None)
                    st.session_state.pop("bk_last_order", None)
                    st.info(
                        f"{broker_type} is an adapter stub — it requires live "
                        "credentials and the external SDK. Registered for metadata "
                        "viewing only; order placement is unavailable."
                    )
                else:
                    st.success(f"{broker_type} broker connected.")
            except Exception as exc:
                st.error(f"Could not connect {broker_type}: {exc}")

        broker = st.session_state.get("bk_broker")
        if broker:
            is_stub = st.session_state.get("bk_broker_type") in ("IBKR", "Alpaca", "Binance", "Future NEPSE")

            # ── Broker health / capabilities (Part 5) ────────────
            broker_type = st.session_state.get("bk_broker_type", "Paper")
            caps = broker_capabilities(broker_type)
            st.markdown("##### 🩺 Broker Info")
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                kpi_card("Connection", "Connected" if not is_stub and getattr(broker, "connected", False) else "Stub / Unavailable")
            with c2:
                last_sync = st.session_state.get("bk_last_sync")
                kpi_card("Last Sync", last_sync or "—")
            with c3:
                latency = st.session_state.get("bk_latency_ms")
                kpi_card("API Latency", f"{latency:.1f}ms" if latency is not None else "—")
            with c4:
                kpi_card("Auth", "Credentials" if caps["requires_auth"] else "None needed")

            st.markdown("##### ℹ️ Available Features")
            for feature in caps["features"]:
                st.markdown(f"- {feature}")
            st.caption(f"Account type: {caps['account_type']} · Order limits: {caps['order_limits']}")

            # ── Test Connection (Part 5) ─────────────────────────
            if st.button("🧪 Test Connection", key="bk_test_conn"):
                if is_stub:
                    st.info(
                        "Stub broker — connectivity requires live credentials "
                        "and the external SDK. Latency cannot be measured."
                    )
                else:
                    try:
                        started = time.monotonic()
                        connected = bool(getattr(broker, "connected", False))
                        elapsed_ms = (time.monotonic() - started) * 1000
                        st.session_state["bk_latency_ms"] = round(elapsed_ms, 2)
                        st.session_state["bk_last_sync"] = time.strftime("%H:%M:%S")
                        st.success(
                            f"Connection OK ({'connected' if connected else 'not connected'}) "
                            f"— latency {elapsed_ms:.1f}ms"
                        )
                    except Exception as exc:
                        st.error(f"Connection test failed: {exc}")

            if is_stub:
                try:
                    st.json(broker.describe())
                except Exception:
                    st.caption("Stub broker — metadata unavailable.")
            else:
                try:
                    balance = broker.get_balance()
                    cols = st.columns(3)
                    with cols[0]:
                        kpi_card("Cash", fmt_rupees(balance.cash))
                    with cols[1]:
                        kpi_card("Equity", fmt_rupees(balance.equity))
                    with cols[2]:
                        kpi_card("Buying Power", fmt_rupees(balance.buying_power))
                except Exception as exc:
                    st.warning(f"Balance unavailable: {exc}")

    # ── Tab 2: Trade ──────────────────────────────────────────────
    with tabs[1]:
        broker = st.session_state.get("bk_broker")
        if broker is None:
            st.info("Connect a broker in the Connection tab first.")
            return

        if st.session_state.get("bk_broker_type") in ("IBKR", "Alpaca", "Binance", "Future NEPSE"):
            st.info("Order placement is unavailable for stub brokers.")
        else:
            col1, col2, col3 = st.columns(3)
            with col1:
                symbol = st.text_input("Symbol", value="NABIL", key="bk_symbol")
            with col2:
                side = st.selectbox("Side", ["BUY", "SELL"], key="bk_side")
            with col3:
                quantity = st.number_input("Quantity", value=10.0, min_value=0.0, key="bk_qty")

            if st.button("Place Order", type="primary", key="bk_order"):
                try:
                    order = Order(
                        symbol=symbol.upper(),
                        side=OrderSide(side),
                        quantity=float(quantity),
                    )
                    result = broker.place_order(order)
                    st.session_state["bk_last_order"] = result
                    if result.status.value == "EXECUTED":
                        st.success(
                            f"Order executed at {result.filled_price} "
                            f"({result.order_id})."
                        )
                    else:
                        st.warning(f"Order status: {result.status.value}")
                except Exception as exc:
                    st.error(f"Order failed: {exc}")

            last_order = st.session_state.get("bk_last_order")
            if last_order:
                st.json(last_order.to_dict())

    # ── Tab 3: Positions ──────────────────────────────────────────
    with tabs[2]:
        broker = st.session_state.get("bk_broker")
        if broker is None:
            st.info("Connect a broker first.")
            return

        if st.session_state.get("bk_broker_type") in ("IBKR", "Alpaca", "Binance", "Future NEPSE"):
            st.info("Positions are unavailable for stub brokers.")
            return

        if st.button("Refresh Positions", key="bk_refresh_positions"):
            try:
                positions = broker.get_positions()
                st.session_state["bk_positions"] = positions
            except Exception as exc:
                st.error(f"Could not fetch positions: {exc}")

        positions = st.session_state.get("bk_positions", [])
        if positions:
            st.dataframe(
                pd.DataFrame([p.to_dict() for p in positions]),
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("No open positions.")
