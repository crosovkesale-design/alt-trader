# language: Python 3.10+, file: trader.py
# главный цикл бота
import time
import logging

from config import CFG
from notify import tg_send, tg_poll
from state import (
    load_state, save_state, set_state, get_state, get_balance_usd,
    positions_allowed, open_count, check_daily_reset, risk_ok,
    DEFAULT_SYM_STATE,
)
from data import fetch_ohlcv, load_model, predict
from trade import (
    update_trailing, check_sl_tp, close_position, open_position,
    correlation_blocked, weekly_cycle_check, weekly_rollover,
    weekly_resume, active_symbols, maybe_daily_report,
)
from commands import handle_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("trader")


def run():
    log.info("=" * 50)
    log.info("alt-autotrader старт (paper)")
    log.info(f"пары: {CFG['SYMBOLS']}")
    log.info(f"interval={CFG['INTERVAL']}")

    state = load_state()
    set_state(state)

    eq0 = get_balance_usd()
    if state["started_equity"] == 0:
        state["started_equity"] = eq0
    if state["peak_equity"] == 0:
        state["peak_equity"] = eq0
    if state["week_started_equity"] == 0:
        state["week_started_equity"] = eq0

    for sym in CFG["SYMBOLS"]:
        s = state["symbols"].get(sym)
        if s and s["position"] != 0:
            log.warning(f"{sym}: сброс фантомной позиции")
            s["position"] = 0
            s["entry_price"] = 0
            s["size"] = 0
    save_state()

    tg_send(
        f"🤖 <b>Альт-трейдер старт</b>\n"
        f"пары: {', '.join(s.split('/')[0] for s in CFG['SYMBOLS'])}\n"
        f"режим: <b>PAPER</b> (ордера не отправляются)\n"
        f"equity: <b>${eq0:.2f}</b>\n"
        f"позиций: {positions_allowed(eq0)}\n"
        f"риск/сделка: ${eq0 * CFG['RISK_PCT']:.2f}\n\n"
        f"команды: /help")

    for sym in CFG["SYMBOLS"]:
        load_model(sym)

    tg_off = 0
    last_hb = 0
    last_health_ping = time.time()

    while True:
        try:
            allowed, why = weekly_cycle_check()
            prev_phase = state.get("phase", "ok")
            if prev_phase != "withdraw_window" and why == "withdraw_window":
                weekly_rollover()
            if prev_phase in ("withdraw_window", "resume_pending") \
                    and why == "ok":
                weekly_resume()
            state["phase"] = why

            tg_off, cmds = tg_poll(tg_off)
            for c in cmds:
                handle_command(c)
                save_state()

            if not allowed:
                log.info(f"пауза: {why}")
                time.sleep(60)
                continue

            equity = get_balance_usd()
            state["peak_equity"] = max(state["peak_equity"], equity)
            if check_daily_reset():
                log.info("новый UTC-день, сброс счётчиков")
            ok, why_risk = risk_ok(equity)

            # ============ ПРОХОД 1: сбор сигналов ============
            signals = []

            for sym in active_symbols():
                s = state["symbols"].setdefault(sym, dict(DEFAULT_SYM_STATE))

                try:
                    df = fetch_ohlcv(sym, 500)
                except Exception as e:
                    log.warning(f"{sym} fetch: {e}")
                    continue

                last_closed = df.iloc[:-1]
                live = df.iloc[-1]
                price = float(last_closed["close"].iloc[-1])
                cts = int(last_closed.index[-1].timestamp())

                update_trailing(sym, float(live["high"]), float(live["low"]))

                sltp = check_sl_tp(sym, float(live["high"]), float(live["low"]))
                if sltp:
                    close_position(sym, sltp[0], sltp[1])
                    save_state()

                if cts == s["last_candle_ts"]:
                    continue

                result = predict(sym, last_closed)
                if result is None:
                    continue
                sig, proba = result

                if s["position"] != 0 and sig != s["position"]:
                    close_position(sym, f"signal {sig}", price)

                if s["position"] == 0 and sig != 0:
                    confidence = float(proba[int(proba.argmax())])
                    signals.append({
                        "sym": sym, "sig": sig, "confidence": confidence,
                        "price": price, "df": last_closed, "proba": proba,
                    })

                s["last_candle_ts"] = cts
                save_state()

                log.info(
                    f"{sym} {last_closed.index[-1]} px={price:.6f} "
                    f"sig={sig} p=[{proba[0]:.2f},{proba[1]:.2f},"
                    f"{proba[2]:.2f}] pos={s['position']} "
                    f"eq=${get_balance_usd():.2f}")

            # ============ ПРОХОД 2: открытие по уверенности ============
            if signals and ok:
                signals.sort(key=lambda x: x["confidence"], reverse=True)
                slots = positions_allowed(equity) - open_count()
                if slots > 0:
                    log.info(f"сигналов: {len(signals)}, слотов: {slots}")
                    for cand in signals[:slots]:
                        blocked, whyc = correlation_blocked(cand["sym"],
                                                            cand["sig"])
                        if blocked:
                            log.info(f"{cand['sym']}: пропущен — {whyc}")
                            continue
                        open_position(cand["sym"], cand["sig"],
                                      cand["price"], equity, cand["df"])
                        save_state()
            elif signals and not ok:
                log.info(f"сигналов {len(signals)}, риск-стоп: {why_risk}")

            maybe_daily_report()
            save_state()

            if time.time() - last_health_ping > CFG["HEALTH_CHECK_SEC"]:
                tg_send(f"⚠️ health: цикл не завершался "
                        f"{(time.time()-last_health_ping)/60:.0f} мин")
                last_health_ping = time.time()

            if time.time() - last_hb > CFG["HEARTBEAT_SECONDS"]:
                eq = get_balance_usd()
                tg_send(
                    f"💓 ${eq:.2f} · поз {open_count()}/"
                    f"{positions_allowed(eq)} · день "
                    f"{state['daily_pnl']:+.4f} · сделок "
                    f"{state['daily_trades']}/{CFG['MAX_TRADES_PER_DAY']}")
                last_hb = time.time()
            last_health_ping = time.time()
            # опрос команд каждые 10 сек, торговый цикл — раз в POLL_SECONDS
            slept = 0
            while slept < CFG["POLL_SECONDS"]:
                time.sleep(10)
                slept += 10
                tg_off, cmds = tg_poll(tg_off)
                for c in cmds:
                    handle_command(c)
                    save_state()

        except KeyboardInterrupt:
            break
        except Exception as e:
            log.exception(f"цикл: {e}")
            tg_send(f"⚠️ ошибка: {e}")
            time.sleep(60)

    tg_send("🤖 остановлен")


if __name__ == "__main__":
    run()
