# language: Python 3.10+, file: trade.py
# торговые операции: риск, корреляция, открытие/закрытие, trailing, стопы
import time
import csv
from datetime import datetime, timezone
from pathlib import Path

from config import CFG
from notify import tg_send
from state import (
    get_state, save_state, get_balance_usd, adjust_equity,
    open_count, risk_ok, check_daily_reset,
)
from data import fetch_ohlcv, atr


# ============================================================
# КОРРЕЛЯЦИЯ
# ============================================================
def correlation_blocked(sym, direction):
    if not CFG["USE_CORRELATION"]:
        return False, "off"
    state = get_state()
    open_syms = [s for s, st in state["symbols"].items()
                 if st["position"] != 0 and s != sym]
    if not open_syms:
        return False, "no_open"
    try:
        import pandas as pd
        n = CFG["CORRELATION_WINDOW"]
        a = fetch_ohlcv(sym, n + 10)["close"].pct_change().dropna().tail(n)
        for other in open_syms:
            b = fetch_ohlcv(other, n + 10)["close"].pct_change().dropna().tail(n)
            aligned = pd.concat([a, b], axis=1).dropna()
            if len(aligned) < 20:
                continue
            corr = aligned.iloc[:, 0].corr(aligned.iloc[:, 1])
            other_dir = state["symbols"][other]["position"]
            if abs(corr) > CFG["MAX_CORRELATION"] and direction == other_dir:
                return True, f"corr {corr:.2f} с {other}"
    except Exception as e:
        print(f"corr {sym}: {e}")
    return False, "ok"


# ============================================================
# ATR СТОПЫ
# ============================================================
def calc_stops(df, direction, entry):
    if CFG["USE_ATR_STOPS"]:
        a = atr(df, 14).iloc[-1]
        if not (a != a) and a > 0:
            sl_dist = a * CFG["ATR_MULTIPLIER"]
            tp_dist = a * CFG["ATR_TP_MULT"]
            if direction == 1:
                return entry - sl_dist, entry + tp_dist
            return entry + sl_dist, entry - tp_dist
    sl_pct = CFG["STOP_LOSS_PCT"] / 100
    tp_pct = CFG["TAKE_PROFIT_PCT"] / 100
    if direction == 1:
        return entry * (1 - sl_pct), entry * (1 + tp_pct)
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
        print(f"csv: {e}")


# ============================================================
# ОТКРЫТИЕ
# ============================================================
def open_position(sym, direction, price, equity, df):
    state = get_state()
    target_usd = min(equity * CFG["RISK_PCT"] * CFG["LEVERAGE"],
                     CFG["MAX_POSITION_USD"])
    if target_usd < 5.0:
        print(f"{sym}: пропуск — notional {target_usd:.1f} < 5")
        return False

    size = round(target_usd / price, 8)
    stop_price, tp_price = calc_stops(df, direction, price)

    s = state["symbols"][sym]
    s["position"] = direction
    s["entry_price"] = price
    s["size"] = size
    s["entry_ts"] = int(time.time())
    s["stop_price"] = stop_price
    s["tp_price"] = tp_price
    s["trail_active"] = False
    s["trail_peak"] = price

    state["total_trades"] += 1
    state["daily_trades"] += 1

    risk_usd = abs(price - stop_price) * size
    tg_send(
        f"🟢 <b>{sym} {'LONG' if direction==1 else 'SHORT'}</b>\n"
        f"вход: {price:.6f}\n"
        f"размер: {size} ≈ ${target_usd:.2f}\n"
        f"стоп: {stop_price:.6f} (риск ${risk_usd:.2f})\n"
        f"тейк: {tp_price:.6f}\n"
        f"<i>paper mode</i>")
    return True


# ============================================================
# ЗАКРЫТИЕ
# ============================================================
def close_position(sym, reason, price):
    state = get_state()
    s = state["symbols"][sym]
    if s["position"] == 0:
        return

    if s["position"] == 1:
        pnl = (price - s["entry_price"]) * s["size"]
    else:
        pnl = (s["entry_price"] - price) * s["size"]

    pnl_pct = pnl / (s["entry_price"] * s["size"]) * 100 if s["size"] else 0
    notional = s["size"] * s["entry_price"]

    state["daily_pnl"] += pnl
    if pnl > 0:
        state["wins"] += 1
    else:
        state["losses"] += 1

    adjust_equity(pnl)
    equity_now = get_balance_usd()

    csv_log_trade([
        datetime.now(timezone.utc).isoformat(),
        sym, "LONG" if s["position"] == 1 else "SHORT",
        f"{s['entry_price']:.8f}", f"{price:.8f}",
        s["size"], f"{notional:.4f}", f"{pnl:.4f}", f"{pnl_pct:.3f}",
        reason, f"{equity_now:.4f}"])

    tg_send(
        f"🔴 <b>{sym} закрыт</b>\n"
        f"причина: {reason}\n"
        f"вход: {s['entry_price']:.6f}\n"
        f"выход: {price:.6f}\n"
        f"PnL: <b>{pnl:+.4f}</b> ({pnl_pct:+.2f}%)\n"
        f"день: {state['daily_pnl']:+.4f}\n"
        f"equity: ${equity_now:.2f}\n"
        f"<i>paper mode</i>")

    s["position"] = 0
    s["entry_price"] = 0
    s["size"] = 0
    s["entry_ts"] = 0
    s["stop_price"] = 0
    s["tp_price"] = 0
    s["trail_active"] = False
    s["trail_peak"] = 0


