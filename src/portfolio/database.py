"""SQLite database manager for persistent portfolio storage.

Provides CRUD operations for holdings, transactions, and portfolio state.
All operations are atomic and thread-safe.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from src.portfolio.models import (
    PortfolioHolding,
    PortfolioTransaction,
    PortfolioSummary,
)

logger = logging.getLogger(__name__)

# Default database path
DEFAULT_DB_PATH = Path.home() / ".nepse" / "portfolio.db"


class PortfolioDatabase:
    """SQLite-backed portfolio database.

    Thread-safe.  Creates tables on first access.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _initialize(self) -> None:
        with self._lock:
            conn = self._connect()
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS portfolio_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS holdings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL UNIQUE,
                    quantity INTEGER NOT NULL DEFAULT 0,
                    average_price REAL NOT NULL DEFAULT 0.0,
                    invested REAL NOT NULL DEFAULT 0.0,
                    realized_pnl REAL NOT NULL DEFAULT 0.0,
                    sector TEXT DEFAULT '',
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    transaction_type TEXT NOT NULL CHECK(transaction_type IN ('BUY','SELL')),
                    quantity INTEGER NOT NULL,
                    price REAL NOT NULL,
                    total REAL NOT NULL,
                    commission REAL NOT NULL DEFAULT 0.0,
                    pnl REAL NOT NULL DEFAULT 0.0,
                    notes TEXT DEFAULT '',
                    timestamp TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE INDEX IF NOT EXISTS idx_transactions_symbol
                    ON transactions(symbol);
                CREATE INDEX IF NOT EXISTS idx_transactions_timestamp
                    ON transactions(timestamp);
            """)
            conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ── Cash balance ─────────────────────────────────────────────

    def get_cash_balance(self) -> float:
        """Return the current cash balance."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT value FROM portfolio_settings WHERE key = 'cash_balance'"
            ).fetchone()
            return float(row["value"]) if row else 0.0

    def set_cash_balance(self, amount: float) -> None:
        """Set the cash balance."""
        with self._lock:
            conn = self._connect()
            conn.execute(
                """INSERT OR REPLACE INTO portfolio_settings (key, value)
                   VALUES ('cash_balance', ?)""",
                (str(amount),),
            )
            conn.commit()

    def adjust_cash(self, delta: float) -> float:
        """Add *delta* to the cash balance.  Returns new balance."""
        with self._lock:
            conn = self._connect()
            current = self.get_cash_balance()
            new_balance = current + delta
            conn.execute(
                """INSERT OR REPLACE INTO portfolio_settings (key, value)
                   VALUES ('cash_balance', ?)""",
                (str(new_balance),),
            )
            conn.commit()
            return new_balance

    # ── Holdings ─────────────────────────────────────────────────

    def get_holdings(self) -> list[PortfolioHolding]:
        """Return all holdings."""
        with self._lock:
            conn = self._connect()
            rows = conn.execute(
                "SELECT * FROM holdings WHERE quantity > 0 ORDER BY symbol"
            ).fetchall()
            return [self._row_to_holding(r) for r in rows]

    def get_holding(self, symbol: str) -> PortfolioHolding | None:
        """Return a single holding by symbol."""
        with self._lock:
            conn = self._connect()
            row = conn.execute(
                "SELECT * FROM holdings WHERE symbol = ?", (symbol.upper(),)
            ).fetchone()
            return self._row_to_holding(row) if row else None

    def upsert_holding(
        self,
        symbol: str,
        quantity: int,
        average_price: float,
        invested: float,
        sector: str = "",
    ) -> PortfolioHolding:
        """Insert or update a holding."""
        with self._lock:
            conn = self._connect()
            existing = conn.execute(
                "SELECT * FROM holdings WHERE symbol = ?", (symbol.upper(),)
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE holdings SET quantity=?, average_price=?, invested=?,
                       updated_at=datetime('now') WHERE symbol=?""",
                    (quantity, average_price, invested, symbol.upper()),
                )
            else:
                conn.execute(
                    """INSERT INTO holdings (symbol, quantity, average_price, invested, sector)
                       VALUES (?, ?, ?, ?, ?)""",
                    (symbol.upper(), quantity, average_price, invested, sector),
                )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM holdings WHERE symbol = ?", (symbol.upper(),)
            ).fetchone()
            return self._row_to_holding(row) if row else PortfolioHolding()

    def remove_holding(self, symbol: str) -> None:
        """Remove a holding entirely."""
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM holdings WHERE symbol = ?", (symbol.upper(),))
            conn.commit()

    def _row_to_holding(self, row: sqlite3.Row) -> PortfolioHolding:
        return PortfolioHolding(
            id=row["id"],
            symbol=row["symbol"],
            quantity=row["quantity"],
            average_price=row["average_price"],
            invested=row["invested"],
            realized_pnl=row["realized_pnl"],
            sector=row["sector"],
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
        )

    # ── Transactions ─────────────────────────────────────────────

    def add_transaction(
        self,
        symbol: str,
        transaction_type: str,
        quantity: int,
        price: float,
        total: float,
        commission: float = 0.0,
        pnl: float = 0.0,
        notes: str = "",
    ) -> int:
        """Record a transaction.  Returns the new transaction ID."""
        with self._lock:
            conn = self._connect()
            cursor = conn.execute(
                """INSERT INTO transactions
                   (symbol, transaction_type, quantity, price, total, commission, pnl, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (symbol.upper(), transaction_type.upper(), quantity, price, total, commission, pnl, notes),
            )
            conn.commit()
            return cursor.lastrowid or 0

    def get_transactions(
        self,
        symbol: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[PortfolioTransaction]:
        """Return transactions, optionally filtered by symbol."""
        with self._lock:
            conn = self._connect()
            if symbol:
                rows = conn.execute(
                    """SELECT * FROM transactions WHERE symbol = ?
                       ORDER BY timestamp DESC LIMIT ? OFFSET ?""",
                    (symbol.upper(), limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM transactions ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [self._row_to_transaction(r) for r in rows]

    def _row_to_transaction(self, row: sqlite3.Row) -> PortfolioTransaction:
        return PortfolioTransaction(
            id=row["id"],
            symbol=row["symbol"],
            transaction_type=row["transaction_type"],
            quantity=row["quantity"],
            price=row["price"],
            total=row["total"],
            commission=row["commission"],
            pnl=row["pnl"],
            notes=row["notes"],
            timestamp=datetime.fromisoformat(row["timestamp"]) if row["timestamp"] else datetime.now(),
        )

    # ── Summary ──────────────────────────────────────────────────

    def get_summary(self, current_prices: dict[str, float] | None = None) -> PortfolioSummary:
        """Build a full portfolio summary.

        Args:
            current_prices: Optional dict of {symbol: current_price} for live valuations.
        """
        with self._lock:
            cash = self.get_cash_balance()
            holdings = self.get_holdings()
            transactions = self.get_transactions(limit=10000)

            current_prices = current_prices or {}
            total_invested = 0.0
            total_current = cash
            total_realized = 0.0
            total_commission = 0.0

            enriched_holdings: list[PortfolioHolding] = []
            for h in holdings:
                price = current_prices.get(h.symbol, h.average_price)
                current_val = price * h.quantity
                unrealized = current_val - h.invested
                unrealized_pct = (unrealized / h.invested * 100) if h.invested > 0 else 0.0
                total_invested += h.invested
                total_current += current_val
                total_realized += h.realized_pnl

                enriched_holdings.append(PortfolioHolding(
                    symbol=h.symbol,
                    quantity=h.quantity,
                    average_price=h.average_price,
                    invested=h.invested,
                    current_price=price,
                    current_value=current_val,
                    unrealized_pnl=unrealized,
                    unrealized_pnl_pct=unrealized_pct,
                    realized_pnl=h.realized_pnl,
                    sector=h.sector,
                    updated_at=h.updated_at,
                ))

            for t in transactions:
                total_commission += t.commission

            # Calculate weights
            for h in enriched_holdings:
                h.weight_pct = (h.current_value / total_current * 100) if total_current > 0 else 0.0

            unrealized_pnl = total_current - cash - total_invested
            total_pnl = unrealized_pnl + total_realized
            total_pnl_pct = (total_pnl / (total_invested or 1)) * 100

            return PortfolioSummary(
                cash=cash,
                invested=total_invested,
                current_value=total_current,
                total_pnl=total_pnl,
                total_pnl_pct=total_pnl_pct,
                unrealized_pnl=unrealized_pnl,
                realized_pnl=total_realized,
                total_commission=total_commission,
                holdings_count=len(enriched_holdings),
                transaction_count=len(transactions),
                holdings=enriched_holdings,
            )
