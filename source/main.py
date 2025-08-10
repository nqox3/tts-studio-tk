import os
import sys
import uuid
import asyncio
import tempfile
import threading
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox

# UI
import customtkinter as ctk

# Audio playback
try:
    import pygame
    PYGAME_OK = True
except Exception:
    PYGAME_OK = False

# TTS engines (lazy imports inside classes)
# edge-tts (online), pyttsx3 (offline)


# --------- TTS ENGINES ---------
class EdgeTTSEngine:
    name = "Edge (онлайн)"

    # Небольшой преднабор голосов (работают “из коробки”)
    PRESET_VOICES = [
        {"id": "ru-RU-SvetlanaNeural", "label": "Svetlana · ru-RU · ♀"},
        {"id": "ru-RU-DmitryNeural",   "label": "Dmitry · ru-RU · ♂"},
        {"id": "uk-UA-PolinaNeural",   "label": "Polina · uk-UA · ♀"},
        {"id": "uk-UA-OstapNeural",    "label": "Ostap · uk-UA · ♂"},
        {"id": "en-US-JennyNeural",    "label": "Jenny · en-US · ♀"},
        {"id": "en-US-GuyNeural",      "label": "Guy · en-US · ♂"},
        {"id": "en-GB-RyanNeural",     "label": "Ryan · en-GB · ♂"},
        {"id": "de-DE-KatjaNeural",    "label": "Katja · de-DE · ♀"},
    ]

    def voices(self):
        # По умолчанию отдаём преднабор (быстро и без сети)
        return self.PRESET_VOICES[:]

    def _to_rate(self, v):
        # v: -50..50 -> "-10%" / "+15%"
        v = int(v)
        sign = "+" if v >= 0 else ""
        return f"{sign}{v}%"

    def _to_volume(self, v):
        # v: -50..50 -> "-20%" / "+10%"
        v = int(v)
        sign = "+" if v >= 0 else ""
        return f"{sign}{v}%"

    def _to_pitch(self, v):
        # v: -12..12 -> "-3Hz" / "+4Hz"
        v = int(v)
        sign = "+" if v >= 0 else ""
        return f"{sign}{v}Hz"

    def synthesize(self, text: str, voice_id: str, rate_val: int, pitch_val: int, volume_val: int) -> str:
        """
        Возвращает путь к временномy MP3 файлу.
        """
        import edge_tts  # импорт внутри, чтобы не мешать офлайн-режиму

        out_path = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex}.mp3")

        async def _run():
            comm = edge_tts.Communicate(
                text=text,
                voice=voice_id,
                rate=self._to_rate(rate_val),
                volume=self._to_volume(volume_val),
                pitch=self._to_pitch(pitch_val),
            )
            await comm.save(out_path)

        # Запуск отдельным asyncio-циклом
        asyncio.run(_run())
        return out_path


class Pyttsx3Engine:
    name = "pyttsx3 (офлайн)"

    def __init__(self):
        self._voices_cache = None

    def _init_engine(self):
        import pyttsx3
        eng = pyttsx3.init()
        return eng

    def voices(self):
        if self._voices_cache is not None:
            return self._voices_cache

        eng = self._init_engine()
        vs = eng.getProperty("voices")

        items = []
        for v in vs:
            # v.id, v.name, v.languages (может быть список байтовых тегов)
            langs = []
            try:
                for l in getattr(v, "languages", []):
                    try:
                        if isinstance(l, (bytes, bytearray)):
                            langs.append(l.decode(errors="ignore"))
                        else:
                            langs.append(str(l))
                    except Exception:
                        pass
            except Exception:
                pass
            lang_str = ",".join(langs) if langs else ""
            label = f"{getattr(v, 'name', 'Voice')} · {lang_str}" if lang_str else getattr(v, "name", "Voice")
            items.append({"id": v.id, "label": label})
        # Кэшируем
        self._voices_cache = items
        return items

    def _rate_to_pyttsx3(self, slider_val: int) -> int:
        # slider: -50..50 → базовый 200 ± 200 (0..400)
        base = 200
        return max(50, min(400, base + int(slider_val) * 4))

    def _volume_to_pyttsx3(self, slider_val: int) -> float:
        # slider: -50..50 -> 0..1
        return max(0.0, min(1.0, (int(slider_val) + 50) / 100.0))

    def synthesize(self, text: str, voice_id: str, rate_val: int, pitch_val: int, volume_val: int) -> str:
        """
        Возвращает путь к временномy WAV файлу (pyttsx3 лучше сохраняет в .wav).
        Параметр pitch_val игнорируется — у pyttsx3 нет настройки тона.
        """
        import pyttsx3

        out_path = os.path.join(tempfile.gettempdir(), f"tts_{uuid.uuid4().hex}.wav")
        eng = self._init_engine()
        try:
            eng.setProperty("voice", voice_id)
        except Exception:
            # если вдруг голос не поддерживается
            pass

        eng.setProperty("rate", self._rate_to_pyttsx3(rate_val))
        eng.setProperty("volume", self._volume_to_pyttsx3(volume_val))

        eng.save_to_file(text, out_path)
        eng.runAndWait()
        return out_path


