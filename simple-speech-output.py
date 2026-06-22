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
import re
import stat
import subprocess
import tempfile
import threading
import tkinter as tk
import webbrowser
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from xml.etree import ElementTree as ET

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


# ── Document loading & formatting parser ──────────────────────────────────────
#
# Reads a user-chosen text, Markdown, or Word (.docx) file and produces an
# *annotated reading*: the document's text with its formatting announced aloud,
# so the listener hears headings, tables, bold/italic/coloured text, etc. — not
# just the bare words. Parsing uses only the standard library, so there are no
# extra dependencies to keep secure/up to date.
#
# Security model: untrusted files are validated before parsing.
#   * Extension allow-list (defence in depth alongside the file dialog filter).
#   * On-disk size cap, so a huge file can't exhaust memory.
#   * .docx is a ZIP container, so we guard against zip bombs (entry count +
#     total uncompressed size) and against XXE / "billion laughs" XML entity
#     attacks (we refuse any DTD/entity declarations).
#   * Decoded text is sanitised of control characters before it reaches TTS.

ALLOWED_EXTS = {".txt", ".text", ".md", ".markdown", ".docx"}
MAX_FILE_SIZE = 10 * 1024 * 1024          # 10 MB on disk
MAX_UNCOMPRESSED = 50 * 1024 * 1024       # 50 MB after ZIP inflation
MAX_ZIP_ENTRIES = 2000                    # sane upper bound for a .docx

# WordprocessingML namespace used inside a .docx document.xml.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

# Map common hex fill/font colours to spoken names; anything else is just
# announced as "coloured" so the listener still knows the text was styled.
_HEX_COLOURS = {
    "ff0000": "red", "00ff00": "green", "008000": "green", "0000ff": "blue",
    "ffff00": "yellow", "ffa500": "orange", "800080": "purple",
    "ffc0cb": "pink", "a52a2a": "brown", "808080": "grey", "000000": "black",
    "ffffff": "white", "00ffff": "cyan", "ff00ff": "magenta",
}


def _w(tag: str) -> str:
    return f"{{{_W_NS}}}{tag}"


class DocumentError(Exception):
    """Raised when a chosen file is unsupported, unsafe, or unparseable."""


@dataclass
class ParsedDocument:
    spoken_text: str            # annotated reading fed to the TTS engine
    source: str = ""            # absolute path of the source file
    kind: str = "text"          # text | markdown | docx


def _normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _sanitize(text: str) -> str:
    """Drop control characters (keep tab/newline) to protect downstream code."""
    return "".join(
        ch for ch in text
        if ch in ("\n", "\t") or ord(ch) >= 32
    )


def _read_text_bytes(path: Path) -> str:
    # Size is already capped by the caller; utf-8-sig strips any BOM.
    return path.read_bytes().decode("utf-8-sig", errors="replace")


def _colour_name(val: str | None) -> str | None:
    """Spoken name for a hex colour value, or None for 'auto'/black/missing."""
    if not val or val.lower() in ("auto", "000000"):
        return None
    return _HEX_COLOURS.get(val.lstrip("#").lower(), "coloured")


# ── Markdown ──────────────────────────────────────────────────────────────────

def _inline_md(text: str, announce: bool) -> str:
    """Render inline Markdown to speech.

    When *announce* is true the formatting is spoken alongside the text
    (e.g. "bold important"); otherwise the markup is simply stripped.
    """
    if announce:
        repl_image = lambda m: f" image {m.group(1)} " if m.group(1) else " image "
        repl_link = lambda m: f" link {m.group(1)} "
        repl_bold = lambda m: f" bold {m.group(2)} "
        repl_italic = lambda m: f" italic {m.group(2)} "
        repl_code = lambda m: f" code {m.group(1)} "
    else:
        repl_image = repl_link = repl_code = lambda m: m.group(1)
        repl_bold = repl_italic = lambda m: m.group(2)

    # Images: ![alt](url)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", repl_image, text)
    # Links: [text](url)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", repl_link, text)
    # Bold (** or __) before italic so the markers aren't half-consumed.
    text = re.sub(r"(\*\*|__)(.+?)\1", repl_bold, text)
    # Italic (* or _)
    text = re.sub(r"(?<!\w)(\*|_)(?!\s)(.+?)(?<!\s)\1(?!\w)", repl_italic, text)
    # Inline code
    text = re.sub(r"`([^`]+)`", repl_code, text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)  # no space before punctuation
    return text.strip()


