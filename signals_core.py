"""Ядро анализа (только numpy). Это инструмент анализа, а не гарантия прибыли."""
import datetime as dt
import json
import ssl
import urllib.error
import urllib.parse
import urllib.request

import numpy as np

YF_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{s}"
SPECIAL = {"XAUUSD": "GC=F", "XAGUSD": "SI=F", "USOIL": "CL=F", "UKOIL": "BZ=F",
           "US500": "^GSPC", "NAS100": "^NDX", "US30": "^DJI"}
CRYPTO = {"BTC", "ETH", "LTC", "XRP", "BNB", "SOL", "DOGE", "ADA"}

# методы анализа: ключ -> (название, максимум баллов метода)
METHODS = {
    "tech": ("Технический", 65),
    "levels": ("Уровни", 15),
    "candles": ("Свечи", 20),
    "patterns": ("Фигуры", 25),
    "breakout": ("Пробой", 25),
    "news": ("Фундаментал", 0),
}
ALL_METHODS = set(METHODS)


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


YAHOO_BUILD = {"3m": ("1m", 3), "10m": ("5m", 2)}     # Yahoo не отдаёт эти ТФ, собираем сами


def resample(d, k):
    """Склеивает по k соседних свечей (отсчёт с конца, чтобы последняя свеча была полной)."""
    n = len(d["c"]) // k * k
    if n < k:
        return d
    sl = slice(len(d["c"]) - n, None)
    out = {}
    for key in d:
        a = np.asarray(d[key])[sl].reshape(-1, k)
        out[key] = (a[:, 0] if key == "o" else a.max(1) if key == "h" else a.min(1) if key == "l"
                    else a[:, -1] if key == "c" else a.sum(1))
    return out


def _fetch_yahoo(sym, interval="5m", rng=None):
    if interval in YAHOO_BUILD:
        base, k = YAHOO_BUILD[interval]
        return resample(_fetch_yahoo(sym, base, rng), k)
    rng = rng or ("1y" if interval == "1d" else "1mo" if interval in ("1h", "60m", "30m") else "5d")
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
    vols = q.get("volume") or [0] * len(q["close"])
    rows = [x for x in zip(q["open"], q["high"], q["low"], q["close"], vols) if None not in x[:4]]
    a = np.array([(r[0], r[1], r[2], r[3], r[4] or 0) for r in rows], dtype=float)
    return {"o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3], "v": a[:, 4]}


# ───────── OTC через OTCharts (Pocket Option) ─────────
OTC_URL = "https://otcharts.com/v1/candles"
OTC_KEY = ""          # задаётся из приложения (вводится на телефоне, в чат не отправляется)
TF_SECONDS = {"1m": 60, "2m": 120, "3m": 180, "5m": 300, "10m": 600, "15m": 900, "30m": 1800,
              "1h": 3600, "4h": 14400, "1d": 86400}
TF_LIST = ("1m", "2m", "3m", "5m", "10m", "15m", "30m", "1h")
PAYOUTS = {}          # символ -> выплата в процентах (из каталога OTCharts)
OTC_ERRORS = {
    400: "Неверный символ или таймфрейм для OTC",
    401: "API-ключ OTCharts не подошёл (проверьте ключ и подтверждение почты)",
    403: "Ваш тариф OTCharts не открывает этот инструмент",
    404: "Символ не найден в каталоге Pocket Option OTC",
    429: "Исчерпана дневная квота запросов OTCharts",
    502: "Источник котировок временно не отвечает",
}


def is_otc(sym):
    return sym.strip().lower().endswith("_otc")


def fetch_otc(sym, interval="5m", limit=200):
    if not OTC_KEY:
        raise RuntimeError("Введите API-ключ OTCharts в поле «API-ключ OTC»")
    tf = TF_SECONDS.get(interval, 300)
    url = OTC_URL + "?" + urllib.parse.urlencode(
        {"venue": "otc", "symbol": sym.strip().replace("/", ""), "tf": tf, "limit": limit})
    req = urllib.request.Request(url, headers={
        "Authorization": "Bearer " + OTC_KEY.strip(),
        "User-Agent": "TradeSignalsApp/1.0",      # со стандартным urllib CDN отвечает 403 (1010)
        "Accept": "application/json"})
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(OTC_ERRORS.get(e.code, f"OTCharts: ошибка {e.code}"))
    rows = sorted(data.get("candles", []), key=lambda x: x["time"])
    if not rows:
        raise RuntimeError("OTCharts не вернул свечей")
    a = np.array([[x["open"], x["high"], x["low"], x["close"]] for x in rows], dtype=float)
    return {"o": a[:, 0], "h": a[:, 1], "l": a[:, 2], "c": a[:, 3]}


def fetch(sym, interval="5m", rng=None, limit=200):
    """OTC-символы (…_otc) идут через OTCharts, остальные через Yahoo Finance."""
    if is_otc(sym):
        return fetch_otc(sym, interval, limit)
    return _fetch_yahoo(sym, interval, rng)


# ───────── каталог OTC (загружается из OTCharts одним запросом) ─────────
OTC_CRYPTO = ("BTC", "ETH", "LTC", "XRP", "DOGE", "ADA", "SOL", "BNB", "TRX", "DOT", "AVAX", "LINK",
              "MATIC", "SHIB", "TON", "BCH", "ATOM", "NEAR", "PEPE")
OTC_COMM = ("XAU", "XAG", "XPT", "XPD", "BRENT", "CRUDE", "OIL", "GAS", "COPPER")
OTC_LEGACY = "OTC Pocket Option"


def group_otc(symbols):
    g = {"OTC: Форекс": [], "OTC: Крипто": [], "OTC: Акции": [], "OTC: Металлы и сырьё": [], "OTC: Прочее": []}
    for s in sorted(set(symbols)):
        base = s[:-4] if s.lower().endswith("_otc") else s
        up = base.upper()
        if s.startswith("#"):
            g["OTC: Акции"].append(s)
        elif up.startswith(OTC_COMM) or any(k in up for k in ("BRENT", "CRUDE")):
            g["OTC: Металлы и сырьё"].append(s)
        elif any(up.startswith(k) for k in OTC_CRYPTO):
            g["OTC: Крипто"].append(s)
        elif len(base) == 6 and base.isalpha():
            g["OTC: Форекс"].append(s)
        else:
            g["OTC: Прочее"].append(s)
    return {k: v for k, v in g.items() if v}


def apply_otc_catalog(symbols):
    global OTC_ALL
    groups = group_otc(symbols)
    if not groups:
        return 0
    for k in [k for k in GROUPS if k.startswith("OTC")]:
        del GROUPS[k]
    GROUPS.update(groups)
    OTC_ALL = [s for v in groups.values() for s in v]
    return len(OTC_ALL)


def load_otc_catalog():
    """GET /v1/symbols?venue=otc (расходует 1 запрос квоты). Возвращает список символов."""
    if not OTC_KEY:
        raise RuntimeError("Введите API-ключ OTCharts в поле «API-ключ OTC»")
    req = urllib.request.Request(
        "https://otcharts.com/v1/symbols?venue=otc",
        headers={"Authorization": "Bearer " + OTC_KEY.strip(),
                 "User-Agent": "TradeSignalsApp/1.0", "Accept": "application/json"})
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=20, context=ctx) as r:
            data = json.load(r)
    except urllib.error.HTTPError as e:
        raise RuntimeError(OTC_ERRORS.get(e.code, f"OTCharts: ошибка {e.code}"))
    syms = [x["symbol"] for x in data.get("symbols", []) if x.get("symbol")]
    pay = {}
    for x in data.get("symbols", []):
        try:
            v = float(x.get("payout"))
        except (TypeError, ValueError):
            continue
        v = v * 100 if 0 < v <= 1.5 else v          # доля (0.92) или проценты (92)
        if 0 < v <= 300:
            pay[x["symbol"]] = round(v)
    PAYOUTS.clear()
    PAYOUTS.update(pay)
    if not syms:
        raise RuntimeError("OTCharts не вернул список инструментов")
    return syms


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

    def merge(idx, vals, better):      # близкие экстремумы (равные значения) сливаем в один
        out = []
        for i in idx:
            if out and i - out[-1] <= n:
                if better(vals[i], vals[out[-1]]):
                    out[-1] = i
            else:
                out.append(i)
        return out
    return merge(hi, h, lambda a, b: a > b), merge(lo, l, lambda a, b: a < b)


