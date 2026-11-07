import json
import os
import threading
import time

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Ellipse, InstructionGroup, Line, Rectangle, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.core.window import Window
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, ObjectProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.slider import Slider
from kivy.uix.textinput import TextInput
from kivy.uix.widget import Widget

import alerts
import journal
import martingale
import risk
import signals_core as core

CYAN, VIOLET = (0.0, 0.85, 1.0, 1), (0.62, 0.36, 1.0, 1)
COLORS = {"call": (0.2, 0.95, 0.5, 1), "put": (1.0, 0.3, 0.42, 1),
          "wait": (0.6, 0.68, 0.85, 1), "pause": (1.0, 0.72, 0.2, 1)}
HEX = {"call": "33f280", "put": "ff4d6b", "wait": "99aad9", "pause": "ffb833"}
FAV = "Избранное"
SCAN_SCOPES = ("Текущая группа", "Все OTC", "Избранное", "Выбранные вручную")
STATUS_RU = {"win": ("плюс", "33f280"), "loss": ("минус", "ff4d6b"), "draw": ("ничья", "99aad9"),
             "stale": ("не проверен", "ffb833"), "pending": ("ждёт", "99aad9")}

DEFAULTS = {"methods": list(core.ALL_METHODS), "weights": {}, "thr": 35, "blackout": "", "mtf": True,
            "vol": True, "session": True, "exp": "Авто", "notify": True, "sound": True, "favs": [],
            "group": "", "pair": "", "interval": "5m", "custom": "",
            "indicators": list(core.DEFAULT_INDICATORS), "mg": {}, "mg2": {}, "risk": dict(risk.DEFAULT),
            "scan": {"scope": "Текущая группа", "picked": [], "pmin": "", "pmax": ""}}


def gradient_texture():
    tex = Texture.create(size=(1, 2), colorfmt="rgba")
    bottom, top = (6, 8, 20, 255), (30, 18, 62, 255)
    tex.blit_buffer(bytes(bottom + top), colorfmt="rgba", bufferfmt="ubyte")
    tex.mag_filter = "linear"
    return tex


class GlowBox(BoxLayout):
    """Стеклянная панель с неоновым свечением и бликом сверху."""
    glow_color = ListProperty([0.0, 0.85, 1.0, 1])
    glow_a = NumericProperty(0.5)
    fill = ListProperty([1, 1, 1, 0.07])
    radius = NumericProperty(dp(16))

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self._redraw, size=self._redraw, glow_color=self._redraw,
                  glow_a=self._redraw, fill=self._redraw)
        self._redraw()

    def _boost(self):
        return 0.0

    def _redraw(self, *_):
        x, y = self.pos
        w, h = self.size
        r, c, boost = self.radius, self.glow_color, self._boost()
        a = min(1.0, self.glow_a + boost)
        f = [min(1.0, v + boost * 0.25) for v in self.fill[:3]] + [self.fill[3]]
        self.canvas.before.clear()
        with self.canvas.before:
            for i in range(6, 0, -1):
                e = i * dp(2.5)
                Color(c[0], c[1], c[2], a * 0.035 * (7 - i))
                RoundedRectangle(pos=(x - e, y - e), size=(w + 2 * e, h + 2 * e), radius=[r + e])
            Color(*f)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[r])
            Color(1, 1, 1, 0.10 + boost * 0.1)
            RoundedRectangle(pos=(x + dp(2), y + h / 2), size=(max(w - dp(4), 0), max(h / 2 - dp(2), 0)),
                             radius=[r, r, dp(3), dp(3)])
            Color(c[0], c[1], c[2], 0.9 * a)
            Line(rounded_rectangle=(x, y, w, h, r), width=dp(1.3))
            Color(1, 1, 1, 0.25)
            Line(points=[x + r, y + h - dp(1), x + w - r, y + h - dp(1)], width=dp(1))


class GlowButton(ButtonBehavior, GlowBox):
    text = StringProperty("")
    toggle = BooleanProperty(False)
    active = BooleanProperty(False)
    dim = BooleanProperty(False)   # приглушать выключенную кнопку-переключатель

    def __init__(self, font=16, **kw):
        super().__init__(**kw)
        self.label = Label(text=self.text, bold=True, font_size=sp(font), color=(1, 1, 1, 1),
                           halign="center", valign="middle")
        self.label.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.add_widget(self.label)
        self.bind(text=lambda _w, v: setattr(self.label, "text", v),
                  state=self._redraw, active=self._on_active, disabled=self._dis)
        self._on_active()

    def _on_active(self, *_):
        if self.dim:
            self.opacity = 1 if self.active else 0.4
        self._redraw()

    def _dis(self, *_):
        self.opacity = 0.45 if self.disabled else 1

    def _boost(self):
        if self.state == "down":
            return 0.55
        return 0.25 if self.active else 0.0

    def on_release(self):
        if self.toggle:
            self.active = not self.active


class CandleChart(Widget):
    """Мини-график: свечи, уровни (жёлтые пунктиры), экстремумы (точки), цена (белый пунктир)."""
    data = ObjectProperty(None, allownone=True)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.bind(pos=self.redraw, size=self.redraw, data=self.redraw)

    def redraw(self, *_):
        self.canvas.clear()
        d = self.data
        if not d or len(d["c"]) < 2 or self.width < 10 or self.height < 10:
            return
        o, h, l, c = d["o"], d["h"], d["l"], d["c"]
        n = len(c)
        lo, hi = min(l), max(h)
        span = (hi - lo) or 1e-9
        lo, hi = lo - span * 0.06, hi + span * 0.06
        span = hi - lo
        x0, y0, w, hh = self.x, self.y, self.width, self.height
        ypos = lambda p: y0 + (p - lo) / span * hh
        cw = w / n
        with self.canvas:
            for L in d.get("levels", []):
                if lo <= L <= hi:
                    Color(1.0, 0.72, 0.2, 0.55)
                    Line(points=[x0, ypos(L), x0 + w, ypos(L)], width=dp(1), dash_length=dp(4), dash_offset=dp(3))
            for i in range(n):
                up = c[i] >= o[i]
                Color(*(COLORS["call"] if up else COLORS["put"]))
                cx = x0 + (i + 0.5) * cw
                Line(points=[cx, ypos(l[i]), cx, ypos(h[i])], width=dp(1))
                top, bot = ypos(max(o[i], c[i])), ypos(min(o[i], c[i]))
                Rectangle(pos=(cx - cw * 0.32, bot), size=(cw * 0.64, max(top - bot, dp(1))))
            Color(*CYAN)
            for i, p in d.get("hp", []):
                Ellipse(pos=(x0 + (i + 0.5) * cw - dp(2.5), ypos(p) + dp(2)), size=(dp(5), dp(5)))
            Color(*VIOLET)
            for i, p in d.get("lp", []):
                Ellipse(pos=(x0 + (i + 0.5) * cw - dp(2.5), ypos(p) - dp(7)), size=(dp(5), dp(5)))
            Color(1, 1, 1, 0.45)
            Line(points=[x0, ypos(c[-1]), x0 + w, ypos(c[-1])], width=dp(1), dash_length=dp(2), dash_offset=dp(2))


def styled_input(**kw):
    return TextInput(multiline=False, write_tab=False, background_normal="", background_active="",
                     background_color=(1, 1, 1, 0.08), foreground_color=(1, 1, 1, 1),
                     cursor_color=CYAN, hint_text_color=(1, 1, 1, 0.38),
                     padding=[dp(12), dp(13)], **kw)


def payout_color(v):
    return "33f280" if v >= 90 else "00d9ff" if v >= 80 else "ffb833" if v >= 70 else "ff4d6b"


def by_payout(vals):
    """Сначала пары с наибольшей выплатой, потом по алфавиту; пары без выплаты в конце."""
    vals = list(vals)
    if not any(v in core.PAYOUTS for v in vals):
        return vals
    return sorted(vals, key=lambda v: (-(core.PAYOUTS.get(v) or -1), v))


class NeonOption(ButtonBehavior, BoxLayout):
    """Строка списка: название слева, выплата справа, подсветка выбранной. Раскладка через BoxLayout."""
    selected = BooleanProperty(False)

    def __init__(self, value, accent=CYAN, **kw):
        super().__init__(orientation="horizontal", size_hint_y=None, height=dp(50),
                         padding=[dp(18), 0, dp(14), 0], spacing=dp(6), **kw)
        self.value = value
        self.accent = accent
        self.name = Label(text=value, font_size=sp(15), halign="left", valign="middle", shorten=True,
                          shorten_from="right")
        self.name.bind(size=lambda w, v: setattr(w, "text_size", v))
        v = core.PAYOUTS.get(value)
        self.pay = Label(text=f"[color={payout_color(v)}]{v}%[/color]" if v else "", markup=True,
                         font_size=sp(15), bold=True, halign="right", valign="middle",
                         size_hint_x=None, width=dp(62))
        self.pay.bind(size=lambda w, val: setattr(w, "text_size", val))
        self.add_widget(self.name)
        self.add_widget(self.pay)
        self.bind(pos=self._draw, size=self._draw, state=self._draw, selected=self._draw)
        self._draw()

    def _draw(self, *_):
        a = self.accent
        self.canvas.before.clear()
        with self.canvas.before:
            Color(a[0], a[1], a[2], 0.38 if self.state == "down" else 0.22 if self.selected else 0.0)
            Rectangle(pos=self.pos, size=self.size)
            Color(a[0], a[1], a[2], 0.18)
            Line(points=[self.x + dp(10), self.y, self.right - dp(10), self.y], width=dp(1))
            if self.selected:
                Color(a[0], a[1], a[2], 1)
                Rectangle(pos=(self.x, self.y + dp(8)), size=(dp(4), self.height - dp(16)))
        on = self.selected or self.state == "down"
        self.name.color = (1, 1, 1, 1) if on else (0.86, 0.9, 1, 1)
        self.name.bold = bool(self.selected)


