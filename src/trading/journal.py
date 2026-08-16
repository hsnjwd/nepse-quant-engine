"""Trade journal for automatic trade logging, analysis, and export.

Features:
- Automatic trade logging from paper trading engine
- Manual notes, mistakes, lessons learned
- Tags, strategy, confidence, emotion tracking
- R multiple, risk %, reward % calculations
- Win/loss analysis with statistics
- CSV export of journal entries
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# User-state root. Defaults to ``~/.nepse``; overridable via NEPSE_HOME
# so tests can isolate from real user data.
NEPSE_HOME = Path(os.environ.get("NEPSE_HOME", str(Path.home() / ".nepse")))

JOURNAL_DIR = NEPSE_HOME / "journal"
JOURNAL_FILE = JOURNAL_DIR / "trade_journal.json"


@dataclass
class JournalEntry:
    """A single trade journal entry."""

    id: str = ""
    symbol: str = ""
    side: str = ""  # BUY | SELL
    quantity: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl: float = 0.0
    pnl_pct: float = 0.0
    commission: float = 0.0
    r_multiple: float = 0.0
    risk_pct: float = 0.0
    reward_pct: float = 0.0
    risk_reward_ratio: float = 0.0
    strategy: str = ""
    confidence: int = 0  # 1-10
    emotion: str = ""
    tags: list[str] = field(default_factory=list)
    notes: str = ""
    mistakes: str = ""
    lessons_learned: str = ""
    screenshot_path: str = ""
    entry_date: str = ""
    exit_date: str = ""
    created_at: datetime = field(default_factory=datetime.now)

    @property
    def is_win(self) -> bool:
        return self.pnl > 0

    @property
    def is_loss(self) -> bool:
        return self.pnl < 0

    @property
    def holding_days(self) -> int:
        if self.entry_date and self.exit_date:
            try:
                ed = datetime.fromisoformat(self.entry_date)
                xd = datetime.fromisoformat(self.exit_date)
                return max(0, (xd - ed).days)
            except (ValueError, TypeError):
                pass
        return 0


@dataclass
class JournalStats:
    """Aggregate statistics from the trade journal."""

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    profit_factor: float = 0.0
    avg_r_multiple: float = 0.0
    avg_holding_days: float = 0.0
    avg_confidence: float = 0.0
    best_strategy: str = ""
    worst_strategy: str = ""
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    total_commission: float = 0.0
    sharpe_ratio: float = 0.0


class TradeJournal:
    """Persistent trade journal with automatic logging and analysis.

    Usage::
        journal = TradeJournal()
        journal.log_trade(symbol="NABIL", side="SELL", ...)
        stats = journal.get_stats()
        csv_data = journal.to_csv()
    """

    def __init__(self) -> None:
        self._entries: list[JournalEntry] = []
        self._load()

    # ── Logging ──────────────────────────────────────────────────

    def log_trade(
        self,
        symbol: str,
        side: str,
        quantity: int,
        entry_price: float,
        exit_price: float,
        pnl: float,
        pnl_pct: float = 0.0,
        commission: float = 0.0,
        r_multiple: float = 0.0,
        risk_pct: float = 0.0,
        reward_pct: float = 0.0,
        strategy: str = "",
        confidence: int = 0,
        emotion: str = "",
        tags: list[str] | None = None,
        notes: str = "",
        mistakes: str = "",
        lessons_learned: str = "",
        screenshot_path: str = "",
        entry_date: str | None = None,
        exit_date: str | None = None,
    ) -> JournalEntry:
        """Record a trade in the journal."""
        entry = JournalEntry(
            id=f"J{len(self._entries) + 1:06d}",
            symbol=symbol.upper(),
            side=side.upper(),
            quantity=quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission=commission,
            r_multiple=r_multiple,
            risk_pct=risk_pct,
            reward_pct=reward_pct,
            risk_reward_ratio=(reward_pct / risk_pct) if risk_pct > 0 else 0.0,
            strategy=strategy,
            confidence=min(max(confidence, 0), 10),
            emotion=emotion,
            tags=tags or [],
            notes=notes,
            mistakes=mistakes,
            lessons_learned=lessons_learned,
            screenshot_path=screenshot_path,
            entry_date=entry_date or str(date.today()),
            exit_date=exit_date or str(date.today()),
        )
        self._entries.insert(0, entry)
        self._save()
        logger.info("[TradeJournal] Logged %s %s %d %s (P&L: %.2f)", entry.id, side, quantity, symbol, pnl)
        return entry

    def update_entry(self, entry_id: str, **kwargs: Any) -> bool:
        """Update fields of an existing journal entry."""
        for entry in self._entries:
            if entry.id == entry_id:
                for key, value in kwargs.items():
                    if hasattr(entry, key):
                        setattr(entry, key, value)
                    else:
                        logger.warning("[TradeJournal] Unknown field '%s' skipped in update_entry", key)
                self._save()
                return True
        return False

    def add_notes(self, entry_id: str, notes: str) -> bool:
        """Add or append notes to an entry."""
        return self.update_entry(entry_id, notes=notes)

    def add_lessons(self, entry_id: str, lessons: str) -> bool:
        """Add lessons learned to an entry."""
        return self.update_entry(entry_id, lessons_learned=lessons)

    def delete_entry(self, entry_id: str) -> bool:
        """Delete an entry by ID."""
        for entry in self._entries:
            if entry.id == entry_id:
                self._entries.remove(entry)
                self._save()
                return True
        return False

    # ── Queries ──────────────────────────────────────────────────

    def get_entries(
        self,
        symbol: str | None = None,
        strategy: str | None = None,
        limit: int = 100,
    ) -> list[JournalEntry]:
        """Get journal entries with optional filters."""
        entries = self._entries
        if symbol:
            entries = [e for e in entries if e.symbol == symbol.upper()]
        if strategy:
            entries = [e for e in entries if e.strategy == strategy]
        return entries[:limit]

    def get_entry(self, entry_id: str) -> JournalEntry | None:
        """Get a single entry by ID."""
        for entry in self._entries:
            if entry.id == entry_id:
                return entry
        return None

    def get_recent(self, limit: int = 20) -> list[JournalEntry]:
        """Get the most recent entries."""
        return self._entries[:limit]

    def count(self) -> int:
        return len(self._entries)

    # ── Statistics ───────────────────────────────────────────────

    def get_stats(self) -> JournalStats:
        """Calculate aggregate statistics from all entries."""
        if not self._entries:
            return JournalStats()

        wins = [e for e in self._entries if e.is_win]
        losses = [e for e in self._entries if e.is_loss]
        total = len(self._entries)

        win_rate = (len(wins) / total * 100) if total > 0 else 0.0
        total_pnl = sum(e.pnl for e in self._entries)
        avg_win = sum(e.pnl for e in wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(e.pnl for e in losses) / len(losses)) if losses else 0.0

        largest_win = max((e.pnl for e in wins), default=0.0)
        largest_loss = min((e.pnl for e in losses), default=0.0)

        total_wins = sum(e.pnl for e in wins) if wins else 0
        total_losses = abs(sum(e.pnl for e in losses)) if losses else 0
        profit_factor = total_wins / total_losses if total_losses > 0 else float("inf")

        r_values = [e.r_multiple for e in self._entries if e.r_multiple > 0]
        avg_r = sum(r_values) / len(r_values) if r_values else 0.0

        holding_days = [e.holding_days for e in self._entries if e.holding_days > 0]
        avg_hold = sum(holding_days) / len(holding_days) if holding_days else 0.0

        confidences = [e.confidence for e in self._entries if e.confidence > 0]
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        # Best/worst strategy by avg P&L
        strategies: dict[str, list[float]] = {}
        for e in self._entries:
            if e.strategy:
                strategies.setdefault(e.strategy, []).append(e.pnl)
        best_strat = ""
        worst_strat = ""
        if strategies:
            avg_by_strat = {k: sum(v) / len(v) for k, v in strategies.items()}
            best_strat = max(avg_by_strat, key=avg_by_strat.get)
            worst_strat = min(avg_by_strat, key=avg_by_strat.get)

        # Consecutive wins/losses (chronological order — entries are newest-first)
        max_consec_wins = 0
        max_consec_losses = 0
        current_wins = 0
        current_losses = 0
        for e in reversed(self._entries):  # oldest first
            if e.is_win:
                current_wins += 1
                current_losses = 0
                max_consec_wins = max(max_consec_wins, current_wins)
            elif e.is_loss:
                current_losses += 1
                current_wins = 0
                max_consec_losses = max(max_consec_losses, current_losses)
            else:
                current_wins = 0
                current_losses = 0

        total_commission = sum(e.commission for e in self._entries)

        return JournalStats(
            total_trades=total,
            wins=len(wins),
            losses=len(losses),
            win_rate=win_rate,
            total_pnl=total_pnl,
            avg_win=avg_win,
            avg_loss=avg_loss,
            largest_win=largest_win,
            largest_loss=largest_loss,
            profit_factor=profit_factor if profit_factor != float("inf") else 999.0,
            avg_r_multiple=avg_r,
            avg_holding_days=avg_hold,
            avg_confidence=avg_confidence,
            best_strategy=best_strat,
            worst_strategy=worst_strat,
            consecutive_wins=max_consec_wins,
            consecutive_losses=max_consec_losses,
            total_commission=total_commission,
        )

    # ── Export ───────────────────────────────────────────────────

    def to_csv(self, entries: list[JournalEntry] | None = None) -> bytes:
        """Export journal entries to CSV."""
        entries = entries or self._entries
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow([
            "ID", "Symbol", "Side", "Quantity", "Entry Price", "Exit Price",
            "P&L", "P&L %", "Commission", "R Multiple", "Risk %", "Reward %",
            "R:R Ratio", "Strategy", "Confidence", "Emotion", "Tags",
            "Entry Date", "Exit Date", "Holding Days", "Notes", "Mistakes",
            "Lessons Learned",
        ])

        for e in entries:
            writer.writerow([
                e.id, e.symbol, e.side, e.quantity, e.entry_price, e.exit_price,
                f"{e.pnl:.2f}", f"{e.pnl_pct:.2f}", f"{e.commission:.2f}",
                f"{e.r_multiple:.2f}", f"{e.risk_pct:.1f}", f"{e.reward_pct:.1f}",
                f"{e.risk_reward_ratio:.2f}", e.strategy, e.confidence, e.emotion,
                ";".join(e.tags), e.entry_date, e.exit_date, e.holding_days,
                e.notes, e.mistakes, e.lessons_learned,
            ])

        return output.getvalue().encode("utf-8")

    def to_json(self) -> bytes:
        """Export all entries to JSON."""
        data = []
        for e in self._entries:
            data.append({
                "id": e.id,
                "symbol": e.symbol,
                "side": e.side,
                "quantity": e.quantity,
                "entry_price": e.entry_price,
                "exit_price": e.exit_price,
                "pnl": e.pnl,
                "pnl_pct": e.pnl_pct,
                "commission": e.commission,
                "r_multiple": e.r_multiple,
                "risk_pct": e.risk_pct,
                "reward_pct": e.reward_pct,
                "risk_reward_ratio": e.risk_reward_ratio,
                "strategy": e.strategy,
                "confidence": e.confidence,
                "emotion": e.emotion,
                "tags": e.tags,
                "entry_date": e.entry_date,
                "exit_date": e.exit_date,
                "notes": e.notes,
                "mistakes": e.mistakes,
                "lessons_learned": e.lessons_learned,
            })
        return json.dumps(data, indent=2).encode("utf-8")

    # ── Persistence ──────────────────────────────────────────────

    def _save(self) -> None:
        try:
            JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
            data = []
            for e in self._entries:
                data.append({
                    "id": e.id,
                    "symbol": e.symbol,
                    "side": e.side,
                    "quantity": e.quantity,
                    "entry_price": e.entry_price,
                    "exit_price": e.exit_price,
                    "pnl": e.pnl,
                    "pnl_pct": e.pnl_pct,
                    "commission": e.commission,
                    "r_multiple": e.r_multiple,
                    "risk_pct": e.risk_pct,
                    "reward_pct": e.reward_pct,
                    "risk_reward_ratio": e.risk_reward_ratio,
                    "strategy": e.strategy,
                    "confidence": e.confidence,
                    "emotion": e.emotion,
                    "tags": e.tags,
                    "notes": e.notes,
                    "mistakes": e.mistakes,
                    "lessons_learned": e.lessons_learned,
                    "screenshot_path": e.screenshot_path,
                    "entry_date": e.entry_date,
                    "exit_date": e.exit_date,
                    "created_at": e.created_at.isoformat(),
                })
            with open(JOURNAL_FILE, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.warning("[TradeJournal] Save error: %s", exc)

    def _load(self) -> None:
        try:
            if JOURNAL_FILE.exists():
                with open(JOURNAL_FILE) as f:
                    data = json.load(f)
                for item in data:
                    self._entries.append(JournalEntry(
                        id=item.get("id", ""),
                        symbol=item.get("symbol", ""),
                        side=item.get("side", ""),
                        quantity=item.get("quantity", 0),
                        entry_price=item.get("entry_price", 0.0),
                        exit_price=item.get("exit_price", 0.0),
                        pnl=item.get("pnl", 0.0),
                        pnl_pct=item.get("pnl_pct", 0.0),
                        commission=item.get("commission", 0.0),
                        r_multiple=item.get("r_multiple", 0.0),
                        risk_pct=item.get("risk_pct", 0.0),
                        reward_pct=item.get("reward_pct", 0.0),
                        risk_reward_ratio=item.get("risk_reward_ratio", 0.0),
                        strategy=item.get("strategy", ""),
                        confidence=item.get("confidence", 0),
                        emotion=item.get("emotion", ""),
                        tags=item.get("tags", []),
                        notes=item.get("notes", ""),
                        mistakes=item.get("mistakes", ""),
                        lessons_learned=item.get("lessons_learned", ""),
                        screenshot_path=item.get("screenshot_path", ""),
                        entry_date=item.get("entry_date", ""),
                        exit_date=item.get("exit_date", ""),
                        created_at=datetime.fromisoformat(item["created_at"]) if item.get("created_at") else datetime.now(),
                    ))
        except Exception as exc:
            logger.warning("[TradeJournal] Load error: %s", exc)
