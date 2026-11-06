"""Уведомление о сигнале: системное уведомление, вибрация и короткий звук (всё в try, без падений)."""
import math
import os
import struct
import wave


def ensure_beep(path):
    if os.path.exists(path):
        return
    fr, n = 22050, int(22050 * 0.25)
    with wave.open(path, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(fr)
        w.writeframes(b"".join(struct.pack("<h", int(12000 * math.sin(2 * math.pi * 880 * i / fr) * (1 - i / n)))
                               for i in range(n)))


def alert(title, text, beep_path=None, loud=True):
    try:
        from plyer import notification
        notification.notify(title=title, message=text, app_name="Trade Signals", timeout=10)
    except Exception:
        pass
    if not loud:
        return
    try:
        from plyer import vibrator
        vibrator.vibrate(time=0.4)
    except Exception:
        pass
    try:
        if beep_path:
            ensure_beep(beep_path)
            from kivy.core.audio import SoundLoader
            snd = SoundLoader.load(beep_path)
            if snd:
                snd.play()
    except Exception:
        pass
