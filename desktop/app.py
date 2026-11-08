"""Trade Signals для компьютера (Windows). Tkinter, ничего ставить не нужно, кроме numpy и certifi."""
import json
import os
import queue
import threading
import time
import tkinter as tk

import journal
import martingale
import risk
import signals_core as core
from ui import (AMBER, BG, BORDER, CARD, CYAN, DIM, FG, FIELD, FONT, GREEN, PANEL, RED, VIOLET, Btn, Card, Field,
                Label, Select, fit_size, mix, text_w)

try:
    import winsound
except ImportError:
    winsound = None

HOME = os.path.join(os.path.expanduser("~"), "TradeSignals")
os.makedirs(HOME, exist_ok=True)
SET_PATH = os.path.join(HOME, "settings.json")
JRN_PATH = os.path.join(HOME, "journal.json")
CAT_PATH = os.path.join(HOME, "otc_catalog.json")
EXP_VALUES = ["Авто", "1 мин", "2 мин", "3 мин", "5 мин", "10 мин", "15 мин", "30 мин", "60 мин"]
SCOPES = ["Текущий список", "Все OTC", "Избранное"]
METHOD_COLORS = {"tech": CYAN, "levels": AMBER, "breakout": "#ff8a3d", "candles": RED, "patterns": VIOLET, "news": GREEN}
DEFAULTS = {"key": "", "thr": 35, "interval": "5m", "group": "OTC Pocket Option", "pair": "", "pmin": "", "pmax": "",
            "scope": SCOPES[0], "sound": True, "blackout": "", "mtf": True, "vol": True, "session": True,
            "exp": "Авто", "methods": ["tech", "levels", "candles", "patterns", "breakout", "news"],
            "indicators": list(core.DEFAULT_INDICATORS), "risk": dict(risk.DEFAULT), "favs": [],
            "mg": {"dep": "1000", "first": "10", "pay": "92", "steps": "5", "mode": "Возврат убытка"}}


def beep(kind):
    def go():
        try:
            if winsound:
                winsound.Beep(1300 if kind == "call" else 650, 260)
        except Exception:
            pass
    threading.Thread(target=go, daemon=True).start()


