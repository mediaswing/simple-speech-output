# Text to Speech

A cross-platform desktop Text-to-Speech app with a Tkinter GUI.

This project is experimental and not under active maintenance. Issues and pull requests may not receive a response.

- **Local voices** are driven by [`pyttsx3`](https://pyttsx3.readthedocs.io/) (offline; uses the operating system's built-in voices).
- **Cloud voices** are provided by [ElevenLabs](https://elevenlabs.io/). Enter a valid API key and your account's voices appear in the same picker.
- Speak text aloud or save it to an audio file.
- Designed to be **fully keyboard-operable** with shortcuts, a native menu bar, and tooltips.

The entire application lives in a single script: `simple-speech-output.py`.

---

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Using the app](#using-the-app)
- [Keyboard shortcuts](#keyboard-shortcuts)
- [Configuration file](#configuration-file)
- [Code reference](#code-reference)
- [Building a Windows executable](#building-a-windows-executable)
- [Troubleshooting](#troubleshooting)

---

## Requirements

- **Python 3.9 or newer** (the code uses `from __future__ import annotations`, so modern type-hint syntax works on 3.9+).
- **Tkinter** — bundled with most Python installers. On the python.org Windows/macOS installers it is included by default. On some Linux distros install it separately (e.g. `sudo apt install python3-tk`).
- **pyttsx3** — local TTS engine.
- **elevenlabs** — cloud TTS SDK (optional at runtime; only needed if you use cloud voices).

Python packages:

```
pyttsx3
elevenlabs
```

> **Platform speech back-ends used by `pyttsx3`:**
> - Windows: SAPI5 (built in)
> - macOS: NSSpeechSynthesizer (built in)
> - Linux: `espeak` (install via your package manager, e.g. `sudo apt install espeak`)

---

## Installation

```bash
# 1. (recommended) create and activate a virtual environment
python -m venv .venv
# Windows:  .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 2. install dependencies
pip install pyttsx3 elevenlabs
```

There is no `requirements.txt` in the repo yet; create one if you like:

```
pyttsx3
elevenlabs
```

---

## Running

```bash
python simple-speech-output.py
```

The window opens with keyboard focus already in the text box, so you can start typing immediately.

---

## Using the app

1. **Type or paste** the text you want spoken into the large text area.
2. *(Optional)* **Connect to ElevenLabs:** paste your API key into the **ElevenLabs API key** field and press **Enter** (or click **Connect**). Once validated, the account's cloud voices are added to the **Voice** dropdown, prefixed with ☁. The key is then saved for next time.
3. **Pick a voice** from the dropdown. Local voices have no prefix; cloud voices are marked ☁.
4. **Set the rate** (words per minute) for local voices. *(Rate does not apply to cloud voices and is disabled when one is selected.)*
5. **Speak** the text aloud, or set an **Output file** and **Save Audio** to write it to disk:
   - Local voices save to `.wav` (or `.aiff` on macOS).
   - Cloud voices save to `.mp3` by default, or `.wav` if you give the file a `.wav` extension.
6. Use **Stop** (or `Esc`) to cancel playback.

---

## Keyboard shortcuts

The modifier key is **⌘ Command on macOS** and **Ctrl on Windows/Linux**.

| Shortcut | Action |
| --- | --- |
| `⌘`/`Ctrl` + `Return` | Speak the text |
| `Esc` | Stop playback |
| `⌘`/`Ctrl` + `S` | Save audio to file |
| `⌘`/`Ctrl` + `O` | Choose output file |
| `⌘`/`Ctrl` + `E` | Focus the text box |
| `⌘`/`Ctrl` + `K` | Focus the API key field |
| `⌘`/`Ctrl` + `Q` | Quit |
| `Tab` / `Shift`+`Tab` | Move between controls |
| `Ctrl` + `Tab` | Insert a literal tab in the text box |
| `F1` | Show the keyboard-shortcuts dialog |

On **Windows/Linux** the underlined letters in labels and buttons are **Alt mnemonics** (e.g. `Alt`+`S` = Speak, `Alt`+`C` = Connect). These are intentionally **not** bound on macOS, where the `Option` key inserts special characters instead.

All actions are also reachable through the native **Speech / File / Help** menu bar, which is exposed to screen readers.

---

## Configuration file

The ElevenLabs API key is persisted in an INI file in your home directory:

```
~/.text-to-speech.ini        (Windows: C:\Users\<you>\.text-to-speech.ini)
```

Format:

```ini
[elevenlabs]
api_key = sk_your_key_here
```

- The file is created automatically after a **successful** connection (an invalid key is never saved).
- On POSIX systems it is written with `0600` permissions (owner read/write only).
- On startup, a saved key is loaded and the app **auto-connects** so cloud voices are ready immediately.
- To forget the key, delete the file or clear the `api_key` value.

---

## Code reference

Everything is in `simple-speech-output.py`.

### Module-level functions

| Function | Description |
| --- | --- |
| `load_api_key() -> str` | Reads the saved ElevenLabs API key from `~/.text-to-speech.ini`. Returns `""` if the file or value is missing or unreadable. |
| `save_api_key(api_key: str) -> None` | Writes the API key to the INI file, preserving any other sections, and sets owner-only (`0600`) permissions where supported. |

Module constant: `CONFIG_PATH` — `pathlib.Path` to the INI file (`~/.text-to-speech.ini`).

### `class TTSEngine(threading.Thread)`

Owns the `pyttsx3` engine on its **own thread** to avoid event-loop conflicts with Tkinter. Commands are queued and processed sequentially.

| Method | Description |
| --- | --- |
| `__init__()` | Sets up the command queue, the `voices` list, and a "ready" event. |
| `run()` | Thread body. Initialises `pyttsx3`, caches available voices, then loops processing `speak` / `save` / `quit` commands from the queue. |
| `wait_ready()` | Blocks until the engine has finished initialising (used so the UI can load voices without freezing). |
| `speak(text, voice_id, rate, done_cb=None)` | Queues a request to speak `text` with the given voice/rate. `done_cb` is invoked when finished. |
| `save(text, voice_id, rate, path, done_cb=None, err_cb=None)` | Queues a request to render `text` to the audio file at `path`. Calls `done_cb` on success or `err_cb(message)` on failure. |
| `stop()` | Immediately stops the current local utterance. |

### `class CloudTTS`

Thin wrapper around the ElevenLabs SDK. All network calls are synchronous and are run off the UI thread by the caller. Uses model `eleven_multilingual_v2`.

| Member | Description |
| --- | --- |
| `connected` *(property)* | `True` once a client has been created via `connect()`. |
| `connect(api_key) -> list[tuple[str, str]]` | Validates the key by calling `voices.get_all()` (this both authenticates **and** fetches voices). Caches `(voice_id, name)` pairs in `self.voices`. Raises on an invalid key. |
| `synthesize(text, voice_id, output_format) -> bytes` | Calls ElevenLabs text-to-speech `convert` and returns the joined audio bytes. `output_format` is an ElevenLabs format string such as `"mp3_44100_128"` or `"wav_44100"`. |

### `class Tooltip`

Lightweight hover/focus tooltip. It appears on **both** mouse hover and **keyboard focus**, so descriptions are reachable without a pointer.

| Method | Description |
| --- | --- |
| `__init__(widget, text)` | Binds enter/focus-in (show) and leave/focus-out/click (hide) events to `widget`. |
| `_show(event=None)` | Creates a borderless tooltip window beneath the widget. |
| `_hide(event=None)` | Destroys the tooltip window. |

### `class TTSApp(tk.Tk)`

The main application window. Wires together the local engine, the cloud engine, and the GUI.

**Setup / infrastructure**

| Method | Description |
| --- | --- |
| `__init__()` | Builds the window, engines, menu, shortcuts, and starts background voice loading; restores and auto-connects a saved API key. |
| `_accel(key) -> str` | Builds a platform-correct menu accelerator label (e.g. `⌘S` or `Ctrl+S`). |
| `_wait_and_load()` | Background helper: waits for the local engine, then loads voices on the UI thread. |
| `_build_ui()` | Constructs all widgets (text area, API-key row, voice/rate row, buttons, output row, status bar) with focus rings, mnemonics, and tooltips. |
| `_make_button(parent, *, tip="", **kwargs) -> tk.Button` | Factory for flat buttons that have a visible keyboard focus ring, Enter/Space activation, and an optional tooltip. |
| `_focus_next(event)` / `_focus_prev(event)` | Move keyboard focus to the next/previous widget (used to free `Tab` from the multiline text box). |
| `_build_menu()` | Creates the native **Speech / File / Help** menu bar with accelerators. |
| `_bind_shortcuts()` | Registers all global keyboard shortcuts (and Alt mnemonics off macOS). |
| `_show_shortcuts()` | Displays the keyboard-shortcuts help dialog (`F1`). |

**Voice handling**

| Method | Description |
| --- | --- |
| `_populate_voices(select_first_cloud=False)` | Rebuilds the unified voice list from local + cloud voices and fills the dropdown. Optionally selects the first cloud voice. |
| `_selected_entry() -> dict` | Returns the currently selected voice entry: `{"provider", "id", "label"}`. |
| `_on_voice_change(event=None)` | Enables/disables the rate spinbox depending on whether a local or cloud voice is selected. |

**ElevenLabs connection**

| Method | Description |
| --- | --- |
| `_connect_cloud()` | Validates the key field and starts a background connection. |
| `_connect_worker(api_key)` | Background thread: calls `CloudTTS.connect()` and dispatches the result to the UI. |
| `_on_cloud_connected(count)` | UI callback on success: saves the key, refreshes the voice picker. |
| `_on_cloud_error(exc)` | UI callback on failure: shows a friendly error dialog. |
| `_explain_cloud_error(exc) -> str` | Converts an exception/HTTP status (401/403/429/…) into a human-readable message. |

**Actions & callbacks**

| Method | Description |
| --- | --- |
| `_get_text() -> str \| None` | Returns the trimmed text, or warns and returns `None` if empty. |
| `_speak()` | Speaks the text using the selected voice (local engine or a cloud worker thread). |
| `_cloud_speak_worker(text, voice_id)` | Background: synthesizes cloud audio to a temp file and plays it. |
| `_stop()` | Stops both the local engine and any running cloud playback process. |
| `_browse()` | Opens a "save as" dialog with sensible defaults per provider/platform. |
| `_save()` | Saves the spoken text to the chosen output file (local engine or cloud worker). |
| `_cloud_save_worker(text, voice_id, path)` | Background: synthesizes cloud audio and writes it to `path` (format chosen by extension). |
| `_play_file(path)` | Cross-platform audio playback: `afplay` (macOS), default association (Windows), `ffplay`/`mpv`/`cvlc`/`xdg-open` (Linux). Blocks until done or stopped. |
| `_first_available(candidates) -> str \| None` | Returns the first command on `PATH` from a list (used to find a Linux player). |
| `_on_speak_done()` | Resets the UI after speaking. |
| `_on_cloud_playback_error(exc)` | Shows an error if cloud playback fails. |
| `_on_save_done(path)` / `_on_save_error(err)` | Success/failure callbacks for saving. |
| `_set_busy(active)` | Toggles Speak/Stop button states and keeps keyboard focus on an enabled control. |

---

## Building a Windows executable

The app can be packaged into a standalone `.exe` with [PyInstaller](https://pyinstaller.org/) so end users don't need Python installed.

> **Important:** PyInstaller does **not** cross-compile. To produce a Windows `.exe` you must run these steps **on Windows** (a real machine, a Windows VM, or a Windows CI runner such as GitHub Actions `windows-latest`).

### 1. Set up on Windows

Open **PowerShell** or **Command Prompt** on the Windows machine:

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install pyttsx3 elevenlabs pyinstaller
```

### 2. Build

A single-file, windowed (no console) build:

```powershell
pyinstaller --onefile --windowed --name TextToSpeech simple-speech-output.py
```

Flag meanings:

| Flag | Purpose |
| --- | --- |
| `--onefile` | Bundle everything into one `.exe`. |
| `--windowed` | GUI app — don't open a console window. (Alias: `--noconsole`.) |
| `--name TextToSpeech` | Name of the output executable. |
| `--icon app.ico` | *(optional)* Use a custom icon. |

The executable is produced at:

```
dist\TextToSpeech.exe
```

### 3. Make sure dependencies are bundled

`pyttsx3` loads its Windows driver (`sapi5`) dynamically, which PyInstaller can miss. If the app crashes on launch with a `pyttsx3.drivers` import error, include the hidden imports explicitly:

```powershell
pyinstaller --onefile --windowed --name TextToSpeech `
  --hidden-import pyttsx3.drivers `
  --hidden-import pyttsx3.drivers.sapi5 `
  --collect-all elevenlabs `
  simple-speech-output.py
```

- `--hidden-import pyttsx3.drivers.sapi5` ensures the Windows speech driver ships with the build.
- `--collect-all elevenlabs` pulls in the SDK's data files and submodules.

### 4. Reproducible builds with a spec file

For repeatable builds, generate and edit a spec file once, then build from it:

```powershell
pyi-makespec --onefile --windowed --name TextToSpeech `
  --hidden-import pyttsx3.drivers.sapi5 --collect-all elevenlabs `
  simple-speech-output.py

pyinstaller TextToSpeech.spec
```

### 5. Test

Run `dist\TextToSpeech.exe` on a **clean** Windows machine (one without Python) to confirm it launches, lists local SAPI5 voices, speaks, and (with a key) loads ElevenLabs voices.

> **Code signing (optional but recommended):** unsigned executables trigger SmartScreen warnings. Sign the `.exe` with a code-signing certificate (`signtool sign /fd SHA256 /a dist\TextToSpeech.exe`) before distribution.

### Building on other platforms

The same `pyinstaller --onefile --windowed ...` command works on **macOS** (produces a `.app`/Unix binary) and **Linux** (produces an ELF binary). Each must be built on its own OS.

---

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `macOS 26 (2603) or later required` crash on launch | Your Python's bundled Tcl/Tk is too old for your macOS. Use a current python.org or Homebrew Python (which ships a compatible Tk). |
| "No voices found" | No local TTS voices are installed. On Linux install `espeak`; on Windows add voices via *Settings → Time & Language → Speech*. |
| Cloud connection says the key was rejected | The API key is wrong or lacks permission. Generate a new key in your ElevenLabs account. |
| Cloud "Speak" fails on Linux with "No audio player found" | Install one of `ffmpeg` (for `ffplay`), `mpv`, or `vlc`. |
| `.exe` crashes immediately on Windows | Rebuild with the `--hidden-import pyttsx3.drivers.sapi5` and `--collect-all elevenlabs` flags shown above. |
| ElevenLabs voices don't appear | Make sure the key is valid and the account actually has voices; check the status bar message after connecting. |
