import threading

from kivy.animation import Animation
from kivy.app import App
from kivy.clock import Clock
from kivy.graphics import Color, Line, Rectangle, RoundedRectangle
from kivy.graphics.texture import Texture
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty, ListProperty, NumericProperty, StringProperty
from kivy.uix.behaviors import ButtonBehavior
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.label import Label
from kivy.uix.scrollview import ScrollView
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput

import signals_core as core

CYAN, VIOLET = (0.0, 0.85, 1.0, 1), (0.62, 0.36, 1.0, 1)
COLORS = {"call": (0.2, 0.95, 0.5, 1), "put": (1.0, 0.3, 0.42, 1),
          "wait": (0.6, 0.68, 0.85, 1), "pause": (1.0, 0.72, 0.2, 1)}
HEX = {"call": "33f280", "put": "ff4d6b", "wait": "99aad9", "pause": "ffb833"}


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
            for i in range(6, 0, -1):           # мягкое свечение вокруг
                e = i * dp(2.5)
                Color(c[0], c[1], c[2], a * 0.035 * (7 - i))
                RoundedRectangle(pos=(x - e, y - e), size=(w + 2 * e, h + 2 * e), radius=[r + e])
            Color(*f)                           # тело
            RoundedRectangle(pos=self.pos, size=self.size, radius=[r])
            Color(1, 1, 1, 0.10 + boost * 0.1)  # глянцевый блик на верхней половине
            RoundedRectangle(pos=(x + dp(2), y + h / 2), size=(max(w - dp(4), 0), max(h / 2 - dp(2), 0)),
                             radius=[r, r, dp(3), dp(3)])
            Color(c[0], c[1], c[2], 0.9 * a)    # неоновая рамка
            Line(rounded_rectangle=(x, y, w, h, r), width=dp(1.3))
            Color(1, 1, 1, 0.25)                # тонкий отсвет по верхней кромке
            Line(points=[x + r, y + h - dp(1), x + w - r, y + h - dp(1)], width=dp(1))


class GlowButton(ButtonBehavior, GlowBox):
    text = StringProperty("")
    toggle = BooleanProperty(False)
    active = BooleanProperty(False)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.label = Label(text=self.text, bold=True, font_size=sp(16), color=(1, 1, 1, 1))
        self.add_widget(self.label)
        self.bind(text=lambda _w, v: setattr(self.label, "text", v),
                  state=self._redraw, active=self._redraw, disabled=self._dis)

    def _dis(self, *_):
        self.opacity = 0.45 if self.disabled else 1

    def _boost(self):
        if self.state == "down":
            return 0.4
        return 0.25 if self.active else 0.0

    def on_release(self):
        if self.toggle:
            self.active = not self.active


def styled_input(**kw):
    return TextInput(multiline=False, write_tab=False, background_normal="", background_active="",
                     background_color=(1, 1, 1, 0.08), foreground_color=(1, 1, 1, 1),
                     cursor_color=CYAN, hint_text_color=(1, 1, 1, 0.38),
                     padding=[dp(12), dp(13)], **kw)


def styled_spinner(**kw):
    return Spinner(background_normal="", background_color=(1, 1, 1, 0.09), color=(1, 1, 1, 1), **kw)


