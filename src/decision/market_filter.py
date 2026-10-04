def market_trend(df):

    latest = df.iloc[-1]


    if latest["Close"] > latest["SMA_50"]:
        return "BULLISH"


    elif latest["Close"] < latest["SMA_50"]:
        return "BEARISH"


    else:
        return "SIDEWAYS"