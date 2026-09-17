"""System tray screen recorder for Windows: ffmpeg capture + Groq transcription."""
import datetime
import os
import threading
import tkinter as tk
import webbrowser
from tkinter import messagebox

import pystray
from PIL import Image, ImageDraw

import config
import user_config
from capture import Recorder, RecordingError
from summarize import summarize_transcript
from transcribe import build_docx, segments_to_text, transcribe_segments, TranscriptionError

GROQ_KEYS_URL = "https://console.groq.com/keys"

recorder = Recorder()
LOG_PATH = user_config.CONFIG_DIR / "tray_app.log"
tk_root: tk.Tk | None = None


def log(message: str):
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        user_config.CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {message}\n")
    except OSError:
        pass


def make_icon_image(color: str) -> Image.Image:
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 6
    draw.ellipse((margin, margin, size - margin, size - margin), fill=color)
    return img


ICON_IDLE = make_icon_image("#4a5568")
ICON_RECORDING = make_icon_image("#e53e3e")


def notify(icon: pystray.Icon, message: str, title: str = "screen-recorder"):
    log(message)
    try:
        icon.notify(message, title)
    except Exception:
        pass


def run_transcription(icon: pystray.Icon, recording_path):
    notify(icon, f"Идёт транскрипция: {recording_path.name}")
    try:
        segments = transcribe_segments(recording_path)
        build_docx(recording_path, segments, summary_note="Саммари готовится — файл обновится автоматически.")
    except TranscriptionError as e:
        notify(icon, f"Транскрипция не удалась: {e}")
        return
    except Exception as e:
        notify(icon, f"Транскрипция не удалась (неожиданная ошибка): {e}")
        return

    notify(icon, "Расшифровка готова, готовлю саммари встречи…")
    try:
        summary = summarize_transcript(segments_to_text(segments))
        docx_path = build_docx(recording_path, segments, summary_markdown=summary)
        notify(icon, f"Расшифровка и саммари готовы: {docx_path.name}")
    except Exception as e:
        build_docx(recording_path, segments, summary_note=f"Не удалось сформировать саммари: {e}")
        notify(icon, f"Расшифровка сохранена, но саммари не получилось: {e}")


def start_recording(icon: pystray.Icon, with_mic: bool, with_system_audio: bool = False):
    if recorder.is_recording:
        return
    try:
        recorder.start(with_mic=with_mic, with_system_audio=with_system_audio)
        icon.icon = ICON_RECORDING
        if with_system_audio:
            label = " (экран + микрофон + системный звук)"
        elif with_mic:
            label = " (экран + микрофон)"
        else:
            label = " (экран)"
        notify(icon, "Запись начата" + label)
    except RecordingError as e:
        notify(icon, f"Не удалось начать запись: {e}")


def stop_recording(icon: pystray.Icon):
    if not recorder.is_recording:
        return
    try:
        output_path = recorder.stop()
        icon.icon = ICON_IDLE
        notify(icon, f"Запись сохранена: {output_path.name}")
        if config.GROQ_API_KEY:
            threading.Thread(target=run_transcription, args=(icon, output_path), daemon=True).start()
        else:
            notify(icon, "Groq-ключ не настроен — транскрипция пропущена. См. «Настроить Groq-ключ» в меню.")
    except RecordingError as e:
        notify(icon, f"Ошибка остановки записи: {e}")


def open_recordings_folder(icon: pystray.Icon, item):
    os.startfile(config.RECORDINGS_DIR)


