# language: Python 3.10+, file: commands.py
# обработка команд из Telegram
from config import CFG
from notify import tg_send
from state import (
    get_state, save_state, get_balance_usd, positions_allowed, open_count,
)
from data import fetch_ohlcv
from trade import close_position, weekly_rollover


def handle_command(cmd):
    state = get_state()
    c = cmd.strip()
    cl = c.lower()

    if cl == "/status":
        eq = get_balance_usd()
        mode = "multi" if state.get("multi_symbol", True) else \
            f"single({state.get('active_symbol')})"
        open_pos = []
        for s, st in state["symbols"].items():
            if st["position"] != 0:
                tag = "L" if st["position"] == 1 else "S"
                open_pos.append(f"{s.split('/')[0]}:{tag}")
        allowed = positions_allowed(eq)
        tg_send(
            f"📊 <b>Статус</b>\n"
            f"режим: {mode} (paper)\n"
            f"equity: <b>${eq:.2f}</b>\n"
            f"позиций разрешено: {allowed}\n"
            f"открыто: {' · '.join(open_pos) or '—'}\n"
            f"сделок: {state['total_trades']} · win/loss "
            f"{state['wins']}/{state['losses']}\n"
            f"день: {state['daily_pnl']:+.4f} · сделок "
            f"{state['daily_trades']}/{CFG['MAX_TRADES_PER_DAY']}\n"
            f"kill: {state['kill_switch']}")

    elif cl == "/tier":
        eq = get_balance_usd()
        allowed = positions_allowed(eq)
        base = CFG["TIER_BASE_EQUITY"]
        step = CFG["TIER_STEP_EQUITY"]
        add = CFG["TIER_STEP_ADD"]
        lines = [f"💰 equity: <b>${eq:.2f}</b>",
                 f"позиций: <b>{allowed}</b>", ""]
        for i in range(6):
            lo = base + i * step
            hi = lo + step
            n = min(CFG["TIER_BASE_POSITIONS"] + i * add,
                    len(CFG["SYMBOLS"]))
            mark = " ←" if lo <= eq < hi else ""
            lines.append(f"  ${lo:.0f}–${hi:.0f} → {n} поз.{mark}")
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
                    df = fetch_ohlcv(sym, 10)
                    close_position(sym, "manual", float(df["close"].iloc[-1]))
                    closed += 1
                except Exception as e:
                    print(f"close {sym}: {e}")
        tg_send(f"✅ закрыто: {closed}")

    elif cl == "/panic":
        state["kill_switch"] = True
        for sym in CFG["SYMBOLS"]:
            if state["symbols"][sym]["position"] != 0:
                try:
                    df = fetch_ohlcv(sym, 10)
                    close_position(sym, "panic", float(df["close"].iloc[-1]))
                except Exception:
                    pass
        tg_send("🚨 PANIC: всё закрыто, kill включён")

    elif cl == "/single":
        state["multi_symbol"] = False
        parts = c.split()
        if len(parts) > 1:
            arg = parts[1].upper().split("/")[0]
            cand = [s for s in CFG["SYMBOLS"]
                    if s.split("/")[0].upper() == arg]
            state["active_symbol"] = cand[0] if cand else CFG["SYMBOLS"][0]
        else:
            state["active_symbol"] = CFG["SYMBOLS"][0]
        keep = state["active_symbol"]
        closed = []
        for sym in CFG["SYMBOLS"]:
            if sym == keep:
                continue
            s = state["symbols"].get(sym)
            if s and s["position"] != 0:
                try:
                    df = fetch_ohlcv(sym, 10)
                    close_position(sym, "switch_single",
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
            f"одна: /single SOL")

    elif cl == "/pairs":
        mode = "multi" if state.get("multi_symbol", True) else "single"
        act = state.get("active_symbol") or "—"
        lines = [f"📋 режим: {mode}", f"активная: {act}", ""]
        for s in CFG["SYMBOLS"]:
            st = state["symbols"][s]
            pos = "—" if st["position"] == 0 else \
                ("L" if st["position"] == 1 else "S")
            lines.append(f"  {s}: {pos}")
        tg_send("\n".join(lines))

    elif cl == "/cycle":
        weekly_rollover()
        state["kill_switch"] = True
        tg_send("📅 ручной цикл, kill включён")

    elif cl == "/reset":
        state["virtual_equity"] = 100.0
        state["daily_pnl"] = 0.0
        state["total_trades"] = 0
        state["wins"] = 0
        state["losses"] = 0
        tg_send("🔄 equity сброшен на $100")

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
            "/reset — сброс equity\n"
            "/help — это\n\n"
            "<i>paper mode — ордера не отправляются</i>")