class NeonSpinner(ButtonBehavior, Label):
    """Поле выбора в неоновом стиле. По нажатию открывает окно со списком (как остальные окна приложения)."""
    glow = ListProperty([0.0, 0.85, 1.0, 1])
    values = ListProperty([])
    right_text = StringProperty("")
    title = StringProperty("Выбор")
    sort_pay = BooleanProperty(False)       # показывать список по убыванию выплаты

    def __init__(self, glow=CYAN, **kw):
        kw.pop("drop_min", None)
        super().__init__(bold=True, color=(1, 1, 1, 1), halign="center", valign="middle", **kw)
        self.glow = list(glow)
        self.tag = Label(markup=True, font_size=sp(13), bold=True, halign="right", valign="middle",
                         size_hint=(None, None), size=(dp(54), dp(30)))
        self.add_widget(self.tag)
        self.bind(pos=self._redraw, size=self._redraw, state=self._redraw, glow=self._redraw,
                  right_text=self._on_tag)
        self.bind(text=self._fit, size=self._fit, right_text=self._fit)
        self._redraw()
        self._fit()

    def _on_tag(self, *_):
        self.tag.text = self.right_text

    def _fit(self, *_):
        """Подбирает размер шрифта: длинное название сначала уменьшается, потом переносится на 2 строки."""
        tag = bool(self.right_text)
        pad_l, pad_r = (dp(16), dp(70)) if tag else (dp(8), dp(28))   # справа место под стрелку
        avail = max(dp(30), self.width - pad_l - pad_r)
        n = max(1, len(self.text))
        fs = min(sp(15), avail / (0.6 * n))
        self.padding = (pad_l, 0, pad_r, 0)
        self.halign = "left" if tag else "center"
        if fs >= sp(12):
            self.font_size, self.max_lines = fs, 1
        else:
            self.font_size, self.max_lines = sp(12), 2
        self.text_size = (self.width, self.height)

    def on_release(self):
        vals = by_payout(self.values) if self.sort_pay else list(self.values)
        if not vals:
            return
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=0)
        box.bind(minimum_height=box.setter("height"))
        pop = Popup(title=self.title, size_hint=(0.92, None), background="",
                    background_color=(0.04, 0.05, 0.12, 1), separator_color=self.glow)
        for v in vals:
            o = NeonOption(v, accent=self.glow)
            o.selected = (v == self.text)
            o.bind(on_release=lambda w: (setattr(self, "text", w.value), pop.dismiss()))
            box.add_widget(o)
        sv = ScrollView()
        sv.add_widget(box)
        pop.content = sv
        pop.height = min(len(vals) * dp(50) + dp(60) + dp(16), Window.height * 0.8)
        pop.open()

    def _redraw(self, *_):
        x, y = self.pos
        w, h = self.size
        c = self.glow
        down = self.state == "down"
        self.tag.pos = (x + w - dp(66), y + (h - dp(30)) / 2)
        self.canvas.before.clear()
        with self.canvas.before:
            for i in range(5, 0, -1):
                e = i * dp(2)
                Color(c[0], c[1], c[2], (0.07 if down else 0.04) * (6 - i))
                RoundedRectangle(pos=(x - e, y - e), size=(w + 2 * e, h + 2 * e), radius=[dp(12) + e])
            Color(c[0] * 0.18 + 0.03, c[1] * 0.18 + 0.03, c[2] * 0.18 + 0.08, 0.95)
            RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(12)])
            Color(1, 1, 1, 0.10)
            RoundedRectangle(pos=(x + dp(2), y + h / 2), size=(max(w - dp(4), 0), max(h / 2 - dp(2), 0)),
                             radius=[dp(12), dp(12), dp(3), dp(3)])
            Color(c[0], c[1], c[2], 1 if down else 0.85)
            Line(rounded_rectangle=(x, y, w, h, dp(12)), width=dp(1.3))
            ax, ay = x + w - dp(16), y + h / 2          # стрелка вниз
            Color(c[0], c[1], c[2], 1)
            Line(points=[ax - dp(5), ay + dp(2.5), ax, ay - dp(2.5), ax + dp(5), ay + dp(2.5)], width=dp(1.6))


def styled_spinner(glow=CYAN, **kw):
    return NeonSpinner(glow=glow, **kw)


def wrap_label(text="", size=14, color=(1, 1, 1, 0.85), markup=False, **kw):
    lb = Label(text=text, font_size=sp(size), color=color, markup=markup, size_hint_y=None,
               halign="left", valign="top", **kw)
    lb.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
    lb.bind(texture_size=lambda w, v: setattr(w, "height", v[1] + dp(4)))
    return lb


CHIPS = [("tech", "Технический", CYAN), ("levels", "Уровни", (1.0, 0.72, 0.2, 1)),
         ("breakout", "Пробой", (1.0, 0.5, 0.15, 1)), ("candles", "Свечи", (1.0, 0.3, 0.42, 1)),
         ("patterns", "Фигуры", VIOLET), ("news", "Фундаментал", (0.2, 0.95, 0.5, 1))]
WEIGHT_KEYS = [("tech", "Технический"), ("levels", "Уровни"), ("breakout", "Пробой"),
               ("candles", "Свечи"), ("patterns", "Фигуры")]


