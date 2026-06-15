#!/usr/bin/env python3
"""Cross-platform GUI Text-to-Speech app.

Local voices are driven by pyttsx3. If the user supplies a valid ElevenLabs
API key, that account's cloud voices become available in the same picker.
"""

from __future__ import annotations

import configparser
import os
import platform
import queue
import stat
import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import pyttsx3


# Persisted settings live in a small INI file in the user's home directory.
CONFIG_PATH = Path.home() / ".text-to-speech.ini"


def load_api_key() -> str:
    """Return the saved ElevenLabs API key, or "" if none is stored."""
    parser = configparser.ConfigParser()
    try:
        parser.read(CONFIG_PATH)
    except (OSError, configparser.Error):
        return ""
    return parser.get("elevenlabs", "api_key", fallback="").strip()


def save_api_key(api_key: str) -> None:
    """Persist the ElevenLabs API key to the INI file (owner-only perms)."""
    parser = configparser.ConfigParser()
    parser.read(CONFIG_PATH)  # preserve any other sections
    if not parser.has_section("elevenlabs"):
        parser.add_section("elevenlabs")
    parser.set("elevenlabs", "api_key", api_key.strip())
    with open(CONFIG_PATH, "w") as f:
        parser.write(f)
    try:
        os.chmod(CONFIG_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    except OSError:
        pass  # best effort (e.g. Windows)


# ── Dedicated TTS worker thread (local / pyttsx3) ─────────────────────────────

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


# ── ElevenLabs cloud engine ───────────────────────────────────────────────────

class CloudTTS:
    """Thin wrapper around the ElevenLabs SDK.

    Network calls are synchronous; callers run them off the UI thread.
    """

    MODEL_ID = "eleven_multilingual_v2"

    def __init__(self):
        self._client = None
        self.voices: list = []  # list of (voice_id, name)

    @property
    def connected(self) -> bool:
        return self._client is not None

    def connect(self, api_key: str):
        """Validate the key and cache the account's voices.

        Listing voices both authenticates the key and gives us the data we
        need, so it doubles as the validation call. Raises on an invalid key.
        """
        from elevenlabs.client import ElevenLabs

        client = ElevenLabs(api_key=api_key.strip())
        resp = client.voices.get_all()  # raises ApiError (401) on a bad key
        self._client = client
        self.voices = [(v.voice_id, v.name or v.voice_id) for v in resp.voices]
        return self.voices

    def synthesize(self, text: str, voice_id: str, output_format: str) -> bytes:
        if not self._client:
            raise RuntimeError("ElevenLabs is not connected.")
        chunks = self._client.text_to_speech.convert(
            voice_id=voice_id,
            text=text,
            model_id=self.MODEL_ID,
            output_format=output_format,
        )
        return b"".join(chunks)


# ── Tooltip / accessible description ──────────────────────────────────────────

class Tooltip:
    """Lightweight hover/focus tooltip.

    Shows on both mouse hover *and* keyboard focus so the description is
    reachable without a pointer.
    """

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        for ev in ("<Enter>", "<FocusIn>"):
            widget.bind(ev, self._show, add="+")
        for ev in ("<Leave>", "<FocusOut>", "<ButtonPress>"):
            widget.bind(ev, self._hide, add="+")

    def _show(self, _event=None):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f"+{x}+{y}")
        tk.Label(
            self._tip, text=self.text, justify="left",
            bg="#ffffe0", fg="black", relief="solid", bd=1,
            font=("Helvetica", 10), padx=6, pady=3,
        ).pack()

    def _hide(self, _event=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ── Main GUI ─────────────────────────────────────────────────────────────────

class TTSApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Text to Speech")
        self.resizable(True, True)
        self.minsize(560, 500)

        self._tts = TTSEngine()
        self._tts.start()

        self._cloud = CloudTTS()
        # Unified voice list; each entry: {"provider", "id", "label"}
        self._voice_entries: list[dict] = []
        self._play_proc: subprocess.Popen | None = None  # active cloud playback

        # Platform modifier: Command on macOS, Control elsewhere.
        self._is_mac = platform.system() == "Darwin"
        self._mod = "Command" if self._is_mac else "Control"
        self._mod_label = "⌘" if self._is_mac else "Ctrl+"

        self._build_ui()
        self._build_menu()
        self._bind_shortcuts()

        # Start keyboard focus in the text box so typing works immediately.
        self.text_box.focus_set()

        # Load local voices after engine is ready (non-blocking)
        self.status_var.set("Initialising voices…")
        threading.Thread(target=self._wait_and_load, daemon=True).start()

        # Restore a previously saved API key and connect automatically.
        saved_key = load_api_key()
        if saved_key:
            self.api_key_var.set(saved_key)
            self._connect_cloud()

    def _accel(self, key: str) -> str:
        """Build a menu accelerator label, e.g. '⌘S' or 'Ctrl+S'."""
        return f"{self._mod_label}{key}"

    def _wait_and_load(self):
        self._tts.wait_ready()
        self.after(0, self._populate_voices)

    def _build_ui(self):
        pad = {"padx": 12, "pady": 6}
        mod = self._mod_label

        # ── Text area ────────────────────────────────────────────────────────
        tk.Label(
            self, text="Text to speak:", anchor="w", underline=0
        ).pack(fill="x", **pad)

        text_frame = tk.Frame(self)
        text_frame.pack(fill="both", expand=True, padx=12, pady=(0, 4))

        self.text_box = tk.Text(
            text_frame, wrap="word", height=10, font=("Helvetica", 14),
            highlightthickness=2, highlightcolor="#1a73e8",
        )
        sb = tk.Scrollbar(text_frame, command=self.text_box.yview)
        self.text_box.configure(yscrollcommand=sb.set)
        self.text_box.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        # Tab should move focus out of the multiline box, not insert a tab,
        # so keyboard users can traverse the form. (Ctrl+Tab inserts a tab.)
        self.text_box.bind("<Tab>", self._focus_next)
        self.text_box.bind("<Shift-Tab>", self._focus_prev)
        self.text_box.bind("<ISO_Left_Tab>", self._focus_prev)  # X11 Shift+Tab
        self.text_box.bind("<Control-Tab>", lambda e: self.text_box.insert("insert", "\t") or "break")
        Tooltip(self.text_box, "Type or paste the text to speak. Tab moves to the next control.")

        # ── ElevenLabs connection row ─────────────────────────────────────────
        el = tk.Frame(self)
        el.pack(fill="x", **pad)

        tk.Label(el, text="ElevenLabs API key:", underline=11).pack(side="left")
        self.api_key_var = tk.StringVar()
        self.api_key_entry = tk.Entry(
            el, textvariable=self.api_key_var, show="•", font=("Helvetica", 12),
            highlightthickness=2, highlightcolor="#1a73e8",
        )
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self.api_key_entry.bind("<Return>", lambda _e: self._connect_cloud())
        Tooltip(self.api_key_entry,
                "ElevenLabs API key. Press Enter to connect and load cloud voices.")

        self.connect_btn = self._make_button(
            el, text="Connect", command=self._connect_cloud, underline=0,
            font=("Helvetica", 12, "bold"),
            tip="Connect to ElevenLabs and load cloud voices.",
        )
        self.connect_btn.pack(side="left")

        # ── Voice + rate row ─────────────────────────────────────────────────
        ctrl = tk.Frame(self)
        ctrl.pack(fill="x", **pad)

        tk.Label(ctrl, text="Voice:", underline=0).grid(row=0, column=0, sticky="w")
        self.voice_var = tk.StringVar()
        self.voice_combo = ttk.Combobox(
            ctrl, textvariable=self.voice_var, state="readonly", width=34
        )
        self.voice_combo.grid(row=0, column=1, sticky="w", padx=(6, 20))
        self.voice_combo.bind("<<ComboboxSelected>>", self._on_voice_change)
        Tooltip(self.voice_combo,
                "Choose a voice. ☁ marks ElevenLabs cloud voices. "
                "Use arrow keys to change selection.")

        tk.Label(ctrl, text="Rate (wpm):", underline=0).grid(row=0, column=2, sticky="w")
        self.rate_var = tk.IntVar(value=175)
        self.rate_spin = tk.Spinbox(
            ctrl, from_=60, to=500, increment=10,
            textvariable=self.rate_var, width=6,
            highlightthickness=2, highlightcolor="#1a73e8",
        )
        self.rate_spin.grid(row=0, column=3, sticky="w", padx=6)
        Tooltip(self.rate_spin,
                "Speaking rate in words per minute (local voices only). "
                "Use Up/Down arrows to adjust.")

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_frame = tk.Frame(self)
        btn_frame.pack(fill="x", **pad)

        self.speak_btn = self._make_button(
            btn_frame, text="▶  Speak", width=12, underline=3,
            bg="#4CAF50", fg="white", activebackground="#45a049",
            command=self._speak,
            tip=f"Speak the text aloud ({mod}Return).",
        )
        self.speak_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = self._make_button(
            btn_frame, text="■  Stop", width=10, underline=6, state="disabled",
            bg="#f44336", fg="white", activebackground="#d32f2f",
            command=self._stop,
            tip="Stop playback (Esc).",
        )
        self.stop_btn.pack(side="left", padx=(0, 8))

        # ── Output file row ───────────────────────────────────────────────────
        out_frame = tk.Frame(self)
        out_frame.pack(fill="x", padx=12, pady=(0, 6))

        tk.Label(out_frame, text="Output file:", underline=0).pack(side="left")
        self.path_var = tk.StringVar()
        self.path_entry = tk.Entry(
            out_frame, textvariable=self.path_var, font=("Helvetica", 12),
            highlightthickness=2, highlightcolor="#1a73e8",
        )
        self.path_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
        Tooltip(self.path_entry, "Destination file for saved audio.")
        self._make_button(
            out_frame, text="Browse…", command=self._browse, underline=0,
            tip=f"Choose where to save the audio file ({mod}O).",
        ).pack(side="left", padx=(0, 8))
        self.save_btn = self._make_button(
            out_frame, text="Save Audio", command=self._save, underline=7,
            tip=f"Synthesize and save the audio to a file ({mod}S).",
        )
        self.save_btn.pack(side="left")

        # ── Status bar (announces app state) ──────────────────────────────────
        self.status_var = tk.StringVar(value="Ready.")
        self.status_label = tk.Label(
            self, textvariable=self.status_var, anchor="w",
            relief="sunken", bd=1, font=("Helvetica", 11), fg="gray40",
        )
        self.status_label.pack(fill="x", side="bottom", ipady=3)

    # ── Accessible widget helpers ──────────────────────────────────────────────

    def _make_button(self, parent, *, tip: str = "", **kwargs) -> tk.Button:
        """Create a flat tk.Button with a visible keyboard focus ring + tooltip."""
        cfg = dict(
            relief="flat", bd=0, font=("Helvetica", 13, "bold"),
            highlightthickness=2, highlightcolor="#1a73e8", takefocus=1,
        )
        cfg.update(kwargs)
        btn = tk.Button(parent, **cfg)
        # Space/Enter activate the focused button (Enter isn't default on tk).
        btn.bind("<Return>", lambda _e: btn.invoke())
        if tip:
            Tooltip(btn, tip)
        return btn

    def _focus_next(self, event):
        event.widget.tk_focusNext().focus_set()
        return "break"

    def _focus_prev(self, event):
        event.widget.tk_focusPrev().focus_set()
        return "break"

    # ── Menu bar (native → screen-reader accessible) ───────────────────────────

    def _build_menu(self):
        menubar = tk.Menu(self)

        speech = tk.Menu(menubar, tearoff=0)
        speech.add_command(
            label="Speak", command=self._speak,
            accelerator=self._accel("Return"),
        )
        speech.add_command(
            label="Stop", command=self._stop, accelerator="Esc",
        )
        speech.add_separator()
        speech.add_command(
            label="Connect to ElevenLabs…", command=self._connect_cloud,
        )
        menubar.add_cascade(label="Speech", menu=speech, underline=0)

        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(
            label="Choose Output File…", command=self._browse,
            accelerator=self._accel("O"),
        )
        filemenu.add_command(
            label="Save Audio", command=self._save,
            accelerator=self._accel("S"),
        )
        filemenu.add_separator()
        filemenu.add_command(
            label="Quit", command=self.destroy, accelerator=self._accel("Q"),
        )
        menubar.add_cascade(label="File", menu=filemenu, underline=0)

        helpmenu = tk.Menu(menubar, tearoff=0)
        helpmenu.add_command(
            label="Keyboard Shortcuts", command=self._show_shortcuts,
            accelerator="F1",
        )
        menubar.add_cascade(label="Help", menu=helpmenu, underline=0)

        self.config(menu=menubar)

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────

    def _bind_shortcuts(self):
        m = self._mod

        def act(fn):
            # Run the action, then swallow the event so it doesn't also type.
            return lambda _e: (fn(), "break")[1]

        # Primary actions (Command on macOS, Control elsewhere).
        self.bind_all(f"<{m}-Return>", act(self._speak))
        self.bind_all(f"<{m}-s>", act(self._save))
        self.bind_all(f"<{m}-o>", act(self._browse))
        self.bind_all(f"<{m}-q>", act(self.destroy))
        # Focus jumps.
        self.bind_all(f"<{m}-e>", act(lambda: self.text_box.focus_set()))
        self.bind_all(f"<{m}-k>", act(lambda: self.api_key_entry.focus_set()))
        # Stop + help work from anywhere.
        self.bind_all("<Escape>", act(self._stop))
        self.bind_all("<F1>", act(self._show_shortcuts))

        # Alt mnemonics (idiomatic on Windows/Linux; Option inserts glyphs on
        # macOS, so only bind them off-mac).
        if not self._is_mac:
            self.bind_all("<Alt-t>", act(lambda: self.text_box.focus_set()))
            self.bind_all("<Alt-a>", act(lambda: self.api_key_entry.focus_set()))
            self.bind_all("<Alt-c>", act(self._connect_cloud))
            self.bind_all("<Alt-v>", act(lambda: self.voice_combo.focus_set()))
            self.bind_all("<Alt-r>", act(lambda: self.rate_spin.focus_set()))
            self.bind_all("<Alt-s>", act(self._speak))
            self.bind_all("<Alt-p>", act(self._stop))
            self.bind_all("<Alt-o>", act(lambda: self.path_entry.focus_set()))
            self.bind_all("<Alt-b>", act(self._browse))
            self.bind_all("<Alt-d>", act(self._save))  # saAve auDio

    def _show_shortcuts(self):
        m = self._mod_label
        lines = [
            f"{m}Return\tSpeak the text",
            "Esc\tStop playback",
            f"{m}S\tSave audio to file",
            f"{m}O\tChoose output file",
            f"{m}E\tFocus the text box",
            f"{m}K\tFocus the API key field",
            f"{m}Q\tQuit",
            "Tab / Shift+Tab\tMove between controls",
            "Ctrl+Tab\tInsert a tab in the text box",
            "F1\tShow this help",
        ]
        messagebox.showinfo("Keyboard Shortcuts", "\n".join(lines))

    # ── Voice list ─────────────────────────────────────────────────────────────

    def _populate_voices(self, select_first_cloud: bool = False):
        """Rebuild the unified voice list from local + (any) cloud voices."""
        entries: list[dict] = []

        for v in self._tts.voices:
            name = v.name or v.id
            for prefix in ("Microsoft ", "MSTTS_V110_", "TTS_MS_"):
                name = name.replace(prefix, "")
            entries.append({"provider": "local", "id": v.id, "label": name})

        first_cloud_idx = len(entries)
        for voice_id, name in self._cloud.voices:
            entries.append(
                {"provider": "cloud", "id": voice_id, "label": f"☁ {name}"}
            )

        if not entries:
            messagebox.showerror("Error", "No voices found.")
            return

        self._voice_entries = entries
        self.voice_combo["values"] = [e["label"] for e in entries]

        if select_first_cloud and self._cloud.voices:
            self.voice_combo.current(first_cloud_idx)
        else:
            # Pick a sensible local default on first load
            preferred = ("zira", "david", "samantha", "alex")
            default = next(
                (i for i, e in enumerate(entries)
                 if e["provider"] == "local"
                 and any(p in e["label"].lower() for p in preferred)),
                0,
            )
            self.voice_combo.current(default)

        self._on_voice_change()
        local_n = sum(e["provider"] == "local" for e in entries)
        cloud_n = len(entries) - local_n
        suffix = f" + {cloud_n} cloud" if cloud_n else ""
        self.status_var.set(f"{local_n} local{suffix} voice(s) loaded. Ready.")

    def _selected_entry(self) -> dict:
        idx = max(self.voice_combo.current(), 0)
        return self._voice_entries[idx]

    def _on_voice_change(self, _event=None):
        """Rate only applies to local voices; grey it out for cloud voices."""
        is_cloud = self._selected_entry()["provider"] == "cloud"
        self.rate_spin.config(state="disabled" if is_cloud else "normal")

    # ── ElevenLabs connection ────────────────────────────────────────────────

    def _connect_cloud(self):
        api_key = self.api_key_var.get().strip()
        if not api_key:
            messagebox.showwarning(
                "No API key", "Enter your ElevenLabs API key first."
            )
            return

        self.connect_btn.config(state="disabled", text="Connecting…")
        self.status_var.set("Validating ElevenLabs API key…")
        threading.Thread(
            target=self._connect_worker, args=(api_key,), daemon=True
        ).start()

    def _connect_worker(self, api_key: str):
        try:
            voices = self._cloud.connect(api_key)
            self.after(0, lambda: self._on_cloud_connected(len(voices)))
        except Exception as exc:
            self.after(0, lambda: self._on_cloud_error(exc))

    def _on_cloud_connected(self, count: int):
        self.connect_btn.config(state="normal", text="Connected ✓")
        save_api_key(self.api_key_var.get())
        if count == 0:
            messagebox.showinfo(
                "Connected",
                "API key is valid, but this account has no voices available.",
            )
        # Refresh the picker and jump to the first cloud voice
        self._populate_voices(select_first_cloud=count > 0)

    def _on_cloud_error(self, exc: Exception):
        self.connect_btn.config(state="normal", text="Connect")
        msg = self._explain_cloud_error(exc)
        self.status_var.set("ElevenLabs connection failed.")
        messagebox.showerror("ElevenLabs", msg)

    @staticmethod
    def _explain_cloud_error(exc: Exception) -> str:
        # ApiError carries a status_code; 401/403 means a bad/unauthorized key.
        status = getattr(exc, "status_code", None)
        if status in (401, 403):
            return "That API key was rejected. Check the key and try again."
        if status == 429:
            return "Rate limit reached. Wait a moment and try again."
        return f"Could not reach ElevenLabs:\n{exc}"

    # ── Actions ──────────────────────────────────────────────────────────────

    def _get_text(self) -> str | None:
        text = self.text_box.get("1.0", "end").strip()
        if not text:
            messagebox.showwarning("No text", "Please enter some text first.")
            return None
        return text

    def _speak(self):
        text = self._get_text()
        if text is None:
            return

        entry = self._selected_entry()
        self._set_busy(True)
        self.status_var.set("Speaking…")

        if entry["provider"] == "local":
            self._tts.speak(
                text, entry["id"], self.rate_var.get(),
                done_cb=lambda: self.after(0, self._on_speak_done),
            )
        else:
            threading.Thread(
                target=self._cloud_speak_worker, args=(text, entry["id"]),
                daemon=True,
            ).start()

    def _cloud_speak_worker(self, text: str, voice_id: str):
        try:
            audio = self._cloud.synthesize(text, voice_id, "mp3_44100_128")
            fd, tmp = tempfile.mkstemp(suffix=".mp3", prefix="eltts_")
            with os.fdopen(fd, "wb") as f:
                f.write(audio)
            self.after(0, lambda: self.status_var.set("Playing…"))
            self._play_file(tmp)  # blocks until playback ends / is stopped
            try:
                os.remove(tmp)
            except OSError:
                pass
            self.after(0, self._on_speak_done)
        except Exception as exc:
            self.after(0, lambda: self._on_cloud_playback_error(exc))

    def _stop(self):
        # Stop whichever engine is currently producing audio.
        self._tts.stop()
        proc = self._play_proc
        if proc and proc.poll() is None:
            proc.terminate()

    def _browse(self):
        is_cloud = self._selected_entry()["provider"] == "cloud"
        if is_cloud:
            ext, filetypes = ".mp3", [("MP3 audio", "*.mp3"), ("All files", "*.*")]
        elif platform.system() == "Darwin":
            ext = ".aiff"
            filetypes = [("AIFF audio", "*.aiff *.aif"), ("All files", "*.*")]
        else:
            ext = ".wav"
            filetypes = [("WAV audio", "*.wav"), ("All files", "*.*")]

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
            messagebox.showwarning(
                "No output file", "Enter or browse for an output file path."
            )
            return

        entry = self._selected_entry()
        self.save_btn.config(state="disabled")
        self.status_var.set(f"Saving to {path}…")

        if entry["provider"] == "local":
            self._tts.save(
                text, entry["id"], self.rate_var.get(), path,
                done_cb=lambda: self.after(0, lambda: self._on_save_done(path)),
                err_cb=lambda e: self.after(0, lambda: self._on_save_error(e)),
            )
        else:
            threading.Thread(
                target=self._cloud_save_worker, args=(text, entry["id"], path),
                daemon=True,
            ).start()

    def _cloud_save_worker(self, text: str, voice_id: str, path: str):
        try:
            fmt = "wav_44100" if path.lower().endswith(".wav") else "mp3_44100_128"
            audio = self._cloud.synthesize(text, voice_id, fmt)
            with open(path, "wb") as f:
                f.write(audio)
            self.after(0, lambda: self._on_save_done(path))
        except Exception as exc:
            self.after(0, lambda: self._on_save_error(self._explain_cloud_error(exc)))

    # ── Cross-platform playback (for cloud audio bytes) ────────────────────────

    def _play_file(self, path: str):
        """Play an audio file, blocking until it finishes or _stop() kills it."""
        system = platform.system()
        if system == "Darwin":
            cmd = ["afplay", path]
        elif system == "Windows":
            # Default association handles mp3; play and return.
            os.startfile(path)  # type: ignore[attr-defined]
            return
        else:
            player = self._first_available(["ffplay", "mpv", "cvlc", "xdg-open"])
            if player is None:
                raise RuntimeError(
                    "No audio player found (install ffplay, mpv or vlc)."
                )
            cmd = (
                [player, "-nodisp", "-autoexit", "-loglevel", "quiet", path]
                if player == "ffplay"
                else [player, path]
            )

        self._play_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._play_proc.wait()
        self._play_proc = None

    @staticmethod
    def _first_available(candidates: list[str]) -> str | None:
        from shutil import which
        return next((c for c in candidates if which(c)), None)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_speak_done(self):
        self._set_busy(False)
        self.status_var.set("Done.")

    def _on_cloud_playback_error(self, exc: Exception):
        self._set_busy(False)
        self.status_var.set("Playback failed.")
        messagebox.showerror("Speak error", self._explain_cloud_error(exc))

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
        # Keep keyboard focus on an enabled, relevant control.
        if active:
            self.stop_btn.focus_set()
        elif self.focus_get() is self.stop_btn:
            self.speak_btn.focus_set()


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = TTSApp()
    app.mainloop()
