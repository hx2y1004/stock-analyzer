import pandas as pd
import numpy as np


def moving_averages(df):
    df = df.copy()
    for period in [5, 20, 60, 120]:
        df[f"MA{period}"] = df["Close"].rolling(window=period).mean()
    return df


def bollinger_bands(df, period=20, std_dev=2):
    df = df.copy()
    df["BB_mid"] = df["Close"].rolling(window=period).mean()
    rolling_std = df["Close"].rolling(window=period).std()
    df["BB_upper"] = df["BB_mid"] + (rolling_std * std_dev)
    df["BB_lower"] = df["BB_mid"] - (rolling_std * std_dev)
    df["BB_width"] = (df["BB_upper"] - df["BB_lower"]) / df["BB_mid"]
    return df


def ichimoku(df):
    df = df.copy()
    high = df["High"]
    low = df["Low"]
    close = df["Close"]

    # 전환선 (9일)
    df["tenkan"] = (high.rolling(9).max() + low.rolling(9).min()) / 2
    # 기준선 (26일)
    df["kijun"] = (high.rolling(26).max() + low.rolling(26).min()) / 2
    # 선행스팬 A
    df["senkou_a"] = ((df["tenkan"] + df["kijun"]) / 2).shift(26)
    # 선행스팬 B (52일)
    df["senkou_b"] = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
    # 후행스팬
    df["chikou"] = close.shift(-26)
    return df


def compute_rsi(df, period=14):
    df = df.copy()
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    df["RSI"] = 100 - (100 / (1 + rs))
    return df


def compute_macd(df):
    df = df.copy()
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]
    return df


def add_all_indicators(df):
    df = moving_averages(df)
    df = bollinger_bands(df)
    df = ichimoku(df)
    df = compute_rsi(df)
    df = compute_macd(df)
    return df