# ───────── компоненты сигнала (баллы: + это CALL, - это PUT) ─────────
def _sma(x, m):
    out = np.full(len(x), np.nan)
    for i in range(m - 1, len(x)):
        w = x[i - m + 1:i + 1]
        if not np.isnan(w).any():
            out[i] = w.mean()
    return out


def ind_ema(d, hi, lo):
    c = d["c"]
    e20, e50 = ema(c, 20), ema(c, 50)
    slope = e50[-1] - e50[-6]
    if c[-1] > e20[-1] > e50[-1] and slope > 0:
        return [(25, "EMA: тренд восходящий (цена > EMA20 > EMA50)")]
    if c[-1] < e20[-1] < e50[-1] and slope < 0:
        return [(-25, "EMA: тренд нисходящий (цена < EMA20 < EMA50)")]
    if c[-1] > e50[-1]:
        return [(8, "EMA: цена выше EMA50, тренд слабо восходящий")]
    return [(-8, "EMA: цена ниже EMA50, тренд слабо нисходящий")]


def ind_ema_cross(d, hi, lo):
    c = d["c"]
    e9, e21 = ema(c, 9), ema(c, 21)
    if e9[-1] > e21[-1]:
        if any(e9[-k] <= e21[-k] for k in (2, 3)):
            return [(12, "EMA 9/21: свежее пересечение вверх")]
        return [(5, "EMA 9 выше EMA 21")]
    if any(e9[-k] >= e21[-k] for k in (2, 3)):
        return [(-12, "EMA 9/21: свежее пересечение вниз")]
    return [(-5, "EMA 9 ниже EMA 21")]


def ind_rsi(d, hi, lo):
    r = rsi(d["c"])
    if r < 30:
        return [(10, f"RSI {r:.0f}: перепроданность")]
    if r > 70:
        return [(-10, f"RSI {r:.0f}: перекупленность")]
    return []


def ind_macd(d, hi, lo):
    c = d["c"]
    macd = ema(c, 12) - ema(c, 26)
    hist = macd - ema(macd, 9)
    if hist[-1] > 0 and hist[-1] > hist[-2]:
        return [(10, "MACD: бычий импульс растёт")]
    if hist[-1] < 0 and hist[-1] < hist[-2]:
        return [(-10, "MACD: медвежий импульс растёт")]
    return []


