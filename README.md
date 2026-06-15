# Simple Speech Output

A lightweight, cross-platform GUI app that converts typed text to speech using your system's built-in voices.

## Features

- Type (or paste) any text and have it read aloud immediately
- Choose from all voices installed on your system
- Adjust speaking rate (60–500 words per minute, default 175)
- Stop playback mid-sentence
- Save audio to a file (WAV on Windows, AIFF on macOS)
- Status bar shows what the engine is doing at all times

## Requirements

- Python 3.10+
- [pyttsx3](https://pypi.org/project/pyttsx3/) — `pip install pyttsx3`
- tkinter (bundled with standard Python on Windows and macOS)

No internet connection required. Speech synthesis uses OS-native engines (SAPI5 on Windows, AVSpeech on macOS, eSpeak on Linux).

## Running from source

```bash
pip install pyttsx3
python simple-speech-output.py
```

## Windows executable

A pre-built `Simple Speech Output.exe` is in the `dist/` folder — no Python installation needed. Double-click to run.

To rebuild the executable after editing the source:

```bash
pip install pyinstaller
pyinstaller "Simple Speech Output.spec"
```

The output lands in `dist/Simple Speech Output.exe`.

## Usage

1. Type or paste text into the text area.
2. Select a voice from the dropdown.
3. Adjust the rate if needed.
4. Click **Speak** to hear it, or **Stop** to interrupt.
5. To save audio: enter or browse for an output file path, then click **Save Audio**.

## File format notes

- **Windows**: saves as `.wav`
- **macOS**: saves as `.aiff`

The file format is determined by what pyttsx3's backend supports on each platform.
