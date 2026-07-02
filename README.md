# Text to Speech

> **Note:** This project is not under active development. Development has been assisted by [Claude Code](https://claude.com/claude-code).

A cross-platform desktop Text-to-Speech app with a Tkinter GUI.

- **Local voices** are driven by [`pyttsx3`](https://pyttsx3.readthedocs.io/) (offline; uses the operating system's built-in voices).
- **Cloud voices** are provided by [ElevenLabs](https://elevenlabs.io/). Enter a valid API key and your account's voices appear in the same picker.
- Speak text aloud or save it to an audio file.
- **Load documents** — open a plain-text, Markdown, or Word (`.docx`) file. The app reads the document *and announces its formatting aloud* — headings, tables, lists, bold/italic/underlined and coloured text — so the listener hears the structure, not just the bare words.
- Designed to be **fully keyboard-operable** with shortcuts, a native menu bar, and tooltips.

The entire application lives in a single script: `simple-speech-output.py`.

---

## Table of contents

- [Requirements](#requirements)
- [Installation](#installation)
- [Running](#running)
- [Using the app](#using-the-app)
- [Loading documents](#loading-documents)
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

### Command-line (headless) mode

Pass any argument and the app runs **without a GUI**, speaking (or saving) text or a document using the voice, rate, and formatting settings last chosen in the GUI (stored in the [configuration file](#configuration-file)):

```bash
# Speak some text aloud with your saved voice
python simple-speech-output.py "Hello there"

# Read a document (formatting announced unless --no-announce)
python simple-speech-output.py --file report.docx --no-announce

# Save to an audio file instead of playing it
python simple-speech-output.py --file notes.md -o notes.wav

# Pipe text in on standard input (note the trailing "-")
echo "Piped text" | python simple-speech-output.py -

# List the available voices, then pick one for this run
python simple-speech-output.py --list-voices
python simple-speech-output.py "Bonjour" --voice samantha --rate 190
```

Useful options: `-f/--file`, `-o/--output`, `-v/--voice` (id or partial name), `-r/--rate`, `--provider {local,cloud}`, `--announce`/`--no-announce`, and `--list-voices`. Cloud (`--provider cloud`) reuses the ElevenLabs API key saved by the GUI. Run with `--help` for the full list. Launching with **no arguments** always opens the GUI.

---

## Using the app

1. **Type or paste** the text you want spoken into the large text area — or **Load File…** to pull text in from a document (see [Loading documents](#loading-documents)).
2. *(Optional)* **Connect to ElevenLabs:** paste your API key into the **ElevenLabs API key** field and press **Enter** (or click **Connect**). Once validated, the account's cloud voices are added to the **Voice** dropdown, prefixed with ☁. The key is then saved for next time. *New to ElevenLabs? See **Help → Setting up ElevenLabs…** for step-by-step instructions, including a button that opens the API-keys page in your browser.*
3. **Pick a voice** from the dropdown. Local voices have no prefix; cloud voices are marked ☁.
4. **Set the rate** (words per minute) for local voices. *(Rate does not apply to cloud voices and is disabled when one is selected.)*
5. **Speak** the text aloud, or set an **Output file** and **Save Audio** to write it to disk:
   - Local voices save to `.wav` (or `.aiff` on macOS).
   - Cloud voices save to `.mp3` by default, or `.wav` if you give the file a `.wav` extension.
   - A **confirmation box** first shows the estimated audio length (e.g. "roughly 2 minutes 5 seconds") and asks whether to continue.
   - While the audio is being written, an animated **progress bar** appears at the bottom of the window; it disappears when the save finishes (or fails).
6. Use **Stop** (or `Esc`) to cancel playback.

---

## Loading documents

The **Document** row (just below the text area) lets you read a file aloud instead of typing text in.

### Loading a file

- Click **Load File…** (or `⌘`/`Ctrl` + `L`, or **File → Load Document…**).
- Only **plain-text, Markdown, and Word** files can be opened:

  | Type | Extensions |
  | --- | --- |
  | Plain text | `.txt`, `.text` |
  | Markdown | `.md`, `.markdown` |
  | Word | `.docx` |

- The app produces an **annotated reading** — the document's text with its formatting *spoken inline* — and fills the text box with it, ready to **Speak** (or **Save Audio**). Because it's just text in the box, you can edit the annotations before speaking.

### Announce formatting toggle

Next to **Load File…** is an **Announce formatting** checkbox (on by default):

- **On** — formatting is spoken alongside the text (see the table below).
- **Off** — only the document's **plain text** is loaded; headings, emphasis, and table markers are dropped, and table cells are read as a simple comma-separated line.

Toggling the checkbox **immediately re-parses the currently loaded document**, so you can switch between the two readings without re-opening the file. (Off Windows/Linux the mnemonic is `Alt`+`N`.)

### How formatting is announced

*(when **Announce formatting** is on)*

The formatting is woven into the spoken text so you hear it as the document is read:

| Element | Spoken as |
| --- | --- |
| Heading | "Heading level 2. *Overview*." |
| Bold | "bold *important*" |
| Italic | "italic *note*" |
| Underline (Word) | "underlined *text*" |
| Coloured text (Word) | "red coloured *warning*" (named for common colours, otherwise "coloured") |
| Highlight (Word) | "yellow highlighted *text*" |
| Link (Markdown) | "link *click here*" |
| Image (Markdown) | "image *alt text*" |
| Inline code (Markdown) | "code *value*" |
| Bullet list item | "Bullet. *…*" |
| Numbered list item | "Item 3. *…*" |
| Blockquote | "Quote. *…*" |
| Code block (Markdown) | "Code block in python." … "End of code block." |
| Table | "Table with 3 rows and 2 columns. Header row: …. Row 1: …. End of table." |

Plain `.txt` files are read as-is (no formatting to announce). Word tables and Markdown pipe tables are both read row by row, with cells separated by a short pause.

### Security

Opened files are treated as untrusted and validated before parsing:

- **Extension allow-list** is enforced in code (not just the file-picker filter).
- **10 MB on-disk size limit** to avoid exhausting memory.
- The chosen path must resolve to a **regular file**.
- `.docx` files are ZIP containers, so the parser guards against **zip bombs** (caps on the number of entries and total uncompressed size) and against **XXE / "billion laughs" XML entity attacks** (any `<!DOCTYPE>`/`<!ENTITY>` declaration is rejected).
- Decoded text is **stripped of control characters** before it reaches the speech engine.

> Parsing uses only the Python standard library (`zipfile` + `xml.etree` for `.docx`, regex for Markdown) — there are **no extra dependencies** to install for document loading.

---

## Keyboard shortcuts

The modifier key is **⌘ Command on macOS** and **Ctrl on Windows/Linux**.

| Shortcut | Action |
| --- | --- |
| `⌘`/`Ctrl` + `Return` | Speak the text |
| `Esc` | Stop playback |
| `⌘`/`Ctrl` + `S` | Save audio to file |
| `⌘`/`Ctrl` + `L` | Load a document (text/Markdown/Word) |
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

The ElevenLabs API key and your voice preferences are persisted in an INI file in your home directory:

```
~/.text-to-speech.ini        (Windows: C:\Users\<you>\.text-to-speech.ini)
```

Format:

```ini
[voice]
provider = local        ; local | cloud
id = com.apple.voice.compact.en-US.Samantha
rate = 175              ; words per minute (local voices only)
announce = true         ; announce document formatting when loading

[elevenlabs]
api_key = sk_your_key_here
```

- The **`[voice]`** section is written whenever you change the voice, rate, or *Announce formatting* toggle in the GUI, and is restored on the next launch. Headless mode reads the same values (see [Command-line mode](#command-line-headless-mode)), so the CLI speaks in the voice you last used in the GUI.
- The **`[elevenlabs]`** section is created automatically after a **successful** connection (an invalid key is never saved).
- On POSIX systems the file is written with `0600` permissions (owner read/write only).
- On startup, a saved key is loaded and the app **auto-connects** so cloud voices are ready immediately; a saved cloud voice is re-selected once the account's voices finish loading.
- To reset, delete the file or clear the value you want to forget.

---

## Code reference

Everything is in `simple-speech-output.py`.

### Module-level functions

| Function | Description |
| --- | --- |
| `load_api_key() -> str` | Reads the saved ElevenLabs API key from `~/.text-to-speech.ini`. Returns `""` if the file or value is missing or unreadable. |
| `save_api_key(api_key: str) -> None` | Writes the API key to the INI file, preserving any other sections, and sets owner-only (`0600`) permissions where supported. |
| `load_document(path_str: str, announce: bool = True) -> ParsedDocument` | Validates (extension, size, regular-file checks), reads, and parses a `.txt`/`.md`/`.docx` file. With `announce=True` it builds an *annotated reading* (formatting spoken inline); with `announce=False` it returns the document's plain text only. Raises `DocumentError` on any unsupported, unsafe, or unparseable input. |

Internal parser helpers: `_parse_markdown()` and `_parse_docx()` (build the annotated reading); `_announce_inline_md()` (rewrites inline Markdown so emphasis/links are spoken); `_docx_paragraph_runs()` / `_docx_table()` (Word run/table rendering, merging adjacent like-styled runs); `_colour_name()` (hex → spoken colour); `_read_text_bytes()`, `_sanitize()` (drops control characters), and `_normalize_newlines()`.

Module constants:

- `CONFIG_PATH` — `pathlib.Path` to the INI file (`~/.text-to-speech.ini`).
- `ALLOWED_EXTS` — the set of permitted file extensions.
- `MAX_FILE_SIZE` (10 MB), `MAX_UNCOMPRESSED` (50 MB), `MAX_ZIP_ENTRIES` (2000) — security limits applied when loading documents.

### `class DocumentError(Exception)`

Raised by `load_document()` when a chosen file is unsupported, unsafe, or unparseable. The GUI catches it and shows the message in an error dialog.

### `@dataclass ParsedDocument`

The result of parsing a document.

| Field | Description |
| --- | --- |
| `spoken_text: str` | The annotated reading (text with formatting announced inline) fed to the TTS engine. |
| `source: str` | Absolute path of the source file. |
| `kind: str` | One of `text`, `markdown`, or `docx`. |

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
| `_build_ui()` | Constructs all widgets (text area, document row, API-key row, voice/rate row, buttons, output row, status bar) with focus rings, mnemonics, and tooltips. |
| `_make_button(parent, *, tip="", **kwargs) -> tk.Button` | Factory for flat buttons that have a visible keyboard focus ring, Enter/Space activation, and an optional tooltip. |
| `_focus_next(event)` / `_focus_prev(event)` | Move keyboard focus to the next/previous widget (used to free `Tab` from the multiline text box). |
| `_build_menu()` | Creates the native **Speech / File / Help** menu bar with accelerators. |
| `_bind_shortcuts()` | Registers all global keyboard shortcuts (and Alt mnemonics off macOS). |
| `_show_shortcuts()` | Displays the keyboard-shortcuts help dialog (`F1`). |
| `_show_elevenlabs_help()` | Opens the **Help → Setting up ElevenLabs…** dialog: step-by-step setup instructions plus a button that opens the ElevenLabs API-keys page in the browser. |

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
| `_open_document()` | Opens a file picker restricted to text/Markdown/Word files, then loads the chosen file via `_load_document_path()`. |
| `_load_document_path(path)` | Parses `path` with `load_document()` (honouring the **Announce formatting** checkbox) and fills the text box with the result. |
| `_on_announce_toggle()` | Re-parses the currently loaded document when the **Announce formatting** checkbox is flipped. |
| `_save()` | Saves the spoken text to the chosen output file (local engine or cloud worker). Always shows a confirmation box with the estimated audio length first. |
| `_estimate_save_seconds(text, entry)` | Estimates the audio/save length from the word count and speaking rate (≈150 wpm assumed for cloud voices). Returns `None` when there's nothing to estimate. |
| `_format_duration(seconds)` | Formats a duration as e.g. "2 minutes 5 seconds" for the confirmation box. |
| `_cloud_save_worker(text, voice_id, path)` | Background: synthesizes cloud audio and writes it to `path` (format chosen by extension). |
| `_play_file(path)` | Cross-platform audio playback: `afplay` (macOS), default association (Windows), `ffplay`/`mpv`/`cvlc`/`xdg-open` (Linux). Blocks until done or stopped. |
| `_first_available(candidates) -> str \| None` | Returns the first command on `PATH` from a list (used to find a Linux player). |
| `_on_speak_done()` | Resets the UI after speaking. |
| `_on_cloud_playback_error(exc)` | Shows an error if cloud playback fails. |
| `_on_save_done(path)` / `_on_save_error(err)` | Success/failure callbacks for saving; both stop the progress bar. |
| `_start_progress()` / `_stop_progress()` | Show/animate and hide the indeterminate progress bar shown while audio is being saved. |
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
  --hidden-import xml.etree.ElementTree `
  --collect-all elevenlabs `
  simple-speech-output.py
```

- `--hidden-import pyttsx3.drivers.sapi5` ensures the Windows speech driver ships with the build.
- `--hidden-import xml.etree.ElementTree` guarantees the `.docx` parser's XML module is bundled. (The import now lives at module top level, which PyInstaller usually picks up automatically, but this flag is a safe belt-and-suspenders.)
- `--collect-all elevenlabs` pulls in the SDK's data files and submodules.

### 4. Reproducible builds with a spec file

For repeatable builds, generate and edit a spec file once, then build from it:

```powershell
pyi-makespec --onefile --windowed --name TextToSpeech `
  --hidden-import pyttsx3.drivers.sapi5 --hidden-import xml.etree.ElementTree `
  --collect-all elevenlabs `
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
| "No module named `xml.etree`" (or similar) when opening a `.docx` in the packaged `.exe` | PyInstaller didn't bundle the XML module. Rebuild adding `--hidden-import xml.etree.ElementTree`. |
| ElevenLabs voices don't appear | Make sure the key is valid and the account actually has voices; check the status bar message after connecting. |
| "Unsupported file type" when loading a document | Only `.txt`, `.text`, `.md`, `.markdown`, and `.docx` files are allowed. Convert other formats (e.g. `.doc`, `.pdf`, `.rtf`) first. |
| "This is not a valid Word (.docx) document" | The file isn't a real `.docx` (e.g. an old `.doc` renamed, or a corrupt file). Re-save it as `.docx` from Word/LibreOffice. |
| "File is too large" / "expands too large" when loading | The document exceeds the 10 MB on-disk (or 50 MB uncompressed) safety limit. Split the file or paste the text directly. |
