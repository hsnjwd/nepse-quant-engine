from typing import Any, Optional


def simulate_trade(
    df: Any,
    start_index: int,
    signal: dict[str, Any],
) -> Optional[dict[str, Any]]:
    """Simulate a single trade from a signal.

    Args:
        df: Historical market data containing OHLCV values.
        start_index: Index of the current candle where the signal is generated.
        signal: Signal payload containing the trade direction and target levels.

    Returns:
        A dictionary describing the simulated trade outcome, or ``None`` when
        the signal does not indicate a buy.
    """

    if signal["signal"] != "BUY":
        return None

    entry = signal["price"]
    stop = signal["stop_loss"]
    target = signal["target1"]

    max_holding = 10

    for i in range(
        start_index + 1,
        min(len(df), start_index + max_holding + 1)
    ):

        candle = df.iloc[i]

        low = candle["Low"]
        high = candle["High"]

        # Stop Loss first
        if low <= stop:

            return {
                "entry_price": entry,
                "exit_price": stop,
                "result": "LOSS",
                "holding_days": i - start_index,
                "return_pct": round(
                    ((stop - entry) / entry) * 100,
                    2
                )
            }

        # Target hit
        if high >= target:

            return {
                "entry_price": entry,
                "exit_price": target,
                "result": "WIN",
                "holding_days": i - start_index,
                "return_pct": round(
                    ((target - entry) / entry) * 100,
                    2
                )
            }

    # Time exit
    exit_price = df.iloc[
        min(start_index + max_holding, len(df) - 1)
    ]["Close"]

    return {
        "entry_price": entry,
        "exit_price": float(exit_price),
        "result": "TIME_EXIT",
        "holding_days": max_holding,
        "return_pct": round(
            ((exit_price - entry) / entry) * 100,
            2
        )
    }