def open_settings_dialog(icon: pystray.Icon):
    if tk_root is None:
        return

    window = tk.Toplevel(tk_root)
    window.title("Настройка Groq-ключа")
    window.resizable(False, False)
    window.attributes("-topmost", True)

    padding = {"padx": 16, "pady": 6}

    tk.Label(
        window,
        justify="left",
        wraplength=380,
        text=(
            "Чтобы получать текстовую расшифровку записей, нужен бесплатный "
            "API-ключ Groq (используется только для распознавания речи):\n\n"
            "1. Открой console.groq.com/keys и войди (можно через Google).\n"
            "2. Нажми «Create API Key», скопируй ключ (начинается с gsk_...).\n"
            "3. Вставь его в поле ниже и нажми «Сохранить»."
        ),
    ).pack(**padding)

    tk.Button(
        window,
        text="Открыть console.groq.com/keys",
        command=lambda: webbrowser.open(GROQ_KEYS_URL),
    ).pack(**padding)

    key_var = tk.StringVar(value=config.GROQ_API_KEY)
    entry = tk.Entry(window, textvariable=key_var, width=50)
    entry.pack(**padding)

    def paste_into_entry(event=None):
        try:
            text = window.clipboard_get()
        except tk.TclError:
            return "break"
        try:
            entry.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        entry.insert("insert", text)
        return "break"

    context_menu = tk.Menu(entry, tearoff=0)
    context_menu.add_command(label="Вставить", command=paste_into_entry)
    context_menu.add_command(label="Копировать", command=lambda: entry.event_generate("<<Copy>>"))
    context_menu.add_command(label="Вырезать", command=lambda: entry.event_generate("<<Cut>>"))
    entry.bind("<Button-3>", lambda e: context_menu.tk_popup(e.x_root, e.y_root))

    # On Windows, Ctrl+<letter> shortcuts are matched by keysym (e.g. "v", "a"),
    # which depends on the active keyboard layout — with a Cyrillic layout active
    # they don't match, so Ctrl+V/A/C/X silently do nothing. Bind by physical
    # keycode instead, which is layout-independent.
    def handle_ctrl_key(event):
        if event.keycode == 86:  # 'V'
            return paste_into_entry()
        if event.keycode == 65:  # 'A' — select all
            entry.select_range(0, tk.END)
            entry.icursor(tk.END)
            return "break"
        if event.keycode == 67:  # 'C'
            entry.event_generate("<<Copy>>")
            return "break"
        if event.keycode == 88:  # 'X'
            entry.event_generate("<<Cut>>")
            return "break"

    entry.bind("<Control-Key>", handle_ctrl_key)

    button_row = tk.Frame(window)
    button_row.pack(pady=(0, 12))

    def on_save():
        key = key_var.get().strip()
        if not key:
            messagebox.showwarning("Пустой ключ", "Вставь ключ или нажми «Отмена».", parent=window)
            return
        config.set_groq_api_key(key)
        notify(icon, "Groq-ключ сохранён")
        window.destroy()

    tk.Button(button_row, text="Сохранить", command=on_save, width=12).pack(side="left", padx=6)
    tk.Button(button_row, text="Отмена", command=window.destroy, width=12).pack(side="left", padx=6)

    window.lift()
    window.focus_force()
    window.grab_set()
    entry.focus_set()


def exit_app(icon: pystray.Icon, item):
    if recorder.is_recording:
        try:
            recorder.stop()
        except RecordingError:
            pass
    icon.stop()
    if tk_root is not None:
        tk_root.after(0, tk_root.quit)


def build_menu() -> pystray.Menu:
    return pystray.Menu(
        pystray.MenuItem(
            "Начать запись (экран)",
            lambda icon, item: start_recording(icon, with_mic=False),
            enabled=lambda item: not recorder.is_recording,
        ),
        pystray.MenuItem(
            "Начать запись (экран + микрофон)",
            lambda icon, item: start_recording(icon, with_mic=True),
            enabled=lambda item: not recorder.is_recording,
        ),
        pystray.MenuItem(
            "Начать запись (экран + микрофон + системный звук)",
            lambda icon, item: start_recording(icon, with_mic=True, with_system_audio=True),
            enabled=lambda item: not recorder.is_recording,
        ),
        pystray.MenuItem(
            "Остановить запись",
            lambda icon, item: stop_recording(icon),
            enabled=lambda item: recorder.is_recording,
        ),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(
            "Настроить Groq-ключ...",
            lambda icon, item: tk_root.after(0, lambda: open_settings_dialog(icon)),
        ),
        pystray.MenuItem("Открыть папку с записями", open_recordings_folder),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Выход", exit_app),
    )


def main():
    global tk_root
    tk_root = tk.Tk()
    tk_root.withdraw()

    icon = pystray.Icon("screen-recorder-win", ICON_IDLE, "Screen Recorder", build_menu())
    threading.Thread(target=icon.run, daemon=True).start()

    if not config.GROQ_API_KEY:
        tk_root.after(500, lambda: (
            notify(icon, "Транскрипция не настроена — заполни Groq-ключ (правый клик по иконке → «Настроить Groq-ключ»)"),
            open_settings_dialog(icon),
        ))

    tk_root.mainloop()


if __name__ == "__main__":
    main()