def num(s):
    try:
        return float(str(s).replace(",", ".").strip())
    except ValueError:
        return None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trade Signals 2.2")
        self.geometry("1560x900")
        self.minsize(1200, 700)
        self.configure(bg=BG)
        self.s = json.loads(json.dumps(DEFAULTS))
        try:
            with open(SET_PATH, encoding="utf-8") as f:
                saved = json.load(f)
            for k, v in saved.items():
                if isinstance(v, dict) and isinstance(self.s.get(k), dict):
                    self.s[k].update(v)
                else:
                    self.s[k] = v
        except Exception:
            pass
        core.OTC_KEY = self.s["key"]
        self.entries = journal.load(JRN_PATH)
        self.last_sig = {}
        self.results = {}
        self.busy = False
        self.cur = None
        self.res = None
        self.sig_info = None
        self.chart = None
        self.auto_k = None
        self.pairs = []
        self.q = queue.Queue()
        self._pv_id = None
        self._load_catalog_cache()
        self.build()
        self.bind("<F5>", lambda e: self.analyze())
        self.bind("<F6>", lambda e: self.scan())
        self.bind("<Control-j>", lambda e: self.tab("Журнал"))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh_groups()
        self.fill_pairs()
        self.after(100, self.poll)
        self.after(1000, self.tick)
        self.after(15000, self.settle_due)
        self.after(3000, self.refresh_payouts)

    # ───────── потоки ─────────
    def ui(self, fn):
        self.q.put(fn)

    def poll(self):
        try:
            while True:
                self.q.get_nowait()()
        except queue.Empty:
            pass
        except Exception as ex:
            self.say(f"Ошибка интерфейса: {ex}")
        self.after(100, self.poll)

    def bg(self, work):
        threading.Thread(target=work, daemon=True).start()

    # ───────── настройки ─────────
    def save(self):
        try:
            with open(SET_PATH, "w", encoding="utf-8") as f:
                json.dump(self.s, f, ensure_ascii=False)
        except Exception:
            pass

    def collect(self):
        s = self.s
        s["key"] = self.e_key.get().strip()
        t = num(self.e_thr.get())
        s["thr"] = int(t) if t is not None else 35
        s["blackout"] = self.e_black.get()
        s["risk"] = {"on": self.b_risk.active, **{k: self.e_risk[k].get() for k in self.e_risk}}
        s["pmin"], s["pmax"] = self.e_pmin.get(), self.e_pmax.get()
        s["interval"], s["group"], s["exp"], s["scope"] = (self.sel_itv.get(), self.sel_group.get(),
                                                           self.sel_exp.get(), self.sel_scope.get())
        s["mg"] = {**{k: self.e_mg[k].get() for k in self.e_mg}, "mode": self.sel_mode.get()}
        s["pair"] = self.cur or ""
        core.OTC_KEY = s["key"]

    def close(self):
        try:
            self.collect()
            self.save()
        finally:
            self.destroy()

    def _load_catalog_cache(self):
        try:
            with open(CAT_PATH, encoding="utf-8") as f:
                c = json.load(f)
            core.apply_otc_catalog(c["symbols"])
            core.PAYOUTS.update(c.get("payout", {}))
        except Exception:
            pass

    # ───────── интерфейс ─────────
    def build(self):
        self.columnconfigure(0, weight=0, minsize=350)
        self.columnconfigure(1, weight=1)
        self.columnconfigure(2, weight=0, minsize=450)
        self.rowconfigure(0, weight=1)
        left = tk.Frame(self, bg=BG)
        mid = tk.Frame(self, bg=BG)
        right = tk.Frame(self, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(10, 5), pady=10)
        mid.grid(row=0, column=1, sticky="nsew", padx=5, pady=10)
        right.grid(row=0, column=2, sticky="nsew", padx=(5, 10), pady=10)
        self.build_left(left)
        self.build_mid(mid)
        self.build_right(right)
        self.right_w = 450
        self.bind("<Configure>", self.on_resize)

    def on_resize(self, e):
        """В узком окне правая колонка уступает место графику (от 360 до 450 пикселей)."""
        if e.widget is not self:
            return
        w = max(360, min(450, int(e.width * 0.29)))
        if w != self.right_w:
            self.right_w = w
            self.columnconfigure(2, minsize=w)
            self.lbl_preset.configure(wraplength=w - 40)

    def build_left(self, f):
        Label(f, "TRADE SIGNALS", CYAN, 15, True).pack(anchor="w", pady=(0, 8))
        self.sel_group = Select(f, list(core.GROUPS) + ["Избранное"], self.s["group"], "Группа инструментов",
                                lambda v: self.fill_pairs(), accent=VIOLET, height=38)
        self.sel_group.pack(fill="x", pady=3)
        r = tk.Frame(f, bg=BG)
        r.pack(fill="x", pady=3)
        r.columnconfigure(1, weight=1)
        self.sel_itv = Select(r, list(core.TF_LIST), self.s["interval"], "Время свечи", lambda v: self.load_preview(),
                              accent=AMBER, width=96, height=38)
        self.sel_itv.grid(row=0, column=0, padx=(0, 5))
        self.sel_exp = Select(r, EXP_VALUES, self.s["exp"] if self.s["exp"] in EXP_VALUES else "Авто",
                              "Экспирация сигнала (Авто — подбирает приложение)", None, accent=GREEN, height=38)
        self.sel_exp.grid(row=0, column=1, sticky="ew", padx=(0, 5))
        self.b_fav = Btn(r, "☆", self.toggle_fav, accent=AMBER, width=42, height=38, size=15)
        self.b_fav.grid(row=0, column=2, padx=(0, 5))
        self.b_all = Btn(r, "Все", self.all_methods, accent=FG, width=48, height=38)
        self.b_all.grid(row=0, column=3)
        Label(f, "Экспирация: «Авто» подбирает срок сама, или выберите свой", DIM, 9).pack(anchor="w")

        mf = tk.Frame(f, bg=BG)
        mf.pack(fill="x", pady=(6, 3))
        self.mchips = {}
        for i, (k, (name, _w)) in enumerate(core.METHODS.items()):
            b = Btn(mf, name, lambda a, k=k: self.on_method(), accent=METHOD_COLORS[k], toggle=True,
                    active=k in self.s["methods"], height=32, size=10)
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=2, pady=2)
            self.mchips[k] = b
        for c in range(3):
            mf.columnconfigure(c, weight=1)

        pf = tk.Frame(f, bg=BG)
        pf.pack(fill="x", pady=(4, 2))
        Label(pf, "выплата от", DIM, 10).pack(side="left")
        self.e_pmin = Field(pf, self.s["pmin"], 4)
        self.e_pmin.pack(side="left", padx=4)
        Label(pf, "до", DIM, 10).pack(side="left")
        self.e_pmax = Field(pf, self.s["pmax"], 4)
        self.e_pmax.pack(side="left", padx=4)
        Label(pf, "%", DIM, 10).pack(side="left")
        for e in (self.e_pmin, self.e_pmax):
            e.bind("<KeyRelease>", lambda ev: self.fill_pairs())

        lf = Card(f)
        lf.pack(fill="both", expand=True, pady=6)
        self.lb = tk.Listbox(lf, bg=CARD, fg=FG, selectbackground=mix(CARD, CYAN, 0.45), selectforeground=FG,
                             highlightthickness=0, bd=0, activestyle="none", exportselection=False,
                             font=("Consolas", 11))
        sb = tk.Scrollbar(lf, command=self.lb.yview)
        self.lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.lb.pack(fill="both", expand=True, padx=4, pady=4)
        self.lb.bind("<<ListboxSelect>>", self.on_select)
        self.lb.bind("<Double-1>", lambda e: self.toggle_fav())

        sf = tk.Frame(f, bg=BG)
        sf.pack(fill="x")
        Label(sf, "Сканер:", DIM, 10).pack(side="left")
        self.sel_scope = Select(sf, SCOPES, self.s["scope"] if self.s["scope"] in SCOPES else SCOPES[0],
                                "Что сканировать", None, accent=VIOLET, height=30, size=10)
        self.sel_scope.pack(side="left", fill="x", expand=True, padx=6)
        bf = tk.Frame(f, bg=BG)
        bf.pack(fill="x", pady=(6, 2))
        self.b_an = Btn(bf, "Анализ  F5", self.analyze, accent=CYAN, height=44, size=12)
        self.b_sc = Btn(bf, "Сканер  F6", self.scan, accent=VIOLET, height=44, size=12)
        self.b_auto = Btn(bf, "Авто", self.on_auto, accent=GREEN, height=44, width=70, toggle=True, size=12)
        self.b_an.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.b_sc.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.b_auto.pack(side="left")
        self.status = Label(f, "", DIM, 10, wraplength=330, justify="left")
        self.status.pack(fill="x", anchor="w", pady=(4, 0))

    def build_mid(self, f):
        self.card = Card(f, BORDER)
        self.card.pack(fill="x")
        self.card.bind("<Configure>", lambda e: self.fit_sig())
        self.lbl_sig = Label(self.card, "Выберите пару и нажмите «Анализ»", FG, 26, True, bg=CARD)
        self.lbl_sig.pack(pady=(14, 4))
        self.lbl_info = Label(self.card, "", mix(FG, BG, 0.2), 11, bg=CARD, justify="center")
        self.lbl_info.pack()
        self.lbl_timer = Label(self.card, "", GREEN, 11, True, bg=CARD)
        self.lbl_timer.pack(pady=(4, 12))
        self.cv_box = Card(f, BORDER)
        self.cv_box.pack(fill="both", expand=True, pady=(10, 0))
        self.cv = tk.Canvas(self.cv_box, bg=CARD, highlightthickness=0, width=300, height=300)
        self.cv.pack(fill="both", expand=True, padx=2, pady=2)
        self.cv.bind("<Configure>", lambda e: self.draw())
        self.cv.bind("<Motion>", self.on_move)
        self.cv.bind("<Leave>", lambda e: self.cv.delete("cross"))

    def build_right(self, f):
        bar = tk.Frame(f, bg=BG)
        bar.pack(fill="x")
        self.pages, self.tabbtn = {}, {}
        names = [("Причины", CYAN), ("Журнал", GREEN), ("Индикаторы", VIOLET), ("Мартингейл", RED), ("Настройки", AMBER)]
        for i, (n, col) in enumerate(names):
            b = Btn(bar, n, lambda a, n=n: self.tab(n), accent=col, toggle=True, height=34, size=10)
            b.grid(row=0, column=i, sticky="ew", padx=2)
            bar.columnconfigure(i, weight=text_w(bar, n, 10) + 14)   # ширина вкладки по длине её названия
            self.tabbtn[n] = b
        self.body = tk.Frame(f, bg=BG)
        self.body.pack(fill="both", expand=True, pady=(8, 0))
        for n, _c in names:
            fr = Card(self.body)
            self.pages[n] = fr
        self.t_reason = self.textbox(self.pages["Причины"])
        self.page_journal(self.pages["Журнал"])
        self.page_ind(self.pages["Индикаторы"])
        self.page_mg(self.pages["Мартингейл"])
        self.page_settings(self.pages["Настройки"])
        self.tab("Причины")
        self.show_journal()

    def set_sig(self, text, fg):
        self.lbl_sig.configure(text=text, fg=fg)
        self.fit_sig()

    def fit_sig(self):
        """Заголовок сигнала уменьшается, чтобы не обрезаться в узком окне."""
        w = self.card.winfo_width() - 30
        if w < 50:
            return
        size = fit_size(self.lbl_sig, self.lbl_sig.cget("text"), 26, True, w, low=12)
        self.lbl_sig.configure(font=(FONT, size, "bold"))
        self.lbl_info.configure(wraplength=w)

    def tab(self, name):
        for n, fr in self.pages.items():
            fr.pack_forget()
            self.tabbtn[n].set(active=False)
        self.pages[name].pack(fill="both", expand=True)
        self.tabbtn[name].set(active=True)
        self.cur_tab = name

    def textbox(self, parent, font=None, height=None):
        t = tk.Text(parent, bg=CARD, fg=FG, relief="flat", wrap="word", font=font or (FONT, 11), state="disabled",
                    highlightthickness=0, padx=10, pady=8, cursor="arrow", width=10, **({"height": height} if height else {}))
        t.pack(fill="both", expand=True)
        t.tag_configure("pos", foreground=GREEN)
        t.tag_configure("neg", foreground=RED)
        t.tag_configure("dim", foreground=DIM)
        t.tag_configure("warn", foreground=AMBER)
        t.tag_configure("h", foreground=CYAN, font=(FONT, 13, "bold"))
        return t

    def put(self, t, rows):
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, tag in rows:
            t.insert("end", text + "\n", tag or ())
        t.configure(state="disabled")

    # ── журнал ──
    def page_journal(self, f):
        top = tk.Frame(f, bg=CARD)
        top.pack(fill="x", padx=8, pady=8)
        Btn(top, "Копировать CSV", self.copy_csv, accent=GREEN, height=30, size=10).pack(side="left")
        Btn(top, "Очистить", self.clear_journal, accent=RED, height=30, size=10, width=90).pack(side="left", padx=6)
        self.t_jrn = self.textbox(f)

    def copy_csv(self):
        rows = ["time;pair;tf;dir;exp;status;entry;exit"]
        for e in self.entries:
            rows.append(";".join(str(x) for x in (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"])),
                                                  e["sym"], e["itv"], e["dir"], e["exp"], e["status"], e["entry"], e["exit"])))
        self.clipboard_clear()
        self.clipboard_append("\n".join(rows))
        self.say("Журнал скопирован в буфер обмена (CSV)")

    def clear_journal(self):
        self.entries.clear()
        journal.save(JRN_PATH, self.entries)
        self.show_journal()

    def show_journal(self):
        st = journal.stats(self.entries)
        cfg = risk.config(self.s["risk"])
        rs = risk.evaluate(self.entries, time.time(), cfg)
        rows = [("Журнал сигналов", "h"),
                (f"Плюс {st['win']}   минус {st['loss']}   ничья {st['draw']}   ждут {st['pending']}"
                 + (f"   процент {st['rate']}%" if st["rate"] is not None else ""), None),
                (f"Сегодня: сделок {rs['n']}, серия минусов {rs['streak']}"
                 + (f", итог {rs['net']:+.2f}" if rs["net"] is not None else ""), "dim")]
        if rs["blocked"]:
            rows.append((rs["why"], "neg"))
        names = {k: v[0] for k, v in core.METHODS.items()}
        names.update({k: v[0] for k, v in core.INDICATORS.items()})

        def hour(e):
            h = time.localtime(e["ts"]).tm_hour // 3 * 3
            return f"{h:02d}-{h + 3:02d} ч"
        groups = [("По набору методов", lambda e: journal.label(e, names)), ("По парам", lambda e: e["sym"]),
                  ("По времени суток", hour), ("По экспирации", lambda e: f"{e['exp']} мин"),
                  ("По таймфрейму", lambda e: e["itv"])]
        shown = False
        for title, fn in groups:
            bd = journal.breakdown(self.entries, fn)
            if not bd:
                continue
            if not shown:
                rows.append(("", None))
                rows.append(("Что работает (зелёный: лучше безубытка, красный: хуже)", "h"))
                shown = True
            rows.append((title, "dim"))
            for k, v in sorted(bd.items(), key=lambda kv: -kv[1]["rate"])[:5]:
                lo, hi = v["rate"] - v["margin"], v["rate"] + v["margin"]
                rows.append((f"  {v['w']}/{v['n']} = {v['rate']:.0f}% (±{v['margin']:.0f}, б/у {v['be']:.0f}%)  {k}",
                             "pos" if lo > v["be"] else "neg" if hi < v["be"] else "warn"))
        rows.append(("", None))
        rows.append(("Последние сигналы", "h"))
        stt = {"win": ("плюс", "pos"), "loss": ("минус", "neg"), "draw": ("ничья", "dim"),
               "stale": ("не проверен", "warn"), "pending": ("ждёт", "dim")}
        for e in sorted(self.entries, key=lambda e: -e["ts"])[:80]:
            t = time.strftime("%d.%m %H:%M", time.localtime(e["ts"]))
            name, tag = stt.get(e["status"], (e["status"], "dim"))
            rows.append((f"{t}  {e['sym']} {e['itv']}  {e['dir'].upper()}  {e['exp']}м  {name}", tag))
        self.put(self.t_jrn, rows)

    # ── индикаторы ──
    def page_ind(self, f):
        Label(f, "Готовые наборы (можно включать несколько)", CYAN, 11, True, bg=CARD).pack(anchor="w", padx=10, pady=(10, 4))
        pf = tk.Frame(f, bg=CARD)
        pf.pack(fill="x", padx=8)
        self.pchips = {}
        for i, p in enumerate(core.PRESETS):
            b = Btn(pf, p["name"], lambda a, p=p: self.apply_preset(p), accent=VIOLET, toggle=True, height=32, size=10)
            b.grid(row=i // 2, column=i % 2, sticky="ew", padx=2, pady=2)
            self.pchips[p["name"]] = b
        for c in range(2):
            pf.columnconfigure(c, weight=1)
        self.lbl_preset = Label(f, "", DIM, 10, bg=CARD, wraplength=410, justify="left")
        self.lbl_preset.pack(anchor="w", padx=10, pady=4)
        Label(f, "Индикаторы (метод «Технический»)", CYAN, 11, True, bg=CARD).pack(anchor="w", padx=10, pady=(6, 4))
        inf = tk.Frame(f, bg=CARD)
        inf.pack(fill="x", padx=8)
        self.ichips = {}
        for i, (k, v) in enumerate(core.INDICATORS.items()):
            b = Btn(inf, v[0], lambda a, k=k: self.on_ind(), accent=CYAN, toggle=True, active=k in self.s["indicators"],
                    height=30, size=10)
            b.grid(row=i // 3, column=i % 3, sticky="ew", padx=2, pady=2)
            b.bind("<Enter>", lambda e, k=k: self.lbl_preset.configure(text=f"{core.INDICATORS[k][0]}: {core.INDICATORS[k][4]}"), add="+")
            self.ichips[k] = b
        for c in range(3):
            inf.columnconfigure(c, weight=1)
        self.t_adv = tk.Text(f, bg=CARD, fg=FG, relief="flat", wrap="word", font=(FONT, 10), state="disabled",
                             highlightthickness=0, height=9, width=10, padx=10, pady=6)
        self.t_adv.pack(fill="both", expand=True, pady=6)
        for tg, col in (("ok", GREEN), ("warn", AMBER), ("tip", CYAN)):
            self.t_adv.tag_configure(tg, foreground=col)
        self.on_ind()

    def preset_on(self, p):
        ind = {k for k, b in self.ichips.items() if b.active}
        meth = {k for k, b in self.mchips.items() if b.active}
        return set(p["ind"]) <= ind and (set(p["methods"]) - {"news"}) <= meth

    def mark_presets(self):
        for p in core.PRESETS:
            self.pchips[p["name"]].set(active=self.preset_on(p))

    def on_ind(self):
        self.s["indicators"] = [k for k, b in self.ichips.items() if b.active]
        self.mark_presets()
        self.t_adv.configure(state="normal")
        self.t_adv.delete("1.0", "end")
        self.t_adv.insert("end", f"Выбрано индикаторов: {len(self.s['indicators'])}\n", "tip")
        for kind, text in core.advise(self.s["indicators"]):
            self.t_adv.insert("end", "• " + text + "\n", kind)
        self.t_adv.configure(state="disabled")

    def on_method(self):
        self.s["methods"] = [k for k, b in self.mchips.items() if b.active]
        self.mark_presets()

    def all_methods(self):
        for b in self.mchips.values():
            b.set(active=True)
        self.on_method()

    def apply_preset(self, p):
        if self.preset_on(p):
            others = [q for q in core.PRESETS if q is not p and self.preset_on(q)]
            keep_i = set().union(*[set(q["ind"]) for q in others]) if others else set()
            keep_m = set().union(*[set(q["methods"]) for q in others]) if others else set()
            for k, b in self.ichips.items():
                if k in p["ind"] and k not in keep_i:
                    b.set(active=False)
            for k, b in self.mchips.items():
                if k in p["methods"] and k not in keep_m and k != "news":
                    b.set(active=False)
            if not any(b.active for b in self.mchips.values()):
                self.mchips["tech"].set(active=True)
            self.lbl_preset.configure(text=f"Набор «{p['name']}» отключён")
        else:
            for k in p["ind"]:
                self.ichips[k].set(active=True)
            for k in p["methods"]:
                self.mchips[k].set(active=True)
            self.lbl_preset.configure(text=f"«{p['name']}»: {p['text']}")
        self.on_method()
        self.on_ind()

    # ── мартингейл ──
    def page_mg(self, f):
        self.e_mg = {}
        for key, name in (("dep", "Депозит"), ("first", "Первая ставка"), ("pay", "Выплата, %"), ("steps", "Ступеней")):
            r = tk.Frame(f, bg=CARD)
            r.pack(fill="x", padx=10, pady=4)
            Label(r, name, DIM, 11, bg=CARD, width=14, anchor="w").pack(side="left")
            e = Field(r, self.s["mg"].get(key, ""))
            e.pack(side="left", fill="x", expand=True)
            e.bind("<KeyRelease>", lambda ev: self.calc())
            self.e_mg[key] = e
        self.sel_mode = Select(f, ["Возврат убытка", "Умножение ×2.2"], self.s["mg"].get("mode", "Возврат убытка"),
                               "Режим", lambda v: self.calc(), accent=RED, height=32, size=10)
        self.sel_mode.pack(fill="x", padx=10, pady=4)
        self.t_calc = self.textbox(f, font=("Consolas", 10))
        self.calc()

    def calc(self):
        dep, first, pay, steps = (num(self.e_mg[k].get()) for k in ("dep", "first", "pay", "steps"))
        if None in (dep, first, pay, steps) or pay <= 0 or steps < 1 or first <= 0:
            self.put(self.t_calc, [("Заполните поля числами", "dim")])
            return
        mode = "mult" if "Умнож" in self.sel_mode.get() else "recover"
        p = martingale.plan(dep, first, pay / 100, int(min(steps, 12)), mode=mode)
        rows = [(f"Безубыток {p['breakeven'] * 100:.1f}% побед. Мартингейл не меняет матожидание.", "dim"),
                (f"Ступеней по карману: {p['affordable']}   Риск потерять серию: {p['p_bust'] * 100:.1f}%", None),
                ("Шаг     Ставка     Всего   Прибыль", "dim")]
        for r in p["rows"]:
            rows.append((f"{r['k']:>3} {r['stake']:>10.2f} {r['total']:>9.2f} {r['profit']:>9.2f}", None if r["afford"] else "neg"))
        self.put(self.t_calc, rows)

    # ── настройки ──
    def page_settings(self, f):
        s = self.s

        def row(label, widget_fn):
            r = tk.Frame(f, bg=CARD)
            r.pack(fill="x", padx=10, pady=3)
            Label(r, label, DIM, 10, bg=CARD, width=22, anchor="w").pack(side="left")
            w = widget_fn(r)
            w.pack(side="left", fill="x", expand=True)
            return w
        Label(f, "API-ключ OTCharts", CYAN, 11, True, bg=CARD).pack(anchor="w", padx=10, pady=(10, 2))
        kr = tk.Frame(f, bg=CARD)
        kr.pack(fill="x", padx=10)
        self.e_key = Field(kr, s["key"], show="•")
        self.e_key.pack(side="left", fill="x", expand=True)
        Btn(kr, "Вставить", self.paste_key, accent=CYAN, height=30, size=10, width=82).pack(side="left", padx=(6, 0))
        kr2 = tk.Frame(f, bg=CARD)
        kr2.pack(fill="x", padx=10, pady=4)
        self.b_show = Btn(kr2, "Показать ключ", self.toggle_key, accent=DIM, height=28, size=9, width=110)
        self.b_show.pack(side="left")
        Btn(kr2, "Список OTC", self.load_catalog, accent=GREEN, height=28, size=10).pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.e_thr = row("Порог сигнала", lambda r: Field(r, s["thr"]))
        self.e_black = row("Новости (12:25-12:45, ...)", lambda r: Field(r, s["blackout"]))
        Label(f, "Фильтры (сигнал блокируется, если условие не выполнено)", CYAN, 11, True, bg=CARD).pack(anchor="w", padx=10, pady=(10, 4))
        tf = tk.Frame(f, bg=CARD)
        tf.pack(fill="x", padx=8)
        self.b_mtf = Btn(tf, "Старший ТФ", lambda a: s.update(mtf=a), accent=CYAN, toggle=True, active=s["mtf"], height=32, size=10)
        self.b_vol = Btn(tf, "Волатильность", lambda a: s.update(vol=a), accent=AMBER, toggle=True, active=s["vol"], height=32, size=10)
        self.b_ses = Btn(tf, "Сессии", lambda a: s.update(session=a), accent=VIOLET, toggle=True, active=s["session"], height=32, size=10)
        for i, b in enumerate((self.b_mtf, self.b_vol, self.b_ses)):
            b.grid(row=0, column=i, sticky="ew", padx=2)
            tf.columnconfigure(i, weight=1)
        self.b_sound = Btn(f, "Звук при сигнале", lambda a: s.update(sound=a), accent=GREEN, toggle=True, active=s["sound"], height=32, size=10)
        self.b_sound.pack(fill="x", padx=10, pady=6)
        Label(f, "Управление рисками", CYAN, 11, True, bg=CARD).pack(anchor="w", padx=10, pady=(6, 2))
        rk = s["risk"]
        self.b_risk = Btn(f, "Включено", None, accent=RED, toggle=True, active=bool(rk.get("on", True)), height=30, size=10)
        self.b_risk.pack(fill="x", padx=10, pady=2)
        self.e_risk = {}
        for k, name in (("deposit", "Депозит"), ("stake_pct", "Ставка, % депозита"), ("loss_pct", "Лимит убытка в день, %"),
                        ("streak", "Пауза после N проигрышей"), ("pause_min", "Пауза, минут")):
            self.e_risk[k] = row(name, lambda r, k=k: Field(r, rk.get(k, "")))
        Label(f, "Настройки сохраняются при закрытии окна. Клавиши: F5, F6, Ctrl+J.", DIM, 9, bg=CARD).pack(anchor="w", padx=10, pady=6)

    def paste_key(self):
        self.e_key.delete(0, "end")
        self.e_key.paste()
        self.collect()
        self.save()
        self.say("Ключ вставлен. Нажмите «Список OTC».")

    def toggle_key(self):
        hidden = self.e_key.cget("show") != ""
        self.e_key.configure(show="" if hidden else "•")
        self.b_show.set(text="Скрыть ключ" if hidden else "Показать ключ")

    def load_catalog(self):
        self.collect()
        self.save()
        self.say("Загружаю список OTC...")

        def work():
            try:
                syms = core.load_otc_catalog()
                core.apply_otc_catalog(syms)
                with open(CAT_PATH, "w", encoding="utf-8") as f:
                    json.dump({"symbols": syms, "payout": core.PAYOUTS}, f)
                self.ui(lambda: (self.refresh_groups(), self.fill_pairs(), self.say(f"Список OTC загружен: {len(syms)} пар")))
            except Exception as ex:
                self.ui(lambda ex=ex: self.say(f"Ошибка: {ex}"))
        self.bg(work)

    def refresh_payouts(self):
        if self.s["key"] and not self.busy:
            def work():
                try:
                    core.OTC_KEY = self.s["key"]
                    core.load_otc_catalog()
                    self.ui(self.fill_pairs)
                except Exception:
                    pass
            self.bg(work)
        self.after(60000, self.refresh_payouts)

    # ───────── список пар ─────────
    def say(self, t):
        self.status.configure(text=t)

    def refresh_groups(self):
        self.sel_group.values = list(core.GROUPS) + ["Избранное"]
        if self.sel_group.get() not in self.sel_group.values:
            self.sel_group.set_value(self.sel_group.values[0])

    def current_pairs(self):
        g = self.sel_group.get()
        pairs = list(self.s["favs"]) if g == "Избранное" else list(core.GROUPS.get(g, []))
        lo, hi = num(self.e_pmin.get()), num(self.e_pmax.get())
        if lo is not None or hi is not None:
            keep = []
            for p in pairs:
                v = core.PAYOUTS.get(p)
                if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
                    continue
                keep.append(p)
            pairs = keep
        return sorted(pairs, key=lambda p: -(core.PAYOUTS.get(p) or 0))

    def row_text(self, p):
        pay = core.PAYOUTS.get(p)
        sg = self.last_sig.get(p)
        star = "★" if p in self.s["favs"] else " "
        return f"{star} {p:<15}{(str(pay) + '%') if pay else '':>5}  {sg[0].upper() if sg else ''}"

    def fill_pairs(self):
        keep = self.cur
        self.pairs = self.current_pairs()
        self.lb.delete(0, "end")
        for i, p in enumerate(self.pairs):
            self.lb.insert("end", self.row_text(p))
            sg = self.last_sig.get(p)
            if sg:
                self.lb.itemconfig(i, fg=GREEN if sg[0] == "call" else RED)
        if keep in self.pairs:
            i = self.pairs.index(keep)
        elif self.pairs:
            i = 0
        else:
            return
        self.lb.selection_set(i)
        self.lb.see(i)
        if self.pairs[i] != self.cur:
            self.select_pair(self.pairs[i])
        self.update_fav()

    def on_select(self, _e):
        sel = self.lb.curselection()
        if sel and sel[0] < len(self.pairs) and self.pairs[sel[0]] != self.cur:
            self.select_pair(self.pairs[sel[0]])

    def select_pair(self, p):
        self.cur = p
        self.update_fav()
        cached = self.results.get(p)
        if cached and cached[1] == self.sel_itv.get():
            self.show(p, cached[0], None)
            return
        self.res = None
        self.sig_info = None
        self.set_sig(p.replace("_otc", " OTC"), FG)
        pay = core.PAYOUTS.get(p)
        self.lbl_info.configure(text=f"Выплата {pay}%, безубыток {martingale.breakeven(pay / 100) * 100:.1f}%" if pay else "")
        self.lbl_timer.configure(text="")
        self.put(self.t_reason, [("Нажмите «Анализ» (F5), чтобы получить сигнал по этой паре", "dim")])
        self.load_preview()

    def update_fav(self):
        self.b_fav.set(text="★" if self.cur in self.s["favs"] else "☆")

    def toggle_fav(self):
        if self.cur:
            f = self.s["favs"]
            f.remove(self.cur) if self.cur in f else f.append(self.cur)
            self.fill_pairs()

    def load_preview(self):
        if self._pv_id:
            self.after_cancel(self._pv_id)
        self._pv_id = self.after(250, self._start_preview)

    def _start_preview(self):
        sym, itv = self.cur, self.sel_itv.get()
        if not sym:
            return

        def work():
            try:
                pv = core.preview(sym, itv)
                self.ui(lambda: self.set_chart(sym, pv["chart"]))
            except Exception as ex:
                self.ui(lambda ex=ex: self.say(f"График {sym}: {ex}"))
        self.bg(work)

    def set_chart(self, sym, ch):
        if sym == self.cur:
            self.chart = ch
            self.draw()

    # ───────── график ─────────
    def geom(self):
        ch = self.chart
        W, H = self.cv.winfo_width(), self.cv.winfo_height()
        pr, pt, pb, pl = 74, 14, 16, 12
        hi, lo = max(ch["h"]), min(ch["l"])
        rng = hi - lo
        if rng <= 0:
            rng = abs(hi) * 0.0004 or 1e-6
        lo -= rng * 0.08
        hi += rng * 0.08
        return W, H, pl, pr, pt, pb, lo, hi

    def draw(self):
        c = self.cv
        c.delete("all")
        ch = self.chart
        if not ch or not ch["c"]:
            return
        W, H, pl, pr, pt, pb, lo, hi = self.geom()
        if W < 120 or H < 120:
            return
        span = hi - lo
        y = lambda v: pt + (hi - v) / span * (H - pt - pb)
        n = len(ch["c"])
        cw = (W - pl - pr) / n
        fmt = lambda v: f"{v:.5g}" if abs(v) < 1000 else f"{v:.2f}"
        for i in range(6):
            v = lo + span * i / 5
            c.create_line(pl, y(v), W - pr, y(v), fill=mix(CARD, BORDER, 0.55))
            c.create_text(W - pr + 8, y(v), text=fmt(v), anchor="w", fill=DIM, font=("Consolas", 9))
        near = sorted([x for x in ch.get("levels", []) if lo <= x <= hi], key=lambda x: abs(x - ch["c"][-1]))[:6]
        for v in near:
            c.create_line(pl, y(v), W - pr, y(v), fill=mix(CARD, AMBER, 0.55), dash=(5, 4))
        body = max(1.0, min(cw * 0.7, 14))
        for i in range(n):
            x = pl + (i + 0.5) * cw
            up = ch["c"][i] >= ch["o"][i]
            col = GREEN if up else RED
            c.create_line(x, y(ch["h"][i]), x, y(ch["l"][i]), fill=col, width=1)
            y1, y2 = y(ch["o"][i]), y(ch["c"][i])
            top, bot = min(y1, y2), max(y1, y2)
            if bot - top < 2:
                bot = top + 2
            c.create_rectangle(x - body / 2, top, x + body / 2, bot, fill=col, outline=col)
        for v in near:      # подписи уровней рисуем поверх свечей, на подложке
            t = c.create_text(pl + 4, y(v) - 7, text=fmt(v), anchor="w", fill=mix(CARD, AMBER, 0.8), font=("Consolas", 8))
            x1, y1, x2, y2 = c.bbox(t)
            c.tag_lower(c.create_rectangle(x1 - 2, y1, x2 + 2, y2, fill=CARD, outline=""), t)
        for i, v in ch.get("hp", []):
            if 0 <= i < n and lo <= v <= hi:
                c.create_oval(pl + (i + .5) * cw - 3, y(v) - 9, pl + (i + .5) * cw + 3, y(v) - 3, fill=CYAN, outline="")
        for i, v in ch.get("lp", []):
            if 0 <= i < n and lo <= v <= hi:
                c.create_oval(pl + (i + .5) * cw - 3, y(v) + 3, pl + (i + .5) * cw + 3, y(v) + 9, fill=VIOLET, outline="")
        last = ch["c"][-1]
        c.create_line(pl, y(last), W - pr, y(last), fill=CYAN, dash=(2, 3))
        c.create_rectangle(W - pr + 2, y(last) - 10, W - 2, y(last) + 10, fill=CYAN, outline="")
        c.create_text(W - pr + 7, y(last), text=fmt(last), anchor="w", fill="#031018", font=("Consolas", 9, "bold"))
        if self.sig_info and self.res and self.res["kind"] in ("call", "put"):
            col = GREEN if self.res["kind"] == "call" else RED
            c.create_text(pl + 8, pt + 4, anchor="nw", text=("▲ CALL" if self.res["kind"] == "call" else "▼ PUT"),
                          fill=col, font=(FONT, 16, "bold"))

    def on_move(self, e):
        self.cv.delete("cross")
        if not self.chart:
            return
        W, H, pl, pr, pt, pb, lo, hi = self.geom()
        if not (pl <= e.x <= W - pr and pt <= e.y <= H - pb):
            return
        v = hi - (e.y - pt) / (H - pt - pb) * (hi - lo)
        self.cv.create_line(pl, e.y, W - pr, e.y, fill=DIM, dash=(2, 4), tags="cross")
        self.cv.create_line(e.x, pt, e.x, H - pb, fill=DIM, dash=(2, 4), tags="cross")
        self.cv.create_text(W - pr + 7, e.y, text=f"{v:.5g}", anchor="w", fill=FG, font=("Consolas", 9), tags="cross")

    # ───────── анализ ─────────
    def exp_minutes(self):
        try:
            return int(self.sel_exp.get().split()[0])
        except (ValueError, IndexError):
            return None

    def params(self):
        self.collect()
        s = self.s
        bl = [x.strip() for x in s["blackout"].split(",") if x.strip()]
        opts = {"mtf": s["mtf"], "vol": s["vol"], "session": s["session"], "exp": self.exp_minutes()}
        return self.sel_itv.get(), s["thr"], bl, set(s["methods"]), s["weights"] if "weights" in s else {}, opts, list(s["indicators"])

    def gate(self):
        rs = risk.evaluate(self.entries, time.time(), risk.config(self.s["risk"]))
        if rs["blocked"]:
            self.say(rs["why"] + " Отключить: Настройки > Управление рисками.")
            self.set_sig("СТОП: риск-менеджмент", AMBER)
            return False
        return True

    def analyze(self, notify=False):
        if self.cur:
            self.launch([self.cur], notify)

    def scan_pairs(self):
        sc = self.sel_scope.get()
        if sc == "Все OTC":
            pairs = list(core.OTC_ALL)
        elif sc == "Избранное":
            pairs = list(self.s["favs"])
        else:
            return list(self.pairs)
        lo, hi = num(self.e_pmin.get()), num(self.e_pmax.get())
        out = []
        for p in pairs:
            v = core.PAYOUTS.get(p)
            if (lo is not None or hi is not None) and (v is None or (lo is not None and v < lo) or (hi is not None and v > hi)):
                continue
            out.append(p)
        return sorted(out, key=lambda p: -(core.PAYOUTS.get(p) or 0))

    def scan(self):
        pairs = self.scan_pairs()
        if not pairs:
            self.say("Нечего сканировать: список пуст (проверьте фильтр выплаты)")
            return
        self.launch(pairs, False)

    def launch(self, pairs, notify):
        if self.busy:
            return
        itv, thr, bl, methods, weights, opts, inds = self.params()
        if not methods:
            self.say("Включите хотя бы один метод анализа")
            return
        if not self.gate():
            return
        self.busy = True
        self.b_an.disabled = self.b_sc.disabled = True

        def work():
            out = []
            for i, sym in enumerate(pairs, 1):
                self.ui(lambda i=i, s=sym: self.say(f"Анализ {i}/{len(pairs)}: {s}"))
                try:
                    res = core.analyze_symbol(sym, itv, thr, bl, methods, weights, opts, inds)
                    out.append((sym, res, None if res else "Недостаточно свечей"))
                except Exception as ex:
                    out.append((sym, None, str(ex)))
                    if "ключ" in str(ex) or "квота" in str(ex):
                        break
            self.ui(lambda: self.done(pairs, itv, out, methods, inds, notify))
        self.bg(work)

    def done(self, pairs, itv, out, methods, inds, notify):
        self.busy = False
        self.b_an.disabled = self.b_sc.disabled = False
        now = time.time()
        fire = None
        for sym, res, _err in out:
            if res:
                self.results[sym] = (res, itv)
            if res and res["kind"] in ("call", "put"):
                if journal.is_new(self.last_sig, sym, res["kind"], now, res["exp_min"]):
                    self.entries.append(journal.new_entry(sym, itv, res, methods, now, inds if "tech" in methods else [],
                                                          core.PAYOUTS.get(sym)))
                    fire = res["kind"]
            else:
                self.last_sig.pop(sym, None)
        journal.save(JRN_PATH, self.entries)
        if fire and self.s["sound"]:
            beep(fire)
        self.fill_pairs()
        errs = [f"{s}: {e}" for s, _r, e in out if e]
        found = sum(1 for _s, r, _e in out if r and r["kind"] in ("call", "put"))
        self.say(f"Готово: проверено {len(out)}, сигналов {found}." + (f" Ошибка: {errs[0]}" if errs else ""))
        self.show_journal()
        pick = self.cur if any(s == self.cur for s, _r, _e in out) else out[0][0]
        if len(out) > 1:
            best = max((x for x in out if x[1]), key=lambda x: abs(x[1]["score"]), default=None)
            if best and best[1]["kind"] in ("call", "put"):
                pick = best[0]
        for sym, res, err in out:
            if sym == pick:
                if sym != self.cur and sym in self.pairs:
                    self.lb.selection_clear(0, "end")
                    i = self.pairs.index(sym)
                    self.lb.selection_set(i)
                    self.lb.see(i)
                    self.cur = sym
                    self.update_fav()
                self.show(sym, res, err)

    def show(self, sym, res, err):
        if err or not res:
            self.set_sig("Ошибка", AMBER)
            self.lbl_info.configure(text=err or "нет данных")
            self.put(self.t_reason, [(err or "нет данных", "neg")])
            return
        self.res = res
        col = {"call": GREEN, "put": RED}.get(res["kind"], AMBER)
        self.card.configure(highlightbackground=col)
        self.set_sig(f"{sym.replace('_otc', ' OTC')}   {res['signal']}", col)
        pay = core.PAYOUTS.get(sym)
        rs = risk.evaluate(self.entries, time.time(), risk.config(self.s["risk"]))
        lines = [f"Балл {res['score']:+d} (порог {res['thr']}), уверенность {res['conf']}%, цена {res['price']:.5g}"]
        if res.get("exp_text"):
            lines.append(res["exp_text"])
        if pay:
            lines.append(f"Выплата {pay}%, безубыток {martingale.breakeven(pay / 100) * 100:.1f}%")
        if rs["stake"]:
            lines.append(f"Ставка по риск-менеджменту: {rs['stake']}")
        self.lbl_info.configure(text="\n".join(lines))
        self.sig_info = {"ts": time.time(), "tf": core.TF_SECONDS.get(self.sel_itv.get(), 300)} if res["kind"] in ("call", "put") else None
        self.chart = res["chart"]
        self.draw()
        rows = [("Причины сигнала", "h")]
        for sc, txt in res["parts"]:
            rows.append((f"{sc:+d}  {txt}" if sc else f"      {txt}", "pos" if sc > 0 else "neg" if sc < 0 else "dim"))
        self.put(self.t_reason, rows)
        self.tab("Причины")

    # ───────── авто, таймеры, журнал ─────────
    def on_auto(self, active):
        self.auto_k = None
        self.say("Авто: анализ выбранной пары на каждой новой свече" if active else "Авто выключено")

    def tick(self):
        now = time.time()
        sg = self.sig_info
        if sg:
            age = now - sg["ts"]
            self.lbl_timer.configure(text=f"Возраст сигнала {int(age // 60)}:{int(age % 60):02d}  |  новая свеча через "
                                          f"{int((sg['tf'] - now % sg['tf']) // 60)}:{int((sg['tf'] - now % sg['tf']) % 60):02d}")
        elif self.cur:
            self.lbl_timer.configure(text="")
        if self.b_auto.active and self.cur and not self.busy:
            tf = core.TF_SECONDS.get(self.sel_itv.get(), 300)
            k = int(now // tf)
            if k != self.auto_k and now % tf >= 3:
                self.auto_k = k
                self.analyze(True)
        self.after(1000, self.tick)

    def settle_due(self):
        due = journal.due(self.entries)
        if due:
            def work():
                for e in due:
                    try:
                        journal.settle(e, core.preview(e["sym"], "1m")["price"])
                    except Exception:
                        pass
                journal.save(JRN_PATH, self.entries)
                self.ui(self.show_journal)
            self.bg(work)
        self.after(15000, self.settle_due)


if __name__ == "__main__":
    App().mainloop()