class Root(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=dp(10), spacing=dp(6), **kw)
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._bg = Rectangle(texture=gradient_texture(), pos=self.pos, size=self.size)
        self.bind(pos=self._upd_bg, size=self._upd_bg)

        self.dir = App.get_running_app().user_data_dir
        self.s = dict(DEFAULTS)
        self.s.update(self._read_json("settings.json", {}))
        self.s["indicators"] = [k for k in self.s.get("indicators", []) if k in core.INDICATORS]
        self.names = {**{k: v[0] for k, v in core.METHODS.items()}, **{k: v[0] for k, v in core.INDICATORS.items()}}
        self.entries = journal.load(os.path.join(self.dir, "journal.json"))
        self.last_sig = {}
        self.settling = set()
        self._busy = False
        self._ev = None
        core.OTC_KEY = self._read_text("otc_key.txt")
        self._restore_catalog()

        # ── заголовок ──
        head = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))

        def hbtn(text, col, fill, cb):
            btn = GlowButton(text=text, font=11, radius=dp(12), glow_color=list(col), glow_a=0.45, fill=fill)
            btn.bind(on_release=lambda *_: cb())
            head.add_widget(btn)
            return btn
        self.j_btn = hbtn("Журнал", COLORS["call"], [0.05, 0.2, 0.12, 0.9], self.open_journal)
        hbtn("Индикаторы", CYAN, [0.0, 0.2, 0.26, 0.9], self.open_indicators)
        hbtn("Мартингейл", COLORS["put"], [0.3, 0.05, 0.1, 0.9], self.open_martingale)
        self.set_btn = hbtn("Настройки", VIOLET, [0.2, 0.1, 0.36, 0.9], self.open_settings)

        # ── пара ──
        row0 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.group = styled_spinner(glow=VIOLET, title="Группа инструментов", text="", values=[], size_hint_x=0.46)
        self.pair = styled_spinner(glow=CYAN, title="Пара (сверху максимальная выплата)", text="", values=[],
                                  sort_pay=True)
        self.group.bind(text=self.on_group)
        self.pair.bind(text=lambda *_: (self.update_fav_btn(), self.update_payout(), self.schedule_preview()))
        row0.add_widget(self.group)
        row0.add_widget(self.pair)

        row1 = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.interval = styled_spinner(glow=(1.0, 0.72, 0.2, 1), title="Время свечи", text=self.s["interval"], values=core.TF_LIST,
                                       size_hint_x=None, width=dp(100))
        self.fav_btn = GlowButton(text="В избранное", font=13, radius=dp(12), glow_color=[1.0, 0.72, 0.2, 1],
                                  glow_a=0.45, fill=[0.3, 0.2, 0.0, 0.9])
        self.fav_btn.bind(on_release=lambda *_: self.toggle_fav())
        self.all_btn = GlowButton(text="Все методы", font=13, radius=dp(12), glow_color=[1, 1, 1, 1],
                                  glow_a=0.35, fill=[1, 1, 1, 0.12])
        self.all_btn.bind(on_release=lambda *_: self.select_all())
        for w in (self.interval, self.fav_btn, self.all_btn):
            row1.add_widget(w)
        self.interval.bind(text=self.on_interval)

        row1b = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
        self.exp_sp = styled_spinner(glow=COLORS["call"], title="Экспирация сигнала (Авто — подбирает приложение)",
                                     text=self.exp_label(self.s.get("exp", "Авто")), values=self.EXP_VALUES)
        self.exp_sp.bind(text=lambda *_: self.on_exp())
        row1b.add_widget(self.exp_sp)

        # ── методы ──
        self.chips = {}
        mrow1 = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        mrow2 = BoxLayout(size_hint_y=None, height=dp(36), spacing=dp(6))
        for i, (key, name, col) in enumerate(CHIPS):
            b = GlowButton(text=name, font=13, toggle=True, dim=True, active=key in self.s["methods"],
                           radius=dp(12), glow_color=list(col), glow_a=0.55,
                           fill=[col[0] * 0.2, col[1] * 0.2, col[2] * 0.2, 0.9])
            b.bind(active=lambda *_: self.save_settings())
            self.chips[key] = b
            (mrow1 if i < 3 else mrow2).add_widget(b)

        # ── кнопки действий ──
        row3 = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.btn = GlowButton(text="Анализ", glow_color=list(CYAN), glow_a=0.6, fill=[0.0, 0.25, 0.32, 0.9])
        self.btn.bind(on_release=lambda *_: self.run())
        self.scan_btn = GlowButton(text="Сканер", glow_color=list(VIOLET), glow_a=0.6, fill=[0.2, 0.1, 0.36, 0.9])
        self.scan_btn.bind(on_release=lambda *_: self.open_scan())
        self.auto = GlowButton(text="Авто", toggle=True, size_hint_x=0.4, glow_color=list(COLORS["call"]),
                               glow_a=0.35, fill=[0.05, 0.2, 0.12, 0.9])
        self.auto.bind(active=self.on_auto)
        for b in (self.btn, self.scan_btn, self.auto):
            row3.add_widget(b)

        # ── карточка сигнала ──
        self.card = GlowBox(orientation="vertical", size_hint_y=None, height=dp(144), padding=dp(6),
                            radius=dp(20))
        self.signal = Label(text="Выберите пару и нажмите «Анализ»", font_size=sp(21), bold=True,
                            halign="center", valign="middle")
        self.signal.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.info = Label(text="", font_size=sp(12), color=(1, 1, 1, 0.78), size_hint_y=None, height=dp(56),
                          halign="center", valign="top")
        self.info.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.timer = Label(text="", font_size=sp(12), bold=True, size_hint_y=None, height=dp(20))
        self.card.add_widget(self.signal)
        self.card.add_widget(self.info)
        self.card.add_widget(self.timer)
        self.style_signal("wait")

        # ── график ──
        cbox = GlowBox(size_hint_y=None, height=dp(130), padding=dp(6), glow_color=[0.0, 0.85, 1.0, 1],
                       glow_a=0.2, radius=dp(14))
        self.chart = CandleChart()
        cbox.add_widget(self.chart)

        # ── причины ──
        self.reasons = wrap_label("", size=14, markup=True)
        self.reasons.bind(on_ref_press=self._on_ref)
        self._list_itv, self._list_results = None, []
        sv = ScrollView(bar_width=dp(3))
        sv.add_widget(self.reasons)
        panel = GlowBox(padding=dp(10), glow_color=[0.62, 0.36, 1.0, 1], glow_a=0.15)
        panel.add_widget(sv)

        warn = Label(text="Не гарантия прибыли. Бинарные опционы - высокий риск.",
                     font_size=sp(11), color=(1, 1, 1, 0.45), size_hint_y=None, height=dp(18))

        for w in (head, row0, row1, row1b, mrow1, mrow2, row3, self.card, cbox, panel, warn):
            self.add_widget(w)

        self._build_settings()
        self._build_indicators()
        self.refresh_groups(initial=True)
        self.update_fav_btn()
        self.update_journal_btn()
        self._tick_ev = Clock.schedule_interval(self._tick, 15)
        Clock.schedule_interval(self._tick_timer, 1)
        Clock.schedule_once(self.refresh_payouts, 3)
        Clock.schedule_interval(self.refresh_payouts, 60)   # выплаты Pocket Option меняются часто

    # ───────── хранение ─────────
    def _read_json(self, name, default):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default

    def _read_text(self, name):
        try:
            with open(os.path.join(self.dir, name), encoding="utf-8") as f:
                return f.read().strip()
        except Exception:
            return ""

    def _write(self, name, text):
        try:
            with open(os.path.join(self.dir, name), "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            pass

    def save_settings(self, *_):
        s = self.s
        s["methods"] = [k for k, b in self.chips.items() if b.active]
        s["weights"] = {k: round(sl.value, 1) for k, sl in self.wsliders.items()} if hasattr(self, "wsliders") else s["weights"]
        if hasattr(self, "thr"):
            s["thr"] = int(self.thr.text or 35)
            s["blackout"] = self.blackout.text
            s["custom"] = self.custom.text
            for k, b in (("mtf", self.t_mtf), ("vol", self.t_vol), ("session", self.t_ses),
                         ("notify", self.t_notify), ("sound", self.t_sound)):
                s[k] = b.active
        if hasattr(self, "r_dep"):
            s["risk"] = {"on": self.r_on.active, "deposit": self.r_dep.text, "stake_pct": self.r_stake.text,
                         "loss_pct": self.r_loss.text, "streak": self.r_streak.text, "pause_min": self.r_pause.text}
        s["interval"] = self.interval.text
        s["group"], s["pair"] = self.group.text, self.pair.text
        self._write("settings.json", json.dumps(s))

    def _upd_bg(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size

    # ───────── настройки ─────────
    def _build_settings(self):
        s = self.s
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=dp(8))
        box.bind(minimum_height=box.setter("height"))

        def row(*widgets, h=44):
            r = BoxLayout(size_hint_y=None, height=dp(h), spacing=dp(8))
            for w in widgets:
                r.add_widget(w)
            box.add_widget(r)

        box.add_widget(wrap_label("Свой тикер (например EURUSD_otc или #AAPL_otc)", 13))
        self.custom = styled_input(text=s["custom"], hint_text="Необязательно")
        self.custom.bind(text=lambda *_: (self.update_payout(), self.schedule_preview()))
        row(self.custom)

        box.add_widget(wrap_label("Ключ OTCharts (хранится только на телефоне)", 13))
        self.key = styled_input(text=core.OTC_KEY, hint_text="API-ключ для OTC-пар", password=True)
        self.key.bind(text=self._on_key)
        self.paste_btn = GlowButton(text="Вставить", font=13, size_hint_x=0.28, radius=dp(12),
                                    glow_color=list(CYAN), glow_a=0.5, fill=[0.0, 0.2, 0.26, 0.9])
        self.paste_btn.bind(on_release=lambda *_: self._paste_key())
        self.cat_btn = GlowButton(text="Список OTC", font=13, size_hint_x=0.34, radius=dp(12),
                                  glow_color=[1.0, 0.72, 0.2, 1], glow_a=0.5, fill=[0.3, 0.2, 0.0, 0.9])
        self.cat_btn.bind(on_release=lambda *_: self.load_catalog())
        row(self.key, self.paste_btn, self.cat_btn)

        box.add_widget(wrap_label("Порог сигнала и окна новостей (UTC, через запятую)", 13))
        self.thr = styled_input(text=str(s["thr"]), input_filter="int", hint_text="Порог", size_hint_x=0.25)
        self.blackout = styled_input(text=s["blackout"], hint_text="12:25-12:45, 14:00-14:20")
        row(self.thr, self.blackout)

        box.add_widget(wrap_label("Фильтры (сигнал блокируется, если условие не выполнено)", 13))

        def toggle(text, key, col):
            b = GlowButton(text=text, font=13, toggle=True, dim=True, active=s[key], radius=dp(12),
                           glow_color=list(col), glow_a=0.55, fill=[col[0] * 0.2, col[1] * 0.2, col[2] * 0.2, 0.9])
            return b
        self.t_mtf = toggle("Старший ТФ", "mtf", CYAN)
        self.t_vol = toggle("Волатильность", "vol", (1.0, 0.72, 0.2, 1))
        self.t_ses = toggle("Сессии", "session", VIOLET)
        row(self.t_mtf, self.t_vol, self.t_ses, h=40)

        box.add_widget(wrap_label("Уведомления о новых сигналах в режиме «Авто»", 13))
        self.t_notify = toggle("Уведомления", "notify", COLORS["call"])
        self.t_sound = toggle("Звук и вибрация", "sound", COLORS["call"])
        row(self.t_notify, self.t_sound, h=40)

        box.add_widget(wrap_label("[b]Управление рисками[/b] (можно отключить)", 15, color=COLORS["put"], markup=True))
        rk = {**risk.DEFAULT, **s.get("risk", {})}
        self.r_on = GlowButton(text="Управление рисками", font=13, toggle=True, dim=True, active=bool(rk["on"]),
                               radius=dp(12), glow_color=list(COLORS["put"]), glow_a=0.55,
                               fill=[0.3, 0.05, 0.1, 0.9])
        row(self.r_on, h=40)

        def rfield(label, key, flt="float"):
            lab = Label(text=label, font_size=sp(13), size_hint_x=0.6, halign="left", valign="middle")
            lab.bind(size=lambda w, v: setattr(w, "text_size", v))
            ti = styled_input(text=str(rk[key]), input_filter=flt)
            row(lab, ti, h=44)
            return ti
        self.r_dep = rfield("Депозит", "deposit")
        self.r_stake = rfield("Ставка, % депозита", "stake_pct")
        self.r_loss = rfield("Лимит убытка за день, %", "loss_pct")
        self.r_streak = rfield("Пауза после проигрышей подряд", "streak", "int")
        self.r_pause = rfield("Длина паузы, минут", "pause_min", "int")
        box.add_widget(wrap_label("Считается по сигналам из журнала: предполагается, что вы входите по каждому. "
                                  "При достижении лимита новые сигналы не показываются, пока не кончится пауза или "
                                  "день. Выключите кнопкой выше, и ограничений не будет.",
                                  12, color=(1, 1, 1, 0.55)))

        box.add_widget(wrap_label("Вес методов: 0 отключает метод, 2 удваивает его вклад", 13))
        self.wsliders, self.wlabels = {}, {}
        for key, name in WEIGHT_KEYS:
            lab = Label(text=name, font_size=sp(13), size_hint_x=0.34, halign="left", valign="middle")
            lab.bind(size=lambda w, v: setattr(w, "text_size", v))
            val = Label(text="", font_size=sp(13), size_hint_x=0.14)
            sl = Slider(min=0, max=2, step=0.1, value=float(s["weights"].get(key, 1.0)))
            sl.bind(value=lambda w, v, vl=val: setattr(vl, "text", f"x{v:.1f}"))
            val.text = f"x{sl.value:.1f}"
            self.wsliders[key] = sl
            row(lab, sl, val, h=34)

        self.reset_w = GlowButton(text="Сбросить веса", font=13, radius=dp(12), glow_color=[1, 1, 1, 1],
                                  glow_a=0.3, fill=[1, 1, 1, 0.1])
        self.reset_w.bind(on_release=lambda *_: [setattr(sl, "value", 1.0) for sl in self.wsliders.values()])
        row(self.reset_w, h=40)

        sv = ScrollView()
        sv.add_widget(box)
        self.settings_popup = Popup(title="Настройки", content=sv, size_hint=(0.96, 0.92), background="",
                                    background_color=(0.04, 0.05, 0.12, 1), separator_color=CYAN)
        self.settings_popup.bind(on_dismiss=lambda *_: self.save_settings())

    def open_settings(self):
        self.settings_popup.open()

    def _paste_key(self):
        """Запасной способ вставки ключа: читает буфер обмена кнопкой."""
        try:
            from kivy.core.clipboard import Clipboard
            text = (Clipboard.paste() or "").strip()
        except Exception:
            text = ""
        if text:
            self.key.text = text
            self.paste_btn.text = f"Вставлено ({len(text)})"
        else:
            self.paste_btn.text = "Буфер пуст"

    def _on_key(self, _w, text):
        core.OTC_KEY = text.strip()
        self._write("otc_key.txt", text.strip())

    # ───────── индикаторы и сочетания ─────────
    def _build_indicators(self):
        where_ru = {"тренд": "трендовый рынок", "боковик": "боковой рынок", "оба": "любой рынок"}
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=dp(8))
        box.bind(minimum_height=box.setter("height"))
        self.advice = wrap_label("", 13, markup=True)
        box.add_widget(self.advice)
        box.add_widget(wrap_label("Готовые сочетания. Это общие правила технического анализа, на истории они "
                                  "не проверены. Можно включать несколько наборов: индикаторы объединяются. Повторное нажатие "
                                  "отключает набор (общие с другими включёнными наборами остаются):", 12, color=(1, 1, 1, 0.6)))
        self.preset_btns = {}
        for p in core.PRESETS:
            btn = GlowButton(text=p["name"], font=14, size_hint_y=None, height=dp(42), radius=dp(12),
                             glow_color=list(VIOLET), glow_a=0.35, fill=[0.12, 0.07, 0.22, 0.9])
            btn.bind(on_release=lambda _w, p=p: self.apply_preset(p))
            self.preset_btns[p["name"]] = btn
            box.add_widget(btn)
            box.add_widget(wrap_label(", ".join(core.INDICATORS[k][0] for k in p["ind"]) + ". " + p["text"],
                                      12, color=(1, 1, 1, 0.6)))
        self.ind_btns = {}
        cur = None
        for key, (name, grp, _cap, _f, desc, where) in core.INDICATORS.items():
            if grp != cur:
                cur = grp
                box.add_widget(wrap_label(f"[b]{grp}[/b]", 15, color=CYAN, markup=True))
            r = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(8))
            btn = GlowButton(text=name, font=13, toggle=True, dim=True, active=key in self.s["indicators"],
                             radius=dp(12), size_hint_x=0.42, glow_color=list(CYAN), glow_a=0.5,
                             fill=[0.0, 0.2, 0.26, 0.9])
            btn.bind(active=lambda *_: self._on_ind_change())
            lab = Label(text=f"{desc} Подходит: {where_ru[where]}.", font_size=sp(11), color=(1, 1, 1, 0.65),
                        halign="left", valign="middle")
            lab.bind(size=lambda w, v: setattr(w, "text_size", v))
            r.add_widget(btn)
            r.add_widget(lab)
            box.add_widget(r)
            self.ind_btns[key] = btn
        sv = ScrollView()
        sv.add_widget(box)
        self.ind_popup = Popup(title="Индикаторы и сочетания", content=sv, size_hint=(0.96, 0.92), background="",
                               background_color=(0.04, 0.05, 0.12, 1), separator_color=CYAN)
        self.ind_popup.bind(on_dismiss=lambda *_: self.save_settings())
        for b in self.chips.values():
            b.bind(active=lambda *_: self._on_ind_change())
        self._on_ind_change()

    def open_indicators(self):
        self.ind_popup.open()

    def _preset_on(self, p):
        """Набор включён, если все его индикаторы и методы уже выбраны."""
        ind = set(self.s["indicators"])
        meth = {k for k, b in self.chips.items() if b.active}
        return set(p["ind"]) <= ind and (set(p["methods"]) - {"news"}) <= meth

    def _mark_presets(self):
        for p in core.PRESETS:
            btn = self.preset_btns[p["name"]]
            on = self._preset_on(p)
            btn.text = p["name"] + ("  (включён)" if on else "")
            btn.glow_a = 1.0 if on else 0.35
            btn.fill = [0.46, 0.22, 0.88, 0.95] if on else [0.12, 0.07, 0.22, 0.9]
            btn.label.color = (1, 1, 1, 1) if on else (0.78, 0.74, 0.92, 1)

    def _on_ind_change(self):
        self.s["indicators"] = [k for k, b in self.ind_btns.items() if b.active]
        self._mark_presets()
        colors = {"ok": "33f280", "warn": "ffb833", "tip": "00d9ff"}
        lines = [f"[color={colors[k]}]{t}[/color]" for k, t in core.advise(self.s["indicators"])]
        self.advice.text = f"[b]Выбрано индикаторов: {len(self.s['indicators'])}[/b]\n" + "\n".join(lines)

    def apply_preset(self, p):
        """Наборы можно включать по несколько: индикаторы и методы объединяются."""
        if self._preset_on(p):
            others = [q for q in core.PRESETS if q is not p and self._preset_on(q)]
            keep_i = set().union(*[set(q["ind"]) for q in others]) if others else set()
            keep_m = set().union(*[set(q["methods"]) for q in others]) if others else set()
            for k, btn in self.ind_btns.items():
                if k in p["ind"] and k not in keep_i:
                    btn.active = False
            for k, btn in self.chips.items():
                if k in p["methods"] and k not in keep_m and k != "news":
                    btn.active = False
            if not any(b.active for b in self.chips.values()):
                self.chips["tech"].active = True
            note = f"Набор «{p['name']}» отключён"
        else:
            for k in p["ind"]:
                self.ind_btns[k].active = True
            for k in p["methods"]:
                if k != "news":
                    self.chips[k].active = True
            note = f"Набор «{p['name']}» добавлен"
        self._on_ind_change()
        self.advice.text = f"[b]{note}[/b]\n" + self.advice.text
        self.save_settings()

    # ───────── калькулятор мартингейла ─────────
    def open_martingale(self):
        """Простой калькулятор: ставка, доходность, длина серии -> таблица ставок."""
        m = {"first": "10", "payout": "80", "steps": "7"}
        m.update(self.s.get("mg2", {}))
        v = core.PAYOUTS.get(self.current_symbol())
        if v:
            m["payout"] = str(v)
        cfg = self.risk_cfg()
        if cfg["on"] and risk.stake(cfg):
            m["first"] = f"{risk.stake(cfg):g}"          # первая ставка из плана рисков
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=dp(8))
        box.bind(minimum_height=box.setter("height"))
        self.m2_in = {}

        def field(key, label):
            lab = wrap_label(label, 14, color=(1, 1, 1, 0.8))
            ti = styled_input(text=str(m[key]), input_filter="float", size_hint_y=None, height=dp(46))
            ti.bind(text=lambda *_: self._m2_calc())
            box.add_widget(lab)
            box.add_widget(ti)
            self.m2_in[key] = ti
        field("first", "Ставка (первая)")
        field("payout", "Доходность, % (например 80)")
        field("steps", "Максимальная убыточная серия")
        self.m2_grid = GridLayout(cols=3, size_hint_y=None, row_default_height=dp(36), row_force_default=True,
                                  spacing=dp(2))
        self.m2_grid.bind(minimum_height=self.m2_grid.setter("height"))
        box.add_widget(self.m2_grid)
        self.m2_sum = wrap_label("", 13, markup=True)
        box.add_widget(self.m2_sum)
        full = GlowButton(text="Подробный расчёт: шансы, депозит", font=14, size_hint_y=None, height=dp(46),
                          radius=dp(12), glow_color=list(COLORS["put"]), glow_a=0.6, fill=[0.3, 0.05, 0.1, 0.9])
        full.bind(on_release=lambda *_: (self.m2_pop.dismiss(), self.open_martingale_full()))
        box.add_widget(full)
        sv = ScrollView()
        sv.add_widget(box)
        pop = Popup(title="Калькулятор мартингейла", content=sv, size_hint=(0.96, 0.92), background="",
                    background_color=(0.04, 0.05, 0.12, 1), separator_color=COLORS["put"])
        pop.bind(on_dismiss=lambda *_: self._m2_save())
        self.m2_pop = pop
        self._m2_calc()
        pop.open()

    def _m2_save(self):
        self.s["mg2"] = {k: ti.text for k, ti in self.m2_in.items()}
        self.save_settings()

    def _m2_calc(self, *_):
        first, pay, steps = (self._num(self.m2_in[k].text) for k in ("first", "payout", "steps"))
        g = self.m2_grid
        g.clear_widgets()
        if None in (first, pay, steps) or not (first > 0 and 1 <= pay <= 300 and 1 <= steps <= 15):
            self.m2_sum.text = "Введите ставку больше 0, доходность 1-300% и серию от 1 до 15."
            return
        steps = int(steps)
        p = martingale.plan(1e12, first, pay / 100, steps, "recover")

        def cell(text, bold=False, col=(1, 1, 1, 0.9)):
            g.add_widget(Label(text=text, bold=bold, color=col, font_size=sp(14)))
        for h in ("Ступень", "Ставка", "Всего вложено"):
            cell(h, True, (0, 0.85, 1, 1))
        for r in p["rows"]:
            cell(str(r["k"]))
            cell(f"{r['stake']:.2f}")
            cell(f"{r['total']:.2f}")
        last = p["rows"][-1]
        cfg = risk.config(self.s.get("risk"))
        rnote = ""
        if cfg["deposit"]:
            pct = 100 * last["total"] / cfg["deposit"]
            warn = cfg["on"] and cfg["loss_pct"] and pct > cfg["loss_pct"]
            rnote = (f"Вся серия = [b]{pct:.0f}%[/b] депозита {cfg['deposit']:g}."
                     + (f" [color=ff4d6b]Это больше дневного лимита убытка ({cfg['loss_pct']:g}%): "
                        f"защита остановит сигналы раньше, чем серия дойдёт до конца.[/color]" if warn else "")
                     + "\n")
        self.m2_sum.text = rnote + (f"Чтобы выдержать {steps} проигрышей подряд, нужно иметь на счёте "
                            f"[b]{last['total']:.2f}[/b].\nВыигрыш на любой ступени возвращает все потери и "
                            f"даёт чистую прибыль [b]{first * pay / 100:.2f}[/b].\n"
                            f"[color=99aad9]Мартингейл не создаёт преимущества: если процент выигрышей ниже "
                            f"{100 / (1 + pay / 100):.1f}%, ставки убыточны в среднем, а серия проигрышей "
                            f"съедает депозит целиком.[/color]")

    def open_martingale_full(self):
        mg = {"deposit": "1000", "first": "10", "payout": "85", "steps": "5", "mult": "2.2", "winrate": "",
              "mode": "Возместить убыток"}
        mg.update(self.s.get("mg", {}))
        cfg = self.risk_cfg()
        if cfg["on"] and cfg["deposit"]:
            mg["deposit"] = f"{cfg['deposit']:g}"
            if risk.stake(cfg):
                mg["first"] = f"{risk.stake(cfg):g}"
        v = core.PAYOUTS.get(self.current_symbol())
        if v:
            mg["payout"] = str(v)               # выплата выбранной пары из Pocket Option
        st = journal.stats(self.entries)
        if not mg["winrate"]:
            mg["winrate"] = f"{st['rate']:.0f}" if st["n"] >= 20 else "55"
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=dp(8))
        box.bind(minimum_height=box.setter("height"))
        self.mg_in = {}

        def field(key, label):
            r = BoxLayout(size_hint_y=None, height=dp(44), spacing=dp(8))
            lab = Label(text=label, font_size=sp(13), size_hint_x=0.58, halign="left", valign="middle")
            lab.bind(size=lambda w, v: setattr(w, "text_size", v))
            ti = styled_input(text=str(mg[key]))
            ti.bind(text=lambda *_: self._mg_calc())
            r.add_widget(lab)
            r.add_widget(ti)
            box.add_widget(r)
            self.mg_in[key] = ti
        field("deposit", "Депозит")
        field("first", "Первая ставка")
        field("payout", "Выплата брокера, %")
        field("steps", "Ступеней (1-12)")
        field("winrate", "Процент выигрышей, %")
        self.mg_mode = styled_spinner(glow=COLORS["put"], title="Режим расчёта", text=mg["mode"], values=("Возместить убыток", "Множитель"),
                                      size_hint_y=None, height=dp(44))
        self.mg_mode.bind(text=lambda *_: self._mg_calc())
        box.add_widget(self.mg_mode)
        field("mult", "Множитель (режим «Множитель»)")
        box.add_widget(wrap_label("«Возместить убыток»: каждая ставка рассчитывается так, чтобы выигрыш покрыл все "
                                  "прошлые потери и принёс прибыль первой ставки. «Множитель»: ставка умножается "
                                  "на заданное число.", 12, color=(1, 1, 1, 0.55)))
        self.mg_out = wrap_label("", 13, markup=True)
        box.add_widget(self.mg_out)
        sv = ScrollView()
        sv.add_widget(box)
        pop = Popup(title="Калькулятор мартингейла", content=sv, size_hint=(0.96, 0.92), background="",
                    background_color=(0.04, 0.05, 0.12, 1), separator_color=COLORS["put"])
        pop.bind(on_dismiss=lambda *_: self._mg_save())
        self._mg_calc()
        pop.open()

    def _mg_save(self):
        d = {k: ti.text for k, ti in self.mg_in.items()}
        d["mode"] = self.mg_mode.text
        self.s["mg"] = d
        self.save_settings()

    def _mg_calc(self, *_):
        def num(k):
            try:
                return float(self.mg_in[k].text.replace(",", ".").replace(" ", ""))
            except Exception:
                return None
        dep, first, pay, steps, wr, mult = (num(k) for k in ("deposit", "first", "payout", "steps", "winrate", "mult"))
        mode = "mult" if self.mg_mode.text.startswith("Множ") else "recover"
        if None in (dep, first, pay, steps, wr) or (mode == "mult" and mult is None):
            self.mg_out.text = "Заполните все поля числами."
            return
        if not (dep > 0 and first > 0 and 1 <= pay <= 300 and 1 <= steps <= 12 and 1 <= wr <= 99
                and (mode != "mult" or mult > 1)):
            self.mg_out.text = ("Проверьте значения: ставка и депозит больше нуля, выплата 1-300%, "
                                "ступеней от 1 до 12, процент выигрышей 1-99%, множитель больше 1.")
            return
        steps = int(steps)
        pr = pay / 100
        p = martingale.plan(dep, first, pr, steps, mode, mult or 2.2, wr / 100)
        q = 1 - wr / 100
        wait = (1 - q ** steps) / ((1 - q) * q ** steps)
        ev_col = "33f280" if p["ev"] > 0 else "ff4d6b"
        lines = [
            f"Безубыточность при выплате {pay:.0f}%: [b]{p['breakeven'] * 100:.1f}%[/b] выигрышей. "
            + ("Ваш процент выше." if wr / 100 > p["breakeven"] else "[color=ff4d6b]Ваш процент ниже: ставки убыточны в среднем.[/color]"),
            f"Средний результат одной серии при {wr:.0f}% выигрышей: [color={ev_col}]{p['ev']:+.2f}[/color].",
            f"Шанс проиграть все {steps} ступеней подряд: [b]{p['p_bust'] * 100:.2f}%[/b]. Потеря: "
            f"[color=ff4d6b]{p['series_loss']:.2f}[/color] ({100 * p['series_loss'] / dep:.0f}% депозита).",
            f"Такая серия из {steps} проигрышей в среднем случается примерно раз в {wait:.0f} сделок.",
            ("Депозита хватает на все ступени." if p["affordable"] == steps else
             f"[color=ff4d6b]Депозита хватает только на {p['affordable']} из {steps} ступеней.[/color]"),
            f"Первая ставка, при которой депозит выдержит {steps} проигрышей подряд: не больше "
            f"[b]{martingale.max_first_stake(dep, pr, steps, mode, mult or 2.2):.2f}[/b].",
            "",
            "[b]Ступени:[/b] ставка | всего вложено | чистая прибыль при победе | шанс дойти",
        ]
        for r in p["rows"]:
            row = f"{r['k']}. {r['stake']:.2f} | {r['total']:.2f} | {r['profit']:+.2f} | {r['p_reach'] * 100:.1f}%"
            lines.append(row if r["afford"] else f"[color=ff4d6b]{row}  (не хватает депозита)[/color]")
        lines += ["", "[color=99aad9]Мартингейл не создаёт преимущества: ожидаемый результат определяют только "
                  "процент выигрышей и выплата, а система ставок лишь делает потери редкими, но крупными. "
                  "Расчёт предполагает независимые сделки с постоянным процентом выигрышей, на практике это не так. "
                  "Это арифметика, а не финансовая рекомендация.[/color]"]
        self.mg_out.text = "\n".join(lines)

    # ───────── каталог OTC ─────────
    def _restore_catalog(self):
        data = self._read_json("otc_catalog.json", None)
        if isinstance(data, dict):                      # новый формат: символы + выплаты
            syms = data.get("symbols")
            core.PAYOUTS.update(data.get("payout", {}))
        else:
            syms = data
        if syms:
            try:
                core.apply_otc_catalog(syms)
            except Exception:
                pass

    def load_catalog(self):
        self.cat_btn.disabled = True
        self.settings_popup.dismiss()
        self.style_signal("wait", "Загружаю список OTC...")
        threading.Thread(target=self._cat_work, daemon=True).start()

    def _cat_work(self):
        try:
            syms = core.load_otc_catalog()
            n = core.apply_otc_catalog(syms)
            self._write("otc_catalog.json", json.dumps({"symbols": syms, "payout": core.PAYOUTS}))
            Clock.schedule_once(lambda dt: self._cat_done(n, None))
        except Exception as e:
            err = str(e)
            Clock.schedule_once(lambda dt: self._cat_done(0, err))

    def refresh_payouts(self, *_):
        """Тихо обновляет выплаты (1 запрос из 7000 в сутки), список пар не трогает."""
        if not core.OTC_KEY or getattr(self, "_pay_busy", False):
            return
        self._pay_busy = True

        def work():
            try:
                syms = core.load_otc_catalog()
                self._write("otc_catalog.json", json.dumps({"symbols": syms, "payout": core.PAYOUTS}))
                Clock.schedule_once(lambda dt: self.update_payout())
            except Exception:
                pass
            self._pay_busy = False
        threading.Thread(target=work, daemon=True).start()

    def _cat_done(self, n, err):
        self.cat_btn.disabled = False
        if err:
            self.style_signal("pause", "Список не загружен")
            self.reasons.text = err
            return
        self.refresh_groups()
        otc = [g for g in core.GROUPS if g.startswith("OTC")]
        if otc:
            self.group.text = otc[0]
        self.style_signal("call", f"Загружено OTC: {n}")
        self.reasons.text = "\n".join(f"{g}: {len(core.GROUPS[g])}" for g in otc)

    # ───────── группы, избранное ─────────
    def group_values(self):
        return ([FAV] if self.s["favs"] else []) + list(core.GROUPS)

    def refresh_groups(self, initial=False):
        vals = self.group_values()
        saved_pair = self.s["pair"]          # запоминаем до того, как смена группы перезапишет пару
        self.group.values = vals
        want_g = self.s["group"] if initial else self.group.text
        if want_g not in vals:
            want_g = vals[0]
        self.group.text = want_g
        self.on_group(None, want_g, keep=saved_pair if initial else None)

    def on_group(self, _w, text, keep=None):
        vals = by_payout(self.s["favs"] if text == FAV else core.GROUPS.get(text, []))
        self.pair.values = vals
        self.pair.text = keep if keep in vals else (vals[0] if vals else "")
        self.update_fav_btn()
        self.save_settings()

    def current_symbol(self):
        return self.custom.text.strip() or self.pair.text

    def update_payout(self, *_):
        sym = self.custom.text.strip() or self.pair.text
        v = core.PAYOUTS.get(sym)
        self.pair.right_text = f"[color={payout_color(v)}]{v}%[/color]" if v else ""

    def update_fav_btn(self, *_):
        self.fav_btn.text = "Убрать из избр." if self.pair.text in self.s["favs"] else "В избранное"

    def toggle_fav(self):
        sym = self.current_symbol()
        if not sym:
            return
        favs = self.s["favs"]
        if sym in favs:
            favs.remove(sym)
        else:
            favs.append(sym)
        was = self.group.text
        self.group.values = self.group_values()
        if was == FAV:
            self.on_group(None, FAV if favs else list(core.GROUPS)[0])
            if not favs:
                self.group.text = list(core.GROUPS)[0]
        self.update_fav_btn()
        self.save_settings()
        self.style_signal("wait", f"Избранное: {len(favs)}")
        self.info.text = ", ".join(favs) if favs else "Список пуст"

    def select_all(self):
        for b in self.chips.values():
            b.active = True

    def schedule_preview(self, *_):
        ev = getattr(self, "_pv_ev", None)
        if ev:
            ev.cancel()
        self._pv_ev = Clock.schedule_once(self._start_preview, 0.5)

    def _start_preview(self, *_):
        sym = self.current_symbol()
        if not sym or self._busy:
            return
        itv = self.interval.text
        self._pv_id = getattr(self, "_pv_id", 0) + 1
        pid = self._pv_id

        def work():
            try:
                r, err = core.preview(sym, itv), None
            except Exception as e:
                r, err = None, str(e)
            Clock.schedule_once(lambda dt: self._preview_done(pid, sym, itv, r, err))
        threading.Thread(target=work, daemon=True).start()

    def _preview_done(self, pid, sym, itv, r, err):
        if pid != getattr(self, "_pv_id", 0) or self._busy:
            return
        if err or not r:
            self.chart.data = None
            self.info.text = (err or "Нет данных")[:120]
            return
        self.style_signal("wait", "Нажмите «Анализ»")
        v = core.PAYOUTS.get(sym)
        self.info.text = f"{sym.upper()} {itv} | цена {r['price']:.5g}" + (f"\nВыплата {v}%" if v else "")
        self.chart.data = r["chart"]
        self.reasons.text = ""

    def on_interval(self, *_):
        self.schedule_preview()
        self.save_settings()
        if self.auto.active:
            self.on_auto(None, True)

    # ───────── карточка ─────────
    def style_signal(self, kind, text=None):
        self._sig = None                     # таймер сигнала включает только show()
        c = COLORS[kind]
        self.card.glow_color = list(c)
        self.card.fill = [c[0] * 0.2 + 0.03, c[1] * 0.2 + 0.03, c[2] * 0.2 + 0.06, 0.92]
        self.signal.color = c
        if text is not None:
            self.signal.text = text
        Animation.cancel_all(self.card, "glow_a")
        if kind in ("call", "put"):
            pulse = (Animation(glow_a=1.0, d=0.9, t="in_out_sine")
                     + Animation(glow_a=0.4, d=0.9, t="in_out_sine"))
            pulse.repeat = True
            pulse.start(self.card)
        else:
            self.card.glow_a = 0.45

    # ───────── запуск анализа ─────────
    EXP_VALUES = ["Экспирация: " + x for x in ("Авто", "1 мин", "2 мин", "3 мин", "5 мин", "10 мин", "15 мин", "30 мин", "60 мин")]

    @staticmethod
    def exp_label(v):
        v = str(v).replace("Экспирация:", "").strip() or "Авто"
        return "Экспирация: " + v

    def on_exp(self):
        self.s["exp"] = self.exp_sp.text.replace("Экспирация:", "").strip()
        self.save_settings()

    def exp_minutes(self):
        t = str(self.s.get("exp", "Авто")).replace("Экспирация:", "").strip()
        try:
            return int(t.split()[0])
        except (ValueError, IndexError):
            return None

    def params(self):
        self.save_settings()
        s = self.s
        bl = [x for x in s["blackout"].split(",") if x.strip()]
        opts = {"mtf": s["mtf"], "vol": s["vol"], "session": s["session"], "exp": self.exp_minutes()}
        return self.interval.text, s["thr"], bl, set(s["methods"]), s["weights"], opts, list(s["indicators"])

    def run(self):
        sym = self.current_symbol()
        if not sym:
            self.style_signal("pause", "Выберите пару")
            return
        self._launch([sym], notify=False)

    # ───────── сканер: выбор пар и фильтр по выплате ─────────
    @staticmethod
    def _num(text):
        try:
            return float(str(text).replace(",", ".").strip())
        except ValueError:
            return None

    def scan_pairs(self):
        sc = self.s["scan"]
        scope = sc.get("scope", SCAN_SCOPES[0])
        if scope == "Все OTC":
            pairs = list(core.OTC_ALL)
        elif scope == "Избранное":
            pairs = list(self.s["favs"])
        elif scope == "Выбранные вручную":
            pairs = list(sc.get("picked", []))
        else:
            g = self.group.text
            if g == FAV:
                pairs = list(self.s["favs"])
            elif g.startswith("Форекс"):
                pairs = list(core.FOREX_ALL)
            else:
                pairs = list(core.GROUPS.get(g, []))
        lo, hi = self._num(sc.get("pmin", "")), self._num(sc.get("pmax", ""))
        if lo is not None or hi is not None:
            keep = []
            for p in pairs:
                v = core.PAYOUTS.get(p)
                if v is None or (lo is not None and v < lo) or (hi is not None and v > hi):
                    continue
                keep.append(p)
            pairs = keep
        return pairs

    def open_scan(self):
        sc = self.s["scan"]
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(10), padding=dp(10))
        box.bind(minimum_height=box.setter("height"))
        box.add_widget(wrap_label("[b]Какие пары сканировать[/b]", 15, color=VIOLET, markup=True))
        self.sc_scope = styled_spinner(glow=VIOLET, title="Какие пары сканировать", text=sc["scope"],
                                       values=SCAN_SCOPES, size_hint_y=None, height=dp(46))
        box.add_widget(self.sc_scope)
        self.sc_pick = GlowButton(text="", font=14, size_hint_y=None, height=dp(44), radius=dp(12),
                                  glow_color=list(CYAN), glow_a=0.5, fill=[0.0, 0.2, 0.26, 0.9])
        self.sc_pick.bind(on_release=lambda *_: self.open_pick())
        box.add_widget(self.sc_pick)
        box.add_widget(wrap_label("[b]Фильтр по выплате Pocket Option[/b]", 15, color=CYAN, markup=True))
        row = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))
        self.sc_min = styled_input(text=str(sc.get("pmin", "")), hint_text="от, %", input_filter="float")
        self.sc_max = styled_input(text=str(sc.get("pmax", "")), hint_text="до, %", input_filter="float")
        row.add_widget(self.sc_min)
        row.add_widget(self.sc_max)
        box.add_widget(row)
        box.add_widget(wrap_label("Оставьте пустым, чтобы не фильтровать. Чтобы взять пары ровно с выплатой 92%, "
                                  "впишите 92 в оба поля. Выплаты есть только у OTC-пар.",
                                  12, color=(1, 1, 1, 0.6)))
        self.sc_info = wrap_label("", 13, markup=True)
        box.add_widget(self.sc_info)
        go = GlowButton(text="Запустить сканер", font=17, size_hint_y=None, height=dp(52), radius=dp(14),
                        glow_color=list(VIOLET), glow_a=0.7, fill=[0.3, 0.14, 0.5, 0.95])
        box.add_widget(go)
        sv = ScrollView()
        sv.add_widget(box)
        pop = Popup(title="Сканер", content=sv, size_hint=(0.96, 0.86), background="",
                    background_color=(0.04, 0.05, 0.12, 1), separator_color=VIOLET)
        self.sc_pop = pop
        for w in (self.sc_scope, self.sc_min, self.sc_max):
            w.bind(text=self._sc_update)
        go.bind(on_release=lambda *_: self.start_scan())
        self._sc_update()
        pop.open()

    def _sc_update(self, *_):
        sc = self.s["scan"]
        sc["scope"] = self.sc_scope.text
        sc["pmin"], sc["pmax"] = self.sc_min.text, self.sc_max.text
        self.sc_pick.text = f"Выбрать пары вручную (отмечено: {len(sc.get('picked', []))})"
        pairs = self.scan_pairs()
        n = len(pairs)
        k = 2 if self.s["mtf"] else 1
        txt = f"Подойдёт пар: [b]{n}[/b]. Запросов к OTCharts примерно от {n} до {n * k} (лимит Lite 7000 в сутки)."
        pays = [core.PAYOUTS[p] for p in core.OTC_ALL if p in core.PAYOUTS]
        if pays:
            txt += f"\nСейчас у OTC-пар выплата от {min(pays)}% до {max(pays)}%."
        if n == 0:
            txt += "\n[color=ffb833]Нет пар с такими условиями.[/color]"
        self.sc_info.text = txt
        self.save_settings()

    def open_pick(self):
        sc = self.s["scan"]
        vals = by_payout(self.pair.values)
        if not vals:
            return
        picked = set(sc.get("picked", []))
        outer = BoxLayout(orientation="vertical", spacing=dp(8))
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=0)
        box.bind(minimum_height=box.setter("height"))
        rows = []
        for v in vals:
            o = NeonOption(v, accent=VIOLET)
            o.selected = v in picked
            o.bind(on_release=lambda w: setattr(w, "selected", not w.selected))
            rows.append(o)
            box.add_widget(o)
        sv = ScrollView()
        sv.add_widget(box)
        outer.add_widget(wrap_label("Отметьте пары из текущей группы. Пары из других групп, отмеченные раньше, "
                                    "сохраняются.", 12, color=(1, 1, 1, 0.6)))
        outer.add_widget(sv)
        btns = BoxLayout(size_hint_y=None, height=dp(46), spacing=dp(8))

        def mk(text, cb, col):
            b = GlowButton(text=text, font=14, radius=dp(12), glow_color=list(col), glow_a=0.6,
                           fill=[col[0] * 0.2, col[1] * 0.2, col[2] * 0.2, 0.9])
            b.bind(on_release=lambda *_: cb())
            btns.add_widget(b)
        mk("Все", lambda: [setattr(o, "selected", True) for o in rows], CYAN)
        mk("Снять", lambda: [setattr(o, "selected", False) for o in rows], COLORS["put"])
        mk("Готово", lambda: done(), COLORS["call"])
        outer.add_widget(btns)
        pop = Popup(title="Пары для сканера", content=outer, size_hint=(0.94, 0.88), background="",
                    background_color=(0.04, 0.05, 0.12, 1), separator_color=VIOLET)

        def done():
            new = {o.value for o in rows if o.selected}
            sc["picked"] = sorted((picked - set(vals)) | new)
            if sc["picked"]:
                self.sc_scope.text = "Выбранные вручную"
            pop.dismiss()
            self._sc_update()
        pop.open()

    def start_scan(self):
        pairs = self.scan_pairs()
        self.sc_pop.dismiss()
        if not pairs:
            self.style_signal("pause", "Нет подходящих пар")
            self.info.text = "Измените выбор пар или фильтр выплаты"
            return
        self._launch(pairs, notify=False)

    def _launch(self, pairs, notify):
        if self._busy:
            return
        cfg = self.risk_cfg()
        if cfg["on"]:
            rs = risk.evaluate(self.entries, time.time(), cfg)
            if rs["blocked"]:
                self.style_signal("pause", "ПАУЗА по рискам")
                self.info.text = rs["why"]
                self.reasons.text = ("Защита депозита включена. Новые сигналы временно не показываются.\n"
                                     "Отключить: Настройки > Управление рисками.")
                return
        self._busy = True
        self.btn.disabled = self.scan_btn.disabled = True
        self.style_signal("wait", "Загрузка...")
        itv, thr, bl, methods, weights, opts, inds = self.params()
        threading.Thread(target=self._work, args=(pairs, itv, thr, bl, methods, weights, opts, inds, notify),
                         daemon=True).start()

    def _work(self, pairs, itv, thr, bl, methods, weights, opts, inds, notify):
        results = []
        for i, sym in enumerate(pairs, 1):
            if len(pairs) > 1:
                Clock.schedule_once(lambda dt, i=i, s=sym: self.style_signal(
                    "wait", f"Анализ {i}/{len(pairs)}: {s}"))
            try:
                res = core.analyze_symbol(sym, itv, thr, bl, methods, weights, opts, inds)
                results.append((sym, res, None if res else "Недостаточно свечей для анализа"))
            except Exception as e:
                msg = str(e)
                results.append((sym, None, msg))
                if "ключ" in msg or "квота" in msg:
                    break
        Clock.schedule_once(lambda dt: self._done(pairs, itv, results, notify, methods, inds))

    def _done(self, pairs, itv, results, notify, methods, inds):
        self._busy = False
        self.btn.disabled = self.scan_btn.disabled = False
        now = time.time()
        for sym, res, _err in results:
            if res and res["kind"] in ("call", "put"):
                self._register(sym, itv, res, methods, notify, now, inds)
            else:
                self.last_sig.pop(sym, None)
        self._list_ts = now
        if len(pairs) == 1:
            sym, res, err = results[0]
            self.show(sym, itv, res, err)
        else:
            self.show_list(itv, results)

    def _register(self, sym, itv, res, methods, notify, now, inds):
        if not journal.is_new(self.last_sig, sym, res["kind"], now, res["exp_min"]):
            return
        self.entries.append(journal.new_entry(sym, itv, res, methods, now, inds if 'tech' in methods else [],
                                                  core.PAYOUTS.get(sym)))
        journal.save(os.path.join(self.dir, "journal.json"), self.entries)
        self.update_journal_btn()
        if notify and self.s["notify"]:
            alerts.alert(f"{res['signal'].split(' ')[0]} {sym}",
                         f"{sym} {itv}: уверенность {res['conf']}%. {res.get('exp_text', '')}",
                         beep_path=os.path.join(self.dir, "beep.wav"), loud=self.s["sound"])

    def _tick_timer(self, _dt):
        sg = getattr(self, "_sig", None)
        if not sg:
            self.timer.text = ""
            return
        now = time.time()
        tf, age = sg["tf"], now - sg["ts"]
        rem = tf - (now % tf)

        def mm(x):
            return f"{int(x) // 60}:{int(x) % 60:02d}"
        if age >= tf:
            self.timer.text = "Сигнал устарел: прошла целая свеча. Повторите анализ"
            self.timer.color = COLORS["put"]
        else:
            self.timer.text = f"Возраст сигнала {mm(age)} | новая свеча через {mm(rem)}"
            self.timer.color = COLORS["call"] if age < 0.4 * tf else COLORS["pause"]

    def _on_ref(self, _w, ref):
        """Нажатие на пару в списке сканера открывает подробное объяснение сигнала."""
        if ref == "__back__":
            if self._list_results:
                self.show_list(self._list_itv, self._list_results)
            return
        for sym, res, _err in self._list_results:
            if sym == ref and res:
                self.show(sym, self._list_itv, res, None, back=True, ts=getattr(self, "_list_ts", None))
                return

    def risk_cfg(self):
        self.save_settings()
        return risk.config(self.s.get("risk"))

    def stake_line(self):
        cfg = self.risk_cfg()
        st = risk.stake(cfg) if cfg["on"] else None
        if not st:
            return None
        return f"[color=00d9ff]Ставка по плану: [b]{st:.2f}[/b] ({cfg['stake_pct']:g}% от депозита {cfg['deposit']:g})[/color]"

    def show(self, sym, itv, res, err, back=False, ts=None):
        if err or not res:
            self.style_signal("pause", "Нет данных")
            self.info.text = ""
            self.reasons.text = err or "Нет данных"
            return
        self.style_signal(res["kind"], res["signal"])
        lines = [f"{sym.upper()} {itv} | цена {res['price']:.5g} | балл {res['score']:+d} "
                 f"(порог {res['thr']}) | {res['conf']}%"]
        if res.get("exp_text"):
            lines.append(res["exp_text"])
        v = core.PAYOUTS.get(sym)
        if v:
            lines.append(f"Выплата {v}% (безубыток {100 / (1 + v / 100):.1f}%)")
        self.info.text = "\n".join(lines)
        self.chart.data = res.get("chart")
        lines = []
        for p, text in res["parts"]:
            col = "33f280" if p > 0 else "ff4d6b" if p < 0 else "99aad9"
            lines.append(f"[color={col}]{p:+d}[/color]  {text}")
        if res["kind"] in ("call", "put"):
            self._sig = {"ts": ts or time.time(), "tf": core.TF_SECONDS.get(itv, 300)}
            sl = self.stake_line()
            if sl:
                lines.insert(0, sl + "\n")
        if back:
            lines.insert(0, "[ref=__back__][color=00d9ff][b]< Назад к списку сканера[/b][/color][/ref]\n")
        self.reasons.text = "\n".join(lines)
        self._tick_timer(0)

    def show_list(self, itv, results):
        self._list_itv, self._list_results = itv, results
        good = [(s, r) for s, r, _ in results if r]
        errs = [e for _, r, e in results if not r and e]
        good.sort(key=lambda x: -abs(x[1]["score"]))
        active = [x for x in good if x[1]["kind"] in ("call", "put")]
        self.style_signal("call" if active else "wait", f"Сигналов: {len(active)} из {len(results)}")
        self.info.text = f"Таймфрейм {itv}" + (f" | ошибок: {len(errs)} ({errs[0][:60]})" if errs else "")
        if good:
            self.chart.data = good[0][1].get("chart")
        lines = []
        for s, r in good:
            exp = f"  {r['exp_min']}м" if r.get("exp_min") else ""
            pv = core.PAYOUTS.get(s)
            pay = f"  [{pv}%]" if pv else ""
            lines.append(f"[ref={s}][color={HEX[r['kind']]}]{s:<10} {r['signal'].split(' ')[0]:<6} "
                         f"{r['score']:+d} ({r['conf']}%){exp}{pay}[/color][/ref]")
        if lines:
            lines.insert(0, "[color=99aad9]Нажмите на пару, чтобы увидеть, почему такой сигнал[/color]")
            sl = self.stake_line()
            if sl:
                lines.insert(1, sl)
        self.reasons.text = "\n".join(lines) or (errs[0] if errs else "Нет данных")

    # ───────── авто-режим ─────────
    def on_auto(self, _w, active):
        if self._ev:
            self._ev.cancel()
            self._ev = None
        if active:
            secs = max(60, core.TF_SECONDS.get(self.interval.text, 300))
            self._auto_tick()
            self._ev = Clock.schedule_interval(lambda dt: self._auto_tick(), secs)

    def _auto_tick(self):
        pairs = list(self.s["favs"]) or [self.current_symbol()]
        pairs = [p for p in pairs if p]
        if pairs:
            self._launch(pairs, notify=True)

    # ───────── журнал ─────────
    def _tick(self, _dt):
        now = time.time()
        for e in journal.due(self.entries, now):
            if e["id"] in self.settling:
                continue
            if now > e["ts"] + e["exp"] * 60 + journal.GRACE:
                journal.settle(e, 0, now)
                journal.save(os.path.join(self.dir, "journal.json"), self.entries)
                self.update_journal_btn()
                continue
            self.settling.add(e["id"])
            threading.Thread(target=self._settle_worker, args=(e["id"], e["sym"]), daemon=True).start()

    def _settle_worker(self, eid, sym):
        try:
            price = core.fetch(sym, "1m", limit=3)["c"][-1]
        except Exception:
            price = None
        Clock.schedule_once(lambda dt: self._settled(eid, price))

    def _settled(self, eid, price):
        self.settling.discard(eid)
        for e in self.entries:
            if e["id"] == eid and e["status"] == "pending":
                if price is not None:
                    journal.settle(e, price)
                    journal.save(os.path.join(self.dir, "journal.json"), self.entries)
                    self.update_journal_btn()
                break

    def update_journal_btn(self):
        st = journal.stats(self.entries)
        self.j_btn.text = f"Журнал {st['rate']:.0f}%" if st["rate"] is not None else "Журнал"

    def open_journal(self):
        st = journal.stats(self.entries, self.names)
        box = BoxLayout(orientation="vertical", size_hint_y=None, spacing=dp(8), padding=dp(8))
        box.bind(minimum_height=box.setter("height"))
        rate = f"{st['rate']:.1f}%" if st["rate"] is not None else "нет данных"
        head = (f"Проверено сигналов: {st['n']} | плюс: {st['win']} | минус: {st['loss']} | ничья: {st['draw']}\n"
                f"Процент плюсовых: [b]{rate}[/b]. Безубыточность при выплате 85%: {journal.BREAKEVEN * 100:.0f}%.\n"
                f"Ждут проверки: {st['pending']} | не проверены вовремя: {st['stale']}")
        box.add_widget(wrap_label(head, 14, markup=True))
        d = st["by_dir"]
        parts = [f"{'CALL' if k == 'call' else 'PUT'}: {v[0]}/{v[1]}" + (f" ({100 * v[0] / v[1]:.0f}%)" if v[1] else "")
                 for k, v in d.items()]
        box.add_widget(wrap_label("По направлению: " + ", ".join(parts), 13))
        good = [(k, v) for k, v in st["by_methods"].items() if v[1] >= 3]
        if good:
            box.add_widget(wrap_label("По наборам методов и индикаторов (от 3 сделок):\n" + "\n".join(
                f"{k}: {v[0]}/{v[1]} ({100 * v[0] / v[1]:.0f}%)" for k, v in good), 13))
        cfg = risk.config(self.s.get("risk"))
        rs = risk.evaluate(self.entries, time.time(), cfg)
        today = f"Сегодня закрыто сигналов: {rs['n']}"
        if rs["net"] is not None:
            today += f", результат при плановой ставке: {rs['net']:+.2f}"
        else:
            today += f", результат в ставках: {rs['units']:+.2f}"
        today += f", проигрышей подряд: {rs['streak']}."
        today += " Защита: " + ("включена." if cfg["on"] else "выключена.")
        if rs["blocked"]:
            today += "\n[color=ffb833]" + rs["why"] + "[/color]"
        box.add_widget(wrap_label(today, 13, markup=True))
        if st["n"] < 10:
            box.add_widget(wrap_label("Разбор по парам, времени и выплате появится после 10 проверенных сигналов.",
                                      12, color=(1, 1, 1, 0.55)))
        else:
            box.add_widget(wrap_label("[b]Что работает[/b]. Зелёный: даже нижняя граница выше безубытка. Красный: "
                                      "даже верхняя граница ниже безубытка. Жёлтый: пока не ясно. Пока сделок меньше "
                                      "30, любые различия чаще всего шум.", 12, markup=True, color=(1, 1, 1, 0.7)))
            for title, fn in self._breakdowns():
                d = journal.breakdown(self.entries, fn)
                if not d:
                    continue
                rows = sorted(d.items(), key=lambda kv: -kv[1]["rate"])[:6]
                out = [f"[b]{title}[/b]"]
                for k, v in rows:
                    lo, hi = v["rate"] - v["margin"], v["rate"] + v["margin"]
                    col = "33f280" if lo > v["be"] else "ff4d6b" if hi < v["be"] else "ffb833"
                    out.append(f"[color={col}]{k}: {v['w']}/{v['n']} = {v['rate']:.0f}% (±{v['margin']:.0f}, "
                               f"безубыток {v['be']:.0f}%)[/color]")
                box.add_widget(wrap_label("\n".join(out), 13, markup=True))
        box.add_widget(wrap_label("Цена на момент экспирации берётся у источника котировок и может слегка "
                                  "отличаться от брокера. Это приблизительная статистика, а не результат реальных сделок.",
                                  12, color=(1, 1, 1, 0.5)))
        lines = []
        for e in reversed(self.entries[-25:]):
            name, col = STATUS_RU.get(e["status"], (e["status"], "99aad9"))
            t = time.strftime("%d.%m %H:%M", time.localtime(e["ts"]))
            d_ = "CALL" if e["dir"] == "call" else "PUT"
            lines.append(f"{t}  {e['sym']} {e['itv']}  {d_}  {e['exp']}м  [color={col}]{name}[/color]")
        box.add_widget(wrap_label("\n".join(lines) or "Пока нет сигналов", 13, markup=True))
        csv_btn = GlowButton(text="Скопировать журнал (CSV)", font=13, size_hint_y=None, height=dp(40),
                             radius=dp(12), glow_color=list(CYAN), glow_a=0.45, fill=[0.0, 0.2, 0.26, 0.9])
        csv_btn.bind(on_release=lambda b: self._copy_csv(b))
        box.add_widget(csv_btn)
        clear = GlowButton(text="Очистить журнал", font=13, size_hint_y=None, height=dp(40), radius=dp(12),
                           glow_color=list(COLORS["put"]), glow_a=0.45, fill=[0.3, 0.05, 0.1, 0.9])
        clear.bind(on_release=lambda *_: self._clear_journal(pop))
        box.add_widget(clear)
        sv = ScrollView()
        sv.add_widget(box)
        pop = Popup(title="Журнал сигналов", content=sv, size_hint=(0.96, 0.92), background="",
                    background_color=(0.04, 0.05, 0.12, 1), separator_color=COLORS["call"])
        pop.open()

    @staticmethod
    def _breakdowns():
        def hour(e):
            h = time.localtime(e["ts"]).tm_hour // 3 * 3
            return f"{h:02d}-{h + 3:02d} ч"

        def pay(e):
            p = e.get("pay")
            return "неизвестна" if not p else "90% и выше" if p >= 0.9 else "80-89%" if p >= 0.8 else "ниже 80%"
        return [("По парам", lambda e: e["sym"]), ("По времени суток (местное)", hour),
                ("По времени экспирации", lambda e: f"{e['exp']} мин"), ("По таймфрейму", lambda e: e["itv"]),
                ("По выплате брокера", pay)]

    def _copy_csv(self, btn):
        rows = ["время;пара;таймфрейм;направление;экспирация_мин;вход;выход;статус;балл;уверенность;выплата;методы;индикаторы"]
        for e in self.entries:
            rows.append(";".join(str(x) for x in (
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"])), e["sym"], e["itv"], e["dir"], e["exp"],
                e["entry"], e.get("exit") or "", e["status"], e["score"], e["conf"], e.get("pay") or "",
                "+".join(e["methods"]), "+".join(e.get("ind", [])))))
        try:
            from kivy.core.clipboard import Clipboard      # импорт здесь: после создания окна
            Clipboard.copy("\n".join(rows))
            btn.text = f"Скопировано строк: {len(self.entries)}. Вставьте в заметки или таблицу"
        except Exception:
            btn.text = "Не удалось скопировать"

    def _clear_journal(self, pop):
        self.entries = []
        self.last_sig = {}
        journal.save(os.path.join(self.dir, "journal.json"), self.entries)
        self.update_journal_btn()
        pop.dismiss()


class SignalApp(App):
    title = "Trade Signals"

    def build(self):
        self.root_widget = Root()
        return self.root_widget

    def on_start(self):
        try:  # Android 13+: разрешения на уведомления и вибрацию
            from android.permissions import request_permissions
            request_permissions(["android.permission.POST_NOTIFICATIONS", "android.permission.VIBRATE"])
        except Exception:
            pass

    def on_pause(self):
        return True   # не закрываться при сворачивании

    def on_stop(self):
        try:
            self.root_widget.save_settings()
        except Exception:
            pass


if __name__ == "__main__":
    SignalApp().run()