def ind_boll(d, hi, lo):
    c = d["c"]
    w = c[-20:]
    ma, sd = w.mean(), w.std(ddof=1)
    if c[-1] < ma - 2 * sd:
        return [(8, "Боллинджер: цена ниже нижней полосы")]
    if c[-1] > ma + 2 * sd:
        return [(-8, "Боллинджер: цена выше верхней полосы")]
    return []


def ind_stoch(d, hi, lo):
    h, l, c = d["h"], d["l"], d["c"]
    n = len(c)
    raw = np.full(n, np.nan)
    for i in range(13, n):
        hh, ll = h[i - 13:i + 1].max(), l[i - 13:i + 1].min()
        raw[i] = 100 * (c[i] - ll) / (hh - ll) if hh > ll else 50.0
    k = _sma(raw, 3)
    dd = _sma(k, 3)
    cross_up = any(k[-j] > dd[-j] and k[-j - 1] <= dd[-j - 1] for j in (1, 2))
    cross_dn = any(k[-j] < dd[-j] and k[-j - 1] >= dd[-j - 1] for j in (1, 2))
    if k[-1] < 25 and cross_up:
        return [(10, f"Стохастик {k[-1]:.0f}: разворот вверх из перепроданности")]
    if k[-1] > 75 and cross_dn:
        return [(-10, f"Стохастик {k[-1]:.0f}: разворот вниз из перекупленности")]
    return []


def ind_cci(d, hi, lo):
    tp = (d["h"] + d["l"] + d["c"]) / 3
    ma = _sma(tp, 20)
    cci = np.full(len(tp), np.nan)
    for i in range(19, len(tp)):
        md = np.abs(tp[i - 19:i + 1] - ma[i]).mean()
        cci[i] = (tp[i] - ma[i]) / (0.015 * md) if md > 0 else 0.0
    if cci[-2] < -100 <= cci[-1]:
        return [(8, f"CCI {cci[-1]:.0f}: выход из зоны перепроданности")]
    if cci[-2] > 100 >= cci[-1]:
        return [(-8, f"CCI {cci[-1]:.0f}: выход из зоны перекупленности")]
    return []


def ind_adx(d, hi, lo, n=14):
    h, l, c = d["h"], d["l"], d["c"]
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    pc = c[:-1]
    tr = np.maximum.reduce([h[1:] - l[1:], np.abs(h[1:] - pc), np.abs(l[1:] - pc)])
    a = ewm(tr, 1 / n)
    a = np.where(a == 0, np.nan, a)
    pdi, mdi = 100 * ewm(plus, 1 / n) / a, 100 * ewm(minus, 1 / n) / a
    s = pdi + mdi
    dx = np.where(s == 0, 0.0, 100 * np.abs(pdi - mdi) / np.where(s == 0, 1, s))
    adx = ewm(np.nan_to_num(dx), 1 / n)[-1]
    if adx >= 25:
        if pdi[-1] > mdi[-1]:
            return [(12, f"ADX {adx:.0f}: сильный восходящий тренд")]
        return [(-12, f"ADX {adx:.0f}: сильный нисходящий тренд")]
    if adx < 20:
        return [(0, f"ADX {adx:.0f}: рынок без тренда, трендовым сигналам доверять меньше")]
    return []


def _psar(d, af0=0.02, afmax=0.2):
    h, l = d["h"], d["l"]
    n = len(h)
    up, ep, af = True, h[0], af0
    sar = l[0]
    trend = np.ones(n, dtype=bool)
    for i in range(1, n):
        s = sar + af * (ep - sar)
        if up:
            s = min(s, l[i - 1], l[i - 2] if i > 1 else l[i - 1])
            if l[i] < s:
                up, s, ep, af = False, ep, l[i], af0
            elif h[i] > ep:
                ep, af = h[i], min(af + af0, afmax)
        else:
            s = max(s, h[i - 1], h[i - 2] if i > 1 else h[i - 1])
            if h[i] > s:
                up, s, ep, af = True, ep, h[i], af0
            elif l[i] < ep:
                ep, af = l[i], min(af + af0, afmax)
        sar, trend[i] = s, up
    return trend


def ind_psar(d, hi, lo):
    t = _psar(d)
    if t[-1] != t[-2] or t[-2] != t[-3]:
        return [(10, "Parabolic SAR развернулся вверх")] if t[-1] else [(-10, "Parabolic SAR развернулся вниз")]
    return [(4, "Parabolic SAR под ценой (рост)")] if t[-1] else [(-4, "Parabolic SAR над ценой (падение)")]


