"""Offline checks with mocked download/transcription (no internet, no model).

Run:  .venv\\Scripts\\python.exe -m unittest discover -s tests -v
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from transcription_app import cli  # noqa: E402
from transcription_app.config import ConfigError, load_config  # noqa: E402
from transcription_app.downloader import AudioDownloader, DownloadedAudio, DownloadFailed, explain_error  # noqa: E402
from transcription_app.inputs import parse_link_text, read_link_file, scan_input_folder  # noqa: E402
from transcription_app.paths import Paths  # noqa: E402
from transcription_app.pipeline import (  # noqa: E402
    EXIT_DEPENDENCY, EXIT_INTERRUPTED, EXIT_JOBS_FAILED, EXIT_OK, BatchRunner, FatalError, StatusLine,
)
from transcription_app.transcriber import TranscriptionFailed, TranscriptionInfo, WhisperTranscriber  # noqa: E402

URL_A = "https://www.youtube.com/watch?v=AAAAAAAAAAA"
URL_B = "https://www.youtube.com/watch?v=BBBBBBBBBBB"
URL_C = "https://youtu.be/CCCCCCCCCCC"


class FakeDownloader:
    """Writes the URL into a fake audio file; fails for URLs listed in `fail`."""

    def __init__(self, fail=()):
        self.fail = set(fail)
        self.calls: list[str] = []

    def download(self, url, dest, on_progress=None):
        self.calls.append(url)
        if url in self.fail:
            raise DownloadFailed("the video is private.")
        audio = Path(dest) / "audio.webm"
        audio.write_text(url, encoding="utf-8")
        return DownloadedAudio(audio, "Some YouTube Title", 125.0, "youtube")


class FakeTranscriber:
    """Behaviour per URL: list of segment texts, 'fail', 'empty', or 'interrupt'."""

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or {}
        self.calls: list[str] = []

    def transcribe(self, audio_path, on_segment, on_progress=None):
        url = Path(audio_path).read_text(encoding="utf-8")
        self.calls.append(url)
        plan = self.behaviour.get(url, [f"Transcript of {url[-11:]}.", "Second line."])
        if plan == "empty":
            return TranscriptionInfo("en", 0.9, 125.0, 0)
        if plan in ("fail", "interrupt"):
            on_segment(0.0, 2.0, "Partial words that must not be saved.")
            if plan == "fail":
                raise TranscriptionFailed("decoder crashed")
            raise KeyboardInterrupt
        t = 0.0
        for text in plan:
            on_segment(t, t + 2.0, text)
            t += 2.0
            if on_progress:
                on_progress(t, 125.0)
        return TranscriptionInfo("en", 0.9, 125.0, len(plan))


class ProjectTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = Paths(self.root)
        self.paths.ensure()
        self.downloader = FakeDownloader()
        self.transcriber = FakeTranscriber()
        self.prepare_calls = 0

    def tearDown(self):
        self._tmp.cleanup()

    def add_input(self, name, content, encoding="utf-8"):
        path = self.paths.input / name
        path.write_bytes(content.encode(encoding) if isinstance(content, str) else content)
        return path

    def run_batch(self, force=False, **config_overrides):
        config = load_config(self.paths.config_file, config_overrides)

        def prepare():
            self.prepare_calls += 1
            return self.downloader, self.transcriber

        lines: list[str] = []
        runner = BatchRunner(self.paths, config, prepare, force=force, out=lines.append,
                             status=StatusLine(stream=io.StringIO()))
        code = runner.run()
        self.output_text = "\n".join(lines)
        return code, runner.summary

    def jobs(self):
        return json.loads(self.paths.state_file.read_text(encoding="utf-8"))["jobs"]

    def output_names(self):
        return sorted(p.name for p in self.paths.output.iterdir())


class FilenameTests(ProjectTestCase):
    def test_exact_filenames_with_spaces_unicode_and_punctuation(self):
        names = ["lecture1.txt", "Networking Basics.txt", "درس نمبر 1.txt", "Report (final), v2!.TXT"]
        urls = [URL_A, URL_B, URL_C, "https://vimeo.com/123456"]
        for name, url in zip(names, urls):
            self.add_input(name, url + "\n")
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.output_names(), sorted(names))
        for name, url in zip(names, urls):
            text = (self.paths.output / name).read_text(encoding="utf-8")
            self.assertIn(f"Transcript of {url[-11:]}.", text)
            self.assertNotIn("http", text)          # no URL in transcript
            self.assertNotIn("YouTube Title", text)  # no video title
        # Input files untouched.
        self.assertEqual((self.paths.input / names[0]).read_text(encoding="utf-8"), URL_A + "\n")

    def test_same_url_in_two_files_gives_two_transcripts(self):
        self.add_input("one.txt", URL_A)
        self.add_input("two.txt", URL_A)
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(self.output_names(), ["one.txt", "two.txt"])

    def test_stable_order_and_non_recursive_scan(self):
        (self.paths.input / "sub").mkdir()
        (self.paths.input / "sub" / "nested.txt").write_text(URL_A)
        self.add_input("b.txt", URL_A)
        self.add_input("A.txt", URL_A)
        self.add_input("notes.md", URL_A)
        self.assertEqual([p.name for p in scan_input_folder(self.paths.input)], ["A.txt", "b.txt"])


class InputValidationTests(ProjectTestCase):
    def test_parse_rules(self):
        self.assertEqual(parse_link_text("  \n\n " + URL_A + "  \n\n"), (URL_A, None))
        self.assertIn("empty", parse_link_text("  \n \t ")[1])
        self.assertIn("2 links", parse_link_text(URL_A + "\n" + URL_B)[1])
        self.assertIn("extra text", parse_link_text("My video: " + URL_A)[1])
        self.assertIn("http", parse_link_text("www.youtube.com/watch?v=x")[1])
        self.assertIn("http", parse_link_text("ftp://example.com/a")[1])

    def test_bom_is_accepted(self):
        path = self.add_input("bom.txt", "\ufeff" + URL_A + "\r\n")
        self.assertEqual(read_link_file(path).url, URL_A)

    def test_invalid_files_get_no_output_and_batch_continues(self):
        self.add_input("empty.txt", "")
        self.add_input("blank.txt", "   \n\n")
        self.add_input("multi.txt", URL_A + "\n" + URL_B + "\n")
        self.add_input("extra.txt", "watch this " + URL_A)
        self.add_input("latin1.txt", "caf\xe9 http://x.com".encode("latin-1"))
        self.add_input("good.txt", URL_C)
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_JOBS_FAILED)
        self.assertEqual(self.output_names(), ["good.txt"])
        self.assertEqual(len(summary.invalid), 5)
        self.assertEqual(summary.completed, ["good.txt"])
        self.assertIn("Invalid link file", self.output_text)

    def test_empty_input_folder_exits_cleanly(self):
        code, _ = self.run_batch()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Put .txt files into", self.output_text)
        self.assertEqual(self.prepare_calls, 0)


class BatchBehaviourTests(ProjectTestCase):
    def test_failed_job_does_not_stop_later_jobs(self):
        self.downloader.fail = {URL_A}
        self.add_input("1 first.txt", URL_A)
        self.add_input("2 second.txt", URL_B)
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_JOBS_FAILED)
        self.assertEqual(summary.failed, ["1 first.txt"])
        self.assertEqual(summary.completed, ["2 second.txt"])
        self.assertEqual(self.output_names(), ["2 second.txt"])
        self.assertEqual(self.jobs()["1 first.txt"]["status"], "failed")

    def test_completed_jobs_are_skipped_on_rerun(self):
        self.add_input("a.txt", URL_A)
        self.run_batch()
        self.downloader.calls.clear()
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(summary.skipped, ["a.txt"])
        self.assertEqual(self.downloader.calls, [])
        self.assertEqual(self.prepare_calls, 1)  # model not loaded when nothing to do

    def test_existing_output_without_record_is_not_trusted(self):
        self.add_input("a.txt", URL_A)
        (self.paths.output / "a.txt").write_text("something old", encoding="utf-8")
        code, summary = self.run_batch()
        self.assertEqual(summary.completed, ["a.txt"])

    def test_changed_url_is_reprocessed(self):
        self.add_input("a.txt", URL_A)
        self.run_batch()
        self.add_input("a.txt", URL_B)
        code, summary = self.run_batch()
        self.assertEqual(summary.completed, ["a.txt"])
        self.assertIn("BBBBBBBBBBB", (self.paths.output / "a.txt").read_text(encoding="utf-8"))

    def test_missing_or_empty_output_is_reprocessed(self):
        self.add_input("a.txt", URL_A)
        self.run_batch()
        (self.paths.output / "a.txt").unlink()
        _, summary = self.run_batch()
        self.assertEqual(summary.completed, ["a.txt"])
        (self.paths.output / "a.txt").write_text("  ", encoding="utf-8")
        _, summary = self.run_batch()
        self.assertEqual(summary.completed, ["a.txt"])

    def test_settings_change_and_force_reprocess(self):
        self.add_input("a.txt", URL_A)
        self.run_batch()
        _, summary = self.run_batch(language="ur")
        self.assertEqual(summary.completed, ["a.txt"])
        _, summary = self.run_batch(language="ur")
        self.assertEqual(summary.skipped, ["a.txt"])
        _, summary = self.run_batch(force=True, language="ur")
        self.assertEqual(summary.completed, ["a.txt"])

    def test_all_segments_saved_with_repetitions(self):
        segments = [f"Line {i}." for i in range(200)] + ["Yes yes yes.", "Yes yes yes."]
        self.transcriber.behaviour = {URL_A: segments}
        self.add_input("long.txt", URL_A)
        self.run_batch()
        lines = (self.paths.output / "long.txt").read_text(encoding="utf-8").split("\n")
        lines = [line for line in lines if line]
        self.assertEqual(lines, segments)

    def test_empty_transcription_is_distinct(self):
        self.transcriber.behaviour = {URL_A: "empty"}
        self.add_input("silent.txt", URL_A)
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_JOBS_FAILED)
        self.assertEqual(summary.empty, ["silent.txt"])
        self.assertEqual(self.output_names(), [])
        self.assertEqual(self.jobs()["silent.txt"]["status"], "empty")

    def test_shared_failure_stops_before_downloads(self):
        self.add_input("a.txt", URL_A)
        config = load_config(self.paths.config_file)

        def prepare():
            raise FatalError("ffmpeg missing", EXIT_DEPENDENCY)

        runner = BatchRunner(self.paths, config, prepare, out=lambda s: None,
                             status=StatusLine(stream=io.StringIO()))
        with self.assertRaises(FatalError):
            runner.run()
        self.assertEqual(self.output_names(), [])


class AtomicOutputTests(ProjectTestCase):
    def assert_no_partials(self):
        leftovers = [p.name for p in self.paths.output.iterdir() if p.name.startswith(".partial-")]
        self.assertEqual(leftovers, [])

    def test_transcription_failure_leaves_no_partial_transcript(self):
        self.transcriber.behaviour = {URL_A: "fail"}
        self.add_input("a.txt", URL_A)
        code, summary = self.run_batch()
        self.assertEqual(summary.failed, ["a.txt"])
        self.assertEqual(self.output_names(), [])
        self.assert_no_partials()

    def test_interrupt_leaves_no_partial_and_keeps_finished_work(self):
        self.add_input("1.txt", URL_A)
        self.add_input("2.txt", URL_B)
        self.add_input("3.txt", URL_C)
        self.transcriber.behaviour = {URL_B: "interrupt"}
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_INTERRUPTED)
        self.assertEqual(self.output_names(), ["1.txt"])
        self.assert_no_partials()
        self.assertEqual(self.jobs()["2.txt"]["status"], "interrupted")
        # Next run: 1 skipped, 2 restarted from scratch, 3 processed.
        self.transcriber.behaviour = {}
        code, summary = self.run_batch()
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(summary.skipped, ["1.txt"])
        self.assertEqual(summary.completed, ["2.txt", "3.txt"])

    def test_failed_reprocess_preserves_previous_transcript(self):
        self.add_input("a.txt", URL_A)
        self.run_batch()
        before = (self.paths.output / "a.txt").read_bytes()
        self.transcriber.behaviour = {URL_A: "fail"}
        code, summary = self.run_batch(force=True)
        self.assertEqual(summary.failed, ["a.txt"])
        self.assertEqual((self.paths.output / "a.txt").read_bytes(), before)
        self.assertIn("earlier transcript", self.output_text)
        self.assert_no_partials()

    def test_failed_replacement_preserves_previous_transcript(self):
        self.add_input("a.txt", URL_A)
        self.run_batch()
        before = (self.paths.output / "a.txt").read_bytes()
        self.transcriber.behaviour = {URL_A: ["Brand new text."]}
        real_replace = os.replace

        def failing_replace(src, dst):
            if Path(dst).name == "a.txt":
                raise PermissionError(13, "Access is denied")
            return real_replace(src, dst)

        with mock.patch("transcription_app.output.os.replace", side_effect=failing_replace):
            code, summary = self.run_batch(force=True)
        self.assertEqual(summary.failed, ["a.txt"])
        self.assertEqual((self.paths.output / "a.txt").read_bytes(), before)
        self.assertIn("Saving failed", self.output_text)
        self.assert_no_partials()
        self.assertEqual(self.jobs()["a.txt"]["status"], "failed")

    def test_stale_partials_are_cleaned(self):
        (self.paths.output / ".partial-abc.tmp").write_text("junk")
        self.add_input("a.txt", URL_A)
        self.run_batch()
        self.assert_no_partials()


class WhisperWrapperTests(unittest.TestCase):
    def test_lazy_segment_generator_is_fully_consumed(self):
        consumed = []

        def segments():
            for i in range(500):
                consumed.append(i)
                yield SimpleNamespace(start=i * 2.0, end=i * 2.0 + 2, text=f" seg {i}")

        model = mock.Mock()
        model.transcribe.return_value = (segments(), SimpleNamespace(language="ur", language_probability=0.8, duration=1000.0))
        config = load_config(Path(tempfile.mkdtemp()) / "config.json", {"language": "ur"})
        received = []
        info = WhisperTranscriber(model, config).transcribe(Path("x.webm"), lambda s, e, t: received.append(t))
        self.assertEqual(len(consumed), 500)
        self.assertEqual(len(received), 500)
        self.assertEqual(info.segments, 500)
        kwargs = model.transcribe.call_args.kwargs
        self.assertEqual(kwargs["task"], "transcribe")
        self.assertEqual(kwargs["language"], "ur")
        self.assertTrue(kwargs["vad_filter"])


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.path = Path(tempfile.mkdtemp()) / "config.json"

    def test_defaults(self):
        config = load_config(self.path)
        self.assertEqual((config.model, config.language, config.device, config.compute_type),
                         ("small", "auto", "cpu", "int8"))
        self.assertIsNone(config.whisper_language)

    def test_cli_overrides_and_validation(self):
        self.assertEqual(load_config(self.path, {"model": "base", "language": "UR"}).language, "ur")
        with self.assertRaises(ConfigError):
            load_config(self.path, {"model": "gigantic"})
        with self.assertRaises(ConfigError):
            load_config(self.path, {"language": "klingon"})
        with self.assertRaises(ConfigError):
            load_config(self.path, {"model": "small.en", "language": "ur"})

    def test_bad_json_and_bad_values(self):
        self.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.path)
        self.path.write_text(json.dumps({"beam_size": 0, "vad_filter": "yes", "colour": 1}), encoding="utf-8")
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_cli_invalid_config_exit_code(self):
        root = self.path.parent
        with mock.patch("sys.stdout", new=io.StringIO()):
            self.assertEqual(cli.main(["--model", "nope"], paths=Paths(root)), 2)


class DownloaderRuleTests(unittest.TestCase):
    def test_rejects_playlists_and_live(self):
        reject = AudioDownloader._reject_unsuitable
        with self.assertRaisesRegex(DownloadFailed, "playlist"):
            reject({"_type": "playlist", "entries": []})
        with self.assertRaisesRegex(DownloadFailed, "still live"):
            reject({"_type": "video", "live_status": "is_live"})
        with self.assertRaisesRegex(DownloadFailed, "not started"):
            reject({"live_status": "is_upcoming"})
        reject({"live_status": "was_live"})  # finished stream recordings are allowed
        reject({"live_status": "not_live"})

    def test_error_explanations(self):
        self.assertIn("private", explain_error("ERROR: [youtube] x: Private video. Sign in if you've been granted access"))
        self.assertIn("age-restricted", explain_error("Sign in to confirm your age. This video may be inappropriate"))
        self.assertIn("not a bot", explain_error("Sign in to confirm you're not a bot"))
        self.assertIn("region", explain_error("The uploader has not made this video available in your country"))
        self.assertIn("internet", explain_error("Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>"))

    def test_options_are_audio_only_without_playlists(self):
        d = AudioDownloader("C:/ff/ffmpeg.exe", "C:/ff/ffprobe.exe", ("deno", "C:/d/deno.exe"))
        opts = d._options(Path("C:/tmp/job"), None, lambda s: None)
        self.assertEqual(opts["format"], "bestaudio/best")
        self.assertTrue(opts["noplaylist"])
        self.assertEqual(opts["js_runtimes"], {"deno": {"path": "C:/d/deno.exe"}})
        self.assertNotIn("cookiesfrombrowser", opts)
        self.assertNotIn("download_ranges", opts)


if __name__ == "__main__":
    unittest.main()
