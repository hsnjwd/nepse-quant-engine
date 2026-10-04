"""Real-data benchmark corpus for the NEPSE Quant Engine (Sprint 11.2).

Selects a reproducible subset of the real scraped NEPSE CSVs that ship
with the repository (``data/raw``) so benchmarks run against genuine
market data instead of synthetic frames.

Design:

- **Deterministic** — selection is seeded, so ``--symbols 50`` picks the
  same files on every machine / run.
- **Reproducible** — the selected symbols and per-symbol row counts are
  recorded in the benchmark JSON output.
- **Read-only** — the corpus never modifies ``data/raw``; the runner
  copies the selected CSVs into a private temp directory.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# The repository ships ~280 real scraped NEPSE OHLCV CSVs here.
DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# A handful of files in data/raw are not regular stock CSVs (e.g. the
# sample/demo file or partial scrapes); exclude them explicitly so a
# corpus selection is always made up of real symbols.
EXCLUDED = {
    "sample",
    "rolling_volume_mean",
}


@dataclass
class CorpusSpec:
    """A reproducible selection of real stock CSVs."""

    symbols: list[str] = field(default_factory=list)
    raw_dir: Path = DEFAULT_RAW_DIR
    seed: int = 42

    @property
    def total_rows(self) -> int:
        """Sum of data rows (excluding headers) across selected files."""
        total = 0
        for name in self.symbols:
            total += _count_rows(self.raw_dir / f"{name}.csv")
        return total

    def to_dict(self) -> dict[str, Any]:
        """Serialisable summary for benchmark output."""
        return {
            "source": "real",
            "seed": self.seed,
            "symbols": self.symbols,
            "symbol_count": len(self.symbols),
            "total_rows": self.total_rows,
        }


def available_symbols(raw_dir: Path = DEFAULT_RAW_DIR) -> list[str]:
    """All stock symbols (lowercase stems) with a non-empty CSV in *raw_dir*."""
    symbols: list[str] = []
    for path in sorted(raw_dir.glob("*.csv")):
        stem = path.stem.lower()
        if stem in EXCLUDED:
            continue
        if _count_rows(path) > 0:
            symbols.append(stem)
    return symbols


def select_corpus(n: int, raw_dir: Path = DEFAULT_RAW_DIR, seed: int = 42) -> CorpusSpec:
    """Pick *n* symbols deterministically from the real corpus.

    Args:
        n: Number of symbols (50 / 100 / 200 / ...).  Clamped to the
            number of available real files.
        raw_dir: Directory containing the scraped CSVs.
        seed: Random seed for reproducible selection.

    Returns:
        A :class:`CorpusSpec` with the selected symbol stems.
    """
    symbols = available_symbols(raw_dir)
    if n <= 0:
        return CorpusSpec(symbols=[], raw_dir=raw_dir, seed=seed)
    rng = random.Random(seed)
    selected = rng.sample(symbols, min(n, len(symbols)))
    return CorpusSpec(symbols=sorted(selected), raw_dir=raw_dir, seed=seed)


def copy_corpus(spec: CorpusSpec, dest_dir: Path) -> list[Path]:
    """Copy the selected CSVs into *dest_dir* (created if needed).

    Copying keeps the benchmark read-only w.r.t. ``data/raw`` and gives
    the runner a self-contained data directory (mirroring the synthetic
    ``write_csvs`` flow) so the scanner/loader benchmarks are identical
    in shape for real and synthetic corpora.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for name in spec.symbols:
        src = spec.raw_dir / f"{name}.csv"
        if not src.exists():
            continue
        dst = dest_dir / f"{name}.csv"
        dst.write_bytes(src.read_bytes())
        copied.append(dst)
    return copied


def _count_rows(path: Path) -> int:
    """Count data rows (lines minus the header)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return 0
    lines = [ln for ln in text.splitlines() if ln.strip()]
    return max(0, len(lines) - 1)
