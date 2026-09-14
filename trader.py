# language: Python 3.10+, file: trader.py
# мультипарный автотрейдер: модель + исполнение + риск + telegram
import os, json, time, csv, logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import ccxt, joblib, requests
import numpy as np
import pandas as pd

from config import CFG

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("trader")


# ============================================================
# STATE
# ============================================================
DEFAULT_SYM_STATE = {
    "position": 0,
    "entry_price": 0.0,
    "size": 0.0,
    "entry_ts": 0,
    "last_candle_ts": 0,
    "exchange_sl_id": None,
    "exchange_tp_id": None,
    "stop_price": 0.0,
    "tp_price": 0.0,
    "trail_active": False,
    "trail_peak": 0.0,
}

DEFAULT_STATE = {
    "symbols": {},
    "daily_pnl": 0.0,
    "daily_reset_ts": 0,
    "daily_trades": 0,
    "peak_equity": 0.0,
    "started_equity": 0.0,
    "week_started_equity": 0.0,
    "kill_switch": False,
    "total_trades": 0,
    "wins": 0,
    "losses": 0,
    "multi_symbol": True,
    "active_symbol": None,
    "phase": "ok",
    "weekly_snapshot": None,
    "last_daily_report_day": -1,
}

def load_state():
    p = Path(CFG["STATE_PATH"])
    if not p.exists():
        s = json.loads(json.dumps(DEFAULT_STATE))
        save_state(s)
        return s
    try:
        raw = json.loads(p.read_text())
        base = json.loads(json.dumps(DEFAULT_STATE))
        base.update(raw)
        for sym in CFG["SYMBOLS"]:
            if sym not in base["symbols"]:
                base["symbols"][sym] = dict(DEFAULT_SYM_STATE)
            else:
                merged = dict(DEFAULT_SYM_STATE)
                merged.update(base["symbols"][sym])
                base["symbols"][sym] = merged
        return base
    except Exception as e:
        log.error(f"state load: {e}")
        return json.loads(json.dumps(DEFAULT_STATE))

def save_state(s):
    Path(CFG["STATE_PATH"]).write_text(json.dumps(s, indent=2, default=str))


# ============================================================
# БИРЖА
# ============================================================
def make_exchange():
    ex = ccxt.binanceusdm({
        "apiKey": CFG["BINANCE_KEY"],
        "secret": CFG["BINANCE_SECRET"],
        "enableRateLimit": True,
        "options": {"defaultType": "future"},
    })
    if CFG["BINANCE_TESTNET"]:
        ex.set_sandbox_mode(True)
    ex.load_markets()
    return ex

def fetch_ohlcv(ex, sym, limit=500):
    raw = ex.fetch_ohlcv(sym, timeframe=CFG["INTERVAL"], limit=limit)
    df = pd.DataFrame(raw, columns=["ts", "open", "high", "low", "close", "volume"])
    df["time"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("time").drop(columns=["ts"])

def get_balance_usd(ex):
    try:
        bal = ex.fetch_balance()
        return float(bal.get("USDT", {}).get("total", 0) or 0)
    except Exception as e:
        log.error(f"balance: {e}")
        return 0.0


def min_notional(ex, sym):
    try:
        m = ex.market(sym)
        min_amt = float(m["limits"]["amount"]["min"] or 0)
        min_cost = float(m["limits"]["cost"]["min"] or 0)
        price = float(m.get("last") or 0)
        notional_from_amt = min_amt * price if price else 0
        return max(min_cost, notional_from_amt)
    except Exception:
        return 5.0


# ============================================================
# ФИЧИ
# ============================================================
def rsi(s, p=14):
    d = s.diff()
    up = d.clip(lower=0).rolling(p).mean()
    dn = (-d.clip(upper=0)).rolling(p).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))

def ema(s, span): return s.ewm(span=span, adjust=False).mean()

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
    f["dow_sin"]  = np.sin(2 * np.pi * dow / 7)
    f["dow_cos"]  = np.cos(2 * np.pi * dow / 7)

    return f.replace([np.inf, -np.inf], np.nan).dropna()


# ============================================================
# МОДЕЛЬ
# ============================================================
_models = {}

def load_model(sym):
    if sym in _models:
        return _models[sym]
    fname = sym.replace("/", "_").replace(":", "_") + ".lgb"
    path = Path(CFG["MODEL_DIR"]) / fname
    if not path.exists():
        log.warning(f"модель не найдена: {path}")
        return None
    bundle = joblib.load(path)
    _models[sym] = (bundle["model"], bundle["features"])
    return _models[sym]


