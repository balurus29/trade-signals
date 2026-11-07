"""Виджеты в неоновом стиле для Tkinter: скруглённые кнопки, выпадающий выбор, поля с рабочей вставкой."""
import tkinter as tk

BG = "#070b17"
PANEL = "#0e1428"
CARD = "#121a33"
FIELD = "#0a1124"
BORDER = "#26325c"
FG = "#eaf0ff"
DIM = "#8a97c4"
CYAN = "#19d3f3"
VIOLET = "#9b6bff"
GREEN = "#2ee58a"
RED = "#ff4d6b"
AMBER = "#ffb833"
FONT = "Segoe UI"


def mix(a, b, t):
    """Смешивает два цвета #rrggbb: t=0 даёт a, t=1 даёт b."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#%02x%02x%02x" % tuple(int(x + (y - x) * t) for x, y in zip(pa, pb))


def rrect(c, x1, y1, x2, y2, r, **kw):
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
           x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return c.create_polygon(pts, smooth=True, **kw)


class Btn(tk.Canvas):
    """Скруглённая кнопка. toggle=True — переключатель (подсвечивается, когда включён)."""

    def __init__(self, master, text="", command=None, accent=CYAN, width=None, height=36, toggle=False,
                 active=False, size=11, bold=True, arrow=False, bg=None):
        super().__init__(master, height=height, bg=bg or master["bg"], highlightthickness=0, bd=0,
                         **({"width": width} if width else {}))
        self.text, self.command, self.accent = text, command, accent
        self.toggle, self.active, self.arrow = toggle, active, arrow
        self.size, self.bold = size, bold
        self.hover = False
        self.disabled = False
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Enter>", lambda e: self._hv(True))
        self.bind("<Leave>", lambda e: self._hv(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.configure(cursor="hand2")

    def _hv(self, v):
        self.hover = v
        self.draw()

    def _click(self, e):
        if self.disabled:
            return
        if not (0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()):
            return
        if self.toggle:
            self.active = not self.active
            self.draw()
        if self.command:
            self.command(self.active) if self.toggle else self.command()

    def set(self, text=None, active=None, accent=None):
        if text is not None:
            self.text = text
        if active is not None:
            self.active = active
        if accent is not None:
            self.accent = accent
        self.draw()

    def draw(self):
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10 or h < 10:
            return
        on = (self.active if self.toggle else False) or self.hover
        acc = self.accent
        fill = mix(PANEL, acc, 0.38 if (self.toggle and self.active) else 0.22 if self.hover else 0.10)
        edge = acc if (not self.toggle or self.active or self.hover) else mix(acc, BG, 0.55)
        if self.toggle and self.active:
            rrect(self, 1, 1, w - 1, h - 1, 11, fill=mix(BG, acc, 0.25), outline="")
        rrect(self, 2, 2, w - 2, h - 2, 10, fill=fill, outline=edge, width=1.4)
        col = FG if (not self.toggle or self.active or self.hover) else mix(FG, BG, 0.25)
        if self.disabled:
            col = DIM
        pad = 24 if self.arrow else 6
        self.create_text((w - (22 if self.arrow else 0)) / 2, h / 2, text=self.text, fill=col,
                         font=(FONT, self.size, "bold" if self.bold else "normal"), width=max(20, w - pad - 6))
        if self.arrow:
            ax, ay = w - 16, h / 2
            self.create_line(ax - 4, ay - 2, ax, ay + 2, ax + 4, ay - 2, fill=acc, width=2)


class Select(Btn):
    """Кнопка-список: по нажатию открывает окно со значениями."""

    def __init__(self, master, values, value=None, title="Выбор", on_change=None, accent=CYAN, **kw):
        super().__init__(master, text=value or (values[0] if values else ""), accent=accent, arrow=True, **kw)
        self.values, self.title, self.on_change = list(values), title, on_change
        self.value = self.text
        self.command = self.open

    def get(self):
        return self.value

    def set_value(self, v, fire=False):
        self.value = v
        self.set(text=v)
        if fire and self.on_change:
            self.on_change(v)

    def open(self):
        if not self.values:
            return
        top = tk.Toplevel(self)
        top.title(self.title)
        top.configure(bg=BG)
        top.transient(self.winfo_toplevel())
        rows = min(len(self.values), 16)
        wide = max(len(str(x)) for x in self.values)
        top.geometry(f"{max(260, wide * 10 + 70)}x{rows * 26 + 50}+{self.winfo_rootx()}+{self.winfo_rooty() + self.winfo_height()}")
        lb = tk.Listbox(top, bg=PANEL, fg=FG, selectbackground=mix(PANEL, self.accent, 0.5), selectforeground=FG,
                        highlightthickness=0, bd=0, activestyle="none", font=(FONT, 11))
        sb = tk.Scrollbar(top, command=lb.yview)
        lb.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        lb.pack(fill="both", expand=True, padx=6, pady=6)
        for x in self.values:
            lb.insert("end", "  " + str(x))
        if self.value in self.values:
            lb.selection_set(self.values.index(self.value))
            lb.see(self.values.index(self.value))

        def pick(e):
            i = lb.nearest(e.y)
            if 0 <= i < len(self.values):
                top.destroy()
                self.set_value(self.values[i], fire=True)
        lb.bind("<ButtonRelease-1>", pick)
        top.bind("<Escape>", lambda e: top.destroy())
        try:
            top.grab_set()
            top.focus_set()
        except Exception:
            pass


def enable_clipboard(entry):
    """Ctrl+V/C/X/A работают при любой раскладке (в русской раскладке Tk их не понимает), плюс меню по правой кнопке."""
    def paste(_e=None):
        try:
            txt = entry.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            if entry.selection_present():
                entry.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        entry.insert("insert", txt.strip().replace("\n", " "))
        return "break"

    def copy(_e=None):
        try:
            entry.clipboard_clear()
            entry.clipboard_append(entry.selection_get())
        except tk.TclError:
            pass
        return "break"

    def cut(_e=None):
        copy()
        try:
            entry.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        return "break"

    def sel_all(_e=None):
        entry.selection_range(0, "end")
        entry.icursor("end")
        return "break"

    def key(e):
        if e.state & 4:
            fn = {86: paste, 67: copy, 88: cut, 65: sel_all}.get(e.keycode)
            if fn:
                return fn()
    entry.bind("<Control-KeyPress>", key)
    entry.bind("<Shift-Insert>", paste)
    menu = tk.Menu(entry, tearoff=0, bg=PANEL, fg=FG)
    menu.add_command(label="Вставить", command=paste)
    menu.add_command(label="Копировать", command=copy)
    menu.add_command(label="Вырезать", command=cut)
    menu.add_command(label="Выделить всё", command=sel_all)
    entry.bind("<Button-3>", lambda e: menu.tk_popup(e.x_root, e.y_root))
    entry.paste = paste
    return entry


def Field(master, text="", width=None, show=""):
    e = tk.Entry(master, bg=FIELD, fg=FG, insertbackground=FG, relief="flat", highlightthickness=1,
                 highlightbackground=BORDER, highlightcolor=CYAN, font=(FONT, 11), show=show,
                 **({"width": width} if width else {}))
    e.insert(0, str(text))
    return enable_clipboard(e)


def Card(master, accent=BORDER, **kw):
    return tk.Frame(master, bg=CARD, highlightthickness=1, highlightbackground=accent, bd=0, **kw)


def Label(master, text="", fg=FG, size=11, bold=False, bg=None, **kw):
    return tk.Label(master, text=text, fg=fg, bg=bg or master["bg"], font=(FONT, size, "bold" if bold else "normal"), **kw)
