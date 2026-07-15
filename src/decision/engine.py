def calculate_confidence(score):
    """
    Convert technical score into confidence %
    """

    confidence = abs(score) * 10

    if confidence > 95:
        confidence = 95

    return confidence



def generate_trade_plan(row, score, signal):

    price = row["Close"]


    # Default response
    plan = {
        "price": price,
        "score": score,
        "signal": signal,
        "confidence": calculate_confidence(score)
    }


    if signal == "BUY":

        plan["entry_zone"] = (
            round(price * 0.98, 2),
            round(price * 1.02, 2)
        )

        plan["target_1"] = round(price * 1.05, 2)
        plan["target_2"] = round(price * 1.10, 2)
        plan["target_3"] = round(price * 1.15, 2)

        plan["stop_loss"] = round(price * 0.95, 2)


    elif signal == "SELL":

        plan["entry_zone"] = None

        plan["target_1"] = round(price * 0.95, 2)
        plan["target_2"] = round(price * 0.90, 2)
        plan["target_3"] = round(price * 0.85, 2)

        plan["stop_loss"] = round(price * 1.05, 2)


    else:

        plan["entry_zone"] = None
        plan["target_1"] = None
        plan["target_2"] = None
        plan["target_3"] = None
        plan["stop_loss"] = None


    return plan