# ============================================================
# TELEGRAM
# ============================================================
def tg_send(text):
    if not CFG["TELEGRAM_TOKEN"] or not CFG["TELEGRAM_CHAT_ID"]:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{CFG['TELEGRAM_TOKEN']}/sendMessage",
            json={"chat_id": CFG["TELEGRAM_CHAT_ID"], "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        log.warning(f"tg: {e}")

def tg_poll(offset):
    if not CFG["TELEGRAM_TOKEN"]:
        return offset, []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{CFG['TELEGRAM_TOKEN']}/getUpdates",
            params={"offset": offset + 1, "timeout": 0}, timeout=10,
        )
        cmds = []
        for m in r.json().get("result", []):
            offset = max(offset, m["update_id"])
            txt = (m.get("message") or {}).get("text", "").strip()
            if txt: cmds.append(txt)
        return offset, cmds
    except Exception:
        return offset, []
      

# ============================================================
# РИСК
# ============================================================
def check_daily_reset(state):
    now = int(time.time())
    if now - state["daily_reset_ts"] > 86400:
        state["daily_pnl"] = 0.0
        state["daily_trades"] = 0
        state["daily_reset_ts"] = now
        return True
    return False


def risk_ok(state, equity):
    if state["kill_switch"]:
        return False, "kill_switch"
    if state["daily_pnl"] <= -CFG["MAX_DAILY_LOSS_USD"]:
        return False, f"daily_loss {state['daily_pnl']:.2f}"
    if state["daily_trades"] >= CFG["MAX_TRADES_PER_DAY"]:
        return False, f"max_trades {state['daily_trades']}"
    if state["peak_equity"] > 0:
        dd = (equity / state["peak_equity"] - 1) * 100
        if dd <= -CFG["MAX_DRAWDOWN_PCT"]:
            return False, f"drawdown {dd:.1f}%"
    return True, "ok"


def open_count(state):
    return sum(1 for s in state["symbols"].values() if s["position"] != 0)


_GLOBAL_STATE = None

def state_is_multi():
    if _GLOBAL_STATE is None:
        return True
    return _GLOBAL_STATE.get("multi_symbol", True)


def positions_allowed(equity):
    if not state_is_multi():
        return 1
    base_eq = CFG["TIER_BASE_EQUITY"]
    if equity < base_eq:
        n = CFG["TIER_MIN_POSITIONS"]
    else:
        steps = int((equity - base_eq) // CFG["TIER_STEP_EQUITY"])
        n = CFG["TIER_BASE_POSITIONS"] + steps * CFG["TIER_STEP_ADD"]
    return min(n, len(CFG["SYMBOLS"]))


# ============================================================
# КОРРЕЛЯЦИЯ
# ============================================================
def correlation_blocked(ex, sym, direction, state):
    if not CFG["USE_CORRELATION"]:
        return False, "off"

    open_syms = [s for s, st in state["symbols"].items()
                 if st["position"] != 0 and s != sym]
    if not open_syms:
        return False, "no_open"

    try:
        n = CFG["CORRELATION_WINDOW"]
        a = fetch_ohlcv(ex, sym, n + 10)["close"].pct_change().dropna().tail(n)

        for other in open_syms:
            b = fetch_ohlcv(ex, other, n + 10)["close"].pct_change().dropna().tail(n)
            aligned = pd.concat([a, b], axis=1).dropna()
            if len(aligned) < 20:
                continue
            corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
            other_dir = state["symbols"][other]["position"]
            if abs(corr) > CFG["MAX_CORRELATION"] and direction == other_dir:
                return True, f"corr {corr:.2f} с {other}"
    except Exception as e:
        log.warning(f"corr {sym}: {e}")
    return False, "ok"


# ============================================================
# ATR СТОПЫ
# ============================================================
def calc_stops(df, direction, entry):
    if CFG["USE_ATR_STOPS"]:
        a = atr(df, 14).iloc[-1]
        if not np.isnan(a) and a > 0:
            sl_dist = a * CFG["ATR_MULTIPLIER"]
            tp_dist = a * CFG["ATR_TP_MULT"]
            if direction == 1:
                return entry - sl_dist, entry + tp_dist
            else:
                return entry + sl_dist, entry - tp_dist

    sl_pct = CFG["STOP_LOSS_PCT"] / 100
    tp_pct = CFG["TAKE_PROFIT_PCT"] / 100
    if direction == 1:
        return entry * (1 - sl_pct), entry * (1 + tp_pct)
    else:
        return entry * (1 + sl_pct), entry * (1 - tp_pct)


# ============================================================
# CSV ЛОГ
# ============================================================
def csv_log_trade(row):
    path = Path(CFG["TRADES_CSV"])
    new = not path.exists()
    try:
        with path.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["timestamp", "symbol", "side", "entry", "exit",
                            "size", "notional", "pnl", "pnl_pct", "reason",
                            "equity_after"])
            w.writerow(row)
    except Exception as e:
        log.error(f"csv: {e}")


