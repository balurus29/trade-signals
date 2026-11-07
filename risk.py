"""Управление рисками: дневной лимит убытка, пауза после серии проигрышей, размер ставки.
Считается по сигналам из журнала (предполагается, что вы входите по каждому из них)."""
import datetime as dt

DEFAULT = {"on": True, "deposit": "", "stake_pct": "2", "loss_pct": "6", "streak": "3", "pause_min": "30"}
DEFAULT_PAYOUT = 0.85


def _f(x):
    try:
        return float(str(x).replace(",", ".").strip())
    except ValueError:
        return None


def config(raw):
    c = dict(DEFAULT)
    c.update(raw or {})
    return {"on": bool(c["on"]), "deposit": _f(c["deposit"]), "stake_pct": _f(c["stake_pct"]),
            "loss_pct": _f(c["loss_pct"]), "streak": int(_f(c["streak"]) or 0),
            "pause_min": _f(c["pause_min"]) or 0}


def day_start(now):
    d = dt.datetime.fromtimestamp(now)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def stake(cfg):
    if cfg["deposit"] and cfg["stake_pct"]:
        return round(cfg["deposit"] * cfg["stake_pct"] / 100, 2)
    return None


def evaluate(entries, now, cfg):
    """Состояние на сейчас: заблокированы ли новые сигналы и почему."""
    start = day_start(now)
    closed = [e for e in entries if e["status"] in ("win", "loss", "draw") and e["ts"] >= start]
    closed.sort(key=lambda e: e["ts"] + e["exp"] * 60)
    units = 0.0
    for e in closed:
        if e["status"] == "win":
            units += e.get("pay") or DEFAULT_PAYOUT
        elif e["status"] == "loss":
            units -= 1
    streak, last_loss = 0, None
    for e in reversed(closed):
        if e["status"] == "draw":
            continue
        if e["status"] == "loss":
            streak += 1
            last_loss = last_loss or e["ts"] + e["exp"] * 60
        else:
            break
    st = stake(cfg)
    res = {"blocked": False, "why": "", "until": None, "units": units, "streak": streak, "stake": st,
           "net": units * st if st else None, "n": len(closed)}
    if not cfg["on"]:
        return res
    if cfg["deposit"] and cfg["loss_pct"] and st and units * st <= -cfg["deposit"] * cfg["loss_pct"] / 100:
        res.update(blocked=True, until=start + 86400,
                   why=f"Дневной лимит убытка {cfg['loss_pct']:.0f}% депозита достигнут "
                       f"({units * st:+.2f}). Новые сигналы до завтра не показываются.")
    elif cfg["streak"] and streak >= cfg["streak"] and last_loss and now < last_loss + cfg["pause_min"] * 60:
        left = int((last_loss + cfg["pause_min"] * 60 - now) / 60) + 1
        res.update(blocked=True, until=last_loss + cfg["pause_min"] * 60,
                   why=f"Подряд проиграно сигналов: {streak}. Пауза ещё около {left} мин.")
    return res
