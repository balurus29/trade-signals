"""Ядро анализа (только numpy, без pandas) - подходит для сборки под Android.
Это инструмент анализа, а не гарантия прибыли."""
import datetime as dt
import json
import ssl
import urllib.parse
import urllib.request

import numpy as np

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{s}"
SPECIAL = {"XAUUSD": "GC=F", "XAGUSD": "SI=F", "USOIL": "CL=F", "UKOIL": "BZ=F",
           "US500": "^GSPC", "NAS100": "^NDX", "US30": "^DJI"}
CRYPTO = {"BTC", "ETH", "LTC", "XRP", "BNB", "SOL", "DOGE", "ADA"}


def now_utc():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


# ───────── данные ─────────
def to_yahoo(sym):
    s = sym.upper().replace("/", "").replace("_OTC", "").replace("-", "").strip()
    if s in SPECIAL:
        return SPECIAL[s]
    if s.endswith("USDT"):
        s = s[:-4] + "USD"
    if s.endswith("USD") and s[:-3] in CRYPTO:
        return f"{s[:-3]}-USD"
    if len(s) == 6 and s.isalpha():
        return s + "=X"
    return s


def fetch(sym, interval="5m", rng=None):
    rng = rng or ("1mo" if interval in ("1h", "60m") else "5d")
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    url = (YF_URL.format(s=urllib.parse.quote(to_yahoo(sym))) + "?"
           + urllib.parse.urlencode({"interval": interval, "range": rng}))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
        data = json.load(r)
    res = data["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    rows = [x for x in zip(q["open"], q["high"], q["low"], q["close"]) if None not in x]
    a = np.array(rows, dtype=float)
    return {"o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3]}


def demo_data(n=400, seed=None):
    rng = np.random.default_rng(seed)
    close = 1.1 + np.cumsum(rng.normal(0, 0.0006, n)) + 0.004 * np.sin(np.arange(n) / 18)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0, 0.0005, n)
    low = np.minimum(open_, close) - rng.uniform(0, 0.0005, n)
    return {"o": open_, "h": high, "l": low, "c": close}


# ───────── индикаторы ─────────
def ewm(x, alpha):
    out = np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = alpha * x[i] + (1 - alpha) * out[i - 1]
    return out


def ema(x, n):
    return ewm(x, 2 / (n + 1))


def rsi(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = ewm(np.clip(d, 0, None), 1 / n), ewm(np.clip(-d, 0, None), 1 / n)
    if dn[-1] == 0:
        return 100.0
    return float(100 - 100 / (1 + up[-1] / dn[-1]))


def atr(d, n=14):
    pc = np.r_[d["c"][0], d["c"][:-1]]
    tr = np.maximum.reduce([d["h"] - d["l"], np.abs(d["h"] - pc), np.abs(d["l"] - pc)])
    return float(ewm(tr, 1 / n)[-1])


def pivots(d, n=3):
    h, l = d["h"], d["l"]
    hi, lo = [], []
    for i in range(n, len(h) - n):
        if h[i] == h[i - n:i + n + 1].max():
            hi.append(i)
        if l[i] == l[i - n:i + n + 1].min():
            lo.append(i)
    return hi, lo


# ───────── компоненты сигнала (баллы: + это CALL, - это PUT) ─────────
def trend_and_momentum(d):
    c = d["c"]
    e20, e50 = ema(c, 20), ema(c, 50)
    out = []
    slope = e50[-1] - e50[-6]
    if c[-1] > e20[-1] > e50[-1] and slope > 0:
        out.append((25, "Тренд восходящий (цена > EMA20 > EMA50)"))
    elif c[-1] < e20[-1] < e50[-1] and slope < 0:
        out.append((-25, "Тренд нисходящий (цена < EMA20 < EMA50)"))
    elif c[-1] > e50[-1]:
        out.append((8, "Цена выше EMA50, тренд слабо восходящий"))
    else:
        out.append((-8, "Цена ниже EMA50, тренд слабо нисходящий"))

    r = rsi(c)
    if r < 30:
        out.append((10, f"RSI {r:.0f}: перепроданность"))
    elif r > 70:
        out.append((-10, f"RSI {r:.0f}: перекупленность"))

    macd = ema(c, 12) - ema(c, 26)
    hist = macd - ema(macd, 9)
    if hist[-1] > 0 and hist[-1] > hist[-2]:
        out.append((10, "MACD: бычий импульс растёт"))
    elif hist[-1] < 0 and hist[-1] < hist[-2]:
        out.append((-10, "MACD: медвежий импульс растёт"))

    w = c[-20:]
    ma, sd = w.mean(), w.std(ddof=1)
    if c[-1] < ma - 2 * sd:
        out.append((8, "Цена ниже нижней полосы Боллинджера"))
    elif c[-1] > ma + 2 * sd:
        out.append((-8, "Цена выше верхней полосы Боллинджера"))
    return out


def support_resistance(d, hi, lo):
    a = atr(d)
    price = d["c"][-1]
    levels = sorted([d["h"][i] for i in hi] + [d["l"][i] for i in lo])
    clusters, cur = [], []
    for lv in levels:
        if cur and lv - cur[-1] > 0.5 * a:
            clusters.append(np.mean(cur))
            cur = []
        cur.append(lv)
    if cur:
        clusters.append(np.mean(cur))
    sup = [x for x in clusters if x <= price]
    res = [x for x in clusters if x > price]
    out = []
    if sup and price - sup[-1] <= 0.7 * a:
        out.append((15, f"Цена у поддержки {sup[-1]:.5g}"))
    if res and res[0] - price <= 0.7 * a:
        out.append((-15, f"Цена у сопротивления {res[0]:.5g}"))
    return out


def candle_patterns(d):
    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
    e20 = ema(c, 20)[-1]
    a = atr(d)
    out = []
    body = lambda i: abs(c[i] - o[i])
    rng = lambda i: max(h[i] - l[i], 1e-12)
    if c[-2] < o[-2] and c[-1] > o[-1] and c[-1] >= o[-2] and o[-1] <= c[-2] and body(-1) > body(-2):
        out.append((15, "Свеча: бычье поглощение"))
    if c[-2] > o[-2] and c[-1] < o[-1] and c[-1] <= o[-2] and o[-1] >= c[-2] and body(-1) > body(-2):
        out.append((-15, "Свеча: медвежье поглощение"))
    lower = min(o[-1], c[-1]) - l[-1]
    upper = h[-1] - max(o[-1], c[-1])
    if lower > 2 * body(-1) and upper < body(-1) + 0.1 * rng(-1) and c[-1] < e20:
        out.append((10, "Свеча: молот после снижения"))
    if upper > 2 * body(-1) and lower < body(-1) + 0.1 * rng(-1) and c[-1] > e20:
        out.append((-10, "Свеча: падающая звезда после роста"))
    if c[-3] < o[-3] and body(-3) > 0.6 * a and body(-2) < 0.3 * a and c[-1] > o[-1] and c[-1] > (o[-3] + c[-3]) / 2:
        out.append((15, "Свеча: утренняя звезда"))
    if c[-3] > o[-3] and body(-3) > 0.6 * a and body(-2) < 0.3 * a and c[-1] < o[-1] and c[-1] < (o[-3] + c[-3]) / 2:
        out.append((-15, "Свеча: вечерняя звезда"))
    if body(-1) < 0.1 * rng(-1):
        out.append((0, "Свеча: доджи (неопределённость)"))
    return out


def chart_patterns(d, hi, lo):
    h, l, c = d["h"], d["l"], d["c"]
    n = len(c)
    tol = 0.6 * atr(d)
    price = c[-1]
    out = []
    recent = lambda i, bars=20: n - 1 - i <= bars
    trough = lambda a, b: l[a:b + 1].min()
    peak = lambda a, b: h[a:b + 1].max()

    if len(hi) >= 3:
        a, b, e = hi[-3:]
        if max(h[a], h[b], h[e]) - min(h[a], h[b], h[e]) <= tol and recent(e):
            neck = min(trough(a, b), trough(b, e))
            out.append((-25, "Фигура: тройная вершина, пробой шеи") if price < neck
                       else (-18, "Фигура: тройная вершина формируется"))
    if len(hi) >= 2:
        a, b = hi[-2:]
        if abs(h[a] - h[b]) <= tol and b - a >= 5 and recent(b, 15) and not (len(hi) >= 3 and abs(h[hi[-3]] - h[b]) <= tol):
            out.append((-22, "Фигура: двойная вершина, пробой шеи") if price < trough(a, b)
                       else (-15, "Фигура: двойная вершина формируется"))
    if len(lo) >= 3:
        a, b, e = lo[-3:]
        if max(l[a], l[b], l[e]) - min(l[a], l[b], l[e]) <= tol and recent(e):
            neck = max(peak(a, b), peak(b, e))
            out.append((25, "Фигура: тройное дно, пробой шеи") if price > neck
                       else (18, "Фигура: тройное дно формируется"))
    if len(lo) >= 2:
        a, b = lo[-2:]
        if abs(l[a] - l[b]) <= tol and b - a >= 5 and recent(b, 15) and not (len(lo) >= 3 and abs(l[lo[-3]] - l[b]) <= tol):
            out.append((22, "Фигура: двойное дно, пробой шеи") if price > peak(a, b)
                       else (15, "Фигура: двойное дно формируется"))
    if len(hi) >= 3:
        s1, hd, s2 = hi[-3:]
        if h[hd] - max(h[s1], h[s2]) > tol and abs(h[s1] - h[s2]) <= 1.5 * tol and recent(s2):
            neck = (trough(s1, hd) + trough(hd, s2)) / 2
            out.append((-25, "Фигура: голова и плечи, пробой шеи") if price < neck
                       else (-15, "Фигура: голова и плечи формируется"))
    if len(lo) >= 3:
        s1, hd, s2 = lo[-3:]
        if min(l[s1], l[s2]) - l[hd] > tol and abs(l[s1] - l[s2]) <= 1.5 * tol and recent(s2):
            neck = (peak(s1, hd) + peak(hd, s2)) / 2
            out.append((25, "Фигура: перевёрнутая голова и плечи, пробой шеи") if price > neck
                       else (15, "Фигура: перевёрнутая голова и плечи формируется"))
    hh = [i for i in hi if n - i <= 80][-4:]
    ll = [i for i in lo if n - i <= 80][-4:]
    if len(hh) >= 3 and len(ll) >= 3:
        sh = np.polyfit(hh, h[hh], 1)[0]
        sl = np.polyfit(ll, l[ll], 1)[0]
        if sh > 0 and sl > 0 and sl > 1.2 * sh:
            out.append((-15, "Фигура: восходящий клин (медвежий)"))
        if sh < 0 and sl < 0 and abs(sh) > 1.2 * abs(sl):
            out.append((15, "Фигура: нисходящий клин (бычий)"))
    return out


def in_blackout(windows):
    now = now_utc().time()
    for w in windows or []:
        try:
            a, b = (dt.datetime.strptime(x.strip(), "%H:%M").time() for x in w.split("-"))
        except ValueError:
            continue
        if a <= now <= b:
            return w
    return None


def analyze(d, threshold=35, blackout=None):
    """Возвращает dict: kind (call/put/wait/pause), signal, score, conf, parts, price."""
    if len(d["c"]) < 80:
        return None
    d = {k: v[-150:] for k, v in d.items()}
    hi, lo = pivots(d)
    parts = trend_and_momentum(d) + support_resistance(d, hi, lo) + candle_patterns(d) + chart_patterns(d, hi, lo)
    score = int(max(-100, min(100, sum(p for p, _ in parts))))
    bull = sum(1 for p, _ in parts if p > 0)
    bear = sum(1 for p, _ in parts if p < 0)
    agree = max(bull, bear) / max(bull + bear, 1)
    conf = int(min(95, abs(score) * 0.8 + agree * 20))
    kind, signal = ("call", "CALL (покупка)") if score >= threshold else \
                   ("put", "PUT (продажа)") if score <= -threshold else ("wait", "ЖДАТЬ")
    w = in_blackout(blackout)
    if w and kind != "wait":
        kind, signal = "pause", f"ПАУЗА: новости {w} UTC"
    return {"kind": kind, "signal": signal, "score": score, "conf": conf,
            "parts": parts, "price": float(d["c"][-1])}


# ───────── списки активов ─────────
def _fx(codes):
    return [f"{c[:3]}/{c[3:]}" for c in codes]


GROUPS = {
    "Форекс: основные": _fx(["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD"]),
    "Форекс: кроссы": _fx([
        "EURGBP", "EURJPY", "EURCHF", "EURAUD", "EURCAD", "EURNZD",
        "GBPJPY", "GBPCHF", "GBPAUD", "GBPCAD", "GBPNZD",
        "AUDJPY", "AUDCHF", "AUDCAD", "AUDNZD",
        "NZDJPY", "NZDCHF", "NZDCAD",
        "CADJPY", "CADCHF", "CHFJPY"]),
    "Форекс: экзотика": _fx([
        "USDMXN", "USDZAR", "USDTRY", "USDSGD", "USDHKD", "USDNOK", "USDSEK",
        "USDCNH", "USDINR", "USDBRL", "EURTRY", "EURNOK", "EURSEK", "EURPLN"]),
    "Металлы и нефть": ["XAU/USD", "XAG/USD", "USOIL", "UKOIL"],
    "Крипто": _fx(["BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD", "BNBUSD", "SOLUSD", "DOGEUSD", "ADAUSD"]),
}
FOREX_ALL = GROUPS["Форекс: основные"] + GROUPS["Форекс: кроссы"] + GROUPS["Форекс: экзотика"]
