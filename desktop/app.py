"""Trade Signals для компьютера (Windows). Tkinter: ничего ставить не нужно, кроме numpy и certifi."""
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk

import journal
import martingale
import risk
import signals_core as core

try:
    import winsound
except ImportError:
    winsound = None

BG, PANEL, FG, DIM = "#0d1117", "#161b22", "#e6edf3", "#8b949e"
GREEN, RED, YELLOW, CYAN = "#2ea043", "#da3633", "#d29922", "#39c5cf"
HOME = os.path.join(os.path.expanduser("~"), "TradeSignals")
os.makedirs(HOME, exist_ok=True)
SET_PATH = os.path.join(HOME, "settings.json")
JRN_PATH = os.path.join(HOME, "journal.json")
CAT_PATH = os.path.join(HOME, "otc_catalog.json")
DEFAULTS = {"key": "", "thr": 35, "interval": "5m", "group": "OTC Pocket Option", "pmin": "", "sound": True,
            "blackout": "", "methods": sorted(core.ALL_METHODS), "indicators": list(core.DEFAULT_INDICATORS),
            "risk": dict(risk.DEFAULT), "favs": [], "mg": {"dep": "1000", "first": "10", "pay": "92", "steps": "5"}}


def beep(kind):
    if not winsound:
        return
    try:
        winsound.Beep(1200 if kind == "call" else 600, 250)
    except Exception:
        pass


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Trade Signals")
        self.geometry("1500x850")
        self.configure(bg=BG)
        self.s = dict(DEFAULTS)
        try:
            with open(SET_PATH, encoding="utf-8") as f:
                self.s.update(json.load(f))
        except Exception:
            pass
        core.OTC_KEY = self.s["key"]
        self.entries = journal.load(JRN_PATH)
        self.last_sig = {}
        self.busy = False
        self.cur = None
        self.res = None
        self.pairs = []
        self.load_catalog_cache()
        self.style_ui()
        self.build()
        self.bind("<F5>", lambda e: self.analyze())
        self.bind("<F6>", lambda e: self.scan())
        self.bind("<Control-j>", lambda e: self.nb.select(1))
        self.bind("<Up>", lambda e: self.move(-1))
        self.bind("<Down>", lambda e: self.move(1))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.fill_pairs()
        self.after(1000, self.tick)
        self.after(500, self.refresh_payouts)

    # ---------- настройки ----------
    def save(self):
        try:
            with open(SET_PATH, "w", encoding="utf-8") as f:
                json.dump(self.s, f, ensure_ascii=False)
        except Exception:
            pass

    def close(self):
        self.collect()
        self.save()
        self.destroy()

    def load_catalog_cache(self):
        try:
            with open(CAT_PATH, encoding="utf-8") as f:
                c = json.load(f)
            core.apply_otc_catalog(c["symbols"])
            core.PAYOUTS.update(c.get("payout", {}))
        except Exception:
            pass

    def style_ui(self):
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL)
        st.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=24, borderwidth=0)
        st.map("Treeview", background=[("selected", "#1f6feb")])
        st.configure("Treeview.Heading", background=BG, foreground=DIM)
        st.configure("TNotebook", background=BG)
        st.configure("TNotebook.Tab", background=PANEL, foreground=FG, padding=(12, 6))
        st.map("TNotebook.Tab", background=[("selected", "#1f6feb")])
        st.configure("TButton", background="#21262d", foreground=FG, padding=6)
        st.map("TButton", background=[("active", "#30363d")])
        st.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG)

    # ---------- интерфейс ----------
    def build(self):
        pw = tk.PanedWindow(self, orient="horizontal", bg=BG, sashwidth=5, bd=0)
        pw.pack(fill="both", expand=True)
        left = tk.Frame(pw, bg=BG)
        mid = tk.Frame(pw, bg=BG)
        right = tk.Frame(pw, bg=BG)
        pw.add(left, minsize=260, width=300)
        pw.add(mid, minsize=500, width=800)
        pw.add(right, minsize=320, width=400)

        # левая панель
        self.group = ttk.Combobox(left, state="readonly", values=list(core.GROUPS) + ["Избранное"])
        self.group.set(self.s["group"] if self.s["group"] in list(core.GROUPS) + ["Избранное"] else "OTC Pocket Option")
        self.group.pack(fill="x", padx=6, pady=(6, 3))
        self.group.bind("<<ComboboxSelected>>", lambda e: self.fill_pairs())
        r = tk.Frame(left, bg=BG)
        r.pack(fill="x", padx=6)
        self.itv = ttk.Combobox(r, state="readonly", width=6, values=list(core.TF_LIST))
        self.itv.set(self.s["interval"])
        self.itv.pack(side="left")
        self.itv.bind("<<ComboboxSelected>>", lambda e: self.load_preview())
        tk.Label(r, text="выплата от", bg=BG, fg=DIM).pack(side="left", padx=(8, 2))
        self.pmin = tk.Entry(r, width=4, bg=PANEL, fg=FG, insertbackground=FG, relief="flat")
        self.pmin.insert(0, self.s["pmin"])
        self.pmin.pack(side="left")
        tk.Label(r, text="%", bg=BG, fg=DIM).pack(side="left")
        self.tree = ttk.Treeview(left, columns=("p", "pay", "sig"), show="headings", selectmode="browse")
        for c, t, w in (("p", "Пара", 130), ("pay", "%", 45), ("sig", "Сигнал", 70)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c == "p" else "center")
        self.tree.tag_configure("call", foreground=GREEN)
        self.tree.tag_configure("put", foreground=RED)
        self.tree.pack(fill="both", expand=True, padx=6, pady=6)
        self.tree.bind("<<TreeviewSelect>>", self.on_select)
        self.tree.bind("<Double-1>", lambda e: self.toggle_fav())
        b = tk.Frame(left, bg=BG)
        b.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(b, text="Анализ (F5)", command=self.analyze).pack(side="left", expand=True, fill="x")
        ttk.Button(b, text="Сканер (F6)", command=self.scan).pack(side="left", expand=True, fill="x", padx=(4, 0))
        self.scan_all = tk.BooleanVar(value=False)
        tk.Checkbutton(left, text="Сканер: весь список (иначе — только выше фильтра)", variable=self.scan_all,
                       bg=BG, fg=DIM, selectcolor=PANEL, activebackground=BG, wraplength=270, justify="left").pack(anchor="w", padx=6)
        self.status = tk.Label(left, text="", bg=BG, fg=DIM, anchor="w", wraplength=280, justify="left")
        self.status.pack(fill="x", padx=6, pady=4)

        # центр
        self.banner = tk.Label(mid, text="Выберите пару", font=("Segoe UI", 22, "bold"), bg=PANEL, fg=FG, pady=10)
        self.banner.pack(fill="x", padx=6, pady=6)
        self.info = tk.Label(mid, text="", bg=BG, fg=DIM, anchor="w", justify="left", font=("Segoe UI", 11))
        self.info.pack(fill="x", padx=8)
        self.canvas = tk.Canvas(mid, bg=PANEL, highlightthickness=0)
        self.canvas.pack(fill="both", expand=True, padx=6, pady=6)
        self.canvas.bind("<Configure>", lambda e: self.draw())

        # правая панель
        self.nb = ttk.Notebook(right)
        self.nb.pack(fill="both", expand=True, padx=6, pady=6)
        self.t_reason = self.text_tab("Причины")
        self.t_jrn = self.text_tab("Журнал")
        self.build_calc()
        self.build_settings()
        self.show_journal()

    def text_tab(self, name):
        f = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(f, text=name)
        t = tk.Text(f, bg=PANEL, fg=FG, relief="flat", wrap="word", font=("Segoe UI", 10), state="disabled")
        t.pack(fill="both", expand=True, padx=4, pady=4)
        t.tag_configure("pos", foreground=GREEN)
        t.tag_configure("neg", foreground=RED)
        t.tag_configure("dim", foreground=DIM)
        t.tag_configure("h", foreground=CYAN, font=("Segoe UI", 11, "bold"))
        return t

    def put(self, t, rows):
        t.configure(state="normal")
        t.delete("1.0", "end")
        for text, tag in rows:
            t.insert("end", text + "\n", tag or ())
        t.configure(state="disabled")

    def build_calc(self):
        f = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(f, text="Мартингейл")
        self.mg = {}
        for key, name in (("dep", "Депозит"), ("first", "Первая ставка"), ("pay", "Выплата, %"), ("steps", "Ступеней")):
            r = tk.Frame(f, bg=PANEL)
            r.pack(fill="x", padx=8, pady=3)
            tk.Label(r, text=name, bg=PANEL, fg=DIM, width=14, anchor="w").pack(side="left")
            e = tk.Entry(r, bg=BG, fg=FG, insertbackground=FG, relief="flat")
            e.insert(0, self.s["mg"].get(key, ""))
            e.pack(side="left", fill="x", expand=True)
            e.bind("<KeyRelease>", lambda ev: self.calc())
            self.mg[key] = e
        self.t_calc = tk.Text(f, bg=PANEL, fg=FG, relief="flat", font=("Consolas", 10), state="disabled", height=20)
        self.t_calc.pack(fill="both", expand=True, padx=4, pady=4)
        self.t_calc.tag_configure("neg", foreground=RED)
        self.t_calc.tag_configure("pos", foreground=GREEN)
        self.t_calc.tag_configure("dim", foreground=DIM)
        self.calc()

    @staticmethod
    def num(s):
        try:
            return float(str(s).replace(",", ".").strip())
        except ValueError:
            return None

    def calc(self):
        dep, first, pay, steps = (self.num(self.mg[k].get()) for k in ("dep", "first", "pay", "steps"))
        if None in (dep, first, pay, steps) or pay <= 0 or steps < 1 or first <= 0:
            self.put(self.t_calc, [("Заполните поля числами", "dim")])
            return
        p = martingale.plan(dep, first, pay / 100, int(min(steps, 12)))
        rows = [(f"Безубыток: {p['breakeven'] * 100:.1f}% побед. Мартингейл не меняет матожидание.", "dim"),
                (f"Ступеней по карману: {p['affordable']}   Риск потерять серию: {p['p_bust'] * 100:.1f}%", None),
                ("Шаг   Ставка    Всего    Прибыль", "dim")]
        for r in p["rows"]:
            rows.append((f"{r['k']:>3}  {r['stake']:>8.2f} {r['total']:>8.2f} {r['profit']:>8.2f}", None if r["afford"] else "neg"))
        self.put(self.t_calc, rows)

    def build_settings(self):
        f = tk.Frame(self.nb, bg=PANEL)
        self.nb.add(f, text="Настройки")
        self.ent = {}
        fields = [("key", "API-ключ OTCharts", self.s["key"]), ("thr", "Порог сигнала", self.s["thr"]),
                  ("blackout", "Новости (12:25-12:45, ...)", self.s["blackout"]),
                  ("deposit", "Депозит", self.s["risk"].get("deposit", "")),
                  ("stake_pct", "Ставка, % депозита", self.s["risk"].get("stake_pct", "")),
                  ("loss_pct", "Лимит убытка/день, %", self.s["risk"].get("loss_pct", "")),
                  ("streak", "Пауза после N проигрышей", self.s["risk"].get("streak", "")),
                  ("pause_min", "Пауза, минут", self.s["risk"].get("pause_min", ""))]
        for k, name, v in fields:
            r = tk.Frame(f, bg=PANEL)
            r.pack(fill="x", padx=8, pady=3)
            tk.Label(r, text=name, bg=PANEL, fg=DIM, width=24, anchor="w").pack(side="left")
            e = tk.Entry(r, bg=BG, fg=FG, insertbackground=FG, relief="flat", show="*" if k == "key" else "")
            e.insert(0, str(v))
            e.pack(side="left", fill="x", expand=True)
            self.ent[k] = e
        self.v_sound = tk.BooleanVar(value=self.s["sound"])
        self.v_risk = tk.BooleanVar(value=bool(self.s["risk"].get("on", True)))
        for var, name in ((self.v_sound, "Звук при сигнале"), (self.v_risk, "Управление рисками включено")):
            tk.Checkbutton(f, text=name, variable=var, bg=PANEL, fg=FG, selectcolor=BG, activebackground=PANEL).pack(anchor="w", padx=8)
        ttk.Button(f, text="Сохранить и загрузить список OTC", command=self.apply_settings).pack(fill="x", padx=8, pady=8)
        tk.Label(f, text="Клавиши: F5 анализ, F6 сканер, ↑/↓ смена пары,\nдвойной щелчок по паре — в избранное, Ctrl+J журнал",
                 bg=PANEL, fg=DIM, justify="left").pack(anchor="w", padx=8)

    def collect(self):
        e = self.ent
        self.s["key"] = e["key"].get().strip()
        try:
            self.s["thr"] = int(float(e["thr"].get()))
        except ValueError:
            pass
        self.s["blackout"] = e["blackout"].get()
        self.s["sound"] = self.v_sound.get()
        self.s["risk"] = {"on": self.v_risk.get(), **{k: e[k].get() for k in ("deposit", "stake_pct", "loss_pct", "streak", "pause_min")}}
        self.s["interval"] = self.itv.get()
        self.s["group"] = self.group.get()
        self.s["pmin"] = self.pmin.get()
        self.s["mg"] = {k: v.get() for k, v in self.mg.items()}
        core.OTC_KEY = self.s["key"]

    def apply_settings(self):
        self.collect()
        self.save()
        self.say("Загружаю список OTC...")

        def work():
            try:
                syms = core.load_otc_catalog()
                core.apply_otc_catalog(syms)
                with open(CAT_PATH, "w", encoding="utf-8") as f:
                    json.dump({"symbols": syms, "payout": core.PAYOUTS}, f)
                self.after(0, lambda: (self.fill_pairs(), self.say(f"Список OTC: {len(syms)} пар")))
            except Exception as ex:
                self.after(0, lambda: self.say(f"Ошибка: {ex}"))
        threading.Thread(target=work, daemon=True).start()

    # ---------- список пар ----------
    def say(self, t):
        self.status.configure(text=t)

    def current_pairs(self):
        g = self.group.get()
        if g == "Избранное":
            pairs = list(self.s["favs"])
        else:
            pairs = list(core.GROUPS.get(g, []))
        lo = self.num(self.pmin.get())
        if lo is not None:
            pairs = [p for p in pairs if (core.PAYOUTS.get(p) or 0) >= lo]
        return sorted(pairs, key=lambda p: -(core.PAYOUTS.get(p) or 0))

    def fill_pairs(self, keep=None):
        keep = keep or self.cur
        self.pairs = self.current_pairs()
        self.tree.delete(*self.tree.get_children())
        for p in self.pairs:
            pay = core.PAYOUTS.get(p)
            sg = self.last_sig.get(p)
            star = "★ " if p in self.s["favs"] else ""
            self.tree.insert("", "end", iid=p, values=(star + p, f"{pay}" if pay else "", sg[0].upper() if sg else ""),
                             tags=(sg[0],) if sg else ())
        if keep in self.pairs:
            self.tree.selection_set(keep)
        elif self.pairs:
            self.tree.selection_set(self.pairs[0])

    def toggle_fav(self):
        if self.cur:
            f = self.s["favs"]
            f.remove(self.cur) if self.cur in f else f.append(self.cur)
            self.fill_pairs()

    def move(self, d):
        if not self.pairs or self.focus_get() is not self.tree:
            return
        i = self.pairs.index(self.cur) if self.cur in self.pairs else 0
        j = max(0, min(len(self.pairs) - 1, i + d))
        self.tree.selection_set(self.pairs[j])
        self.tree.see(self.pairs[j])
        return "break"

    def on_select(self, _e):
        sel = self.tree.selection()
        if sel and sel[0] != self.cur:
            self.cur = sel[0]
            self.res = None
            self.banner.configure(text=self.cur, fg=FG)
            self.info.configure(text="")
            self.load_preview()

    def load_preview(self):
        sym, itv = self.cur, self.itv.get()
        if not sym:
            return

        def work():
            try:
                pv = core.preview(sym, itv)
                self.after(0, lambda: self.set_chart(sym, pv["chart"], pv["price"]))
            except Exception as ex:
                self.after(0, lambda: self.say(f"График {sym}: {ex}"))
        threading.Thread(target=work, daemon=True).start()

    def set_chart(self, sym, ch, price):
        if sym != self.cur:
            return
        self.chart = ch
        self.price = price
        self.draw()

    # ---------- график ----------
    def draw(self):
        c = self.canvas
        c.delete("all")
        ch = getattr(self, "chart", None)
        if not ch or not ch["c"]:
            return
        W, H = c.winfo_width(), c.winfo_height()
        if W < 100 or H < 100:
            return
        pad_r, pad = 70, 15
        n = len(ch["c"])
        hi, lo = max(ch["h"]), min(ch["l"])
        lv = [x for x in ch.get("levels", []) if lo * 0.995 < x < hi * 1.005]
        hi, lo = max([hi] + lv), min([lo] + lv)
        span = (hi - lo) or 1e-9
        y = lambda v: pad + (hi - v) / span * (H - 2 * pad)
        cw = (W - pad_r - pad) / n
        for i in range(5):
            v = lo + span * i / 4
            c.create_line(pad, y(v), W - pad_r, y(v), fill="#21262d")
            c.create_text(W - pad_r + 6, y(v), text=f"{v:.5g}", anchor="w", fill=DIM, font=("Consolas", 9))
        for v in lv:
            c.create_line(pad, y(v), W - pad_r, y(v), fill=YELLOW, dash=(4, 3))
        for i in range(n):
            x = pad + (i + 0.5) * cw
            up = ch["c"][i] >= ch["o"][i]
            col = GREEN if up else RED
            c.create_line(x, y(ch["h"][i]), x, y(ch["l"][i]), fill=col)
            y1, y2 = y(ch["o"][i]), y(ch["c"][i])
            c.create_rectangle(x - cw * 0.35, min(y1, y2), x + cw * 0.35, max(y1, y2) + 1, fill=col, outline=col)
        last = ch["c"][-1]
        c.create_line(pad, y(last), W - pad_r, y(last), fill=CYAN, dash=(2, 2))
        c.create_rectangle(W - pad_r + 2, y(last) - 9, W - 2, y(last) + 9, fill=CYAN, outline=CYAN)
        c.create_text(W - pad_r + 6, y(last), text=f"{last:.5g}", anchor="w", fill="#000", font=("Consolas", 9, "bold"))

    # ---------- анализ ----------
    def params(self):
        self.collect()
        bl = [x.strip() for x in self.s["blackout"].split(",") if x.strip()]
        opts = {"mtf": True, "vol": True, "session": True}
        return self.itv.get(), self.s["thr"], bl, set(self.s["methods"]), {}, opts, list(self.s["indicators"])

    def gate(self):
        rs = risk.evaluate(self.entries, time.time(), risk.config(self.s["risk"]))
        if rs["blocked"]:
            self.say(rs["why"] + " Отключить: Настройки.")
            return False
        return True

    def analyze(self):
        if self.cur:
            self.launch([self.cur])

    def scan(self):
        pairs = self.pairs if self.scan_all.get() else self.current_pairs()
        self.launch(pairs)

    def launch(self, pairs):
        if self.busy or not pairs:
            return
        if not self.gate():
            return
        self.busy = True
        itv, thr, bl, methods, weights, opts, inds = self.params()
        key_hold = core.OTC_KEY

        def work():
            out = []
            for i, sym in enumerate(pairs, 1):
                self.after(0, lambda i=i, s=sym: self.say(f"Анализ {i}/{len(pairs)}: {s}"))
                try:
                    res = core.analyze_symbol(sym, itv, thr, bl, methods, weights, opts, inds)
                    out.append((sym, res, None if res else "Недостаточно свечей"))
                except Exception as ex:
                    out.append((sym, None, str(ex)))
                    if "ключ" in str(ex) or "квота" in str(ex):
                        break
            self.after(0, lambda: self.done(pairs, itv, out, methods, inds))
        threading.Thread(target=work, daemon=True).start()

    def done(self, pairs, itv, out, methods, inds):
        self.busy = False
        now = time.time()
        sound = None
        for sym, res, err in out:
            if res and res["kind"] in ("call", "put"):
                if journal.is_new(self.last_sig, sym, res["kind"], now, res["exp_min"]):
                    self.entries.append(journal.new_entry(sym, itv, res, methods, now, inds if "tech" in methods else [],
                                                          core.PAYOUTS.get(sym)))
                    sound = res["kind"]
            else:
                self.last_sig.pop(sym, None)
        journal.save(JRN_PATH, self.entries)
        if sound and self.s["sound"]:
            beep(sound)
        self.fill_pairs()
        errs = [f"{s}: {e}" for s, _r, e in out if e]
        self.say(f"Готово: {len(out)} пар. " + (errs[0] if errs else ""))
        self.show_journal()
        pick = self.cur if any(s == self.cur for s, _r, _e in out) else out[0][0]
        for sym, res, err in out:
            if sym == pick:
                self.show(sym, res, err)

    def show(self, sym, res, err):
        if err or not res:
            self.banner.configure(text="Ошибка", fg=YELLOW)
            self.put(self.t_reason, [(err or "нет данных", "neg")])
            return
        self.res = res
        col = {"call": GREEN, "put": RED}.get(res["kind"], YELLOW)
        self.banner.configure(text=f"{sym}   {res['signal']}", fg=col)
        pay = core.PAYOUTS.get(sym)
        rs = risk.evaluate(self.entries, time.time(), risk.config(self.s["risk"]))
        lines = [f"Счёт {res['score']:+d}, уверенность {res['conf']}%, цена {res['price']:.5g}"]
        if res.get("exp_text"):
            lines.append(res["exp_text"])
        if pay:
            lines.append(f"Выплата {pay}%, безубыток {martingale.breakeven(pay / 100) * 100:.1f}%")
        if rs["stake"]:
            lines.append(f"Ставка по риск-менеджменту: {rs['stake']}")
        self.info.configure(text="\n".join(lines))
        self.chart = res["chart"]
        self.price = res["price"]
        self.draw()
        rows = [("Причины сигнала", "h")]
        for sc, txt in res["parts"]:
            rows.append((f"{sc:+d}  {txt}" if sc else f"     {txt}", "pos" if sc > 0 else "neg" if sc < 0 else "dim"))
        self.put(self.t_reason, rows)
        self.nb.select(0)

    # ---------- журнал ----------
    def show_journal(self):
        st = journal.stats(self.entries)
        rows = [("Журнал", "h"),
                (f"Побед {st['win']}, поражений {st['loss']}, ничьих {st['draw']}, ожидают {st['pending']}"
                 + (f", процент {st['rate']}%" if st["rate"] is not None else ""), None)]
        rs = risk.evaluate(self.entries, time.time(), risk.config(self.s["risk"]))
        rows.append((f"Сегодня: сделок {rs['n']}, серия проигрышей {rs['streak']}" +
                     (f", итог {rs['net']:+.2f}" if rs["net"] is not None else ""), "dim"))
        if rs["blocked"]:
            rows.append((rs["why"], "neg"))
        rows.append(("", None))
        for e in sorted(self.entries, key=lambda e: -e["ts"])[:60]:
            t = time.strftime("%d.%m %H:%M", time.localtime(e["ts"]))
            tag = {"win": "pos", "loss": "neg"}.get(e["status"], "dim")
            rows.append((f"{t}  {e['sym']} {e['itv']} {e['dir'].upper()}  {e['status']}", tag))
        self.put(self.t_jrn, rows)

    def settle_due(self):
        due = journal.due(self.entries)
        if not due or getattr(self, "_settling", False):
            return
        self._settling = True

        def work():
            try:
                for e in due:
                    try:
                        pv = core.preview(e["sym"], "1m")
                        journal.settle(e, pv["price"])
                    except Exception:
                        pass
                journal.save(JRN_PATH, self.entries)
                self.after(0, self.show_journal)
            finally:
                self._settling = False
        threading.Thread(target=work, daemon=True).start()

    def tick(self):
        self.settle_due()
        self.after(15000, self.tick)

    def refresh_payouts(self):
        if self.s["key"] and not self.busy:
            def work():
                try:
                    core.OTC_KEY = self.s["key"]
                    core.load_otc_catalog()
                    self.after(0, lambda: self.fill_pairs())
                except Exception:
                    pass
            threading.Thread(target=work, daemon=True).start()
        self.after(60000, self.refresh_payouts)


if __name__ == "__main__":
    App().mainloop()