def _md_is_table_separator(line: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$", line))


def _md_split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    return [c.strip() for c in line.split("|")]


def _parse_markdown(raw: str, announce: bool) -> str:
    lines = _normalize_newlines(raw).split("\n")
    out: list[str] = []
    in_fence = False
    fence_lang = ""
    code: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]

        fence = re.match(r"^\s*(```|~~~)(.*)$", line)
        if fence:
            if not in_fence:
                in_fence, fence_lang, code = True, fence.group(2).strip(), []
            else:
                if announce:
                    out.append(f"Code block in {fence_lang}."
                               if fence_lang else "Code block.")
                out.extend(code)
                if announce:
                    out.append("End of code block.")
                in_fence = False
            i += 1
            continue
        if in_fence:
            code.append(line)
            i += 1
            continue

        # GFM pipe table: a row, then a |---|---| separator line.
        if "|" in line and i + 1 < len(lines) and _md_is_table_separator(lines[i + 1]):
            header = _md_split_row(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip() and "|" in lines[i]:
                rows.append(_md_split_row(lines[i]))
                i += 1
            if announce:
                out.append(f"Table with {len(rows) + 1} rows "
                           f"and {len(header)} columns.")
                out.append("Header row: "
                           + "; ".join(_inline_md(c, announce) for c in header) + ".")
                for r, cells in enumerate(rows, start=1):
                    out.append(f"Row {r}: "
                               + "; ".join(_inline_md(c, announce) for c in cells)
                               + ".")
                out.append("End of table.")
            else:
                out.append(", ".join(_inline_md(c, announce) for c in header))
                for cells in rows:
                    out.append(", ".join(_inline_md(c, announce) for c in cells))
            continue

        heading = re.match(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if heading:
            text = _inline_md(heading.group(2), announce)
            if announce:
                out.append(f"Heading level {len(heading.group(1))}. {text}.")
            else:
                out.append(text)
            i += 1
            continue

        if re.match(r"^\s{0,3}([-*_])(\s*\1){2,}\s*$", line):  # --- *** ___
            i += 1  # thematic break: nothing to read
            continue

        quote = re.match(r"^\s*>\s?(.*)$", line)
        if quote:
            text = _inline_md(quote.group(1), announce)
            out.append(f"Quote. {text}" if announce else text)
            i += 1
            continue

        item = re.match(r"^\s*([-*+]|\d+[.)])\s+(.*)$", line)
        if item:
            marker, body_txt = item.group(1), _inline_md(item.group(2), announce)
            if not announce:
                out.append(body_txt)
            elif marker[0].isdigit():
                out.append(f"Item {marker.rstrip('.)')}. {body_txt}")
            else:
                out.append(f"Bullet. {body_txt}")
            i += 1
            continue

        out.append(_inline_md(line, announce))
        i += 1

    return "\n".join(out)


# ── Word (.docx) ──────────────────────────────────────────────────────────────

def _docx_toggle(el) -> bool:
    """True when a boolean run property (<w:b/>, <w:i/>, …) is actually on."""
    if el is None:
        return False
    return (el.get(_w("val")) or "true") not in ("0", "false", "off")


def _docx_paragraph_runs(para, announce: bool) -> str:
    """Text for one paragraph, merging adjacent like-styled runs.

    When *announce* is true, run-level formatting (bold/italic/underline/
    colour/highlight) is spoken; otherwise only the words are returned.
    """
    if not announce:
        return "".join(
            t.text or "" for t in para.iter(_w("t"))
        ).strip()

    segments: list[tuple[tuple, str]] = []
    cur_sig = None
    cur_text: list[str] = []

    for run in para.iter(_w("r")):
        rtext = "".join(t.text or "" for t in run.iter(_w("t")))
        if not rtext:
            continue
        rpr = run.find(_w("rPr"))
        bold = italic = under = False
        colour = highlight = None
        if rpr is not None:
            bold = _docx_toggle(rpr.find(_w("b")))
            italic = _docx_toggle(rpr.find(_w("i")))
            u = rpr.find(_w("u"))
            under = u is not None and (u.get(_w("val")) or "single") != "none"
            c = rpr.find(_w("color"))
            if c is not None:
                colour = _colour_name(c.get(_w("val")))
            h = rpr.find(_w("highlight"))
            if h is not None:
                hv = h.get(_w("val"))
                highlight = hv if hv and hv != "none" else None

        sig = (bold, italic, under, colour, highlight)
        if sig == cur_sig:
            cur_text.append(rtext)
        else:
            if cur_text:
                segments.append((cur_sig, "".join(cur_text)))
            cur_sig, cur_text = sig, [rtext]
    if cur_text:
        segments.append((cur_sig, "".join(cur_text)))

    parts: list[str] = []
    for sig, txt in segments:
        txt = txt.strip()
        if not txt:
            continue
        bold, italic, under, colour, highlight = sig
        pre = []
        if bold:
            pre.append("bold")
        if italic:
            pre.append("italic")
        if under:
            pre.append("underlined")
        if colour:
            pre.append(colour if colour == "coloured" else f"{colour} coloured")
        if highlight:
            pre.append(f"{highlight} highlighted")
        parts.append(f"{' '.join(pre)} {txt}" if pre else txt)
    return " ".join(parts).strip()


def _docx_table(tbl, announce: bool) -> list[str]:
    rows = []
    ncols = 0
    for tr in tbl.findall(_w("tr")):
        cells = []
        for tc in tr.findall(_w("tc")):
            cell = " ".join(
                t for t in
                (_docx_paragraph_runs(p, announce) for p in tc.findall(_w("p")))
                if t
            )
            cells.append(cell.strip())
        ncols = max(ncols, len(cells))
        rows.append(cells)

    if not announce:
        return [", ".join(cells) for cells in rows]

    out = [f"Table with {len(rows)} rows and {ncols} columns."]
    for idx, cells in enumerate(rows, start=1):
        out.append(f"Row {idx}: " + "; ".join(cells) + ".")
    out.append("End of table.")
    return out


def _parse_docx(path: Path, announce: bool) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if "word/document.xml" not in names:
                raise DocumentError("This is not a valid Word (.docx) document.")
            if len(names) > MAX_ZIP_ENTRIES:
                raise DocumentError("Document has too many internal parts.")
            if sum(info.file_size for info in z.infolist()) > MAX_UNCOMPRESSED:
                raise DocumentError("Document expands too large to open safely.")
            with z.open("word/document.xml") as f:
                data = f.read(MAX_UNCOMPRESSED + 1)
    except zipfile.BadZipFile:
        raise DocumentError("This is not a valid Word (.docx) document.")

    if len(data) > MAX_UNCOMPRESSED:
        raise DocumentError("Document is too large to open safely.")
    # A genuine document.xml has no DTD; refuse one to block XXE / entity bombs.
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise DocumentError("Document contains disallowed XML declarations.")

    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise DocumentError("Could not parse the Word document.")

    body = root.find(_w("body"))
    if body is None:
        return ""

    out: list[str] = []
    # Walk the body's direct children so paragraphs and tables stay in order.
    for child in body:
        if child.tag == _w("p"):
            text = _docx_paragraph_runs(child, announce)
            if not text:
                continue
            style = None
            ppr = child.find(_w("pPr"))
            if ppr is not None:
                pstyle = ppr.find(_w("pStyle"))
                if pstyle is not None:
                    style = pstyle.get(_w("val"))
            if announce and style and style.lower().startswith("heading"):
                level = "".join(c for c in style if c.isdigit()) or "1"
                out.append(f"Heading level {level}. {text}.")
            else:
                out.append(text)
        elif child.tag == _w("tbl"):
            out.extend(_docx_table(child, announce))

    return "\n".join(out)


# ── Public entry point ────────────────────────────────────────────────────────

def load_document(path_str: str, announce: bool = True) -> ParsedDocument:
    """Validate, read, and parse a document. Raises DocumentError on any issue.

    When *announce* is false the result is the document's plain text only; when
    true the formatting is announced inline (headings, tables, bold, etc.).
    """
    try:
        resolved = Path(path_str).expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        raise DocumentError("File not found.")
    if not resolved.is_file():
        raise DocumentError("The chosen path is not a regular file.")

    ext = resolved.suffix.lower()
    if ext not in ALLOWED_EXTS:
        raise DocumentError(
            f"Unsupported file type '{ext or '(none)'}'. "
            "Only .txt, .md/.markdown, and .docx files are allowed."
        )
    if resolved.stat().st_size > MAX_FILE_SIZE:
        raise DocumentError("File is too large (10 MB limit).")

    if ext == ".docx":
        spoken, kind = _parse_docx(resolved, announce), "docx"
    elif ext in (".md", ".markdown"):
        spoken, kind = _parse_markdown(_read_text_bytes(resolved), announce), "markdown"
    else:
        spoken, kind = _read_text_bytes(resolved), "text"

    spoken = _sanitize(_normalize_newlines(spoken)).strip()
    return ParsedDocument(spoken_text=spoken, source=str(resolved), kind=kind)


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
        self._parsed: ParsedDocument | None = None  # last loaded document

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

        # ── Input document row ────────────────────────────────────────────────
        doc_frame = tk.Frame(self)
        doc_frame.pack(fill="x", **pad)

        tk.Label(doc_frame, text="Document:", underline=0).pack(side="left")
        self.doc_var = tk.StringVar(value="No file loaded.")

        # Load button sits at the right; checkbox to its left; label fills middle.
        self._make_button(
            doc_frame, text="Load File…", command=self._open_document, underline=0,
            tip=f"Open a text, Markdown, or Word file ({mod}L). Its text fills the "
                "box, with formatting announced unless the checkbox is off.",
        ).pack(side="right", padx=(8, 0))

        self.announce_var = tk.BooleanVar(value=True)
        self.announce_chk = tk.Checkbutton(
            doc_frame, text="Announce formatting", variable=self.announce_var,
            command=self._on_announce_toggle, underline=1,  # 'n' (Alt+N off-mac)
            highlightthickness=2, highlightcolor="#1a73e8", takefocus=1,
        )
        self.announce_chk.pack(side="right", padx=(8, 0))
        self.announce_chk.bind("<Return>", lambda _e: self.announce_chk.invoke())
        Tooltip(self.announce_chk,
                "When on, headings, tables, bold and coloured text are spoken "
                "aloud as you read a loaded document. Turn off to read the plain "
                "text only. Re-parses the current document immediately.")

        tk.Label(
            doc_frame, textvariable=self.doc_var, anchor="w", fg="gray40",
            font=("Helvetica", 11),
        ).pack(side="left", fill="x", expand=True, padx=(6, 6))

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

        # Indeterminate progress bar shown only while an audio save is running
        # (neither engine reports a percentage, so it just animates activity).
        # Created now but not packed; _start/_stop_progress show/hide it.
        self.progress = ttk.Progressbar(self, mode="indeterminate")

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
            label="Load Document…", command=self._open_document,
            accelerator=self._accel("L"),
        )
        filemenu.add_separator()
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
        helpmenu.add_command(
            label="Setting up ElevenLabs…", command=self._show_elevenlabs_help,
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
        self.bind_all(f"<{m}-l>", act(self._open_document))
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
            self.bind_all("<Alt-l>", act(self._open_document))
            self.bind_all("<Alt-n>", act(lambda: self.announce_chk.invoke()))
            self.bind_all("<Alt-b>", act(self._browse))
            self.bind_all("<Alt-d>", act(self._save))  # saAve auDio

    def _show_shortcuts(self):
        m = self._mod_label
        lines = [
            f"{m}Return\tSpeak the text",
            "Esc\tStop playback",
            f"{m}S\tSave audio to file",
            f"{m}L\tLoad a document (text/Markdown/Word)",
            f"{m}O\tChoose output file",
            f"{m}E\tFocus the text box",
            f"{m}K\tFocus the API key field",
            f"{m}Q\tQuit",
            "Tab / Shift+Tab\tMove between controls",
            "Ctrl+Tab\tInsert a tab in the text box",
            "F1\tShow this help",
        ]
        messagebox.showinfo("Keyboard Shortcuts", "\n".join(lines))

    # ── ElevenLabs setup help ──────────────────────────────────────────────────

    ELEVENLABS_KEYS_URL = "https://elevenlabs.io/app/settings/api-keys"

    def _show_elevenlabs_help(self):
        """Explain, in a dialog, how to get and use an ElevenLabs API key."""
        body = (
            "ElevenLabs gives you high-quality cloud voices. It is OPTIONAL — the "
            "app already works offline with your computer's built-in voices, with "
            "no account or internet needed.\n\n"
            "To add ElevenLabs cloud voices:\n\n"
            "1.  Go to elevenlabs.io and create a free account (or sign in).\n\n"
            "2.  Verify your email address if prompted.\n\n"
            "3.  Open your account menu (top-right) and choose \"API Keys\", or "
            "go directly to:\n"
            "        elevenlabs.io/app/settings/api-keys\n\n"
            "4.  Click \"Create API Key\", give it any name, and copy the key. "
            "It begins with \"sk_\".\n\n"
            "5.  Back in this app, paste the key into the \"ElevenLabs API key\" "
            "box and press Enter (or click Connect).\n\n"
            "6.  Once the key is accepted, your account's voices appear in the "
            "Voice list marked with a cloud symbol (☁). Pick one and press "
            "Speak.\n\n"
            "Good to know:\n"
            "  •  The free plan includes a monthly character allowance.\n"
            "  •  Your key is saved on this computer only (in your home "
            "folder, readable by you alone) so you don't have to paste it again.\n"
            "  •  Speaking rate applies to local voices only, not cloud voices."
        )

        dlg = tk.Toplevel(self)
        dlg.title("Setting up ElevenLabs")
        dlg.transient(self)
        dlg.resizable(True, True)
        dlg.minsize(520, 460)

        frame = tk.Frame(dlg)
        frame.pack(fill="both", expand=True, padx=14, pady=12)

        tk.Label(
            frame, text="How to set up ElevenLabs cloud voices",
            font=("Helvetica", 14, "bold"), anchor="w",
        ).pack(fill="x", pady=(0, 8))

        text_wrap = tk.Frame(frame)
        text_wrap.pack(fill="both", expand=True)
        txt = tk.Text(
            text_wrap, wrap="word", font=("Helvetica", 12),
            relief="flat", padx=6, pady=6, highlightthickness=2,
            highlightcolor="#1a73e8",
        )
        sb = tk.Scrollbar(text_wrap, command=txt.yview)
        txt.configure(yscrollcommand=sb.set)
        txt.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        txt.insert("1.0", body)
        txt.config(state="disabled")  # read-only, but still scrollable/selectable

        btns = tk.Frame(frame)
        btns.pack(fill="x", pady=(10, 0))

        open_btn = self._make_button(
            btns, text="Open ElevenLabs Website", underline=0,
            command=lambda: webbrowser.open(self.ELEVENLABS_KEYS_URL),
            tip="Open the ElevenLabs API keys page in your web browser.",
        )
        open_btn.pack(side="left")

        close_btn = self._make_button(
            btns, text="Close", underline=0, command=dlg.destroy,
            tip="Close this help window (Esc).",
        )
        close_btn.pack(side="right")

        dlg.bind("<Escape>", lambda _e: dlg.destroy())
        # Make it modal and give the Close button initial keyboard focus.
        dlg.grab_set()
        close_btn.focus_set()

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

    def _estimate_save_seconds(self, text: str, entry: dict) -> float | None:
        """Rough estimate of the save time, based on spoken-audio length.

        Audio length ≈ words ÷ words-per-minute. For local voices we use the
        chosen rate; cloud voices have no rate control, so assume ~150 wpm.
        Returns None when there's nothing to estimate.
        """
        words = len(text.split())
        if words == 0:
            return None
        rate = self.rate_var.get() if entry["provider"] == "local" else 150
        return words / max(rate, 1) * 60.0

    @staticmethod
    def _format_duration(seconds: float) -> str:
        minutes, secs = divmod(int(round(seconds)), 60)
        parts = []
        if minutes:
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
        if secs or not minutes:
            parts.append(f"{secs} second{'s' if secs != 1 else ''}")
        return " ".join(parts)

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

        # Always confirm the save, showing the estimated audio length.
        est = self._estimate_save_seconds(text, entry)
        length = (f"roughly {self._format_duration(est)} long"
                  if est is not None else "of unknown length")
        proceed = messagebox.askyesno(
            "Save audio",
            f"This audio is {length}, so saving it may take a little while.\n\n"
            "Do you want to continue?",
            default="yes",
        )
        if not proceed:
            self.status_var.set("Save cancelled.")
            return

        self.save_btn.config(state="disabled")
        self._start_progress()
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

    # ── Document loading / saving ──────────────────────────────────────────────

    def _open_document(self):
        """Let the user pick a text/Markdown/Word file, then load + parse it."""
        path = filedialog.askopenfilename(
            title="Load document…",
            filetypes=[
                ("Supported documents", "*.txt *.text *.md *.markdown *.docx"),
                ("Text files", "*.txt *.text"),
                ("Markdown files", "*.md *.markdown"),
                ("Word documents", "*.docx"),
            ],
        )
        if not path:
            return
        self._load_document_path(path)

    def _on_announce_toggle(self):
        """Re-parse the currently loaded document with the new announce setting."""
        if self._parsed:
            self._load_document_path(self._parsed.source)

    def _load_document_path(self, path: str):
        """Parse *path* (honouring the announce toggle) into the text box."""
        announce = self.announce_var.get()
        try:
            doc = load_document(path, announce=announce)
        except DocumentError as exc:
            self.status_var.set("Could not load document.")
            messagebox.showerror("Cannot open file", str(exc))
            return
        except Exception as exc:  # unexpected parser failure
            self.status_var.set("Could not load document.")
            messagebox.showerror("Cannot open file", f"Unexpected error:\n{exc}")
            return

        self._parsed = doc
        self.text_box.delete("1.0", "end")
        self.text_box.insert("1.0", doc.spoken_text)
        name = Path(doc.source).name
        self.doc_var.set(f"{name}  ({doc.kind})")
        mode = "formatting announced" if announce else "plain text"
        self.status_var.set(f"Loaded {name}: {mode}. Press Speak to read it.")

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
        self._stop_progress()
        self.save_btn.config(state="normal")
        self.status_var.set(f"Saved: {path}")
        messagebox.showinfo("Saved", f"Audio saved to:\n{path}")

    def _on_save_error(self, err: str):
        self._stop_progress()
        self.save_btn.config(state="normal")
        self.status_var.set("Save failed.")
        messagebox.showerror("Save error", err)

    # ── Save progress bar (indeterminate; runs while audio is written) ──────────

    def _start_progress(self):
        self.progress.pack(fill="x", side="bottom", padx=12, pady=(0, 4))
        self.progress.start(12)  # animation step in ms

    def _stop_progress(self):
        self.progress.stop()
        self.progress.pack_forget()

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