# --------- UI APP ---------
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("TTS Studio TK")
        self.geometry("980x640")
        self.minsize(900, 580)

        ctk.set_appearance_mode("dark")           # "light", "dark", "system"
        ctk.set_default_color_theme("blue")       # "blue", "dark-blue", "green"

        # Engines
        self.edge_engine = EdgeTTSEngine()
        self.offline_engine = Pyttsx3Engine()

        self.current_engine_name = tk.StringVar(value=self.edge_engine.name)
        self.voice_var = tk.StringVar()
        self.voice_label_to_id = {}

        # State
        self.audio_path = None
        self.audio_is_paused = False

        # Init audio backend
        if PYGAME_OK:
            try:
                pygame.mixer.init()
            except Exception as e:
                messagebox.showwarning("Аудио", f"Не удалось инициализировать плеер pygame.\n{e}")
        else:
            messagebox.showinfo("Аудио", "Модуль pygame не установлен. Воспроизведение будет через внешний плеер.")

        # Layout
        self._build_layout()

        # Load initial voices (Edge)
        self._load_voices_for_engine(self.edge_engine)

    # ---------- UI BUILD ----------
    def _build_layout(self):
        self.grid_columnconfigure(0, weight=0)   # left panel
        self.grid_columnconfigure(1, weight=1)   # right
        self.grid_rowconfigure(0, weight=1)

        # Left panel (controls)
        left = ctk.CTkFrame(self)
        left.grid(row=0, column=0, sticky="nsw", padx=14, pady=14)

        # Right panel (text + player)
        right = ctk.CTkFrame(self)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, 14), pady=14)
        right.grid_rowconfigure(0, weight=1)
        right.grid_columnconfigure(0, weight=1)

        # --- Left: Controls ---
        ctk.CTkLabel(left, text="Движок озвучки", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(12, 6)
        )
        self.engine_menu = ctk.CTkOptionMenu(
            left,
            values=[self.edge_engine.name, self.offline_engine.name],
            variable=self.current_engine_name,
            command=self._on_engine_change,
            width=250,
        )
        self.engine_menu.grid(row=1, column=0, padx=12, pady=6, sticky="w")

        self.refresh_btn = ctk.CTkButton(left, text="Обновить голоса", command=self._refresh_voices, width=250)
        self.refresh_btn.grid(row=2, column=0, padx=12, pady=(0, 10), sticky="w")

        ctk.CTkLabel(left, text="Голос", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=3, column=0, sticky="w", padx=12, pady=(10, 6)
        )
        self.voice_menu = ctk.CTkOptionMenu(left, values=["—"], variable=self.voice_var, width=250)
        self.voice_menu.grid(row=4, column=0, padx=12, pady=6, sticky="w")

        # Sliders
        self.rate_label = ctk.CTkLabel(left, text="Скорость: 0%")
        self.rate_label.grid(row=5, column=0, sticky="w", padx=12, pady=(10, 0))
        self.rate_slider = ctk.CTkSlider(left, from_=-50, to=50, number_of_steps=20, command=self._on_rate_change, width=250)
        self.rate_slider.set(0)
        self.rate_slider.grid(row=6, column=0, padx=12, pady=6, sticky="w")

        self.pitch_label = ctk.CTkLabel(left, text="Тон: 0 Hz (только Edge)")
        self.pitch_label.grid(row=7, column=0, sticky="w", padx=12, pady=(10, 0))
        self.pitch_slider = ctk.CTkSlider(left, from_=-12, to=12, number_of_steps=24, command=self._on_pitch_change, width=250)
        self.pitch_slider.set(0)
        self.pitch_slider.grid(row=8, column=0, padx=12, pady=6, sticky="w")

        self.vol_label = ctk.CTkLabel(left, text="Громкость: 0%")
        self.vol_label.grid(row=9, column=0, sticky="w", padx=12, pady=(10, 0))
        self.vol_slider = ctk.CTkSlider(left, from_=-50, to=50, number_of_steps=20, command=self._on_vol_change, width=250)
        self.vol_slider.set(0)
        self.vol_slider.grid(row=10, column=0, padx=12, pady=6, sticky="w")

        # Generate + Save
        self.gen_btn = ctk.CTkButton(left, text="Сгенерировать", command=self._on_generate, width=250)
        self.gen_btn.grid(row=11, column=0, padx=12, pady=(16, 8), sticky="w")

        self.save_btn = ctk.CTkButton(left, text="Сохранить в файл", command=self._on_save, state="disabled", width=250)
        self.save_btn.grid(row=12, column=0, padx=12, pady=(0, 12), sticky="w")

        # Theme
        ctk.CTkLabel(left, text="Тема", font=ctk.CTkFont(size=14, weight="bold")).grid(
            row=13, column=0, sticky="w", padx=12, pady=(6, 4)
        )
        theme_row = ctk.CTkFrame(left, fg_color="transparent")
        theme_row.grid(row=14, column=0, padx=8, pady=(0, 12), sticky="w")
        self.appearance_menu = ctk.CTkOptionMenu(theme_row, values=["dark", "light", "system"], command=ctk.set_appearance_mode, width=120)
        self.appearance_menu.set("dark")
        self.appearance_menu.grid(row=0, column=0, padx=4)
        self.color_menu = ctk.CTkOptionMenu(theme_row, values=["blue", "dark-blue", "green"], command=ctk.set_default_color_theme, width=120)
        self.color_menu.set("blue")
        self.color_menu.grid(row=0, column=1, padx=4)

        # --- Right: Text + Player + Status ---
        title = ctk.CTkLabel(right, text="🎙️ TTS Studio TK", font=ctk.CTkFont(size=22, weight="bold"))
        title.grid(row=0, column=0, sticky="w", padx=14, pady=(14, 6))

        self.textbox = ctk.CTkTextbox(right, height=360, wrap="word")
        self.textbox.grid(row=1, column=0, sticky="nsew", padx=14, pady=(6, 6))
        self.textbox.insert("1.0", "Введи текст для озвучки...")

        # Player controls
        player = ctk.CTkFrame(right)
        player.grid(row=2, column=0, sticky="ew", padx=14, pady=(6, 6))
        player.grid_columnconfigure(0, weight=0)
        player.grid_columnconfigure(1, weight=0)
        player.grid_columnconfigure(2, weight=0)
        player.grid_columnconfigure(3, weight=1)

        self.play_btn = ctk.CTkButton(player, text="▶ Play", command=self._on_play, state="disabled", width=80)
        self.play_btn.grid(row=0, column=0, padx=6, pady=6)
        self.pause_btn = ctk.CTkButton(player, text="⏸ Pause", command=self._on_pause, state="disabled", width=80)
        self.pause_btn.grid(row=0, column=1, padx=6, pady=6)
        self.stop_btn = ctk.CTkButton(player, text="⏹ Stop", command=self._on_stop, state="disabled", width=80)
        self.stop_btn.grid(row=0, column=2, padx=6, pady=6)

        self.status = ctk.CTkLabel(player, text="Готово", anchor="w")
        self.status.grid(row=0, column=3, sticky="ew", padx=10)

    # ---------- UI CALLBACKS ----------
    def _on_engine_change(self, _value):
        engine = self._get_engine()
        # В офлайн-режиме pitch не работает — отключим
        self.pitch_slider.configure(state=("normal" if engine is self.edge_engine else "disabled"))
        self._load_voices_for_engine(engine)

    def _refresh_voices(self):
        engine = self._get_engine()
        # Для Edge можно было бы грузить полный список из сети,
        # но чтобы не зависеть от API — оставим преднабор (быстро).
        # Если хочешь онлайн-список — напишу доработку.
        self._load_voices_for_engine(engine)
        self._set_status("Список голосов обновлён")

    def _on_rate_change(self, value):
        self.rate_label.configure(text=f"Скорость: {int(value)}%")

    def _on_pitch_change(self, value):
        self.pitch_label.configure(text=f"Тон: {int(value)} Hz (только Edge)")

    def _on_vol_change(self, value):
        self.vol_label.configure(text=f"Громкость: {int(value)}%")

    def _on_generate(self):
        text = self.textbox.get("1.0", "end").strip()
        if not text:
            messagebox.showinfo("TTS", "Введи текст")
            return

        engine = self._get_engine()
        voice_label = self.voice_var.get().strip()
        if not voice_label or voice_label not in self.voice_label_to_id:
            messagebox.showinfo("TTS", "Выбери голос")
            return
        voice_id = self.voice_label_to_id[voice_label]

        rate_val = int(self.rate_slider.get())
        pitch_val = int(self.pitch_slider.get())
        vol_val = int(self.vol_slider.get())

        self._toggle_controls(False)
        self._set_status("Генерация звука...")

        def worker():
            try:
                out_path = engine.synthesize(text, voice_id, rate_val, pitch_val, vol_val)
            except Exception as e:
                self.after(0, lambda: self._on_generate_done(error=str(e)))
                return
            self.after(0, lambda: self._on_generate_done(path=out_path))

        threading.Thread(target=worker, daemon=True).start()

    def _on_generate_done(self, path=None, error=None):
        self._toggle_controls(True)
        if error:
            self._set_status(f"Ошибка: {error}")
            messagebox.showerror("TTS", f"Ошибка синтеза:\n{error}")
            return

        self.audio_path = path
        self._set_status(f"Готово: {os.path.basename(path)}")
        self.play_btn.configure(state="normal")
        self.pause_btn.configure(state="normal")
        self.stop_btn.configure(state="normal")
        self.save_btn.configure(state="normal")
        self.audio_is_paused = False

    def _on_save(self):
        if not self.audio_path or not os.path.exists(self.audio_path):
            messagebox.showinfo("Сохранение", "Нет сгенерированного аудио")
            return

        ext = os.path.splitext(self.audio_path)[1].lower()  # .mp3 / .wav
        filetypes = [("Аудио", f"*{ext}"), ("Все файлы", "*.*")]
        init_name = f"tts{ext}"

        out = filedialog.asksaveasfilename(
            title="Сохранить как",
            defaultextension=ext,
            filetypes=filetypes,
            initialfile=init_name,
        )
        if not out:
            return
        try:
            shutil.copyfile(self.audio_path, out)
            self._set_status(f"Сохранено: {os.path.basename(out)}")
        except Exception as e:
            messagebox.showerror("Сохранение", f"Не удалось сохранить файл:\n{e}")

    def _on_play(self):
        if not self.audio_path or not os.path.exists(self.audio_path):
            return

        if PYGAME_OK:
            try:
                pygame.mixer.music.load(self.audio_path)
                pygame.mixer.music.play()
                self.audio_is_paused = False
                self._set_status("Воспроизведение...")
            except Exception as e:
                messagebox.showwarning("Плеер", f"Ошибка проигрывания: {e}\nОткрою внешний плеер.")
                self._open_external_player(self.audio_path)
        else:
            self._open_external_player(self.audio_path)

    def _on_pause(self):
        if not PYGAME_OK:
            return  # во внешнем плеере пауза недоступна
        try:
            if self.audio_is_paused:
                pygame.mixer.music.unpause()
                self.audio_is_paused = False
                self._set_status("Воспроизведение...")
            else:
                pygame.mixer.music.pause()
                self.audio_is_paused = True
                self._set_status("Пауза")
        except Exception:
            pass

    def _on_stop(self):
        if PYGAME_OK:
            try:
                pygame.mixer.music.stop()
                self._set_status("Стоп")
            except Exception:
                pass

    # ---------- HELPERS ----------
    def _get_engine(self):
        return self.edge_engine if self.current_engine_name.get() == self.edge_engine.name else self.offline_engine

    def _load_voices_for_engine(self, engine):
        voices = engine.voices()
        if not voices:
            voices = [{"id": "", "label": "Голоса не найдены"}]
        values = [v["label"] for v in voices]
        self.voice_label_to_id = {v["label"]: v["id"] for v in voices}
        self.voice_menu.configure(values=values)
        # Try to select a good default
        default_label = None
        if engine is self.edge_engine:
            for lbl in values:
                if "ru-RU" in lbl or "Svetlana" in lbl:
                    default_label = lbl
                    break
        if not default_label:
            default_label = values[0]
        self.voice_menu.set(default_label)

        # Enable/disable pitch slider
        self.pitch_slider.configure(state=("normal" if engine is self.edge_engine else "disabled"))

    def _toggle_controls(self, enable: bool):
        state = "normal" if enable else "disabled"
        self.gen_btn.configure(state=state)
        self.engine_menu.configure(state=state)
        self.voice_menu.configure(state=state)
        self.refresh_btn.configure(state=state)
        self.rate_slider.configure(state=state)
        # pitch только у Edge — но блокировать всё равно когда работаем
        self.pitch_slider.configure(state=state if self._get_engine() is self.edge_engine else state)
        self.vol_slider.configure(state=state)
        self.save_btn.configure(state=("normal" if (enable and self.audio_path) else "disabled"))

    def _set_status(self, text: str):
        self.status.configure(text=text)

    def _open_external_player(self, path: str):
        # Fallback — открыть стандартным плеером ОС
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)
            elif sys.platform == "darwin":
                os.system(f'open "{path}"')
            else:
                os.system(f'xdg-open "{path}"')
            self._set_status("Открыт внешний плеер")
        except Exception as e:
            messagebox.showerror("Плеер", f"Не удалось открыть файл:\n{e}")


if __name__ == "__main__":
    app = App()
    app.mainloop()
