def calculate_position_size(result):
    """
    Calculate recommended position size for multiple portfolio sizes.
    """

    price = result["price"]
    stop = result["stop_loss"]

    if price is None or stop is None or price <= stop:
        return {}

    risk_per_share = price - stop

    portfolios = [50000, 100000, 250000, 500000, 1000000]

    output = {}

    for capital in portfolios:

        risk_amount = capital * 0.02

        shares = int(risk_amount / risk_per_share)

        investment = round(shares * price, 2)

        output[str(capital)] = {
            "shares": shares,
            "investment": investment,
            "risk_amount": round(risk_amount, 2),
        }

    return {
        "position_size": output
    }