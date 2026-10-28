"""Transcribe video links from input/*.txt into output/ with matching filenames.

Run `python main.py --help` for options. Everything is transcribed locally.
"""

import sys
from pathlib import Path

# Make the package importable no matter which folder the terminal is in.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from transcription_app.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
