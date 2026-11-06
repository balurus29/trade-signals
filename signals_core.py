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


def _fetch_yahoo(sym, interval="5m", rng=None):
    rng = rng or ("1y" if interval == "1d" else "1mo" if interval in ("1h", "60m") else "5d")
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
TF_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}
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


def chart_data(d, hi, lo, n=60):
    L = len(d["c"])
    s = max(0, L - n)
    levels = [round(p, 8) for p, t in _levels(d, hi, lo) if t >= 2]
    return {"o": d["o"][s:].tolist(), "h": d["h"][s:].tolist(), "l": d["l"][s:].tolist(),
            "c": d["c"][s:].tolist(), "levels": levels,
            "hp": [(i - s, float(d["h"][i])) for i in hi if i >= s],
            "lp": [(i - s, float(d["l"][i])) for i in lo if i >= s]}


def analyze(d, threshold=35, blackout=None, methods=None, weights=None):
    """methods: набор ключей из METHODS (None = все); weights: множители методов (по умолчанию 1).
    Возвращает dict: kind (call/put/wait/pause), signal, score, conf, parts, price, thr, chart."""
    methods = set(methods) if methods is not None else set(ALL_METHODS)
    weights = weights or {}
    if len(d["c"]) < 80:
        return None
    price = float(d["c"][-1])
    directional = {m for m in methods & {"tech", "levels", "candles", "patterns", "breakout"}
                   if weights.get(m, 1.0) > 0}
    if not directional:
        msg = ("Фундаментал сам направление не даёт: он только блокирует сигналы в окна новостей. "
               "Добавьте ещё один метод." if "news" in methods else "Выберите хотя бы один метод анализа.")
        return {"kind": "wait", "signal": "ВЫБЕРИТЕ МЕТОД", "score": 0, "conf": 0,
                "parts": [(0, msg)], "price": price, "thr": 0, "chart": None}
    d = {k: v[-150:] for k, v in d.items()}
    hi, lo = pivots(d)
    parts = []

    def add(key, items):
        w = weights.get(key, 1.0)
        parts.extend((p if w == 1.0 else int(round(p * w)), t) for p, t in items)

    if "tech" in directional:
        add("tech", trend_and_momentum(d) + divergences(d, hi, lo) + volume_confirm(d))
    if "levels" in directional:
        add("levels", support_resistance(d, hi, lo))
    if "breakout" in directional:
        add("breakout", breakouts(d, hi, lo))
    if "candles" in directional:
        add("candles", candle_patterns(d))
    if "patterns" in directional:
        add("patterns", chart_patterns(d, hi, lo))

    cap = sum(METHODS[m][1] * weights.get(m, 1.0) for m in directional)
    total_cap = sum(v[1] for v in METHODS.values())
    thr = max(10, round(threshold * cap / total_cap))
    score = int(max(-100, min(100, sum(p for p, _ in parts))))
    bull = sum(1 for p, _ in parts if p > 0)
    bear = sum(1 for p, _ in parts if p < 0)
    agree = max(bull, bear) / max(bull + bear, 1)
    conf = int(min(95, min(abs(score) / (cap * 0.7), 1) * 75 + agree * 20))
    kind, signal = ("call", "CALL (покупка)") if score >= thr else \
                   ("put", "PUT (продажа)") if score <= -thr else ("wait", "ЖДАТЬ")
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
    return {"1m": "5m", "5m": "15m", "15m": "1h", "1h": "4h" if is_otc(sym) else "1d"}.get(interval)


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


def analyze_symbol(sym, interval, threshold=35, blackout=None, methods=None, weights=None, opts=None):
    """Загрузка данных, анализ, фильтры (волатильность, сессии, старший ТФ), экспирация."""
    opts = opts if opts is not None else {}
    d = fetch(sym, interval)
    res = analyze(d, threshold, blackout, methods, weights)
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
