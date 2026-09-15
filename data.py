# language: Python 3.10+, file: data.py
# данные Binance + фичи + загрузка модели
import requests
import joblib
import numpy as np
import pandas as pd
from pathlib import Path
from config import CFG

_FAPI = "https://fapi.binance.com"
_models = {}


def fetch_ohlcv(sym, limit=500):
    sym_api = sym.split(":")[0].replace("/", "")
    r = requests.get(f"{_FAPI}/fapi/v1/klines", params={
        "symbol": sym_api,
        "interval": CFG["INTERVAL"],
        "limit": limit,
    }, timeout=20)
    r.raise_for_status()
    raw = r.json()
    df = pd.DataFrame(raw, columns=[
        "ts", "open", "high", "low", "close", "volume",
        "close_time", "qav", "trades", "tbbav", "tbqav", "ignore"])
    df = df[["ts", "open", "high", "low", "close", "volume"]].copy()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("time").drop(columns=["ts"])


def rsi(s, p=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(p).mean()
    dn = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def ema(s, span):
    return s.ewm(span=span, adjust=False).mean()


def atr(df, p=14):
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"] - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(p).mean()


def macd(s):
    line = ema(s, 12) - ema(s, 26)
    return line, ema(line, 9), line - ema(line, 9)


def build_features(df):
    f = pd.DataFrame(index=df.index)
    c, h, l, v = df["close"], df["high"], df["low"], df["volume"]

    for p in [1, 3, 6, 12, 24, 48]:
        f[f"ret_{p}"] = c.pct_change(p)

    logret = np.log(c / c.shift())
    for p in [12, 24, 48, 96]:
        f[f"vol_{p}"] = logret.rolling(p).std()

    f["rsi_14"] = rsi(c, 14)
    f["rsi_28"] = rsi(c, 28)

    ml, ms, mh = macd(c)
    f["macd_line"], f["macd_hist"] = ml, mh

    for s in [9, 21, 50, 100, 200]:
        f[f"close_over_ema_{s}"] = c / ema(c, s) - 1.0

    for p in [20, 50]:
        mid, sd = c.rolling(p).mean(), c.rolling(p).std()
        up, lo = mid + 2 * sd, mid - 2 * sd
        f[f"bb_pos_{p}"] = (c - lo) / (up - lo).replace(0, np.nan)

    f["atr_pct_14"] = atr(df, 14) / c

    vm, vs = v.rolling(24).mean(), v.rolling(24).std()
    f["vol_zscore"] = (v - vm) / vs.replace(0, np.nan)
    f["vol_ratio_6_24"] = v.rolling(6).mean() / vm.replace(0, np.nan)

    f["body_ratio"] = (c - df["open"]) / (h - l).replace(0, np.nan)
    f["upper_wick"] = (h - c.combine(df["open"], max)) / (h - l).replace(0, np.nan)
    f["lower_wick"] = (c.combine(df["open"], min) - l) / (h - l).replace(0, np.nan)

    for p in [24, 96]:
        hh, ll = h.rolling(p).max(), l.rolling(p).min()
        f[f"range_pos_{p}"] = (c - ll) / (hh - ll).replace(0, np.nan)

    hour, dow = df.index.hour, df.index.dayofweek
    f["hour_sin"] = np.sin(2 * np.pi * hour / 24)
    f["hour_cos"] = np.cos(2 * np.pi * hour / 24)
    f["dow_sin"] = np.sin(2 * np.pi * dow / 7)
    f["dow_cos"] = np.cos(2 * np.pi * dow / 7)

    return f.replace([np.inf, -np.inf], np.nan).dropna()


def load_model(sym):
    if sym in _models:
        return _models[sym]
    fname = sym.replace("/", "_").replace(":", "_") + ".lgb"
    path = Path(CFG["MODEL_DIR"]) / fname
    if not path.exists():
        print(f"модель не найдена: {path}")
        return None
    bundle = joblib.load(path)
    _models[sym] = (bundle["model"], bundle["features"])
    print(f"модель загружена: {fname}")
    return _models[sym]


def predict(sym, df_closed):
    bundle = load_model(sym)
    if bundle is None:
        return None
    model, feat_names = bundle
    feats = build_features(df_closed)
    if len(feats) < 1:
        return None
    X = feats[feat_names].iloc[[-1]]
    proba = model.predict_proba(X)[0]
    sig = int(proba.argmax()) - 1
    return sig, proba
