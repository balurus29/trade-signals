"""Журнал сигналов: запоминает выданные сигналы и проверяет их по цене на момент экспирации."""
import json
import time

GRACE = 180          # сколько секунд после экспирации ещё можно проверить цену
BREAKEVEN = 1 / 1.85  # безубыточный процент побед при выплате 85%


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def save(path, entries):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(entries[-500:], f)
    except Exception:
        pass


def is_new(last, sym, kind, now, exp_min):
    """Новый сигнал: направление сменилось или прошлый уже истёк. Обновляет словарь last."""
    prev = last.get(sym)
    if prev and prev[0] == kind and now < prev[1] + exp_min * 60:
        return False
    last[sym] = (kind, now)
    return True


def new_entry(sym, itv, res, methods, now=None, indicators=None, payout=None):
    now = now or time.time()
    return {"pay": round(payout / 100, 4) if payout else None, "id": f"{int(now * 1000)}-{sym}", "sym": sym, "itv": itv, "dir": res["kind"],
            "entry": res["price"], "ts": now, "exp": res.get("exp_min", 5), "score": res["score"],
            "conf": res["conf"], "methods": sorted(methods), "ind": sorted(indicators or []),
            "status": "pending", "exit": None}


def due(entries, now=None):
    now = now or time.time()
    return [e for e in entries if e["status"] == "pending" and now >= e["ts"] + e["exp"] * 60]


def settle(e, price, now=None):
    now = now or time.time()
    if now > e["ts"] + e["exp"] * 60 + GRACE:
        e["status"] = "stale"          # проверили слишком поздно: цена уже не та
        return e
    e["exit"] = float(price)
    diff = e["exit"] - e["entry"]
    if abs(diff) <= 1e-9 * abs(e["entry"]):
        e["status"] = "draw"
    elif (diff > 0) == (e["dir"] == "call"):
        e["status"] = "win"
    else:
        e["status"] = "loss"
    return e


def label(e, names=None):
    """Название набора методов (и индикаторов) для статистики."""
    names = names or {}
    out = []
    for m in e["methods"]:
        if m == "tech" and e.get("ind"):
            out.append(names.get("tech", "tech") + "[" + ", ".join(names.get(i, i) for i in e["ind"]) + "]")
        else:
            out.append(names.get(m, m))
    return " + ".join(out)


def stats(entries, names=None):
    s = {"win": 0, "loss": 0, "draw": 0, "stale": 0, "pending": 0}
    by_dir = {"call": [0, 0], "put": [0, 0]}
    by_methods = {}
    for e in entries:
        s[e["status"]] = s.get(e["status"], 0) + 1
        if e["status"] in ("win", "loss"):
            w = 1 if e["status"] == "win" else 0
            by_dir[e["dir"]][0] += w
            by_dir[e["dir"]][1] += 1
            k = label(e, names)
            by_methods.setdefault(k, [0, 0])
            by_methods[k][0] += w
            by_methods[k][1] += 1
    n = s["win"] + s["loss"]
    s["rate"] = round(100 * s["win"] / n, 1) if n else None
    s["n"] = n
    s["by_dir"], s["by_methods"] = by_dir, by_methods
    return s


def breakdown(entries, keyfn, min_n=3):
    """Разбор по произвольному признаку: ключ -> победы, сделки, процент, погрешность, безубыток."""
    d = {}
    for e in entries:
        if e["status"] not in ("win", "loss"):
            continue
        k = keyfn(e)
        if k is None:
            continue
        r = d.setdefault(k, {"w": 0, "n": 0, "pay": 0.0})
        r["n"] += 1
        r["w"] += 1 if e["status"] == "win" else 0
        r["pay"] += e.get("pay") or 0.85
    out = {}
    for k, r in d.items():
        if r["n"] < min_n:
            continue
        p = r["w"] / r["n"]
        out[k] = {"w": r["w"], "n": r["n"], "rate": 100 * p,
                  "margin": 196 * (p * (1 - p) / r["n"]) ** 0.5,      # 95% интервал, в п.п.
                  "be": 100 / (1 + r["pay"] / r["n"])}
    return out
