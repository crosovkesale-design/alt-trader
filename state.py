# language: Python 3.10+, file: state.py
# состояние бота: equity, позиции, persistence
import json
import time
from pathlib import Path
from config import CFG


DEFAULT_SYM_STATE = {
    "position": 0,
    "entry_price": 0.0,
    "size": 0.0,
    "entry_ts": 0,
    "last_candle_ts": 0,
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
    "virtual_equity": 100.0,
}

_GLOBAL_STATE = None


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
        print(f"state load fail: {e}")
        return json.loads(json.dumps(DEFAULT_STATE))


def save_state(s=None):
    if s is None:
        s = _GLOBAL_STATE
    Path(CFG["STATE_PATH"]).write_text(json.dumps(s, indent=2, default=str))


def set_state(s):
    global _GLOBAL_STATE
    _GLOBAL_STATE = s


def get_state():
    return _GLOBAL_STATE


def get_balance_usd():
    if _GLOBAL_STATE is None:
        return 100.0
    return float(_GLOBAL_STATE.get("virtual_equity", 100.0))


def adjust_equity(delta):
    if _GLOBAL_STATE is None:
        return
    _GLOBAL_STATE["virtual_equity"] = round(
        float(_GLOBAL_STATE.get("virtual_equity", 100.0)) + delta, 4)


def state_is_multi():
    if _GLOBAL_STATE is None:
        return True
    return _GLOBAL_STATE.get("multi_symbol", True)


def positions_allowed(equity=None):
    if equity is None:
        equity = get_balance_usd()
    if not state_is_multi():
        return 1
    base_eq = CFG["TIER_BASE_EQUITY"]
    if equity < base_eq:
        n = CFG["TIER_MIN_POSITIONS"]
    else:
        steps = int((equity - base_eq) // CFG["TIER_STEP_EQUITY"])
        n = CFG["TIER_BASE_POSITIONS"] + steps * CFG["TIER_STEP_ADD"]
    return min(n, len(CFG["SYMBOLS"]))


def open_count():
    if _GLOBAL_STATE is None:
        return 0
    return sum(1 for s in _GLOBAL_STATE["symbols"].values()
               if s["position"] != 0)


def check_daily_reset():
    s = _GLOBAL_STATE
    now = int(time.time())
    if now - s["daily_reset_ts"] > 86400:
        s["daily_pnl"] = 0.0
        s["daily_trades"] = 0
        s["daily_reset_ts"] = now
        return True
    return False


def risk_ok(equity):
    s = _GLOBAL_STATE
    if s["kill_switch"]:
        return False, "kill_switch"
    if s["daily_pnl"] <= -CFG["MAX_DAILY_LOSS_USD"]:
        return False, f"daily_loss {s['daily_pnl']:.2f}"
    if s["daily_trades"] >= CFG["MAX_TRADES_PER_DAY"]:
        return False, f"max_trades {s['daily_trades']}"
    if s["peak_equity"] > 0:
        dd = (equity / s["peak_equity"] - 1) * 100
        if dd <= -CFG["MAX_DRAWDOWN_PCT"]:
            return False, f"drawdown {dd:.1f}%"
    return True, "ok"