def ind_supertrend(d, hi, lo, n=10, mult=3.0):
    h, l, c = d["h"], d["l"], d["c"]
    a = atr_series(d, n)
    hl2 = (h + l) / 2
    ub, lb = hl2 + mult * a, hl2 - mult * a
    fub, flb = ub.copy(), lb.copy()
    dirn = np.ones(len(c))
    for i in range(1, len(c)):
        fub[i] = ub[i] if ub[i] < fub[i - 1] or c[i - 1] > fub[i - 1] else fub[i - 1]
        flb[i] = lb[i] if lb[i] > flb[i - 1] or c[i - 1] < flb[i - 1] else flb[i - 1]
        if dirn[i - 1] == 1:
            dirn[i] = -1 if c[i] < flb[i] else 1
        else:
            dirn[i] = 1 if c[i] > fub[i] else -1
    if dirn[-1] != dirn[-2] or dirn[-2] != dirn[-3]:
        return [(15, "Supertrend: разворот вверх")] if dirn[-1] == 1 else [(-15, "Supertrend: разворот вниз")]
    return [(8, "Supertrend: восходящий")] if dirn[-1] == 1 else [(-8, "Supertrend: нисходящий")]


def ind_ichimoku(d, hi, lo):
    h, l, c = d["h"], d["l"], d["c"]
    n = len(c)
    if n < 80:
        return []
    mid = lambda a, b: (h[a:b].max() + l[a:b].min()) / 2
    tenkan, kijun = mid(n - 9, n), mid(n - 26, n)
    i = n - 26                                     # облако сдвинуто на 26 свечей вперёд
    span_a = (mid(i - 8, i + 1) + mid(i - 25, i + 1)) / 2
    span_b = mid(i - 51, i + 1)
    top, bot = max(span_a, span_b), min(span_a, span_b)
    if c[-1] > top:
        return [(15, "Ichimoku: цена выше облака, Tenkan выше Kijun")] if tenkan > kijun else [(6, "Ichimoku: цена выше облака")]
    if c[-1] < bot:
        return [(-15, "Ichimoku: цена ниже облака, Tenkan ниже Kijun")] if tenkan < kijun else [(-6, "Ichimoku: цена ниже облака")]
    return [(0, "Ichimoku: цена внутри облака, направления нет")]


def ind_donchian(d, hi, lo):
    h, l, c = d["h"], d["l"], d["c"]
    if c[-1] > h[-21:-1].max():
        return [(10, "Дончиан: пробой максимума 20 свечей")]
    if c[-1] < l[-21:-1].min():
        return [(-10, "Дончиан: пробой минимума 20 свечей")]
    return []


def ind_fib(d, hi, lo):
    if not hi or not lo:
        return []
    h, l, c = d["h"], d["l"], d["c"]
    n, a = len(c), atr(d)
    ih, il = hi[-1], lo[-1]
    if il < ih and n - 1 - ih <= 60:               # было движение вверх, идёт откат
        rng = h[ih] - l[il]
        if rng >= 2 * a and l[il] < c[-1] < h[ih]:
            for r in (0.382, 0.5, 0.618):
                if abs(c[-1] - (h[ih] - r * rng)) <= 0.3 * a:
                    return [(10, f"Фибоначчи: откат к {r * 100:.1f}% после роста")]
    elif ih < il and n - 1 - il <= 60:             # было движение вниз, идёт отскок
        rng = h[ih] - l[il]
        if rng >= 2 * a and l[il] < c[-1] < h[ih]:
            for r in (0.382, 0.5, 0.618):
                if abs(c[-1] - (l[il] + r * rng)) <= 0.3 * a:
                    return [(-10, f"Фибоначчи: отскок к {r * 100:.1f}% после падения")]
    return []


def support_resistance(d, hi, lo):
    """Только уровни минимум с двумя касаниями: слабые не влияют на балл (график показывает те же)."""
    a = atr(d)
    price = d["c"][-1]
    clusters = sorted(p for p, t in _levels(d, hi, lo) if t >= 2)
    sup = [x for x in clusters if x <= price]
    res = [x for x in clusters if x > price]
    out = []
    if sup and price - sup[-1] <= 0.7 * a:
        out.append((15, f"Цена у поддержки {sup[-1]:.5g}"))
    if res and res[0] - price <= 0.7 * a:
        out.append((-15, f"Цена у сопротивления {res[0]:.5g}"))
    return out


def _levels(d, hi, lo):
    """Уровни как кластеры экстремумов: список (цена, число касаний)."""
    a = atr(d)
    pts = sorted([d["h"][i] for i in hi] + [d["l"][i] for i in lo])
    out, cur = [], []
    for lv in pts:
        if cur and lv - cur[-1] > 0.5 * a:
            out.append((float(np.mean(cur)), len(cur)))
            cur = []
        cur.append(lv)
    if cur:
        out.append((float(np.mean(cur)), len(cur)))
    return out