# ============================================================
# НЕДЕЛЬНЫЙ ЦИКЛ
# ============================================================
def weekly_cycle_check():
    if not CFG["WEEKLY_CYCLE"]:
        return True, "off"
    now = datetime.now(timezone.utc)
    wd, h = now.weekday(), now.hour

    if CFG["PAUSE_SATURDAY"] and wd == 5:
        return False, "saturday"

    if wd == CFG["WITHDRAW_DAY"] and h >= CFG["WITHDRAW_HOUR_UTC"]:
        return False, "withdraw_window"

    resume_day = (CFG["WITHDRAW_DAY"] + 1) % 7
    if wd == resume_day and h < CFG["RESUME_HOUR_UTC"]:
        return False, "resume_pending"

    return True, "ok"


def weekly_rollover(ex, state):
    log.info("воскресное окно: закрываю всё")
    for sym in CFG["SYMBOLS"]:
        s = state["symbols"].get(sym)
        if s and s["position"] != 0:
            try:
                df = fetch_ohlcv(ex, sym, 10)
                close_position(ex, state, sym, "weekly_close", float(df["close"].iloc[-1]))
            except Exception as e:
                log.error(f"weekly {sym}: {e}")

    eq = get_balance_usd(ex)
    state["weekly_snapshot"] = {"equity_before": eq, "ts": time.time()}
    save_state(state)

    tg_send(
        f"📅 <b>Воскресное окно</b>\n"
        f"все позиции закрыты\n"
        f"баланс: <b>${eq:.2f}</b>\n"
        f"снимай сколько нужно в приложении Binance\n"
        f"торговля вернётся в понедельник в {CFG['RESUME_HOUR_UTC']:02d}:00 UTC"
    )


def weekly_resume(ex, state):
    eq_now = get_balance_usd(ex)
    snap = state.get("weekly_snapshot") or {}
    eq_before = snap.get("equity_before", eq_now)
    withdrawn = max(0, eq_before - eq_now)

    state["week_started_equity"] = eq_now
    state["weekly_snapshot"] = None
    save_state(state)

    if eq_now < CFG["MIN_TRADE_EQUITY"]:
        tg_send(
            f"⚠️ <b>Мало капитала</b>\n"
            f"баланс: ${eq_now:.2f} < ${CFG['MIN_TRADE_EQUITY']:.2f}\n"
            f"торговля не начнётся до пополнения"
        )
        return

    tg_send(
        f"▶️ <b>Новая неделя</b>\n"
        f"было: ${eq_before:.2f}\n"
        f"снято: ${withdrawn:.2f}\n"
        f"рабочий: <b>${eq_now:.2f}</b>\n"
        f"позиций: {positions_allowed(eq_now)}\n"
        f"риск/сделка: ${eq_now * CFG['RISK_PCT']:.2f}"
    )


# ============================================================
# АКТИВНЫЕ ПАРЫ
# ============================================================
def active_symbols(state):
    if state.get("multi_symbol", True):
        return list(CFG["SYMBOLS"])
    sym = state.get("active_symbol") or CFG["SYMBOLS"][0]
    if sym not in CFG["SYMBOLS"]:
        sym = CFG["SYMBOLS"][0]
    return [sym]


# ============================================================
# ОРДЕРА
# ============================================================
def size_for(ex, sym, price, equity):
    target = min(equity * CFG["RISK_PCT"] * CFG["LEVERAGE"],
                 CFG["MAX_POSITION_USD"])
    mn = min_notional(ex, sym)
    if target < mn:
        return None, f"notional {target:.1f} < min {mn:.1f}"
    contracts = target / price
    m = ex.market(sym)
    step = float(ex['limits']['amount'].get('step') or ex['precision'].get('amount') or 0.001)
    min_amt = float(m["limits"]["amount"]["min"] or step)
    contracts = max(min_amt, round(contracts / step) * step)
    return contracts, "ok"


