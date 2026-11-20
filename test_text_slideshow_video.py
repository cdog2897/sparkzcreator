import json
import math
import re
import shutil
import struct
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

from generate_video import OUTPUT_DIR
from text_slideshow import export_text_slideshow
from text_slideshow_video import (
    AUDIO_FADE_OUT_SECONDS,
    DEFAULT_SLIDE_SECONDS,
    REEL_FPS,
    REEL_HEIGHT,
    REEL_WIDTH,
    VIDEO_FILENAME,
    audio_fade_out_seconds,
    audio_from_url,
    build_reel_command,
    collect_decks,
    export_decks,
    frames_for_seconds,
    main,
    plan_reel,
    render_text_slideshow_video,
    resolve_ffmpeg,
)
from web_app import app


def _slide(color: str) -> dict:
    return {
        "background_color": color,
        "background_color_2": color,
        "elements": [],
    }


def _write_tone(path: Path, seconds: float, rate: int = 44100, frequency: float = 440.0) -> None:
    count = max(1, int(seconds * rate))
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(count):
            sample = int(0.45 * 32767 * math.sin(2 * math.pi * frequency * index / rate))
            frames.extend(struct.pack("<h", sample))
        handle.writeframes(frames)


def _ffmpeg(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [resolve_ffmpeg(), "-hide_banner", *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _probe_text(path: Path) -> str:
    return _ffmpeg("-i", str(path)).stderr


def _duration(path: Path) -> float:
    match = re.search(
        r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)",
        _probe_text(path),
    )
    if match is None:
        raise AssertionError(f"No duration in {_probe_text(path)}")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def _frame_count(path: Path) -> int:
    completed = _ffmpeg("-i", str(path), "-map", "0:v:0", "-f", "null", "-")
    frames = [int(value) for value in re.findall(r"frame=\s*(\d+)", completed.stderr)]
    if not frames:
        raise AssertionError(completed.stderr[-500:])
    return frames[-1]


def _center_rgb(path: Path, seconds: float) -> tuple[int, int, int]:
    completed = subprocess.run(
        [
            resolve_ffmpeg(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(path),
            "-ss",
            f"{seconds:.3f}",
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr.decode()[-500:])
    data = completed.stdout
    expected = REEL_WIDTH * REEL_HEIGHT * 3
    if len(data) != expected:
        raise AssertionError(f"Expected {expected} bytes, got {len(data)}")
    index = ((REEL_HEIGHT // 2) * REEL_WIDTH + (REEL_WIDTH // 2)) * 3
    return tuple(data[index:index + 3])


def _mean_volume(path: Path, start: float, length: float) -> float:
    completed = _ffmpeg(
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{length:.3f}",
        "-i",
        str(path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    )
    match = re.search(r"mean_volume:\s*(-inf|-?\d+(?:\.\d+)?) dB", completed.stderr)
    if match is None:
        raise AssertionError(completed.stderr[-500:])
    if match.group(1) == "-inf":
        return -1000.0
    return float(match.group(1))


def _faststart(path: Path) -> bool:
    blob = path.read_bytes()
    moov = blob.find(b"moov")
    mdat = blob.find(b"mdat")
    return moov >= 0 and mdat >= 0 and moov < mdat


def _near(color: tuple[int, int, int], expected: tuple[int, int, int], tolerance: int = 8) -> bool:
    return all(abs(channel - target) <= tolerance for channel, target in zip(color, expected))


class ReelPlanTests(unittest.TestCase):
    def test_default_duration_is_between_two_and_a_half_and_three_seconds(self) -> None:
        self.assertGreaterEqual(DEFAULT_SLIDE_SECONDS, 2.5)
        self.assertLessEqual(DEFAULT_SLIDE_SECONDS, 3.0)
        plan = plan_reel(4)
        self.assertEqual(plan.slide_durations[0], plan.slide_durations[-1])
        self.assertEqual(plan.fps, 30)
        self.assertEqual((plan.width, plan.height), (1080, 1920))
        self.assertAlmostEqual(plan.duration, 4 * plan.slide_durations[0])

    def test_first_slide_can_hold_longer_and_duration_snaps_to_frames(self) -> None:
        plan = plan_reel(3, 2.75, 4)
        self.assertEqual(plan.slide_frames[0], 120)
        self.assertEqual(plan.slide_frames[1], frames_for_seconds(2.75))
        self.assertEqual(plan.slide_durations[1], plan.slide_durations[2])
        self.assertGreater(plan.slide_durations[0], plan.slide_durations[1])
        self.assertAlmostEqual(plan.duration, sum(plan.slide_durations))
        self.assertEqual(plan.crossfade_seconds, 0.0)

    def test_crossfade_shortens_the_timeline_by_the_overlaps(self) -> None:
        plan = plan_reel(3, 2.0, None, 0.4)
        self.assertAlmostEqual(plan.crossfade_seconds, 0.4)
        self.assertAlmostEqual(plan.duration, 2.0 * 3 - 0.4 * 2)

    def test_crossfade_must_be_shorter_than_every_slide(self) -> None:
        with self.assertRaises(ValueError):
            plan_reel(2, 1.0, None, 1.0)
        with self.assertRaises(ValueError):
            plan_reel(2, 2.0, None, 3.0)

    def test_empty_deck_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            plan_reel(0)

    def test_audio_fade_is_short_and_fits_a_tiny_video(self) -> None:
        self.assertLessEqual(AUDIO_FADE_OUT_SECONDS, 1.0)
        self.assertAlmostEqual(audio_fade_out_seconds(11.0), AUDIO_FADE_OUT_SECONDS)
        self.assertAlmostEqual(audio_fade_out_seconds(0.4), 0.2)
        self.assertEqual(audio_fade_out_seconds(0.0), 0.0)


class ReelCommandTests(unittest.TestCase):
    def test_silent_command_is_a_reels_mp4(self) -> None:
        plan = plan_reel(2, 2.75, 4.0, 0)
        command = build_reel_command(
            [Path("01.png"), Path("02.png")],
            Path("video.mp4"),
            plan,
            ffmpeg="ffmpeg",
        )
        rendered = " ".join(command)
        self.assertIn("libx264", command)
        self.assertIn("yuv420p", command)
        self.assertIn("aac", command)
        self.assertIn("+faststart", command)
        self.assertIn("avc1", command)
        self.assertIn("1080:1920", rendered)
        self.assertIn("fps=30", rendered)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=44100", command)
        self.assertNotIn("-stream_loop", command)
        self.assertNotIn("xfade", rendered)
        self.assertIn("concat=n=2", rendered)
        self.assertNotIn("afade", rendered)

    def test_audio_is_looped_trimmed_and_faded(self) -> None:
        plan = plan_reel(2, 2.75)
        command = build_reel_command(
            [Path("01.png"), Path("02.png")],
            Path("video.mp4"),
            plan,
            Path("song.mp3"),
            ffmpeg="ffmpeg",
        )
        rendered = " ".join(command)
        self.assertEqual(command[command.index("-stream_loop") + 1], "-1")
        self.assertIn("song.mp3", command)
        self.assertIn(f"atrim=end={plan.duration:.6f}", rendered)
        fade = audio_fade_out_seconds(plan.duration)
        self.assertIn(f"afade=t=out:st={plan.duration - fade:.6f}:d={fade:.6f}", rendered)
        self.assertNotIn("anullsrc", rendered)

    def test_crossfade_uses_xfade_instead_of_a_hard_concat(self) -> None:
        plan = plan_reel(2, 2.0, None, 0.35)
        command = build_reel_command(
            [Path("01.png"), Path("02.png")],
            Path("video.mp4"),
            plan,
            ffmpeg="ffmpeg",
        )
        rendered = " ".join(command)
        self.assertIn("xfade=transition=fade:duration=0.366667", rendered)
        self.assertNotIn("concat=", rendered)

    def test_resolve_ffmpeg_ignores_a_bad_override(self) -> None:
        with patch.dict("os.environ", {"FFMPEG_PATH": "/bin/echo"}):
            binary = resolve_ffmpeg()
        self.assertNotEqual(binary, "/bin/echo")
        self.assertTrue(Path(binary).is_file())


class ReelRenderTests(unittest.TestCase):
    def test_hard_cuts_hold_the_first_slide_and_keep_a_silent_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            result = render_text_slideshow_video(
                [_slide("#000000"), _slide("#ffffff")],
                output_dir=Path(temp_name),
                seconds_per_slide=0.5,
                first_slide_seconds=1.0,
                preset="ultrafast",
            )
            video = result.path
            self.assertEqual(video.name, "video.mp4")
            self.assertTrue((Path(temp_name) / "01.png").is_file())
            self.assertTrue((Path(temp_name) / "02.png").is_file())
            details = _probe_text(video)
            self.assertIn("Video: h264", details)
            self.assertIn("yuv420p", details)
            self.assertIn("1080x1920", details)
            self.assertIn("30 fps", details)
            self.assertIn("Audio: aac", details)
            self.assertIn("44100 Hz", details)
            self.assertIn("stereo", details)
            self.assertTrue(_faststart(video))
            self.assertAlmostEqual(_duration(video), 1.5, delta=0.04)
            self.assertEqual(_frame_count(video), 45)
            self.assertTrue(_near(_center_rgb(video, 0.2), (0, 0, 0)))
            self.assertTrue(_near(_center_rgb(video, 0.8), (0, 0, 0)))
            self.assertTrue(_near(_center_rgb(video, 1.2), (255, 255, 255)))
            self.assertLess(_mean_volume(video, 0.2, 0.3), -60)

    def test_short_music_loops_and_fades_and_long_music_is_trimmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)
            short = folder / "short.wav"
            long = folder / "long.wav"
            _write_tone(short, 0.2)
            _write_tone(long, 4.0)
            slides = [_slide("#112233")]

            looped = render_text_slideshow_video(
                slides,
                output_dir=folder / "looped",
                seconds_per_slide=1.0,
                audio_path=short,
                preset="ultrafast",
            )
            trimmed = render_text_slideshow_video(
                slides,
                output_dir=folder / "trimmed",
                seconds_per_slide=1.0,
                audio_path=long,
                preset="ultrafast",
            )

            self.assertAlmostEqual(_duration(looped.path), 1.0, delta=0.04)
            self.assertAlmostEqual(_duration(trimmed.path), 1.0, delta=0.04)
            self.assertIn("Audio: aac", _probe_text(looped.path))
            # The source tone is only 0.2s, so energy after that means it looped.
            self.assertGreater(_mean_volume(looped.path, 0.25, 0.2), -35)
            # Fade out starts at 0.5s of this 1s video.
            self.assertLess(
                _mean_volume(looped.path, 0.8, 0.15),
                _mean_volume(looped.path, 0.25, 0.2) - 3,
            )

    def test_crossfade_blends_the_middle_of_the_cut(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            result = render_text_slideshow_video(
                [_slide("#000000"), _slide("#ffffff")],
                output_dir=Path(temp_name),
                seconds_per_slide=1.0,
                crossfade_seconds=0.4,
                preset="ultrafast",
            )
            video = result.path
            self.assertAlmostEqual(result.plan.duration, 1.6, delta=0.001)
            self.assertAlmostEqual(_duration(video), 1.6, delta=0.04)
            self.assertTrue(_near(_center_rgb(video, 0.15), (0, 0, 0)))
            blended = _center_rgb(video, 0.8)
            self.assertGreater(min(blended), 40)
            self.assertLess(max(blended), 220)
            self.assertTrue(_near(_center_rgb(video, 1.4), (255, 255, 255)))

    def test_png_export_does_not_write_a_video(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name) / "pngs"
            export_text_slideshow([_slide("#010101")], output_dir=folder)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["01.png"])
            self.assertFalse((folder / VIDEO_FILENAME).exists())


class ReelBatchTests(unittest.TestCase):
    def test_cli_exports_a_folder_of_decks_with_and_without_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            quiet = root / "quiet"
            scored = root / "scored"
            for folder, color in ((quiet, "#101010"), (scored, "#f0f0f0")):
                folder.mkdir()
                (folder / "deck.json").write_text(json.dumps({
                    "slides": [_slide(color)],
                    "seconds_per_slide": 0.5,
                }), encoding="utf-8")
            tone = root / "tone.wav"
            _write_tone(tone, 0.15)

            self.assertEqual(main([str(quiet)]), 0)
            self.assertEqual(
                main([str(scored), "--audio", str(tone), "--seconds", "0.5"]),
                0,
            )

            quiet_video = quiet / "output" / "video.mp4"
            scored_video = scored / "output" / "video.mp4"
            self.assertTrue(quiet_video.is_file())
            self.assertTrue((quiet / "output" / "01.png").is_file())
            self.assertTrue(scored_video.is_file())
            self.assertAlmostEqual(_duration(quiet_video), 0.5, delta=0.04)
            self.assertAlmostEqual(_duration(scored_video), 0.5, delta=0.04)
            self.assertLess(_mean_volume(quiet_video, 0.05, 0.2), -60)
            self.assertGreater(_mean_volume(scored_video, 0.05, 0.2), -40)
            self.assertIn("1080x1920", _probe_text(quiet_video))

    def test_cli_batch_writes_each_deck_and_png_folders_keep_their_slides(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            batch = root / "decks"
            batch.mkdir()
            for name in ("one", "two"):
                folder = batch / name
                folder.mkdir()
                (folder / "deck.json").write_text(json.dumps({
                    "slides": [_slide("#222222"), _slide("#dddddd")],
                }), encoding="utf-8")
            png_deck = root / "already-exported"
            png_deck.mkdir()
            Image.new("RGB", (32, 48), (0, 0, 0)).save(png_deck / "01.png")
            Image.new("RGB", (32, 48), (255, 255, 255)).save(png_deck / "02.png")

            self.assertEqual(collect_decks(batch), [batch / "one", batch / "two"])
            results = export_decks(batch, seconds_per_slide=0.5, preset="ultrafast")
            self.assertEqual(
                [result.path for result in results],
                [batch / "one" / "output" / "video.mp4", batch / "two" / "output" / "video.mp4"],
            )
            self.assertTrue(all(result.path.is_file() for result in results))

            code = main([str(png_deck), "--seconds", "0.5", "--first-seconds", "1"])
            self.assertEqual(code, 0)
            self.assertTrue((png_deck / "video.mp4").is_file())
            self.assertTrue((png_deck / "01.png").is_file())
            self.assertTrue((png_deck / "02.png").is_file())
            self.assertAlmostEqual(_duration(png_deck / "video.mp4"), 1.5, delta=0.04)
            self.assertTrue(_near(_center_rgb(png_deck / "video.mp4", 0.2), (0, 0, 0)))
            self.assertTrue(_near(_center_rgb(png_deck / "video.mp4", 1.2), (255, 255, 255)))

    def test_cli_reports_a_missing_folder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            missing = Path(temp_name) / "nope"
            self.assertEqual(main([str(missing)]), 1)

    def test_cli_audio_url_uses_the_saved_mp3(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            deck = root / "deck"
            deck.mkdir()
            (deck / "deck.json").write_text(json.dumps({
                "slides": [_slide("#202020")],
                "seconds_per_slide": 0.5,
            }), encoding="utf-8")
            tone = root / "reel-saved.wav"
            _write_tone(tone, 0.2)
            url = "https://www.instagram.com/reel/example/"

            def fake_import(imported_url: str, browser: str | None = None) -> Path:
                self.assertEqual(imported_url, url)
                self.assertIsNone(browser)
                return tone

            with patch("web_app.import_instagram_music", side_effect=fake_import):
                code = main([str(deck), "--audio-url", url, "--seconds", "0.5"])

            video = deck / "output" / "video.mp4"
            self.assertEqual(code, 0)
            self.assertTrue(video.is_file())
            self.assertGreater(_mean_volume(video, 0.05, 0.2), -40)

    def test_cli_audio_url_can_use_the_chrome_login(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            deck = root / "deck"
            deck.mkdir()
            (deck / "deck.json").write_text(json.dumps({
                "slides": [_slide("#111111")],
            }), encoding="utf-8")
            tone = root / "chrome.mp3"
            tone.write_bytes(b"not-read")
            url = "https://www.instagram.com/reel/chrome/"

            with patch("web_app.import_instagram_music", return_value=tone) as mocked:
                with patch("text_slideshow_video.export_decks", return_value=[]) as exported:
                    code = main([
                        str(deck),
                        "--audio-url",
                        url,
                        "--chrome-login",
                    ])

            self.assertEqual(code, 0)
            mocked.assert_called_once_with(url, browser="chrome")
            self.assertEqual(exported.call_args.kwargs["audio_path"], tone)

    def test_cli_audio_url_rejects_a_non_instagram_url(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            deck = Path(temp_name) / "deck"
            deck.mkdir()
            (deck / "deck.json").write_text(json.dumps({
                "slides": [_slide("#111111")],
            }), encoding="utf-8")
            code = main([
                str(deck),
                "--audio-url",
                "https://www.tiktok.com/@example/video/1",
            ])
            self.assertEqual(code, 1)
            self.assertFalse((deck / "output" / "video.mp4").exists())

    def test_cli_rejects_an_audio_file_and_an_audio_url_together(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            deck = Path(temp_name) / "deck"
            deck.mkdir()
            (deck / "deck.json").write_text("{}", encoding="utf-8")
            tone = Path(temp_name) / "tone.wav"
            _write_tone(tone, 0.1)
            code = main([
                str(deck),
                "--audio",
                str(tone),
                "--audio-url",
                "https://www.instagram.com/reel/example/",
            ])
            self.assertEqual(code, 1)
            self.assertFalse((deck / "output" / "video.mp4").exists())

    def test_audio_from_url_reuses_the_instagram_importer(self) -> None:
        tone = Path("music/reel-test.mp3")
        with patch("web_app.import_instagram_music", return_value=tone) as mocked:
            saved = audio_from_url("  https://www.instagram.com/reel/example/  ", browser="chrome")
        self.assertEqual(saved, tone)
        mocked.assert_called_once_with("https://www.instagram.com/reel/example/", browser="chrome")


class ReelApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.folders: list[Path] = []

    def tearDown(self) -> None:
        for folder in self.folders:
            shutil.rmtree(folder, ignore_errors=True)
            (folder.parent / f"{folder.name}.zip").unlink(missing_ok=True)

    def _keep(self, response) -> Path:
        folder = OUTPUT_DIR / response.headers["x-output-folder"]
        self.folders.append(folder)
        return folder

    def test_export_video_without_audio_from_the_api(self) -> None:
        response = self.client.post(
            "/api/text-slideshow/export-video",
            data={
                "slides": json.dumps([_slide("#000000"), _slide("#ffffff")]),
                "seconds_per_slide": "0.5",
                "crossfade_seconds": "0",
            },
        )
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertTrue(response.headers["content-type"].startswith("video/mp4"))
        self.assertIn("video.mp4", response.headers["content-disposition"])
        self.assertAlmostEqual(float(response.headers["x-video-duration"]), 1.0, delta=0.02)
        folder = self._keep(response)
        video = folder / "video.mp4"
        self.assertTrue(video.is_file())
        self.assertTrue((folder / "01.png").is_file())
        self.assertTrue((folder / "02.png").is_file())
        self.assertGreater(len(response.content), 1000)
        self.assertTrue(response.content.startswith(b"\x00\x00\x00") or b"ftyp" in response.content[:32])
        details = _probe_text(video)
        self.assertIn("1080x1920", details)
        self.assertIn("Audio: aac", details)
        self.assertTrue(_faststart(video))
        self.assertLess(_mean_volume(video, 0.1, 0.2), -60)

    def test_export_video_with_uploaded_audio(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            tone = Path(temp_name) / "tone.wav"
            _write_tone(tone, 0.2)
            response = self.client.post(
                "/api/text-slideshow/export-video",
                data={
                    "slides": json.dumps([_slide("#445566")]),
                    "seconds_per_slide": "1",
                },
                files={"audio": ("tone.wav", tone.read_bytes(), "audio/wav")},
            )
        self.assertEqual(response.status_code, 200, response.content[:300])
        folder = self._keep(response)
        video = folder / "video.mp4"
        self.assertAlmostEqual(_duration(video), 1.0, delta=0.04)
        self.assertGreater(_mean_volume(video, 0.25, 0.2), -35)

    def test_export_video_uses_a_saved_music_track(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            music_dir = Path(temp_name)
            tone = music_dir / "pulled.wav"
            _write_tone(tone, 0.4)
            with patch("web_app.MUSIC_DIR", music_dir):
                response = self.client.post(
                    "/api/text-slideshow/export-video",
                    data={
                        "slides": json.dumps([_slide("#445566")]),
                        "seconds_per_slide": "1",
                        "music_name": "pulled.wav",
                    },
                )
        self.assertEqual(response.status_code, 200, response.content[:300])
        folder = self._keep(response)
        video = folder / "video.mp4"
        self.assertGreater(_mean_volume(video, 0.2, 0.2), -35)

    def test_export_video_rejects_an_unknown_music_track(self) -> None:
        response = self.client.post(
            "/api/text-slideshow/export-video",
            data={
                "slides": json.dumps([_slide("#000000")]),
                "seconds_per_slide": "0.5",
                "music_name": "missing-reel-track.mp3",
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("Unknown music track", response.json()["detail"])

    def test_export_video_rejects_a_bad_duration(self) -> None:
        response = self.client.post(
            "/api/text-slideshow/export-video",
            data={
                "slides": json.dumps([_slide("#000000")]),
                "seconds_per_slide": "0.1",
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("between", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