def breakouts(d, hi, lo):
    """Пробой уровня (по закрытию), ретест пробитого уровня, ложный пробой."""
    o, h, l, c = d["o"], d["h"], d["l"], d["c"]
    a = atr(d)
    price = c[-1]
    body = abs(c[-1] - o[-1])
    pad = 0.15 * a
    found = []
    for L, touches in _levels(d, hi, lo):
        if touches < 2:
            continue
        name = f"{L:.5g}"
        near = abs(price - L) <= 3 * a
        # ретест: уровень пробит 3-12 свечей назад, цена вернулась и отскочила
        retest = False
        if near and len(c) > 16:
            if min(c[-14:-6]) < L and max(c[-12:-3]) > L + pad and l[-1] <= L + 0.3 * a and c[-1] > L and c[-1] > o[-1]:
                found.append((25, f"Ретест {name}: бывшее сопротивление держит как поддержка"))
                retest = True
            if max(c[-14:-6]) > L and min(c[-12:-3]) < L - pad and h[-1] >= L - 0.3 * a and c[-1] < L and c[-1] < o[-1]:
                found.append((-25, f"Ретест {name}: бывшая поддержка держит как сопротивление"))
                retest = True
        if retest:
            continue
        # ложный пробой: свеча открылась по эту сторону, тень за уровнем, закрытие обратно
        if h[-1] > L + pad and c[-1] < L and o[-1] < L:
            found.append((-18, f"Ложный пробой сопротивления {name}"))
            continue
        if l[-1] < L - pad and c[-1] > L and o[-1] > L:
            found.append((18, f"Ложный пробой поддержки {name}"))
            continue
        # пробой: прошлые закрытия по одну сторону, последнее по другую
        if c[-1] > L + pad and min(c[-3:-1]) <= L:
            strong = body >= 0.8 * a
            found.append((20 if strong else 10, f"Пробой сопротивления {name} вверх" + ("" if strong else " (слабая свеча)")))
        elif c[-1] < L - pad and max(c[-3:-1]) >= L:
            strong = body >= 0.8 * a
            found.append((-20 if strong else -10, f"Пробой поддержки {name} вниз" + ("" if strong else " (слабая свеча)")))
    found.sort(key=lambda x: -abs(x[0]))
    return found[:2]


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


def rsi_series(c, n=14):
    d = np.diff(c, prepend=c[0])
    up, dn = ewm(np.clip(d, 0, None), 1 / n), ewm(np.clip(-d, 0, None), 1 / n)
    out = 100 - 100 / (1 + up / np.where(dn == 0, np.nan, dn))
    return np.where(np.isnan(out), 100.0, out)


def atr_series(d, n=14):
    pc = np.r_[d["c"][0], d["c"][:-1]]
    tr = np.maximum.reduce([d["h"] - d["l"], np.abs(d["h"] - pc), np.abs(d["l"] - pc)])
    return ewm(tr, 1 / n)


def divergences(d, hi, lo):
    """Дивергенции цены с RSI и гистограммой MACD по двум последним экстремумам."""
    c, h, l = d["c"], d["h"], d["l"]
    n = len(c)
    r = rsi_series(c)
    macd = ema(c, 12) - ema(c, 26)
    hist = macd - ema(macd, 9)
    ok = lambda i, j: n - 1 - j <= 30 and 5 <= j - i <= 60
    out = []
    if len(lo) >= 2 and ok(lo[-2], lo[-1]):
        i, j = lo[-2], lo[-1]
        if l[j] < l[i]:
            if r[j] > r[i] + 2:
                out.append((15, "Бычья дивергенция RSI (цена ниже, RSI выше)"))
            if hist[j] > hist[i]:
                out.append((12, "Бычья дивергенция MACD"))
    if len(hi) >= 2 and ok(hi[-2], hi[-1]):
        i, j = hi[-2], hi[-1]
        if h[j] > h[i]:
            if r[j] < r[i] - 2:
                out.append((-15, "Медвежья дивергенция RSI (цена выше, RSI ниже)"))
            if hist[j] < hist[i]:
                out.append((-12, "Медвежья дивергенция MACD"))
    return out


def volume_confirm(d):
    """Объём есть не везде (у Forex и OTC его нет), тогда проверка пропускается."""
    v = d.get("v")
    if v is None or len(v) < 25 or not np.any(v > 0):
        return []
    avg = v[-21:-1].mean()
    if avg <= 0 or v[-1] < 1.8 * avg:
        return []
    if d["c"][-1] > d["o"][-1]:
        return [(8, "Всплеск объёма подтверждает рост")]
    if d["c"][-1] < d["o"][-1]:
        return [(-8, "Всплеск объёма подтверждает падение")]
    return []


# ключ: (название, группа, макс. баллов, функция(d, hi, lo), описание, где работает)
INDICATORS = {
    "ema": ("EMA 20/50", "Тренд", 25, ind_ema, "Две скользящие: направление и сила тренда.", "тренд"),
    "ema_cross": ("EMA 9/21", "Тренд", 12, ind_ema_cross, "Быстрое пересечение средних: ранний сигнал смены движения.", "тренд"),
    "adx": ("ADX/DI", "Тренд", 12, ind_adx, "Показывает, есть ли тренд вообще и в какую сторону.", "оба"),
    "psar": ("Parabolic SAR", "Тренд", 10, ind_psar, "Точки под/над ценой, разворот точек даёт сигнал.", "тренд"),
    "supertrend": ("Supertrend", "Тренд", 15, ind_supertrend, "Линия на основе ATR, чёткие развороты тренда.", "тренд"),
    "ichimoku": ("Ichimoku", "Тренд", 15, ind_ichimoku, "Облако, Tenkan и Kijun: тренд и зоны поддержки.", "тренд"),
    "rsi": ("RSI 14", "Импульс", 10, ind_rsi, "Перекупленность и перепроданность.", "боковик"),
    "stoch": ("Стохастик", "Импульс", 10, ind_stoch, "Разворот осциллятора в крайних зонах.", "боковик"),
    "cci": ("CCI 20", "Импульс", 8, ind_cci, "Выход из зон перекупленности и перепроданности.", "боковик"),
    "macd": ("MACD", "Импульс", 10, ind_macd, "Рост или спад импульса движения.", "тренд"),
    "diverg": ("Дивергенции", "Импульс", 20, divergences, "Цена и RSI/MACD расходятся: возможный разворот.", "оба"),
    "boll": ("Боллинджер", "Волатильность", 8, ind_boll, "Выход цены за границы нормального диапазона.", "боковик"),
    "donchian": ("Дончиан 20", "Волатильность", 10, ind_donchian, "Пробой максимума или минимума 20 свечей.", "тренд"),
    "fib": ("Фибоначчи", "Уровни", 10, ind_fib, "Откат к 38,2/50/61,8% последнего движения.", "тренд"),
    "volume": ("Объём", "Объём", 8, lambda d, hi, lo: volume_confirm(d), "Всплеск объёма подтверждает свечу (есть не у всех активов).", "оба"),
}
DEFAULT_INDICATORS = ["ema", "rsi", "macd", "boll", "diverg", "volume"]