def market_order(ex, sym, side, size, reduce=False):
    if not CFG["LIVE"]:
        return {"price": 0, "size": size, "id": "paper"}
    try:
        o = ex.create_order(sym, "market", side, size,
                            params={"reduceOnly": reduce})
        oid = o["id"]
        for _ in range(10):
            time.sleep(0.5)
            oo = ex.fetch_order(oid, sym)
            if oo["status"] == "closed":
                return {
                    "price": float(oo.get("average") or oo.get("price") or 0),
                    "size": float(oo.get("filled") or size),
                    "id": oid,
                }
        return {"price": 0, "size": size, "id": oid, "partial": True}
    except Exception as e:
        return {"error": str(e)}


def place_stops(ex, sym, direction, size, stop_price, tp_price):
    if not CFG["LIVE"]:
        return None, None
    side = "sell" if direction == 1 else "buy"
    sl_id = tp_id = None
    try:
        sl = ex.create_order(sym, "STOP_MARKET", side, size, params={
            "stopPrice": stop_price, "reduceOnly": True,
            "workingType": "MARK_PRICE"})
        sl_id = sl["id"]
    except Exception as e:
        log.error(f"SL {sym}: {e}")
    try:
        tp = ex.create_order(sym, "TAKE_PROFIT_MARKET", side, size, params={
            "stopPrice": tp_price, "reduceOnly": True,
            "workingType": "MARK_PRICE"})
        tp_id = tp["id"]
    except Exception as e:
        log.error(f"TP {sym}: {e}")
    return sl_id, tp_id


def cancel_order_safe(ex, sym, oid):
    if not oid or not CFG["LIVE"]:
        return
    try:
        ex.cancel_order(oid, sym)
    except Exception:
        pass


def cancel_stops(ex, sym, sstate):
    cancel_order_safe(ex, sym, sstate.get("exchange_sl_id"))
    cancel_order_safe(ex, sym, sstate.get("exchange_tp_id"))
    sstate["exchange_sl_id"] = None
    sstate["exchange_tp_id"] = None


def move_stop(ex, sym, sstate, new_stop):
    if not CFG["LIVE"]:
        sstate["stop_price"] = new_stop
        return
    cancel_order_safe(ex, sym, sstate.get("exchange_sl_id"))
    sstate["exchange_sl_id"] = None
    if sstate["position"] == 0:
        return
    side = "sell" if sstate["position"] == 1 else "buy"
    try:
        sl = ex.create_order(sym, "STOP_MARKET", side, sstate["size"], params={
            "stopPrice": new_stop, "reduceOnly": True,
            "workingType": "MARK_PRICE"})
        sstate["exchange_sl_id"] = sl["id"]
        sstate["stop_price"] = new_stop
    except Exception as e:
        log.error(f"move SL {sym}: {e}")


# ============================================================
# TRAILING
# ============================================================
def update_trailing(ex, state, sym, high, low):
    if not CFG["USE_TRAILING"]:
        return
    s = state["symbols"][sym]
    if s["position"] == 0 or s["entry_price"] == 0:
        return

    ep = s["entry_price"]
    act = CFG["TRAIL_ACTIVATE_PCT"] / 100
    dist = CFG["TRAIL_DISTANCE_PCT"] / 100

    if s["position"] == 1:
        profit_pct = (high / ep - 1)
        if profit_pct >= act:
            s["trail_peak"] = max(s.get("trail_peak", 0), high)
            new_stop = s["trail_peak"] * (1 - dist)
            if new_stop > s["stop_price"]:
                s["trail_active"] = True
                move_stop(ex, sym, s, new_stop)
                log.info(f"{sym}: trailing SL → {new_stop:.6f}")
    else:
        profit_pct = (1 - low / ep)
        if profit_pct >= act:
            peak = s.get("trail_peak") or 1e18
            peak = min(peak if peak > 0 else low, low)
            s["trail_peak"] = peak
            new_stop = peak * (1 + dist)
            if s["stop_price"] == 0 or new_stop < s["stop_price"]:
                s["trail_active"] = True
                move_stop(ex, sym, s, new_stop)
                log.info(f"{sym}: trailing SL → {new_stop:.6f}")
              

