"""기술적 지표 (일봉·주봉 공용). 표준식은 트레이딩뷰·증권사 HTS 와 같은 Wilder 평활(RMA)을 쓴다.

add_all_indicators(df) 가 붙이는 컬럼 (기존 이름 유지 — bot_engine · app 차트 · ai_analysis 가 그대로 읽는다)
  MA5 · MA20 · MA60 · MA120 / BB_mid · BB_upper · BB_lower · BB_width / tenkan · kijun · senkou_a · senkou_b · chikou /
  RSI / MACD · MACD_signal · MACD_hist
  + (추가) ATR · ADX · PLUS_DI · MINUS_DI · OBV · OBV_MA20
"""
import pandas as pd
import numpy as np


def _rma(s, period):
    """Wilder 평활 (RMA) = ewm(alpha=1/period, adjust=False). 첫 period 개가 모이기 전은 NaN."""
    return s.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


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
    mid = df["BB_mid"].replace(0, np.nan)                      # 0 나눗셈 방어
    df["BB_width"] = (df["BB_upper"] - df["BB_lower"]) / mid
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


def rsi_series(close, period=14):
    """Wilder RSI. 평균 하락폭이 0 이면 100(상승만), 상승·하락 모두 0 이면 50(변화 없음)."""
    delta = close.diff()
    gain = _rma(delta.clip(lower=0), period)
    loss = _rma(-delta.clip(upper=0), period)
    rsi = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi = rsi.where(loss != 0, np.where(gain > 0, 100.0, 50.0))
    return rsi.where(gain.notna() & loss.notna())


def compute_rsi(df, period=14):
    df = df.copy()
    df["RSI"] = rsi_series(df["Close"], period)
    return df


def compute_macd(df):
    df = df.copy()
    ema12 = df["Close"].ewm(span=12, adjust=False).mean()
    ema26 = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_hist"] = df["MACD"] - df["MACD_signal"]
    return df


def true_range(df):
    """TR = max(고가-저가, |고가-전일 종가|, |저가-전일 종가|). 첫 봉은 고가-저가."""
    prev = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - prev).abs(), (df["Low"] - prev).abs()], axis=1).max(axis=1)
    return tr


def atr_series(df, period=14):
    """ATR(14): True Range 의 Wilder 평균 (전일 종가 갭 포함)."""
    return _rma(true_range(df), period)


def adx_series(df, period=14):
    """ADX · +DI · -DI (Wilder). → (adx, plus_di, minus_di). ADX ≥ 25 추세장, < 20 횡보장."""
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    atr = atr_series(df, period).replace(0, np.nan)
    plus_di = 100 * _rma(plus_dm, period) / atr
    minus_di = 100 * _rma(minus_dm, period) / atr
    den = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / den.replace(0, np.nan)
    dx = dx.where(~((den == 0) & plus_di.notna()), 0.0)        # 방향 움직임이 아예 없으면 DX 0 (추세 없음)
    adx = _rma(dx, period)
    return adx, plus_di, minus_di


def obv_series(df):
    """OBV: 오른 날 거래량 +, 내린 날 −, 같으면 0 을 누적."""
    sign = np.sign(df["Close"].diff()).fillna(0)
    return (sign * df["Volume"].fillna(0)).cumsum()


def compute_trend_strength(df):
    df = df.copy()
    df["ATR"] = atr_series(df)
    df["ADX"], df["PLUS_DI"], df["MINUS_DI"] = adx_series(df)
    if "Volume" in df.columns:
        df["OBV"] = obv_series(df)
        df["OBV_MA20"] = df["OBV"].rolling(20).mean()
    return df


def add_all_indicators(df):
    df = moving_averages(df)
    df = bollinger_bands(df)
    df = ichimoku(df)
    df = compute_rsi(df)
    df = compute_macd(df)
    df = compute_trend_strength(df)
    return df