# готовые сочетания: общие правила технического анализа, на истории НЕ проверены
PRESETS = [
    {"name": "Тренд + импульс", "ind": ["ema", "macd", "rsi"], "methods": ["tech"],
     "text": "Тренд задаёт направление, RSI и MACD подсказывают момент входа. Классика для трендового рынка."},
    {"name": "Сильный тренд", "ind": ["ema", "adx", "supertrend"], "methods": ["tech", "breakout"],
     "text": "ADX фильтрует боковик, Supertrend и EMA ведут по тренду. Хуже работает на боковом рынке."},
    {"name": "Боковик и отскоки", "ind": ["boll", "stoch", "rsi"], "methods": ["tech", "levels", "candles"],
     "text": "Ловит отскоки от границ диапазона вместе с уровнями и свечами. В сильном тренде даёт много ложных."},
    {"name": "Откат по тренду", "ind": ["ema", "fib", "stoch"], "methods": ["tech", "levels", "candles"],
     "text": "Вход в сторону тренда после отката к уровню Фибоначчи и развороту осциллятора."},
    {"name": "Пробой", "ind": ["donchian", "adx", "volume"], "methods": ["tech", "breakout"],
     "text": "Пробой диапазона при подтверждении силы тренда. Ложные пробои отсеивает метод «Пробой»."},
    {"name": "Разворот", "ind": ["diverg", "rsi", "stoch"], "methods": ["tech", "levels", "candles", "patterns"],
     "text": "Дивергенции, перекупленность и разворотные свечи и фигуры у сильных уровней. Редкие, но осторожные сигналы."},
    {"name": "Ichimoku-набор", "ind": ["ichimoku", "macd", "adx"], "methods": ["tech"],
     "text": "Облако показывает тренд и зоны, MACD и ADX подтверждают его силу."},
]


def advise(selected):
    """Подсказки по выбранным индикаторам: [(kind, text)], kind = ok / warn / tip."""
    sel = [k for k in selected if k in INDICATORS]
    if not sel:
        return [("warn", "Не выбрано ни одного индикатора: метод «Технический» ничего не даст.")]
    groups = {}
    for k in sel:
        groups.setdefault(INDICATORS[k][1], []).append(INDICATORS[k][0])
    out = []
    trend, mom = groups.get("Тренд", []), groups.get("Импульс", [])
    if not trend:
        out.append(("warn", "Нет индикатора тренда: сигналы могут идти против общего движения."))
    if len(mom) >= 3:
        out.append(("warn", f"Осцилляторы ({', '.join(mom)}) считаются по похожим данным и дублируют друг друга: это не независимые подтверждения."))
    if len(trend) >= 4:
        out.append(("warn", f"Трендовых индикаторов много ({len(trend)}), они почти всегда совпадают и не добавляют информации."))
    if trend and mom and "adx" not in sel:
        out.append(("tip", "Совет: на боковом рынке трендовые индикаторы и осцилляторы противоречат друг другу. ADX покажет, есть ли тренд."))
    if "volume" in sel:
        out.append(("warn", "Объём есть не у всех активов: у Форекс и OTC его нет, проверка будет пропущена."))
    if len(sel) > 8:
        out.append(("warn", "Индикаторов много: растёт шанс случайных совпадений. Обычно хватает 3-5 из разных групп."))
    if trend and mom and (groups.get("Волатильность") or "adx" in sel) and len(sel) <= 8 and not any(k == "warn" for k, _ in out):
        out.append(("ok", "Хорошая структура: есть тренд, импульс и фильтр рынка (волатильность или ADX)."))
    if not out:
        out.append(("ok", "Замечаний нет."))
    return out


def chart_data(d, hi, lo, n=60):
    L = len(d["c"])
    s = max(0, L - n)
    levels = [round(p, 8) for p, t in _levels(d, hi, lo) if t >= 2]
    return {"o": d["o"][s:].tolist(), "h": d["h"][s:].tolist(), "l": d["l"][s:].tolist(),
            "c": d["c"][s:].tolist(), "levels": levels,
            "hp": [(i - s, float(d["h"][i])) for i in hi if i >= s],
            "lp": [(i - s, float(d["l"][i])) for i in lo if i >= s]}