# ============================================================
# ОТКРЫТИЕ / ЗАКРЫТИЕ
# ============================================================
def open_position(ex, state, sym, direction, price, equity, df):
    size, why = size_for(ex, sym, price, equity)
    if size is None:
        log.info(f"{sym}: пропуск — {why}")
        return False

    side = "buy" if direction == 1 else "sell"
    res = market_order(ex, sym, side, size)
    if "error" in res:
        tg_send(f"⚠️ {sym}: {res['error']}")
        return False

    entry = res["price"] or price
    filled = res["size"] or size

    stop_price, tp_price = calc_stops(df, direction, entry)

    s = state["symbols"][sym]
    s["position"] = direction
    s["entry_price"] = entry
    s["size"] = filled
    s["entry_ts"] = int(time.time())
    s["stop_price"] = stop_price
    s["tp_price"] = tp_price
    s["trail_active"] = False
    s["trail_peak"] = entry

    sl_id, tp_id = place_stops(ex, sym, direction, filled, stop_price, tp_price)
    s["exchange_sl_id"], s["exchange_tp_id"] = sl_id, tp_id

    state["total_trades"] += 1
    state["daily_trades"] += 1

    notional = filled * entry
    risk_usd = abs(entry - stop_price) * filled

    tg_send(
        f"🟢 <b>{sym} {'LONG' if direction==1 else 'SHORT'}</b>\n"
        f"вход: {entry:.6f}\n"
        f"размер: {filled} ≈ ${notional:.2f}\n"
        f"стоп: {stop_price:.6f} (риск ${risk_usd:.2f})\n"
        f"тейк: {tp_price:.6f}\n"
        f"{'стопы на бирже' if sl_id else 'стоп в боте'}"
    )
    return True


def close_position(ex, state, sym, reason, fallback):
    s = state["symbols"][sym]
    if s["position"] == 0:
        return

    cancel_stops(ex, sym, s)

    side = "sell" if s["position"] == 1 else "buy"
    res = market_order(ex, sym, side, s["size"], reduce=True)
    if "error" in res:
        log.error(f"close {sym}: {res['error']}")
        return

    exit_price = res["price"] or fallback

    if s["position"] == 1:
        pnl = (exit_price - s["entry_price"]) * s["size"]
    else:
        pnl = (s["entry_price"] - exit_price) * s["size"]

    pnl_pct = pnl / (s["entry_price"] * s["size"]) * 100 if s["size"] else 0
    notional = s["size"] * s["entry_price"]

    state["daily_pnl"] += pnl
    if pnl > 0: state["wins"] += 1
    else: state["losses"] += 1

    equity = get_balance_usd(ex) or state["peak_equity"]
    csv_log_trade([
        datetime.now(timezone.utc).isoformat(),
        sym, "LONG" if s["position"] == 1 else "SHORT",
        f"{s['entry_price']:.8f}", f"{exit_price:.8f}",
        s["size"], f"{notional:.4f}", f"{pnl:.4f}", f"{pnl_pct:.3f}",
        reason, f"{equity:.4f}",
    ])

    tg_send(
        f"🔴 <b>{sym} закрыт</b>\n"
        f"причина: {reason}\n"
        f"вход: {s['entry_price']:.6f}\n"
        f"выход: {exit_price:.6f}\n"
        f"PnL: <b>{pnl:+.3f} USDT</b> ({pnl_pct:+.2f}%)\n"
        f"день: {state['daily_pnl']:+.3f}"
    )

    s["position"] = 0
    s["entry_price"] = 0
    s["size"] = 0
    s["entry_ts"] = 0
    s["stop_price"] = 0
    s["tp_price"] = 0
    s["trail_active"] = False
    s["trail_peak"] = 0


# ============================================================
# СТОП/ТЕЙК
# ============================================================
def check_sl_tp(state, sym, high, low):
    s = state["symbols"][sym]
    if s["position"] == 0 or s["entry_price"] == 0:
        return None
    sl = s.get("stop_price") or 0
    tp = s.get("tp_price") or 0
    if sl == 0 or tp == 0:
        return None
    if s["position"] == 1:
        if low <= sl: return ("stop_loss", sl)
        if high >= tp: return ("take_profit", tp)
    else:
        if high >= sl: return ("stop_loss", sl)
        if low <= tp: return ("take_profit", tp)
    return None


# ============================================================
# DAILY REPORT
# ============================================================
def maybe_daily_report(ex, state):
    now = datetime.now(timezone.utc)
    if now.hour != CFG["DAILY_REPORT_HOUR"]:
        return
    today = now.toordinal()
    if state.get("last_daily_report_day") == today:
        return
    state["last_daily_report_day"] = today

    eq = get_balance_usd(ex)
    week_start = state.get("week_started_equity", eq)
    week_pnl = eq - week_start

    tg_send(
        f"📊 <b>Итог дня</b>\n"
        f"PnL день: {state['daily_pnl']:+.3f}\n"
        f"сделок: {state['daily_trades']}\n"
        f"всего: {state['total_trades']} · win/loss {state['wins']}/{state['losses']}\n"
        f"капитал: <b>${eq:.2f}</b>\n"
        f"неделя: {week_pnl:+.2f}\n"
        f"позиции: {positions_allowed(eq)}"
    )


