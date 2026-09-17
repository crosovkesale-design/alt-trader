# language: Python 3.10+, file: config.py
# Центральный конфиг для торгового бота
import os

CFG = {
    # Биржа
    "BINANCE_KEY": os.environ.get("BINANCE_KEY", ""),
    "BINANCE_SECRET": os.environ.get("BINANCE_SECRET", ""),
    "BINANCE_TESTNET": True,
    "LIVE": os.environ.get("LIVE", "false").lower() == "true",

    # Телеграм
    "TELEGRAM_TOKEN": os.environ.get("TELEGRAM_TOKEN", ""),
    "TELEGRAM_CHAT_ID": os.environ.get("TELEGRAM_CHAT_ID", ""),

    # Пара и таймфреймы
    "SYMBOLS": os.environ.get(
        "SYMBOLS",
        "SOL/USDT,DOGE/USDT,XRP/USDT,ADA/USDT,TRX/USDT"
    ).split(","),
    "INTERVAL": os.environ.get("INTERVAL", "15m"),

    # Риск
    "LEVERAGE": int(os.environ.get("LEVERAGE", "5")),
    "RISK_PCT": float(os.environ.get("RISK_PCT", "0.10")),
    "MAX_POSITION_USD": float(os.environ.get("MAX_POSITION_USD", "20")),
    "MIN_POSITION_USD": float(os.environ.get("MIN_POSITION_USD", "5")),
    "MIN_TRADE_EQUITY": float(os.environ.get("MIN_TRADE_EQUITY", "5")),

    # Стопы
    "USE_ATR_STOPS": os.environ.get("USE_ATR_STOPS", "true").lower() == "true",
    "ATR_MULTIPLIER": float(os.environ.get("ATR_MULTIPLIER", "1.5")),
    "STOP_LOSS_PCT": float(os.environ.get("STOP_LOSS_PCT", "1.3")),
    "TAKE_PROFIT_PCT": float(os.environ.get("TAKE_PROFIT_PCT", "3.0")),
    "ATR_TP_MULT": float(os.environ.get("ATR_TP_MULT", "3.0")),

    # Следование (Trailing)
    "USE_TRAILING": os.environ.get("USE_TRAILING", "true").lower() == "true",
    "TRAIL_ACTIVATE_PCT": float(os.environ.get("TRAIL_ACTIVATE_PCT", "1.0")),
    "TRAIL_DISTANCE_PCT": float(os.environ.get("TRAIL_DISTANCE_PCT", "0.6")),

    # Лимиты
    "MAX_DAILY_LOSS_USD": float(os.environ.get("MAX_DAILY_LOSS_USD", "10")),
    "MAX_DRAWDOWN_PCT": float(os.environ.get("MAX_DRAWDOWN_PCT", "30")),
    "MAX_TRADES_PER_DAY": int(os.environ.get("MAX_TRADES_PER_DAY", "8")),
    "MAX_ENTRY_DELAY_PCT": float(os.environ.get("MAX_ENTRY_DELAY_PCT", "0.3")),

    # Ступень роста
    "TIER_BASE_EQUITY": float(os.environ.get("TIER_BASE_EQUITY", "100")),
    "TIER_STEP_EQUITY": float(os.environ.get("TIER_STEP_EQUITY", "100")),
    "TIER_BASE_POSITIONS": int(os.environ.get("TIER_BASE_POSITIONS", "2")),
    "TIER_STEP_ADD": int(os.environ.get("TIER_STEP_ADD", "2")),
    "TIER_MIN_POSITIONS": int(os.environ.get("TIER_MIN_POSITIONS", "1")),

    # Корреляция
    "USE_CORRELATION": os.environ.get("USE_CORRELATION", "true").lower() == "true",
    "MAX_CORRELATION": float(os.environ.get("MAX_CORRELATION", "0.8")),
    "CORRELATION_WINDOW": int(os.environ.get("CORRELATION_WINDOW", "100")),

    # Недельный цикл
    "WEEKLY_CYCLE": os.environ.get("WEEKLY_CYCLE", "true").lower() == "true",
    "WITHDRAW_DAY": int(os.environ.get("WITHDRAW_DAY", "6")),
    "WITHDRAW_HOUR_UTC": int(os.environ.get("WITHDRAW_HOUR_UTC", "22")),
    "RESUME_HOUR_UTC": int(os.environ.get("RESUME_HOUR_UTC", "2")),
    "PAUSE_SATURDAY": os.environ.get("PAUSE_SATURDAY", "false").lower() == "true",

    # Отчёты
    "DAILY_REPORT_HOUR": int(os.environ.get("DAILY_REPORT_HOUR", "23")),
    "HEARTBEAT_SECONDS": int(os.environ.get("HEARTBEAT_SECONDS", "3600")),

    # Файлы
    "MODEL_DIR": os.environ.get("MODEL_DIR", "models"),
    "STATE_PATH": os.environ.get("STATE_PATH", "state.json"),
    "TRADES_CSV": os.environ.get("TRADES_CSV", "trades.csv"),
    "POLL_SECONDS": int(os.environ.get("POLL_SECONDS", "180")),

    # ---------- новые ----------
    "MAX_SIGNALS_PER_CYCLE":  int(os.environ.get("MAX_SIGNALS_PER_CYCLE", "20")),
    "HEALTH_CHECK_SEC":       int(os.environ.get("HEALTH_CHECK_SEC", "1800")),
 
}
