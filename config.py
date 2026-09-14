# language: Python 3.10+, file: config.py
# центральный конфиг. всё через env — Railway подставит при деплое.
import os

def env(k, d): return os.environ.get(k, d)

CFG = {
    # ---------- биржа ----------
    "BINANCE_KEY":     env("BINANCE_KEY", ""),
    "BINANCE_SECRET":  env("BINANCE_SECRET", ""),
    "BINANCE_TESTNET": env("BINANCE_TESTNET", "true").lower() == "true",
    "LIVE":            env("LIVE", "false").lower() == "true",

    # ---------- telegram ----------
    "TELEGRAM_TOKEN":   env("TELEGRAM_TOKEN", ""),
    "TELEGRAM_CHAT_ID": env("TELEGRAM_CHAT_ID", ""),

    # ---------- пары и таймфрейм ----------
    "SYMBOLS": env("SYMBOLS",
        "SOL/USDT:USDT,DOGE/USDT:USDT,XRP/USDT:USDT,ADA/USDT:USDT,TRX/USDT:USDT"
    ).split(","),
    "INTERVAL": env("INTERVAL", "15m"),

    # ---------- риск ----------
    "LEVERAGE":           int(env("LEVERAGE", "5")),
    "RISK_PCT":           float(env("RISK_PCT", "0.10")),
    "MAX_POSITION_USD":   float(env("MAX_POSITION_USD", "20")),
    "MIN_POSITION_USD":   float(env("MIN_POSITION_USD", "6")),
    "MIN_TRADE_EQUITY":   float(env("MIN_TRADE_EQUITY", "8")),

    # ---------- стопы ----------
    "USE_ATR_STOPS":      env("USE_ATR_STOPS", "true").lower() == "true",
    "ATR_MULTIPLIER":     float(env("ATR_MULTIPLIER", "1.5")),
    "STOP_LOSS_PCT":      float(env("STOP_LOSS_PCT", "1.5")),
    "TAKE_PROFIT_PCT":    float(env("TAKE_PROFIT_PCT", "3.0")),
    "ATR_TP_MULT":        float(env("ATR_TP_MULT", "3.0")),

    # ---------- trailing ----------
    "USE_TRAILING":       env("USE_TRAILING", "true").lower() == "true",
    "TRAIL_ACTIVATE_PCT": float(env("TRAIL_ACTIVATE_PCT", "1.0")),
    "TRAIL_DISTANCE_PCT": float(env("TRAIL_DISTANCE_PCT", "1.0")),

    # ---------- лимиты ----------
    "MAX_DAILY_LOSS_USD":   float(env("MAX_DAILY_LOSS_USD", "2.0")),
    "MAX_DRAWDOWN_PCT":     float(env("MAX_DRAWDOWN_PCT", "30")),
    "MAX_TRADES_PER_DAY":   int(env("MAX_TRADES_PER_DAY", "8")),
    "MAX_ENTRY_DELAY_PCT":  float(env("MAX_ENTRY_DELAY_PCT", "0.3")),

    # ---------- ступени роста ----------
    "TIER_BASE_EQUITY":    float(env("TIER_BASE_EQUITY", "100")),
    "TIER_STEP_EQUITY":    float(env("TIER_STEP_EQUITY", "100")),
    "TIER_BASE_POSITIONS": int(env("TIER_BASE_POSITIONS", "2")),
    "TIER_STEP_ADD":       int(env("TIER_STEP_ADD", "2")),
    "TIER_MIN_POSITIONS":  int(env("TIER_MIN_POSITIONS", "1")),

    # ---------- корреляция ----------
    "USE_CORRELATION":     env("USE_CORRELATION", "true").lower() == "true",
    "MAX_CORRELATION":     float(env("MAX_CORRELATION", "0.8")),
    "CORRELATION_WINDOW":  int(env("CORRELATION_WINDOW", "100")),

    # ---------- недельный цикл ----------
    "WEEKLY_CYCLE":        env("WEEKLY_CYCLE", "true").lower() == "true",
    "WITHDRAW_DAY":        int(env("WITHDRAW_DAY", "6")),
    "WITHDRAW_HOUR_UTC":   int(env("WITHDRAW_HOUR_UTC", "22")),
    "RESUME_HOUR_UTC":     int(env("RESUME_HOUR_UTC", "2")),
    "PAUSE_SATURDAY":      env("PAUSE_SATURDAY", "false").lower() == "true",

    # ---------- отчёты ----------
    "DAILY_REPORT_HOUR":   int(env("DAILY_REPORT_HOUR", "23")),
    "HEARTBEAT_SECONDS":   int(env("HEARTBEAT_SECONDS", "3600")),

    # ---------- файлы ----------
    "MODEL_DIR":   env("MODEL_DIR", "models"),
    "STATE_PATH":  env("STATE_PATH", "state.json"),
    "TRADES_CSV":  env("TRADES_CSV", "trades.csv"),
    "POLL_SECONDS": int(env("POLL_SECONDS", "180")),
}