# ============================================================
# РЕКОНСИЛЯЦИЯ
# ============================================================
def reconcile(ex, state):
    log.info("реконсиляция с биржей")
    try:
        positions = ex.fetch_positions(CFG["SYMBOLS"])
    except Exception as e:
        log.error(f"reconcile fetch: {e}")
        return

    on_ex = {}
    for p in positions:
        contracts = float(p.get("contracts") or 0)
        if contracts > 0:
            on_ex[p["symbol"]] = p

    for sym in CFG["SYMBOLS"]:
        s = state["symbols"][sym]
        pos = on_ex.get(sym)

        if pos is None:
            if s["position"] != 0:
                log.warning(f"{sym}: state={s['position']} но биржа пуста → сброс")
                s["position"] = 0
                s["entry_price"] = 0
                s["size"] = 0
                s["stop_price"] = 0
                s["tp_price"] = 0
        else:
            side = 1 if pos["side"] == "long" else -1
            entry = float(pos.get("entryPrice") or 0)
            size = float(pos.get("contracts") or 0)
            if s["position"] != side or abs(s["size"] - size) > size * 0.01:
                log.warning(f"{sym}: реконсиляция {side}/{size}")
                s["position"] = side
                s["entry_price"] = entry
                s["size"] = size
    save_state(state)


# ============================================================
# ОБРАБОТЧИК КОМАНД
# ============================================================
def handle_command(ex, state, cmd):
    c = cmd.strip()
    cl = c.lower()

    if cl == "/status":
        eq = get_balance_usd(ex)
        week_start = state.get("week_started_equity", eq)
        week_pnl = eq - week_start
        week_pct = (week_pnl / week_start * 100) if week_start > 0 else 0
        _, phase = weekly_cycle_check()
        mode = "multi" if state.get("multi_symbol", True) else f"single({state.get('active_symbol')})"
        open_pos = []
        for s, st in state["symbols"].items():
            if st["position"] != 0:
                tag = "L" if st["position"] == 1 else "S"
                open_pos.append(f"{s.split('/')[0]}:{tag}")
        allowed = positions_allowed(eq)
        tg_send(
            f"📊 <b>Статус</b>\n"
            f"фаза: {phase}\n"
            f"режим: {mode}\n"
            f"капитал: <b>${eq:.2f}</b>\n"
            f"позиций разрешено: {allowed}\n"
            f"открыто: {' · '.join(open_pos) or '—'}\n"
            f"неделя: {week_pnl:+.3f} ({week_pct:+.1f}%)\n"
            f"риск/сделка: ${eq * CFG['RISK_PCT']:.2f}\n"
            f"сделок: {state['total_trades']} · win/loss {state['wins']}/{state['losses']}\n"
            f"день: {state['daily_pnl']:+.3f} · сделок {state['daily_trades']}/{CFG['MAX_TRADES_PER_DAY']}\n"
            f"kill: {state['kill_switch']}"
        )

    elif cl == "/tier":
        eq = get_balance_usd(ex)
        allowed = positions_allowed(eq)
        base = CFG["TIER_BASE_EQUITY"]
        step = CFG["TIER_STEP_EQUITY"]
        add = CFG["TIER_STEP_ADD"]
        lines = [f"💰 equity: <b>${eq:.2f}</b>",
                 f"позиций сейчас: <b>{allowed}</b>", "",
                 f"сетка (потолок {len(CFG['SYMBOLS'])} пар):"]
        for i in range(6):
            lo = base + i * step
            hi = lo + step
            n = min(CFG["TIER_BASE_POSITIONS"] + i * add, len(CFG["SYMBOLS"]))
            mark = " ←" if lo <= eq < hi else ""
            lines.append(f"  ${lo:.0f}–${hi:.0f} → {n} поз.{mark}")
        if eq < base:
            lines.insert(2, f"  <${base:.0f} → {CFG['TIER_MIN_POSITIONS']} поз. ←")
        tg_send("\n".join(lines))

    elif cl == "/stop":
        state["kill_switch"] = True
        tg_send("🛑 kill-switch включён")

    elif cl == "/start":
        state["kill_switch"] = False
        tg_send("▶️ kill-switch снят")

    elif cl == "/close":
        closed = 0
        for sym in CFG["SYMBOLS"]:
            if state["symbols"][sym]["position"] != 0:
                try:
                    df = fetch_ohlcv(ex, sym, 10)
                    close_position(ex, state, sym, "manual", float(df["close"].iloc[-1]))
                    closed += 1
                except Exception as e:
                    log.error(f"close {sym}: {e}")
        tg_send(f"✅ закрыто позиций: {closed}")

    elif cl == "/panic":
        state["kill_switch"] = True
        for sym in CFG["SYMBOLS"]:
            if state["symbols"][sym]["position"] != 0:
                try:
                    df = fetch_ohlcv(ex, sym, 10)
                    close_position(ex, state, sym, "panic", float(df["close"].iloc[-1]))
                except Exception:
                    pass
        tg_send("🚨 PANIC: всё закрыто, kill-switch включён")

    elif cl == "/single":
        state["multi_symbol"] = False
        parts = c.split()
        if len(parts) > 1:
            arg = parts[1].upper().split("/")[0]
            candidates = [s for s in CFG["SYMBOLS"] if s.split("/")[0].upper() == arg]
            state["active_symbol"] = candidates[0] if candidates else CFG["SYMBOLS"][0]
        else:
            state["active_symbol"] = CFG["SYMBOLS"][0]

        keep = state["active_symbol"]
        closed = []
        for sym in CFG["SYMBOLS"]:
            if sym == keep: continue
            s = state["symbols"].get(sym)
            if s and s["position"] != 0:
                try:
                    df = fetch_ohlcv(ex, sym, 10)
                    close_position(ex, state, sym, "switch_single",
                                   float(df["close"].iloc[-1]))
                    closed.append(sym)
                except Exception:
                    pass
        tail = f"\nзакрыто: {len(closed)}" if closed else ""
        tg_send(f"🎯 single: <b>{keep}</b>{tail}\nвозврат: /multi")

    elif cl == "/multi":
        state["multi_symbol"] = True
        tg_send(
            f"🌐 multi: все пары\n"
            f"{', '.join(s.split('/')[0] for s in CFG['SYMBOLS'])}\n"
            f"одна: /single SOL"
        )

    elif cl == "/pairs":
        mode = "multi" if state.get("multi_symbol", True) else "single"
        act = state.get("active_symbol") or "—"
        lines = [f"📋 режим: {mode}",
                 f"активная: {act}", ""]
        for s in CFG["SYMBOLS"]:
            st = state["symbols"][s]
            pos = "—" if st["position"] == 0 else ("L" if st["position"] == 1 else "S")
            lines.append(f"  {s}: {pos}")
        tg_send("\n".join(lines))

    elif cl == "/cycle":
        weekly_rollover(ex, state)
        state["kill_switch"] = True
        tg_send("📅 ручной цикл, kill включён")

    elif cl == "/help":
        tg_send(
            "/status — состояние\n"
            "/tier — сетка ступеней\n"
            "/stop — пауза\n"
            "/start — снять паузу\n"
            "/close — закрыть все\n"
            "/panic — закрыть всё + kill\n"
            "/single [SOL] — одна монета\n"
            "/multi — все пары\n"
            "/pairs — список пар\n"
            "/cycle — недельный цикл\n"
            "/help — это"
        )