def preview(sym, interval):
    """Лёгкая загрузка для графика сразу после выбора пары (без анализа и фильтров)."""
    d = fetch(sym, interval)
    if len(d["c"]) < 20:
        raise RuntimeError("Недостаточно свечей для графика")
    hi, lo = pivots(d)
    try:
        ch = chart_data(d, hi, lo)
    except Exception:
        ch = {"o": d["o"][-60:].tolist(), "h": d["h"][-60:].tolist(), "l": d["l"][-60:].tolist(),
              "c": d["c"][-60:].tolist(), "levels": [], "hp": [], "lp": []}
    return {"chart": ch, "price": float(d["c"][-1])}


def analyze(d, threshold=35, blackout=None, methods=None, weights=None, indicators=None):
    """methods: набор ключей из METHODS (None = все); weights: множители методов;
    indicators: ключи из INDICATORS для метода «Технический» (None = набор по умолчанию).
    Возвращает dict: kind (call/put/wait/pause), signal, score, conf, parts, price, thr, chart."""
    methods = set(methods) if methods is not None else set(ALL_METHODS)
    weights = weights or {}
    ind_keys = [k for k in (indicators if indicators is not None else DEFAULT_INDICATORS) if k in INDICATORS]
    if len(d["c"]) < 80:
        return None
    price = float(d["c"][-1])
    directional = {m for m in methods & {"tech", "levels", "candles", "patterns", "breakout"}
                   if weights.get(m, 1.0) > 0}
    if "tech" in directional and not ind_keys:
        directional.discard("tech")
    if not directional:
        if "news" in methods and not methods & {"tech", "levels", "candles", "patterns", "breakout"}:
            msg = "Фундаментал сам направление не даёт: он только блокирует сигналы в окна новостей. Добавьте ещё один метод."
        elif "tech" in methods and not ind_keys:
            msg = "Для метода «Технический» не выбрано ни одного индикатора."
        else:
            msg = "Выберите хотя бы один метод анализа."
        return {"kind": "wait", "signal": "ВЫБЕРИТЕ МЕТОД", "score": 0, "conf": 0,
                "parts": [(0, msg)], "price": price, "thr": 0, "chart": None}
    d = {k: v[-150:] for k, v in d.items()}
    hi, lo = pivots(d)
    parts, other_sum, tech_sum = [], 0, 0

    def add(key, items):
        w = weights.get(key, 1.0)
        items = [(p if w == 1.0 else int(round(p * w)), t) for p, t in items]
        parts.extend(items)
        return sum(p for p, _ in items)

    if "tech" in directional:
        items = []
        for k in ind_keys:
            items += INDICATORS[k][3](d, hi, lo)
        lim = 65 * max(1.0, weights.get("tech", 1.0))
        tech_sum = max(-lim, min(lim, add("tech", items)))      # много индикаторов не заглушают остальные методы
    if "levels" in directional:
        other_sum += add("levels", support_resistance(d, hi, lo))
    if "breakout" in directional:
        other_sum += add("breakout", breakouts(d, hi, lo))
    if "candles" in directional:
        other_sum += add("candles", candle_patterns(d))
    if "patterns" in directional:
        other_sum += add("patterns", chart_patterns(d, hi, lo))

    caps = {m: METHODS[m][1] for m in directional}
    if "tech" in directional:
        caps["tech"] = min(65, sum(INDICATORS[k][2] for k in ind_keys))
    cap = sum(caps[m] * weights.get(m, 1.0) for m in directional)
    total_cap = sum(v[1] for v in METHODS.values())
    thr = max(10, round(threshold * cap / total_cap))
    score = int(max(-100, min(100, other_sum + tech_sum)))
    bull = sum(1 for p, _ in parts if p > 0)
    bear = sum(1 for p, _ in parts if p < 0)
    agree = max(bull, bear) / max(bull + bear, 1)
    conf = int(min(95, min(abs(score) / (cap * 0.7), 1) * 75 + agree * 20))
    kind, signal = ("call", "CALL (покупка)") if score >= thr else \
                   ("put", "PUT (продажа)") if score <= -thr else ("wait", "ЖДАТЬ")
    # при двух и более источниках сигнал нужно подтвердить минимум двумя факторами в одну сторону
    sources = (len(ind_keys) if "tech" in directional else 0) + len(directional - {"tech"})
    if kind in ("call", "put") and sources >= 2 and (bull if kind == "call" else bear) < 2:
        kind, signal = "wait", "ЖДАТЬ"
        parts.append((0, "Баллов хватает, но подтверждающий фактор только один, сигнал не выдан"))
    if "news" in methods:
        w = in_blackout(blackout)
        if w and kind != "wait":
            kind, signal = "pause", f"ПАУЗА: новости {w} UTC"
    return {"kind": kind, "signal": signal, "score": score, "conf": conf, "parts": parts,
            "price": price, "thr": thr, "chart": chart_data(d, hi, lo)}


# ───────── фильтры, старший таймфрейм, экспирация ─────────
SESSION_WINDOWS = [("06:55", "07:20", "открытие Лондона"), ("07:55", "08:20", "открытие Лондона"),
                   ("12:55", "13:20", "открытие Нью-Йорка"), ("13:25", "13:50", "открытие Нью-Йорка"),
                   ("14:25", "14:50", "открытие американских бирж"), ("21:55", "22:30", "ролловер, спреды шире")]
EXP_STEPS = [1, 2, 3, 5, 10, 15, 30, 60, 240]