# ============================================================
# TRAILING
# ============================================================
def update_trailing(sym, high, low):
    if not CFG["USE_TRAILING"]:
        return
    state = get_state()
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
                s["stop_price"] = new_stop
                print(f"{sym}: trailing SL → {new_stop:.6f}")
    else:
        profit_pct = (1 - low / ep)
        if profit_pct >= act:
            peak = s.get("trail_peak") or 1e18
            peak = min(peak if peak > 0 else low, low)
            s["trail_peak"] = peak
            new_stop = peak * (1 + dist)
            if s["stop_price"] == 0 or new_stop < s["stop_price"]:
                s["trail_active"] = True
                s["stop_price"] = new_stop
                print(f"{sym}: trailing SL → {new_stop:.6f}")


# ============================================================
# SL/TP ПРОВЕРКА
# ============================================================
def check_sl_tp(sym, high, low):
    state = get_state()
    s = state["symbols"][sym]
    if s["position"] == 0 or s["entry_price"] == 0:
        return None
    sl = s.get("stop_price") or 0
    tp = s.get("tp_price") or 0
    if sl == 0 or tp == 0:
        return None
    if s["position"] == 1:
        if low <= sl:
            return ("stop_loss", sl)
        if high >= tp:
            return ("take_profit", tp)
    else:
        if high >= sl:
            return ("stop_loss", sl)
        if low <= tp:
            return ("take_profit", tp)
    return None


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


def weekly_rollover():
    state = get_state()
    print("воскресное окно: закрываю всё")
    for sym in CFG["SYMBOLS"]:
        s = state["symbols"].get(sym)
        if s and s["position"] != 0:
            try:
                df = fetch_ohlcv(sym, 10)
                close_position(sym, "weekly_close", float(df["close"].iloc[-1]))
            except Exception as e:
                print(f"weekly {sym}: {e}")
    eq = get_balance_usd()
    state["weekly_snapshot"] = {"equity_before": eq, "ts": time.time()}
    save_state()
    tg_send(
        f"📅 <b>Воскресное окно</b>\n"
        f"все позиции закрыты\n"
        f"equity: <b>${eq:.2f}</b>\n"
        f"торговля вернётся в понедельник в {CFG['RESUME_HOUR_UTC']:02d}:00 UTC")


def weekly_resume():
    state = get_state()
    eq_now = get_balance_usd()
    snap = state.get("weekly_snapshot") or {}
    eq_before = snap.get("equity_before", eq_now)
    state["week_started_equity"] = eq_now
    state["weekly_snapshot"] = None
    save_state()
    tg_send(
        f"▶️ <b>Новая неделя</b>\n"
        f"было: ${eq_before:.2f}\n"
        f"рабочий: <b>${eq_now:.2f}</b>")


# ============================================================
# АКТИВНЫЕ ПАРЫ
# ============================================================
def active_symbols():
    state = get_state()
    if state.get("multi_symbol", True):
        return list(CFG["SYMBOLS"])
    sym = state.get("active_symbol") or CFG["SYMBOLS"][0]
    if sym not in CFG["SYMBOLS"]:
        sym = CFG["SYMBOLS"][0]
    return [sym]


# ============================================================
# DAILY REPORT
# ============================================================
def maybe_daily_report():
    state = get_state()
    now = datetime.now(timezone.utc)
    if now.hour != CFG["DAILY_REPORT_HOUR"]:
        return
    today = now.toordinal()
    if state.get("last_daily_report_day") == today:
        return
    state["last_daily_report_day"] = today
    eq = get_balance_usd()
    tg_send(
        f"📊 <b>Итог дня</b>\n"
        f"PnL день: {state['daily_pnl']:+.4f}\n"
        f"сделок: {state['daily_trades']}\n"
        f"всего: {state['total_trades']} · win/loss "
        f"{state['wins']}/{state['losses']}\n"
        f"equity: <b>${eq:.2f}</b>\n"
        f"<i>paper mode</i>")
