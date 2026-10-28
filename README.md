# Local Video Transcription

Turns video links into text transcripts **on your own computer**.

You put a `.txt` file containing one video link (usually YouTube) into `input`.
The app downloads only the audio and transcribes the whole recording locally with
[faster-whisper](https://github.com/SYSTRAN/faster-whisper). It saves the transcript
in `output` under **exactly the same filename**.

```
input/lecture1.txt            ->  output/lecture1.txt
input/Networking Basics.txt   ->  output/Networking Basics.txt
input/درس نمبر 1.txt           ->  output/درس نمبر 1.txt
```

The input file holds the link; the output file holds only the spoken words. The output contains no timestamps, titles, or URLs.

No transcription service, API key, or paid account is used. Your audio and transcripts never leave your computer.

---

## Everyday use (after setup)

1. Create a text file in the `input` folder, for example `input\Networking Basics.txt`.
2. Paste **one** link into it, e.g. `https://www.youtube.com/watch?v=VIDEO_ID`, and save.
3. Double-click **`run.bat`**.
4. Read your transcript in `output\Networking Basics.txt`.

You will see progress like:

```
[2/8] Networking Basics.txt
  Processing: new link file.
  Downloading audio...
  Audio downloaded (00:45:10 long).
  Transcribing: 00:14:32 / 00:45:10
  Saved: output/Networking Basics.txt, language: en
```

At the end the app shows how many files were completed, skipped, failed, or had no speech. It also shows the elapsed time and the output folder.

---

## One-time setup (Windows)

### What you need

| Requirement | Notes |
|---|---|
| **Python 3.11 – 3.14** (64-bit) | 3.12 recommended. Python 3.15 is **not** supported yet (some speech packages have no 3.15 build). Get it from <https://www.python.org/downloads/windows/>, or run `winget install Python.Python.3.12`. |
| Internet (during setup) | To install packages, download FFmpeg if missing, and download the speech model once. |
| ~1.5 GB free disk | Packages, plus about 480 MB for the default `small` model. |

Everything else is installed automatically by `setup.bat`:

- **Python packages** (into a private `.venv` folder): `yt-dlp` (with `yt-dlp-ejs`), `faster-whisper`, and the **Deno** JavaScript runtime. Current yt-dlp versions need a JavaScript runtime plus `yt-dlp-ejs` for full YouTube support; Deno is the runtime yt-dlp recommends.
- **FFmpeg + ffprobe**: setup reuses a working copy that is already on your PATH. If none is found, it downloads one into `tools\ffmpeg\bin` from the [yt-dlp FFmpeg-Builds](https://github.com/yt-dlp/FFmpeg-Builds) releases (the source the yt-dlp README recommends). It checks the published SHA-256 checksum before using it. (The pip package called `ffmpeg` is **not** FFmpeg and is not used.)
- **Speech model**: the configured Whisper model is downloaded into `models\` once.

No administrator rights are needed, and nothing is installed system-wide or added to your PATH.

### Steps

1. Install Python (see above) if you don't have it.
2. Double-click **`setup.bat`**.
3. Wait until it prints **`Setup complete`**. It only says this after it has run FFmpeg, ffprobe and Deno, and loaded and test-run the speech model.

If something fails, setup tells you what is wrong and how to fix it. You can run `setup.bat` again at any time: it reuses what already works and repairs what's missing.

---

## Options

Settings live in `config.json`. Command-line options override it for one run:

```bat
run.bat                      :: normal run (uses config.json)
run.bat --language ur        :: the videos are in Urdu
run.bat --language en        :: the videos are in English
run.bat --language auto      :: detect the language automatically (default)
run.bat --model medium       :: bigger model: slower, usually more accurate
run.bat --force              :: redo every file, even ones already done
run.bat --help               :: list all options and exit codes
```

(`python main.py ...` with the same options works too, if you run it with `.venv\Scripts\python.exe`.)

### Language: English, Urdu, or automatic

- `auto` detects the language from the start of each recording. This works well for most English videos.
- For Urdu, setting `--language ur` (or `"language": "ur"` in `config.json`) is more reliable. Automatic detection sometimes confuses Urdu with Hindi and may then write Hindi script.
- The app always transcribes in the spoken language. It never translates.

### Model size vs. speed and accuracy

| Model | Download | Speed on CPU | Accuracy |
|---|---|---|---|
| `tiny`, `base` | 75 / 145 MB | fastest | lowest |
| **`small`** (default) | 480 MB | moderate | good balance |
| `medium` | 1.5 GB | slow | better, especially for Urdu |
| `large-v3` | 3 GB | very slow on CPU | best |
| `turbo` | 1.6 GB | faster than large-v3 | close to large-v3 |

Models ending in `.en` are English-only. Don't use them for Urdu. A new model is downloaded automatically the first time you use it. The terminal says clearly when it is downloading the **speech model** rather than a video.

### Processing time

Transcription runs on your CPU, so it takes real time. A long lecture can take a large part of its own length, or longer, with bigger models or on slower computers. The app shows how far it has got in the audio (e.g. `00:14:32 / 00:45:10`) instead of guessing a finish time.

An NVIDIA GPU can be used by setting `"device": "cuda"` and `"compute_type": "float16"`. That requires installing CUDA 12 and cuDNN 9 yourself. CPU is the reliable default.

### Other `config.json` settings

`beam_size` (1–10), `vad_filter` (skip silence, true by default; it reduces invented text in silent parts), `paragraph_pause_seconds` (start a new paragraph after a pause), `download_retries`, and `socket_timeout_seconds`. `config.json` has a short `_help` section describing each one. If `config.json` is invalid, the app explains what is wrong before doing anything.

---

## Reruns, changed links and `--force`

The app remembers finished jobs in `state\jobs.json`. On each run:

- **Skipped** means the file was already transcribed with the same link and settings, and its transcript still exists and is valid.
- A file is **reprocessed** when:
  - its link changed
  - the model or language settings changed
  - its transcript was deleted or is empty
  - the last attempt failed or was interrupted
  - you use `--force`

A transcript file that merely exists is never trusted on its own. If you edit a finished transcript by hand, the app keeps your version; use `--force` to regenerate it.

If two input files contain the same link, each still gets its own transcript.

**Safety:**
- Transcripts are written to a hidden temporary file first. They appear under the final name only when transcription has fully finished, so you never get a half-written transcript.
- If a re-transcription fails, the earlier transcript is kept and the app says so.
- Input files are never changed.

**Ctrl+C** stops the run cleanly. Finished transcripts are kept. The interrupted file starts again from the beginning next time, and finished files are skipped.

---

## Input file rules

- `.txt` files directly in `input` (subfolders are ignored). `.TXT` works too.
- UTF-8 text. A BOM, spaces, and blank lines are fine.
- **Exactly one** `http://` or `https://` link and nothing else. Empty files, files with two links, or a link plus notes are reported as invalid and get no transcript. The rest of the batch continues.
- A video link that also contains `&list=...` is fine: only that one video is processed. A **playlist-only** or channel link is rejected.
- Live streams that are still live are rejected; recordings of finished streams are fine.
- Other websites work when yt-dlp supports them, but not every site is guaranteed.

---

## Keeping YouTube downloads working

YouTube changes often. When downloads start failing with `HTTP Error 403`, `n challenge`, or "format not available", update yt-dlp by double-clicking **`setup.bat`** again (needs internet). It upgrades yt-dlp and its YouTube components.

---

## Common problems

| Message | What to do |
|---|---|
| `No supported Python found` (setup) | Install Python 3.12 from python.org, then run setup.bat again. |
| `The app is not set up yet` | Run `setup.bat` first. |
| `Package installation failed` | Check your internet, proxy, or firewall, and run setup.bat again. |
| `ffmpeg is not available` | Run setup.bat (it installs a local copy). Or put `ffmpeg.exe` and `ffprobe.exe` in `tools\ffmpeg\bin`. |
| `No JavaScript runtime for YouTube` | Run setup.bat again (it reinstalls Deno into `.venv`). |
| `Could not load speech model` | The model is downloaded once and needs internet. Check your connection, or pick a smaller model. |
| `the video is private` / `unavailable` | The video can't be watched publicly. |
| `age-restricted` / `needs a signed-in account` / `not a bot` | The app deliberately does **not** read your browser cookies or passwords, so these videos are skipped. "Not a bot" checks are often temporary: try later or update yt-dlp. |
| `region-restricted` | The video isn't available from your country. |
| `no internet connection` / `timed out` | Check your network and run again; finished files are skipped. |
| `Saving failed ... open in another program` | Close the transcript in Word/Notepad and run again. |
| `No speech found` | The recording had no recognisable speech. No transcript is written. |

Details of every run are in `logs\` (transcript text is never written to logs).

### Exit codes (for scripts)

| Code | Meaning |
|---|---|
| 0 | Everything processed or skipped (also when `input` is empty) |
| 1 | One or more files failed, were invalid, or had no speech |
| 2 | Invalid configuration or command-line value |
| 3 | A required dependency is missing |
| 4 | The speech model could not be downloaded or loaded |
| 130 | Cancelled with Ctrl+C |

---

## Accuracy

Automatic transcription **makes mistakes**, especially with names, technical terms, accents, background noise, music, or mixed languages. Urdu accuracy is generally lower than English and improves with larger models (`medium`, `large-v3`). The app does not polish, summarise, or censor the text, and it never fills in missing speech. Check important transcripts by listening.

---

## Folder overview

```
input/      your link files (.txt)
output/     transcripts (same filenames)
models/     downloaded speech models (created by setup)
logs/       diagnostic logs
state/      job records used to skip finished work
tools/      project-local FFmpeg (only if setup had to download it)
main.py     the application  (python main.py --help)
config.json settings
setup.bat   one-time setup / repair / update
run.bat     double-click to process the input folder
tests/      offline checks: .venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Where internet is needed

- **Setup:** Python packages, FFmpeg (only when missing), and the speech model.
- **Each run:** downloading video audio. A new model is downloaded only if you switch to one not used before.
- **Never:** transcription itself is completely offline.