def in_session_window(now=None):
    t = (now or now_utc()).time()
    for a, b, name in SESSION_WINDOWS:
        ta, tb = (dt.datetime.strptime(x, "%H:%M").time() for x in (a, b))
        if ta <= t <= tb:
            return f"{name} ({a}-{b} UTC)"
    return None


def vol_ratio(d):
    """Текущая волатильность (ATR) относительно обычной за последние 150 свечей."""
    a = atr_series(d)[-150:]
    med = float(np.median(a))
    return float(a[-1] / med) if med > 0 else None


def htf_interval(sym, interval):
    return {"1m": "5m", "2m": "10m", "3m": "15m", "5m": "15m", "10m": "30m", "15m": "1h", "30m": "1h",
            "1h": "4h" if is_otc(sym) else "1d"}.get(interval)


def htf_trend(sym, htf):
    d = fetch(sym, htf)
    c = d["c"]
    if len(c) < 60:
        return "flat"
    e20, e50 = ema(c, 20), ema(c, 50)
    if c[-1] > e20[-1] > e50[-1]:
        return "up"
    if c[-1] < e20[-1] < e50[-1]:
        return "down"
    return "flat"


def expiry_hint(interval, conf):
    """Ориентир: сильнее сигнал, тем короче срок. Это эвристика, а не проверенное правило."""
    tf_min = TF_SECONDS.get(interval, 300) / 60
    n = 1 if conf >= 75 else 2 if conf >= 55 else 3
    m = min(EXP_STEPS, key=lambda x: abs(x - tf_min * n))
    return m, n


def fmt_minutes(m):
    return f"{m} мин" if m < 60 else f"{m // 60} ч"


def analyze_symbol(sym, interval, threshold=35, blackout=None, methods=None, weights=None, opts=None, indicators=None):
    """Загрузка данных, анализ, фильтры (волатильность, сессии, старший ТФ), экспирация."""
    opts = opts if opts is not None else {}
    d = fetch(sym, interval)
    res = analyze(d, threshold, blackout, methods, weights, indicators)
    if not res or res["kind"] not in ("call", "put"):
        return res
    direction = "CALL" if res["kind"] == "call" else "PUT"

    def block(kind, signal, note):
        res["kind"], res["signal"] = kind, signal
        res["parts"].append((0, note))

    if opts.get("vol", True):
        ratio = vol_ratio(d)
        if ratio is not None and ratio < 0.5:
            block("pause", "ПАУЗА: узкий рынок", f"Фильтр волатильности: ATR всего {ratio:.1f} от обычного, сигнал {direction} не выдан")
    if res["kind"] in ("call", "put") and opts.get("session", True):
        w = in_session_window()
        if w:
            block("pause", "ПАУЗА: " + w.split(" (")[0], f"Фильтр времени: {w}, сигнал {direction} не выдан")
    if res["kind"] in ("call", "put") and opts.get("mtf", True):
        htf = htf_interval(sym, interval)
        if htf:
            try:
                tr = htf_trend(sym, htf)
            except Exception:
                tr = None
                res["parts"].append((0, f"Старший ТФ {htf}: данные недоступны, проверка пропущена"))
            if tr:
                names = {"up": "восходящий", "down": "нисходящий", "flat": "без явного тренда"}
                against = (tr == "up" and res["kind"] == "put") or (tr == "down" and res["kind"] == "call")
                if against:
                    block("wait", f"ЖДАТЬ: против тренда {htf}", f"Старший ТФ {htf}: {names[tr]}, сигнал {direction} отменён")
                else:
                    res["parts"].append((0, f"Старший ТФ {htf}: {names[tr]}, не противоречит сигналу"))
    if res["kind"] in ("call", "put"):
        m, n = expiry_hint(interval, res["conf"])
        fixed = opts.get("exp")
        if fixed:
            m = int(fixed)
            n = max(1, round(m * 60 / TF_SECONDS.get(interval, 300)))
            res["exp_min"], res["exp_candles"] = m, n
            note = ""
            if m * 60 > 6 * TF_SECONDS.get(interval, 300):
                note = ". Срок намного длиннее свечи: точность ниже, лучше взять свечу покрупнее"
            res["exp_text"] = f"Экспирация (ваша): {fmt_minutes(m)} ({n} {'свеча' if n == 1 else 'свечи' if n < 5 else 'свечей'}){note}"
        else:
            res["exp_min"], res["exp_candles"] = m, n
            res["exp_text"] = f"Экспирация ориентир: {fmt_minutes(m)} ({n} {'свеча' if n == 1 else 'свечи'})"
    return res


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
    "OTC Pocket Option": [
        "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "USDCHF_otc", "USDCAD_otc", "AUDUSD_otc",
        "EURGBP_otc", "EURJPY_otc", "EURCHF_otc", "GBPJPY_otc", "GBPAUD_otc",
        "AUDCAD_otc", "AUDCHF_otc", "AUDJPY_otc", "USDMXN_otc", "USDINR_otc",
        "USDPHP_otc", "USDPKR_otc", "USDBRL_otc",
        "BTCUSD_otc", "ETHUSD_otc", "XAUUSD_otc", "XAGUSD_otc", "UKBrent_otc", "USCrude_otc"],
}
FOREX_ALL = GROUPS["Форекс: основные"] + GROUPS["Форекс: кроссы"] + GROUPS["Форекс: экзотика"]

OTC_ALL = GROUPS["OTC Pocket Option"]
