# language: Python 3.10+, file: notify.py
# telegram — отправка и приём сообщений
import requests
from config import CFG


def tg_send(text):
    if not CFG["TELEGRAM_TOKEN"] or not CFG["TELEGRAM_CHAT_ID"]:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{CFG['TELEGRAM_TOKEN']}/sendMessage",
            json={"chat_id": CFG["TELEGRAM_CHAT_ID"], "text": text,
                  "parse_mode": "HTML"},
            timeout=15)
    except Exception as e:
        print(f"tg_send fail: {e}")


def tg_poll(offset):
    if not CFG["TELEGRAM_TOKEN"]:
        return offset, []
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{CFG['TELEGRAM_TOKEN']}/getUpdates",
            params={"offset": offset + 1, "timeout": 0}, timeout=15)
        cmds = []
        for m in r.json().get("result", []):
            offset = max(offset, m["update_id"])
            txt = (m.get("message") or {}).get("text", "").strip()
            if txt:
                cmds.append(txt)
        return offset, cmds
    except Exception:
        return offset, []
