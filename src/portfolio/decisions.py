def portfolio_decision(holding):
    """
    Portfolio decision engine.
    """

    analysis = holding["analysis"]

    signal = analysis["signal"]
    pnl = holding["pnl_pct"]

    if signal == "BUY":
        if pnl < 10:
            return "ADD"
        return "HOLD"

    if signal == "SELL":
        if pnl > 0:
            return "BOOK PROFIT"
        return "EXIT"

    if pnl >= 20:
        return "PARTIAL PROFIT"

    return "HOLD"