def calculate_support_resistance(df, window=20):

    support = (
        df["Low"]
        .rolling(window)
        .min()
        .iloc[-1]
    )


    resistance = (
        df["High"]
        .rolling(window)
        .max()
        .iloc[-1]
    )


    return {
        "support": round(support,2),
        "resistance": round(resistance,2)
    }