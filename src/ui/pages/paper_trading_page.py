"""Paper Trading page — simulate buy/sell with live prices, trade history, and open positions."""

from __future__ import annotations

from typing import Any
from datetime import date

import streamlit as st
import pandas as pd

from src.ui.components import kpi_card
from src.ui.helpers import (
    section_header,
    divider,
    fmt_rupees,
    fmt_pct,
    safe_get,
    safe_float,
)
from src.ui.theme import theme
from src.data import DataService as _DataService


def _svc() -> _DataService:
    return _DataService()


def render() -> None:
    """Render the Paper Trading page with live prices."""
    section_header("Paper Trading", "Simulate trades with virtual capital and live prices")

    # ── Initialise state ─────────────────────────────────────────
    if "paper_balance" not in st.session_state:
        st.session_state.paper_balance = 1_000_000.0
    if "paper_positions" not in st.session_state:
        st.session_state.paper_positions = []
    if "paper_trades" not in st.session_state:
        st.session_state.paper_trades = []

    # ── Update all positions with live prices ────────────────────
    _update_live_prices()

    positions = st.session_state.paper_positions
    trades = st.session_state.paper_trades
    balance = st.session_state.paper_balance

    total_invested = sum(p.get("invested", 0) for p in positions)
    live_value = sum(p.get("current_value", p.get("invested", 0)) for p in positions)
    total_pnl = sum(p.get("pnl", 0) for p in positions)

    cols = st.columns(4)
    with cols[0]:
        kpi_card("💰 Balance", fmt_rupees(balance))
    with cols[1]:
        kpi_card("📈 Invested", fmt_rupees(total_invested))
    with cols[2]:
        kpi_card("💵 Total Value", fmt_rupees(balance + live_value))
    with cols[3]:
        pnl_delta = f"{fmt_pct(total_pnl / max(total_invested, 1))}" if total_invested > 0 else ""
        kpi_card("📊 P&L", fmt_rupees(total_pnl), delta=pnl_delta)

    divider()

    # ── Buy / Sell ───────────────────────────────────────────────
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("##### Buy")
        buy_symbol = st.text_input("Symbol", key="buy_symbol", placeholder="e.g., NABIL")
        buy_qty = st.number_input("Quantity", min_value=1, value=100, key="buy_qty")

        # Show live price preview
        if buy_symbol:
            try:
                live = _svc().get_stock(buy_symbol.upper().strip())
                if live:
                    st.caption(f"Live LTP: {fmt_rupees(live.ltp)}")
            except Exception:
                pass

        if st.button("🟢 Buy", use_container_width=True, type="primary") and buy_symbol:
            _execute_buy(buy_symbol.upper().strip(), buy_qty)

    with col2:
        st.markdown("##### Sell")
        sell_symbols = [p.get("symbol", "?") for p in positions] if positions else [""]
        sell_symbol = st.selectbox("Position", sell_symbols, key="sell_symbol")

        # Show position info
        if sell_symbol and positions:
            pos = next((p for p in positions if p["symbol"] == sell_symbol), None)
            if pos:
                st.caption(
                    f"Holding: {pos.get('qty', 0)} shares @ {fmt_rupees(pos.get('avg_price', 0))} "
                    f"| P&L: {fmt_rupees(pos.get('pnl', 0))}"
                )

        sell_qty = st.number_input("Quantity", min_value=1, value=100, key="sell_qty")
        if st.button("🔴 Sell", use_container_width=True) and sell_symbol:
            _execute_sell(sell_symbol.upper().strip(), sell_qty)

    divider()

    # ── Open Positions ───────────────────────────────────────────
    section_header("Open Positions")
    if positions:
        rows = []
        for p in positions:
            rows.append({
                "Symbol": p.get("symbol", "—"),
                "Qty": p.get("qty", 0),
                "Avg Price": fmt_rupees(safe_float(p.get("avg_price", 0))),
                "Invested": fmt_rupees(safe_float(p.get("invested", 0))),
                "LTP": fmt_rupees(safe_float(p.get("ltp", 0))),
                "Current Value": fmt_rupees(safe_float(p.get("current_value", 0))),
                "P&L": fmt_rupees(safe_float(p.get("pnl", 0))),
                "P&L %": fmt_pct(safe_float(p.get("pnl_pct", 0))),
            })
        df = pd.DataFrame(rows)

        def color_pnl(val: str) -> str:
            if val.startswith("₹ -") or val.startswith("-"):
                return "color: #FF5252"
            if val.startswith("₹") and not val.startswith("₹ -"):
                return "color: #00C853"
            return ""

        st.dataframe(
            df.style.applymap(color_pnl, subset=["P&L", "P&L %"]),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No open positions.")

    divider()

    # ── Trade History ────────────────────────────────────────────
    section_header("Trade History")
    if trades:
        df_trades = pd.DataFrame(trades)
        st.dataframe(df_trades, use_container_width=True, hide_index=True)
    else:
        st.info("No trades executed yet.")


def _update_live_prices() -> None:
    """Refresh live prices for all open positions."""
    positions = st.session_state.get("paper_positions", [])
    updated = False
    for p in positions:
        try:
            live = _svc().get_stock(p.get("symbol", ""))
            if live and live.ltp > 0:
                p["ltp"] = live.ltp
                qty = p.get("qty", 1)
                p["current_value"] = live.ltp * qty
                invested = p.get("invested", 0)
                pnl = p["current_value"] - invested
                p["pnl"] = pnl
                p["pnl_pct"] = (pnl / invested) if invested > 0 else 0
                updated = True
        except Exception:
            pass
    if updated:
        st.session_state.paper_positions = positions


def _get_live_price(symbol: str) -> float:
    """Get a live price for a symbol, returning 0 on failure."""
    try:
        live = _svc().get_stock(symbol)
        return live.ltp if live else 0.0
    except Exception:
        return 0.0


def _execute_buy(symbol: str, qty: int) -> None:
    """Execute a buy trade at live market price."""
    price = _get_live_price(symbol)
    if price <= 0:
        st.error(f"Could not fetch live price for {symbol}")
        return

    cost = price * qty
    if cost > st.session_state.paper_balance:
        st.error(f"Insufficient balance. Need {fmt_rupees(cost)}, have {fmt_rupees(st.session_state.paper_balance)}")
        return

    # Deduct balance
    st.session_state.paper_balance -= cost

    # Add / update position
    positions = st.session_state.paper_positions
    existing = next((p for p in positions if p["symbol"] == symbol), None)
    if existing:
        total_qty = existing["qty"] + qty
        total_cost = existing["invested"] + cost
        existing["qty"] = total_qty
        existing["avg_price"] = total_cost / total_qty
        existing["invested"] = total_cost
        existing["ltp"] = price
        existing["current_value"] = price * total_qty
        existing["pnl"] = existing["current_value"] - existing["invested"]
        existing["pnl_pct"] = existing["pnl"] / existing["invested"] if existing["invested"] > 0 else 0
    else:
        positions.append({
            "symbol": symbol,
            "qty": qty,
            "avg_price": price,
            "invested": cost,
            "ltp": price,
            "current_value": cost,
            "pnl": 0.0,
            "pnl_pct": 0.0,
        })

    # Record trade
    st.session_state.paper_trades.append({
        "Date": str(date.today()),
        "Type": "BUY",
        "Symbol": symbol,
        "Qty": qty,
        "Price": fmt_rupees(price),
        "Total": fmt_rupees(cost),
    })

    st.success(f"Bought {qty} shares of {symbol} at {fmt_rupees(price)}")
    st.rerun()


def _execute_sell(symbol: str, qty: int) -> None:
    """Execute a sell trade at live market price."""
    positions = st.session_state.paper_positions
    pos = next((p for p in positions if p["symbol"] == symbol), None)
    if not pos:
        st.error(f"No position found for {symbol}")
        return

    if qty > pos["qty"]:
        st.error(f"Can't sell {qty} shares, only have {pos['qty']}")
        return

    price = _get_live_price(symbol)
    if price <= 0:
        st.error(f"Could not fetch live price for {symbol}")
        return

    proceeds = price * qty
    cost_basis = pos["avg_price"] * qty
    trade_pnl = proceeds - cost_basis

    # Add to balance
    st.session_state.paper_balance += proceeds

    # Update / remove position
    if qty >= pos["qty"]:
        positions.remove(pos)
    else:
        pos["qty"] -= qty
        pos["invested"] = pos["avg_price"] * pos["qty"]
        pos["ltp"] = price
        pos["current_value"] = price * pos["qty"]
        pos["pnl"] = pos["current_value"] - pos["invested"]
        pos["pnl_pct"] = pos["pnl"] / pos["invested"] if pos["invested"] > 0 else 0

    # Record trade
    st.session_state.paper_trades.append({
        "Date": str(date.today()),
        "Type": "SELL",
        "Symbol": symbol,
        "Qty": qty,
        "Price": fmt_rupees(price),
        "Total": fmt_rupees(proceeds),
        "P&L": fmt_rupees(trade_pnl),
    })

    st.success(f"Sold {qty} shares of {symbol} at {fmt_rupees(price)} (P&L: {fmt_rupees(trade_pnl)})")
    st.rerun()