class Root(BoxLayout):
    def __init__(self, **kw):
        super().__init__(orientation="vertical", padding=dp(12), spacing=dp(10), **kw)
        with self.canvas.before:
            Color(1, 1, 1, 1)
            self._bg = Rectangle(texture=gradient_texture(), pos=self.pos, size=self.size)
        self.bind(pos=self._upd_bg, size=self._upd_bg)
        groups = list(core.GROUPS)

        title = Label(text="[b]TRADE[/b] [color=00d9ff]SIGNALS[/color]", markup=True,
                      font_size=sp(22), size_hint_y=None, height=dp(34))

        row0 = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.group = styled_spinner(text=groups[0], values=groups, size_hint_x=0.55)
        self.pair = styled_spinner(text=core.GROUPS[groups[0]][0], values=core.GROUPS[groups[0]])
        self.group.bind(text=self.on_group)
        row0.add_widget(self.group)
        row0.add_widget(self.pair)

        row1 = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.custom = styled_input(hint_text="Свой тикер (необязательно)")
        self.interval = styled_spinner(text="5m", values=("1m", "5m", "15m", "1h"), size_hint_x=0.3)
        row1.add_widget(self.custom)
        row1.add_widget(self.interval)

        row2 = BoxLayout(size_hint_y=None, height=dp(48), spacing=dp(8))
        self.thr = styled_input(text="35", input_filter="int", hint_text="Порог", size_hint_x=0.25)
        self.blackout = styled_input(hint_text="Новости UTC: 12:25-12:45, 14:00-14:20")
        row2.add_widget(self.thr)
        row2.add_widget(self.blackout)

        row3 = BoxLayout(size_hint_y=None, height=dp(54), spacing=dp(10))
        self.btn = GlowButton(text="Анализ", glow_color=list(CYAN), glow_a=0.6,
                              fill=[0.0, 0.25, 0.32, 0.9])
        self.btn.bind(on_release=lambda *_: self.run())
        self.scan_btn = GlowButton(text="Сканер Форекс", glow_color=list(VIOLET), glow_a=0.6,
                                   fill=[0.2, 0.1, 0.36, 0.9])
        self.scan_btn.bind(on_release=lambda *_: self.scan())
        self.auto = GlowButton(text="Авто 60 с", toggle=True, size_hint_x=0.4,
                               glow_color=list(COLORS["call"]), glow_a=0.35, fill=[0.05, 0.2, 0.12, 0.9])
        self.auto.bind(active=self.on_auto)
        for b in (self.btn, self.scan_btn, self.auto):
            row3.add_widget(b)

        self.card = GlowBox(orientation="vertical", size_hint_y=None, height=dp(120),
                            padding=dp(8), radius=dp(20))
        self.signal = Label(text="Выберите пару и нажмите «Анализ»", font_size=sp(24), bold=True,
                            halign="center", valign="middle")
        self.signal.bind(size=lambda w, v: setattr(w, "text_size", v))
        self.info = Label(text="", font_size=sp(14), color=(1, 1, 1, 0.75), size_hint_y=None,
                          height=dp(26))
        self.card.add_widget(self.signal)
        self.card.add_widget(self.info)
        self.style_signal("wait")

        self.reasons = Label(text="", markup=True, font_size=sp(15), size_hint_y=None,
                             halign="left", valign="top")
        self.reasons.bind(width=lambda w, v: setattr(w, "text_size", (v, None)))
        self.reasons.bind(texture_size=lambda w, v: setattr(w, "height", v[1]))
        sv = ScrollView(bar_width=dp(3))
        sv.add_widget(self.reasons)
        panel = GlowBox(padding=dp(12), glow_color=[0.62, 0.36, 1.0, 1], glow_a=0.15)
        panel.add_widget(sv)

        warn = Label(text="Не гарантия прибыли. Бинарные опционы - высокий риск.",
                     font_size=sp(12), color=(1, 1, 1, 0.45), size_hint_y=None, height=dp(22))

        for w in (title, row0, row1, row2, row3, self.card, panel, warn):
            self.add_widget(w)
        self._ev = None

    def _upd_bg(self, *_):
        self._bg.pos, self._bg.size = self.pos, self.size

    def style_signal(self, kind, text=None):
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

    # ── управление ──
    def on_group(self, _w, text):
        vals = core.GROUPS[text]
        self.pair.values = vals
        self.pair.text = vals[0]

    def on_auto(self, _w, active):
        if self._ev:
            self._ev.cancel()
            self._ev = None
        if active:
            self.run()
            self._ev = Clock.schedule_interval(lambda dt: self.run(), 60)

    def params(self):
        thr = int(self.thr.text or 35)
        bl = [x for x in self.blackout.text.split(",") if x.strip()]
        return self.interval.text, thr, bl

    # ── одиночный анализ ──
    def run(self):
        self.style_signal("wait", "Загрузка...")
        sym = self.custom.text.strip() or self.pair.text
        itv, thr, bl = self.params()
        threading.Thread(target=self._work, args=(sym, itv, thr, bl), daemon=True).start()

    def _work(self, sym, itv, thr, bl):
        try:
            res = core.analyze(core.fetch(sym, itv), thr, bl)
            err = None if res else "Недостаточно свечей для анализа"
        except Exception as e:
            res, err = None, f"Ошибка данных: {e}"
        Clock.schedule_once(lambda dt: self.show(sym, itv, res, err))

    def show(self, sym, itv, res, err):
        if err:
            self.style_signal("pause", "Нет данных")
            self.reasons.text = err
            return
        self.style_signal(res["kind"], res["signal"])
        self.info.text = (f"{sym.upper()} {itv} | цена {res['price']:.5g} | "
                          f"балл {res['score']:+d} | уверенность {res['conf']}%")
        lines = []
        for p, text in res["parts"]:
            col = "33f280" if p > 0 else "ff4d6b" if p < 0 else "99aad9"
            lines.append(f"[color={col}]{p:+d}[/color]  {text}")
        self.reasons.text = "\n".join(lines)

    # ── сканер всех форекс-пар ──
    def scan(self):
        itv, thr, bl = self.params()
        self.scan_btn.disabled = True
        self.reasons.text = ""
        threading.Thread(target=self._scan, args=(itv, thr, bl), daemon=True).start()

    def _scan(self, itv, thr, bl):
        pairs = core.FOREX_ALL
        found, fails = [], 0
        for i, sym in enumerate(pairs, 1):
            Clock.schedule_once(lambda dt, i=i, s=sym: self.style_signal(
                "wait", f"Сканирую {i}/{len(pairs)}: {s}"))
            try:
                res = core.analyze(core.fetch(sym, itv), thr, bl)
                if res:
                    found.append((sym, res))
            except Exception:
                fails += 1
        Clock.schedule_once(lambda dt: self._scan_done(itv, found, fails, len(pairs)))

    def _scan_done(self, itv, found, fails, total):
        self.scan_btn.disabled = False
        found.sort(key=lambda x: -abs(x[1]["score"]))
        active = [x for x in found if x[1]["kind"] in ("call", "put")]
        self.style_signal("call" if active else "wait", f"Сигналов: {len(active)} из {total}")
        self.info.text = f"Таймфрейм {itv}" + (f" | не загрузилось: {fails}" if fails else "")
        lines = [f"[color={HEX[r['kind']]}]{s:<8} {r['signal'].split(' ')[0]:<5} "
                 f"{r['score']:+d} ({r['conf']}%)[/color]" for s, r in found]
        self.reasons.text = "\n".join(lines) or "Нет данных"


class SignalApp(App):
    title = "Trade Signals"

    def build(self):
        return Root()


if __name__ == "__main__":
    SignalApp().run()
