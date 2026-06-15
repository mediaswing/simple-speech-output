#!/usr/bin/env python3
"""Cross-platform GUI Text-to-Speech app using pyttsx3."""

import platform
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import pyttsx3


# ── Dedicated TTS worker thread ──────────────────────────────────────────────

class TTSEngine(threading.Thread):
    """Owns the pyttsx3 engine on its own thread to avoid event-loop conflicts."""

    def __init__(self):
        super().__init__(daemon=True)
        self._q: queue.Queue = queue.Queue()
        self.voices: list = []
        self._ready = threading.Event()
        self._engine: pyttsx3.Engine | None = None

    # called once by start()
    def run(self):
        engine = pyttsx3.init()
        self._engine = engine
        self.voices = list(engine.getProperty("voices") or [])
        self._ready.set()

        while True:
            try:
                item = self._q.get(timeout=0.05)
            except queue.Empty:
                continue

            cmd, *args = item
            if cmd == "quit":
                break
            elif cmd == "speak":
                text, voice_id, rate, done_cb = args
                engine.setProperty("voice", voice_id)
                engine.setProperty("rate", rate)
                engine.say(text)
                engine.runAndWait()
                if done_cb:
                    done_cb()
            elif cmd == "save":
                text, voice_id, rate, path, done_cb, err_cb = args
                try:
                    engine.setProperty("voice", voice_id)
                    engine.setProperty("rate", rate)
                    engine.save_to_file(text, path)
                    engine.runAndWait()
                    if done_cb:
                        done_cb()
                except Exception as exc:
                    if err_cb:
                        err_cb(str(exc))

    def wait_ready(self):
        self._ready.wait()

    def speak(self, text: str, voice_id: str, rate: int, done_cb=None):
        self._q.put(("speak", text, voice_id, rate, done_cb))

    def save(self, text: str, voice_id: str, rate: int, path: str,
             done_cb=None, err_cb=None):
        self._q.put(("save", text, voice_id, rate, path, done_cb, err_cb))

    def stop(self):
        if self._engine:
            self._engine.stop()


# ── Main GUI ─────────────────────────────────────────────────────────────────

class TTSApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Text to Speech")
        self.resizable(True, True)
        self.minsize(560, 440)

        self._tts = TTSEngine()
        self._tts.start()

        self._build_ui()

        # Load voices after engine is ready (non-blocking)
        self.status_var.set("Initialising voices…")
        threading.Thread(target=self._wait_and_load, daemon=True).start()

    def _wait_and_load(self):
        self._tts.wait_ready()
        self.after(0, self._load_voices)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}

        # ── Text area ────────────────────────────────────────────────────────
        tk.Label(self, text="Text to speak:", anchor="w").pack(fill="x", **pad)

        text_frame = tk.Frame(self)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        self.text_box = tk.Text(
            text_frame, wrap="word", height=10, font=("Helvetica", 14)
        )
        sb = tk.Scrollbar(text_frame, command=self.text_box.yview)
        self.text_box.configure(yscrollcommand=sb.set)
        self.text_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        # ── Voice + rate row ─────────────────────────────────────────────────
        ctrl = tk.Frame(self)
        ctrl.pack(fill="x", **pad)

        tk.Label(ctrl, text="Voice:").grid(row=0, column=0, sticky="w")
        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(
            ctrl, textvariable=self.voice_var, state="readonly", width=30
        )
        self.voice_combo.grid(row=0, column=1, sticky="w", padx=(6, 20))

        tk.Label(ctrl, text="Rate (wpm):").grid(row=0, column=2, sticky="w")
        self.rate_var = tk.IntVar(value=175)
        tk.Spinbox(
            ctrl, from_=60, to=500, increment=10,
            textvariable=self.rate_var, width=6
        ).grid(row=0, column=3, sticky="w", padx=6)

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", **pad)

        btn_cfg = dict(relief="flat", font=("Helvetica", 13, "bold"), bd=0)

        self.speak_btn = tk.Button(
            btn_frame, text="▶  Speak", width=12,
            bg="#4CAF50", fg="white", activebackground="#45a049",
            command=self._speak, **btn_cfg
        )
        self.speak_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = tk.Button(
            btn_frame, text="■  Stop", width=10, state="disabled",
            bg="#f44336", fg="white", activebackground="#d32f2f",
            command=self._stop, **btn_cfg
        )
        self.stop_btn.pack(side="left", padx=(0, 8))

        # ── Output file row ───────────────────────────────────────────────────
        out_frame = tk.Frame(self)
        out_frame.pack(fill="x", padx=12, pady=(0, 6))

        tk.Label(out_frame, text="Output file:").pack(side="left")
        self.path_var = tk.StringVar()
        tk.Entry(out_frame, textvariable=self.path_var, font=("Helvetica", 12)) \
            .pack(side="left", fill="x", expand=True, padx=(6, 6))
        tk.Button(out_frame, text="Browse…", command=self._browse, **btn_cfg) \
            .pack(side="left", padx=(0, 8))
        self.save_btn = tk.Button(
            out_frame, text="Save Audio", command=self._save, **btn_cfg
        )
        self.save_btn.pack(side="left")

        # ── Status bar ───────────────────────────────────────────────────────
        self.status_var = tk.StringVar(value="Ready.")
        tk.Label(
            self, textvariable=self.status_var, anchor="w",
            relief="sunken", bd=1, font=("Helvetica", 11), fg="gray40"
        ).pack(fill="x", side="bottom", ipady=3)

    def _load_voices(self):
        voices = self._tts.voices
        if not voices:
            messagebox.showerror("Error", "No voices found via pyttsx3.")
            return

        self._voices = voices
        labels = []
        for v in voices:
            name = v.name or v.id
            # Trim common verbose prefixes on Windows
            for prefix in ("Microsoft ", "MSTTS_V110_", "TTS_MS_"):
                name = name.replace(prefix, "")
            labels.append(name)

        self.voice_combo["values"] = labels

        # Pick a sensible default
        preferred = ("zira", "david", "samantha", "alex")
        default = next(
            (i for i, v in enumerate(voices)
             if any(p in (v.name or "").lower() for p in preferred)),
            0,
        )
        self.voice_combo.current(default)
        self.status_var.set(f"{len(voices)} voice(s) loaded. Ready.")

    # ── Actions ──────────────────────────────────────────────────────────────

    def _get_text(self) -> str | None:
        text = self.text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("No text", "Please enter some text first.")
            return None
        return text

    def _selected_voice_id(self) -> str:
        idx = self.voice_combo.current()
        return self._voices[max(idx, 0)].id

    def _speak(self):
        text = self._get_text()
        if text is None:
            return
        self._set_busy(True)
        self.status_var.set("Speaking…")
        self._tts.speak(
            text,
            self._selected_voice_id(),
            self.rate_var.get(),
            done_cb=lambda: self.after(0, self._on_speak_done),
        )

    def _stop(self):
        self._tts.stop()

    def _browse(self):
        is_mac = platform.system() == "Darwin"
        ext = ".aiff" if is_mac else ".wav"
        filetypes = (
            [("AIFF audio", "*.aiff *.aif"), ("All files", "*.*")]
            if is_mac
            else [("WAV audio", "*.wav"), ("All files", "*.*")]
        )
        path = filedialog.asksaveasfilename(
            defaultextension=ext,
            filetypes=filetypes,
            title="Choose output file…",
            initialfile=self.path_var.get() or None,
        )
        if path:
            self.path_var.set(path)

    def _save(self):
        text = self._get_text()
        if text is None:
            return

        path = self.path_var.get().strip()
        if not path:
            messagebox.showwarning("No output file", "Enter or browse for an output file path.")
            return

        self.save_btn.config(state="disabled")
        self.status_var.set(f"Saving to {path}…")

        self._tts.save(
            text,
            self._selected_voice_id(),
            self.rate_var.get(),
            path,
            done_cb=lambda: self.after(0, lambda: self._on_save_done(path)),
            err_cb=lambda e: self.after(0, lambda: self._on_save_error(e)),
        )

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_speak_done(self):
        self._set_busy(False)
        self.status_var.set("Done.")

    def _on_save_done(self, path: str):
        self.save_btn.config(state="normal")
        self.status_var.set(f"Saved: {path}")
        messagebox.showinfo("Saved", f"Audio saved to:\n{path}")

    def _on_save_error(self, err: str):
        self.save_btn.config(state="normal")
        self.status_var.set("Save failed.")
        messagebox.showerror("Save error", err)

    def _set_busy(self, active: bool):
        self.speak_btn.config(state="disabled" if active else "normal")
        self.stop_btn.config(state="normal" if active else "disabled")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TTSApp()
    app.mainloop()