# ============================================================
# ГЛАВНЫЙ ЦИКЛ
# ============================================================
def run():
    global _GLOBAL_STATE

    log.info("=" * 50)
    log.info("alt-autotrader старт")
    log.info(f"пары: {CFG['SYMBOLS']}")
    log.info(f"interval={CFG['INTERVAL']} testnet={CFG['BINANCE_TESTNET']} live={CFG['LIVE']}")

    ex = make_exchange()
    state = load_state()
    _GLOBAL_STATE = state

    eq0 = get_balance_usd(ex) or 10.0
    if state["started_equity"] == 0: state["started_equity"] = eq0
    if state["peak_equity"] == 0: state["peak_equity"] = eq0
    if state["week_started_equity"] == 0: state["week_started_equity"] = eq0

    reconcile(ex, state)

    tg_send(
        f"🤖 <b>Альт-трейдер старт</b>\n"
        f"пары: {', '.join(s.split('/')[0] for s in CFG['SYMBOLS'])}\n"
        f"режим: {'LIVE' if CFG['LIVE'] else 'TESTNET'}\n"
        f"капитал: <b>${eq0:.2f}</b>\n"
        f"позиций: {positions_allowed(eq0)}\n"
        f"риск/сделка: ${eq0 * CFG['RISK_PCT']:.2f}"
    )

    tg_off = 0
    last_hb = 0

    while True:
        try:
            # ----- недельный цикл -----
            allowed, why = weekly_cycle_check()
            prev_phase = state.get("phase", "ok")
            if prev_phase != "withdraw_window" and why == "withdraw_window":
                weekly_rollover(ex, state)
            if prev_phase in ("withdraw_window", "resume_pending") and why == "ok":
                weekly_resume(ex, state)
            state["phase"] = why
            save_state(state)

            # ----- команды -----
            tg_off, cmds = tg_poll(tg_off)
            for c in cmds:
                handle_command(ex, state, c)
                save_state(state)

            # ----- пауза -----
            if not allowed:
                log.info(f"пауза: {why}")
                time.sleep(60)
                continue

            # ----- equity, сброс дня -----
            equity = get_balance_usd(ex) or state["peak_equity"]
            state["peak_equity"] = max(state["peak_equity"], equity)
            if check_daily_reset(state):
                log.info("новый UTC-день, сброс счётчиков")
            ok, why_risk = risk_ok(state, equity)

            if equity < CFG["MIN_TRADE_EQUITY"]:
                log.warning(f"equity ${equity:.2f} < минимума ${CFG['MIN_TRADE_EQUITY']:.2f}")
                time.sleep(300)
                continue

            # ----- обход пар -----
            syms = active_symbols(state)
            for sym in syms:
                s = state["symbols"].setdefault(sym, dict(DEFAULT_SYM_STATE))

                try:
                    df = fetch_ohlcv(ex, sym, 500)
                except Exception as e:
                    log.warning(f"{sym} fetch: {e}")
                    continue

                last_closed = df.iloc[:-1]
                live = df.iloc[-1]
                price = float(last_closed["close"].iloc[-1])
                cts = int(last_closed.index[-1].timestamp())

                # trailing — обновляем стоп
                update_trailing(ex, state, sym, float(live["high"]), float(live["low"]))

                # SL/TP
                sltp = check_sl_tp(state, sym, float(live["high"]), float(live["low"]))
                if sltp:
                    close_position(ex, state, sym, sltp[0], sltp[1])
                    save_state(state)

                # новая свеча?
                if cts == s["last_candle_ts"]:
                    continue

                bundle = load_model(sym)
                if bundle is None:
                    continue
                model, feat_names = bundle

                feats = build_features(last_closed)
                if len(feats) < 1:
                    continue

                X = feats[feat_names].iloc[[-1]]
                proba = model.predict_proba(X)[0]
                sig = int(proba.argmax()) - 1

                # проверить задержку входа
                delay_pct = abs(price / float(last_closed["close"].iloc[-1]) - 1) * 100

                # закрытие при смене сигнала
                if s["position"] != 0 and sig != s["position"]:
                    close_position(ex, state, sym, f"signal {sig}", price)

                # открытие
                if s["position"] == 0 and sig != 0:
                    if not ok:
                        log.info(f"{sym}: {sig} пропущен — {why_risk}")
                    elif delay_pct > CFG["MAX_ENTRY_DELAY_PCT"]:
                        log.info(f"{sym}: {sig} пропущен — цена ушла {delay_pct:.2f}%")
                    elif open_count(state) >= positions_allowed(equity):
                        log.info(f"{sym}: {sig} пропущен — лимит поз.")
                    else:
                        blocked, whyc = correlation_blocked(ex, sym, sig, state)
                        if blocked:
                            log.info(f"{sym}: {sig} пропущен — {whyc}")
                        else:
                            open_position(ex, state, sym, sig, price, equity, last_closed)

                s["last_candle_ts"] = cts
                save_state(state)

                log.info(
                    f"{sym} {last_closed.index[-1]} px={price:.6f} "
                    f"sig={sig} p=[{proba[0]:.2f},{proba[1]:.2f},{proba[2]:.2f}] "
                    f"pos={s['position']} risk={why_risk}"
                )

            # ----- дневной отчёт -----
            maybe_daily_report(ex, state)
            save_state(state)

            # ----- heartbeat -----
            if time.time() - last_hb > CFG["HEARTBEAT_SECONDS"]:
                eq = get_balance_usd(ex)
                tg_send(
                    f"💓 ${eq:.2f} · поз {open_count(state)}/{positions_allowed(eq)} · "
                    f"день {state['daily_pnl']:+.3f} · сделок {state['daily_trades']}/{CFG['MAX_TRADES_PER_DAY']}"
                )
                last_hb = time.time()

            time.sleep(CFG["POLL_SECONDS"])

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.exception(f"цикл: {e}")
            tg_send(f"⚠️ ошибка: {e}")
            time.sleep(60)

    tg_send("🤖 остановлен")


if __name__ == "__main__":
    run()
