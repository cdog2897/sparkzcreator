import io
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

from PIL import Image
from fastapi.testclient import TestClient

from generate_video import OUTPUT_DIR, ffmpeg_exe, probe_duration
from slideshow import (
    DEFAULT_IMAGE_SECONDS,
    REFERENCE_FONT_SIZE,
    REFERENCE_FRAME_HEIGHT,
    REFERENCE_FRAME_WIDTH,
    REFERENCE_LINE_SPACING,
    SLIDESHOW_FPS,
    SLIDESHOW_HEIGHT,
    SLIDESHOW_WIDTH,
    TEXT_COLOR_HEX,
    cover_crop_filter,
    demo_play_seconds,
    fitted_text_metrics,
    inspect_demo_video,
    main as slideshow_main,
    quantized_slide_seconds,
    read_hook_text,
    render_fast_cuts,
    render_slideshow,
    slide_frame_count,
    slideshow_duration_seconds,
    slideshow_image_sets,
    slideshow_plan,
    slideshow_text_metrics,
    text_draw_position,
    validate_image_seconds,
)
from web_app import app
import web_app


SAMPLE_TEXT = "oh to travel the world\nwith the love of my life"


def png_bytes(color: tuple[int, int, int], size: tuple[int, int] = (48, 64)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class SlideshowTimingTests(unittest.TestCase):
    def test_default_duration_is_ten_frames_at_24fps(self) -> None:
        self.assertEqual(DEFAULT_IMAGE_SECONDS, 0.417)
        self.assertEqual(SLIDESHOW_FPS, 24)
        self.assertEqual(slide_frame_count(0.417), 10)
        self.assertEqual(quantized_slide_seconds(0.417), 10 / 24)

    def test_cuts_land_on_frame_boundaries(self) -> None:
        frames = slide_frame_count(0.417)
        for image_index in range(1, 21):
            cut_frame = image_index * frames
            self.assertEqual(cut_frame, int(cut_frame))
            self.assertEqual(cut_frame / SLIDESHOW_FPS * SLIDESHOW_FPS, cut_frame)

    def test_video_length_is_images_times_quantized_duration(self) -> None:
        # Twenty reference photos end on the end-card cut at 8.333s.
        self.assertEqual(slideshow_duration_seconds(20, 0.417), 200 / 24)
        self.assertEqual(
            slideshow_plan(20, 0.417),
            {
                "fps": 24,
                "imageCount": 20,
                "framesPerImage": 10,
                "slideSeconds": 10 / 24,
                "durationSeconds": 200 / 24,
            },
        )
        self.assertEqual(slideshow_duration_seconds(21, 0.417), 210 / 24)
        self.assertEqual(demo_play_seconds(0.5), 12 / 24)
        self.assertAlmostEqual(
            slideshow_duration_seconds(2, 0.417) + demo_play_seconds(0.5),
            32 / 24,
        )

    def test_half_frames_round_away_from_zero(self) -> None:
        self.assertEqual(slide_frame_count(10.5 / 24), 11)
        self.assertEqual(slide_frame_count(0.5), 12)

    def test_duration_bounds(self) -> None:
        with self.assertRaises(ValueError):
            slide_frame_count(0)
        with self.assertRaises(ValueError):
            validate_image_seconds(0.01)
        with self.assertRaises(ValueError):
            slideshow_duration_seconds(0, 0.417)

    def test_text_metrics_match_the_960px_reference_and_scale(self) -> None:
        self.assertEqual(
            slideshow_text_metrics(960),
            (REFERENCE_FONT_SIZE, REFERENCE_LINE_SPACING),
        )
        self.assertEqual(slideshow_text_metrics(960), (35, -2))
        self.assertEqual(slideshow_text_metrics(1920), (70, -4))
        self.assertEqual(fitted_text_metrics(SAMPLE_TEXT, 960), (35, -2))
        self.assertEqual(fitted_text_metrics(SAMPLE_TEXT, 1920), (70, -4))
        # Width share stays constant: 70/1920 == 35/960.
        self.assertEqual(
            slideshow_text_metrics(SLIDESHOW_WIDTH)[0] / SLIDESHOW_WIDTH,
            REFERENCE_FONT_SIZE / REFERENCE_FRAME_WIDTH,
        )

    def test_caption_stays_centered_like_the_reference(self) -> None:
        self.assertEqual(REFERENCE_FRAME_WIDTH, 960)
        self.assertEqual(REFERENCE_FRAME_HEIGHT, 576)
        self.assertEqual(text_draw_position(), ("(w-text_w)/2", "(h-text_h)/2"))

    def test_long_lines_shrink_instead_of_leaving_the_frame(self) -> None:
        font_size, _line_spacing = fitted_text_metrics("w" * 80, 1920)
        self.assertLess(font_size, 70)

    def test_cover_crop_is_full_bleed_and_still(self) -> None:
        video_filter = cover_crop_filter(SLIDESHOW_WIDTH, SLIDESHOW_HEIGHT)
        self.assertIn("force_original_aspect_ratio=increase", video_filter)
        self.assertIn("crop=1920:1080:(iw-1920)/2:(ih-1080)/2", video_filter)
        self.assertNotIn("zoompan", video_filter)
        self.assertNotIn("pad=", video_filter)

    def test_text_color_is_the_tiktok_yellow_swatch(self) -> None:
        self.assertEqual(TEXT_COLOR_HEX, "#F2CD46")


class SlideshowRenderTests(unittest.TestCase):
    def test_render_holds_each_photo_for_ten_frames(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            images = []
            for index, color in enumerate(((20, 24, 38), (180, 90, 40))):
                path = temp_dir / f"photo_{index}.png"
                Image.new("RGB", (200, 320), color).save(path)
                images.append(path)
            output_path = temp_dir / "slideshow.mp4"
            render_slideshow(
                images,
                text=SAMPLE_TEXT,
                image_seconds=0.417,
                music_path=None,
                width=320,
                height=568,
                output_path=output_path,
            )

            self.assertAlmostEqual(probe_duration(output_path), 20 / 24, places=2)
            self.assertEqual(_video_packet_count(output_path), 20)

    def test_default_canvas_is_16_by_9(self) -> None:
        self.assertEqual((SLIDESHOW_WIDTH, SLIDESHOW_HEIGHT), (1920, 1080))
        self.assertEqual(SLIDESHOW_WIDTH / SLIDESHOW_HEIGHT, 16 / 9)

    def test_cover_crop_fills_the_frame_with_no_bars(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            temp_dir = Path(temp_name)
            portrait = temp_dir / "portrait.png"
            landscape = temp_dir / "landscape.png"
            Image.new("RGB", (40, 220), (220, 30, 40)).save(portrait)
            Image.new("RGB", (420, 50), (30, 70, 210)).save(landscape)
            output_path = temp_dir / "filled.mp4"
            render_slideshow(
                [portrait, landscape],
                text=SAMPLE_TEXT,
                image_seconds=0.417,
                music_path=None,
                width=320,
                height=180,
                output_path=output_path,
            )
            first = _frame_at(output_path, 0)
            second = _frame_at(output_path, 0.5)
            self.assertEqual(first.size, (320, 180))
            self.assertEqual(second.size, (320, 180))
            self.assertTrue(_is_red(first.getpixel((4, 4))))
            self.assertTrue(_is_red(first.getpixel((316, 176))))
            self.assertTrue(_is_blue(second.getpixel((4, 4))))
            self.assertTrue(_is_blue(second.getpixel((316, 176))))
            self.assertTrue(_has_yellow(first))
            self.assertTrue(_has_yellow(second))


def _frame_at(path: Path, seconds: float) -> Image.Image:
    import subprocess

    from generate_video import ffmpeg_exe

    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        subprocess.run(
            [
                ffmpeg_exe(),
                "-y",
                "-ss",
                str(seconds),
                "-i",
                str(path),
                "-frames:v",
                "1",
                "-update",
                "1",
                handle.name,
            ],
            check=True,
            capture_output=True,
        )
        with Image.open(handle.name) as frame:
            return frame.convert("RGB")


def _is_red(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return red > 160 and green < 80 and blue < 90


def _is_blue(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return blue > 140 and red < 80 and green < 120


def _has_yellow(frame: Image.Image) -> bool:
    width, height = frame.size
    for y in range(height // 2 - 24, height // 2 + 24):
        for x in range(width // 2 - 40, width // 2 + 40):
            red, green, blue = frame.getpixel((x, y))
            if red > 180 and 140 < green < 230 and blue < 120 and red > green > blue:
                return True
    return False


def _video_packet_count(path: Path) -> int:
    import subprocess

    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-count_packets",
            "-show_entries",
            "stream=nb_read_packets",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(completed.stdout.strip())


class SlideshowEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_defaults_describe_the_reference_timing(self) -> None:
        response = self.client.get("/api/slideshow")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["defaultImageDuration"], 0.417)
        self.assertEqual(payload["fps"], 24)
        self.assertEqual(payload["width"], 1920)
        self.assertEqual(payload["height"], 1080)
        self.assertEqual(payload["textColor"], "#F2CD46")
        self.assertEqual(payload["fontSizeAtReference"], 35)
        self.assertEqual(payload["referenceWidth"], 960)

    def test_missing_images_are_rejected(self) -> None:
        response = self.client.post(
            "/api/slideshow/render",
            data={"text": SAMPLE_TEXT, "image_duration": "0.417"},
        )
        self.assertEqual(response.status_code, 422)

    def test_invalid_duration_is_rejected(self) -> None:
        response = self.client.post(
            "/api/slideshow/render",
            data={"text": SAMPLE_TEXT, "image_duration": "0", "music_name": "none"},
            files=[("images", ("a.png", png_bytes((10, 10, 10)), "image/png"))],
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("duration", response.json()["detail"].lower())

    def test_unreadable_image_is_rejected(self) -> None:
        response = self.client.post(
            "/api/slideshow/render",
            data={"image_duration": "0.417", "music_name": "none"},
            files=[("images", ("notes.txt", b"not an image", "text/plain"))],
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("readable image", response.json()["detail"])

    def test_music_path_cannot_escape_the_library(self) -> None:
        response = self.client.post(
            "/api/slideshow/render",
            data={"image_duration": "0.417", "music_name": "../generate_video.py"},
            files=[("images", ("a.png", png_bytes((10, 10, 10)), "image/png"))],
        )
        self.assertEqual(response.status_code, 400)

    def test_render_endpoint_starts_a_job_for_ordered_images(self) -> None:
        output_path = OUTPUT_DIR / "slideshow_endpoint_test.mp4"
        calls: list[tuple[int, str, float, object]] = []

        def fake_render(image_paths, text="", image_seconds=0.417, music_path=None, progress_callback=None, **_kwargs):
            calls.append((len(image_paths), text, image_seconds, music_path))
            if progress_callback is not None:
                progress_callback("Rendering test.")
            output_path.parent.mkdir(exist_ok=True)
            output_path.write_bytes(b"slideshow")
            return output_path

        try:
            with patch("web_app.render_slideshow", side_effect=fake_render):
                response = self.client.post(
                    "/api/slideshow/render",
                    data={
                        "text": SAMPLE_TEXT,
                        "image_duration": "0.417",
                        "music_name": "none",
                    },
                    files=[
                        ("images", ("first.png", png_bytes((20, 20, 20)), "image/png")),
                        ("images", ("second.png", png_bytes((200, 20, 20)), "image/png")),
                    ],
                )
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["plan"]["framesPerImage"], 10)
                self.assertEqual(body["plan"]["imageCount"], 2)
                self.assertEqual(body["plan"]["durationSeconds"], 20 / 24)
                job_id = body["job"]["id"]
                job = self._wait_for_job(job_id)
            self.assertEqual(job["status"], "complete", job.get("error"))
            self.assertEqual(calls, [(2, SAMPLE_TEXT, 0.417, None)])
            self.assertTrue(job["outputUrl"])
        finally:
            output_path.unlink(missing_ok=True)
            shutil.rmtree(OUTPUT_DIR / "tmp", ignore_errors=True)

    def _wait_for_job(self, job_id: str) -> dict:
        import time

        last = {}
        for _ in range(50):
            last = self.client.get(f"/api/jobs/{job_id}").json()["job"]
            if last["status"] in {"complete", "error"}:
                return last
            time.sleep(0.02)
        return last


def _write_demo(
    path: Path,
    *,
    seconds: float = 0.5,
    size: tuple[int, int] = (40, 220),
    color: str = "0x00C040",
    audio: bool = False,
    fps: int = 24,
) -> None:
    frames = max(1, int(round(seconds * fps)))
    duration = frames / fps
    command = [
        ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={color}:s={size[0]}x{size[1]}:r={fps}:d={duration:.6f}",
    ]
    if audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency=880:sample_rate=44100:d={duration:.6f}",
                "-shortest",
                "-c:a",
                "aac",
            ]
        )
    else:
        command.append("-an")
    command.extend(
        [
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(fps),
            str(path),
        ]
    )
    subprocess.run(command, check=True, capture_output=True)


def _write_tone(path: Path, seconds: float = 3.0) -> None:
    rate = 44100
    count = max(1, int(seconds * rate))
    with wave.open(str(path), "w") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        frames = bytearray()
        for index in range(count):
            sample = int(0.45 * 32767 * math.sin(2 * math.pi * 440 * index / rate))
            frames.extend(struct.pack("<h", sample))
        handle.writeframes(frames)


def _has_audio_stream(path: Path) -> bool:
    completed = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return "Audio:" in completed.stderr


def _mean_volume(path: Path, start: float, length: float) -> float:
    completed = subprocess.run(
        [
            ffmpeg_exe(),
            "-hide_banner",
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
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    match = re.search(r"mean_volume:\s*(-inf|-?\d+(?:\.\d+)?) dB", completed.stderr)
    if match is None:
        raise AssertionError(completed.stderr[-500:])
    if match.group(1) == "-inf":
        return -1000.0
    return float(match.group(1))


def _is_green(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return green > 80 and green > red + 30 and green > blue


class SlideshowDemoRenderTests(unittest.TestCase):
    def _photos(self, folder: Path, count: int = 2) -> list[Path]:
        colors = ((20, 24, 38), (180, 90, 40), (30, 70, 210))
        paths = []
        for index in range(count):
            path = folder / f"photo_{index}.png"
            Image.new("RGB", (80, 120), colors[index % len(colors)]).save(path)
            paths.append(path)
        return paths

    def test_export_without_demo_matches_the_photo_cut(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)
            output = folder / "plain.mp4"
            render_slideshow(
                self._photos(folder),
                text=SAMPLE_TEXT,
                image_seconds=0.417,
                music_path=None,
                width=320,
                height=180,
                output_path=output,
                preset="ultrafast",
            )
            self.assertAlmostEqual(probe_duration(output), 20 / 24, places=2)
            self.assertEqual(_frame_at(output, 0).size, (320, 180))
            self.assertFalse(_has_audio_stream(output))
            self.assertTrue(_has_yellow(_frame_at(output, 0)))

    def test_demo_appends_after_the_cuts_and_drops_the_hook(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)
            demo = folder / "demo.mp4"
            _write_demo(demo, seconds=0.5, audio=False)
            output = folder / "with-demo.mp4"
            render_slideshow(
                self._photos(folder),
                text=SAMPLE_TEXT,
                image_seconds=0.417,
                music_path=None,
                demo_path=demo,
                keep_demo_audio=False,
                width=320,
                height=180,
                output_path=output,
                preset="ultrafast",
            )
            info = inspect_demo_video(demo)
            expected = slideshow_duration_seconds(2, 0.417) + info.play_seconds
            self.assertAlmostEqual(probe_duration(output), expected, places=2)
            self.assertAlmostEqual(expected, 32 / 24)
            opening = _frame_at(output, 0)
            during_demo = _frame_at(output, (20 / 24) + 0.08)
            self.assertEqual(opening.size, (320, 180))
            self.assertEqual(during_demo.size, (320, 180))
            self.assertTrue(_has_yellow(opening))
            self.assertFalse(_has_yellow(during_demo))
            self.assertTrue(_is_green(during_demo.getpixel((2, 2))))
            self.assertTrue(_is_green(during_demo.getpixel((318, 178))))
            self.assertFalse(_has_audio_stream(output))

    def test_full_canvas_stays_1920x1080(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)
            demo = folder / "demo.mp4"
            _write_demo(demo, seconds=0.25, size=(90, 160), audio=False)
            output = folder / "full.mp4"
            photos = self._photos(folder, count=1)
            render_slideshow(
                photos,
                text=SAMPLE_TEXT,
                image_seconds=0.417,
                demo_path=demo,
                width=SLIDESHOW_WIDTH,
                height=SLIDESHOW_HEIGHT,
                output_path=output,
                preset="ultrafast",
            )
            frame = _frame_at(output, 0)
            self.assertEqual(frame.size, (SLIDESHOW_WIDTH, SLIDESHOW_HEIGHT))
            self.assertEqual((SLIDESHOW_WIDTH, SLIDESHOW_HEIGHT), (1920, 1080))
            info = inspect_demo_video(demo)
            self.assertAlmostEqual(
                probe_duration(output),
                slideshow_duration_seconds(1, 0.417) + info.play_seconds,
                places=2,
            )

    def test_demo_audio_is_off_unless_requested_and_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)
            demo = folder / "demo.mp4"
            _write_demo(demo, seconds=0.5, audio=True)
            silent = folder / "off.mp4"
            mixed = folder / "on.mp4"
            photos = self._photos(folder)
            shared = dict(
                text=SAMPLE_TEXT,
                image_seconds=0.417,
                music_path=None,
                demo_path=demo,
                width=160,
                height=90,
                preset="ultrafast",
            )
            render_slideshow(photos, output_path=silent, keep_demo_audio=False, **shared)
            render_slideshow(photos, output_path=mixed, keep_demo_audio=True, **shared)
            self.assertFalse(_has_audio_stream(silent))
            self.assertTrue(_has_audio_stream(mixed))
            photo_level = _mean_volume(mixed, 0.05, 0.35)
            demo_level = _mean_volume(mixed, (20 / 24) + 0.05, 0.2)
            self.assertLess(photo_level, -45)
            self.assertGreater(demo_level, -35)

    def test_music_continues_under_the_demo_and_silent_demo_is_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)
            demo = folder / "silent.mp4"
            _write_demo(demo, seconds=0.5, audio=False)
            music = folder / "song.wav"
            _write_tone(music, seconds=4)
            output = folder / "music.mp4"
            render_slideshow(
                self._photos(folder),
                text=SAMPLE_TEXT,
                image_seconds=0.417,
                music_path=music,
                demo_path=demo,
                keep_demo_audio=True,
                width=160,
                height=90,
                output_path=output,
                preset="ultrafast",
            )
            info = inspect_demo_video(demo)
            self.assertFalse(info.has_audio)
            expected = slideshow_duration_seconds(2, 0.417) + info.play_seconds
            self.assertAlmostEqual(probe_duration(output), expected, places=2)
            self.assertTrue(_has_audio_stream(output))
            self.assertGreater(_mean_volume(output, 0.05, 0.3), -40)
            self.assertGreater(_mean_volume(output, (20 / 24) + 0.05, 0.2), -40)

    def test_music_and_demo_audio_mix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name)
            demo = folder / "demo.mp4"
            _write_demo(demo, seconds=0.5, audio=True)
            music = folder / "song.wav"
            _write_tone(music, seconds=4)
            output = folder / "mix.mp4"
            render_slideshow(
                self._photos(folder),
                text="",
                image_seconds=0.417,
                music_path=music,
                demo_path=demo,
                keep_demo_audio=True,
                width=160,
                height=90,
                output_path=output,
                preset="ultrafast",
            )
            self.assertTrue(_has_audio_stream(output))
            self.assertGreater(_mean_volume(output, 0.05, 0.3), -40)
            self.assertGreater(_mean_volume(output, (20 / 24) + 0.05, 0.2), -40)
            self.assertFalse(_has_yellow(_frame_at(output, (20 / 24) + 0.08)))

    def test_batch_cli_reuses_one_demo_across_sets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            root = Path(temp_name)
            batch = root / "batch"
            one = batch / "one"
            two = batch / "two"
            one.mkdir(parents=True)
            two.mkdir()
            Image.new("RGB", (32, 48), (10, 10, 10)).save(one / "a.png")
            Image.new("RGB", (32, 48), (20, 20, 20)).save(one / "b.png")
            (one / "hook.txt").write_text("first hook\nsecond line\n", encoding="utf-8")
            for name, color in (("a.png", (30, 30, 30)), ("b.png", (40, 40, 40)), ("c.png", (50, 50, 50))):
                Image.new("RGB", (32, 40), color).save(two / name)
            (two / "text.txt").write_text("other hook", encoding="utf-8")
            demo = root / "demo.mov"
            _write_demo(demo, seconds=0.5, audio=True)
            self.assertEqual(read_hook_text(one, "fallback"), "first hook\nsecond line")
            self.assertEqual(slideshow_image_sets(batch), [one, two])

            results = render_fast_cuts(
                batch,
                text="fallback",
                demo_path=demo,
                keep_demo_audio=False,
                output_dir=root / "out",
                width=320,
                height=180,
                preset="ultrafast",
            )
            self.assertEqual(
                [path.name for path in results],
                ["one.mp4", "two.mp4"],
            )
            info = inspect_demo_video(demo)
            self.assertAlmostEqual(
                probe_duration(results[0]),
                slideshow_duration_seconds(2, 0.417) + info.play_seconds,
                places=2,
            )
            self.assertAlmostEqual(
                probe_duration(results[1]),
                slideshow_duration_seconds(3, 0.417) + info.play_seconds,
                places=2,
            )
            self.assertEqual(_frame_at(results[0], 0).size, (320, 180))
            self.assertFalse(_has_audio_stream(results[0]))
            self.assertTrue(_has_yellow(_frame_at(results[0], 0)))
            self.assertFalse(_has_yellow(_frame_at(results[0], (20 / 24) + 0.08)))

            with patch("slideshow.render_fast_cuts", return_value=[root / "unused.mp4"]) as rendered:
                code = slideshow_main(
                    [
                        str(one),
                        "--text",
                        "from the flag",
                        "--demo",
                        str(demo),
                        "--keep-demo-audio",
                        "--music",
                        str(root / "song.wav"),
                        "--output",
                        str(root / "single-out.mp4"),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(rendered.call_args.args[0], one)
            self.assertEqual(rendered.call_args.kwargs["demo_path"], demo)
            self.assertTrue(rendered.call_args.kwargs["keep_demo_audio"])
            self.assertEqual(rendered.call_args.kwargs["text"], "from the flag")
            self.assertEqual(slideshow_main([str(one), "--demo", str(root / "missing.mp4")]), 1)
            self.assertEqual(slideshow_main([str(one), "--keep-demo-audio"]), 1)


class SlideshowDemoEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.temp = tempfile.TemporaryDirectory()
        self._demo_dir = web_app.DEMO_DIR
        web_app.DEMO_DIR = Path(self.temp.name) / "demo"

    def tearDown(self) -> None:
        web_app.DEMO_DIR = self._demo_dir
        self.temp.cleanup()

    def test_saved_demo_is_preselected_until_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            demo = Path(temp_name) / "tour.mp4"
            _write_demo(demo, seconds=0.5, audio=True)
            empty = self.client.get("/api/slideshow")
            self.assertEqual(empty.status_code, 200)
            self.assertIsNone(empty.json()["demo"])

            uploaded = self.client.post(
                "/api/slideshow/demo",
                files=[("demo", ("tour.mp4", demo.read_bytes(), "video/mp4"))],
            )
            self.assertEqual(uploaded.status_code, 200, uploaded.text)
            payload = uploaded.json()["demo"]
            self.assertEqual(payload["name"], "tour.mp4")
            self.assertTrue(payload["hasAudio"])
            self.assertAlmostEqual(payload["durationSeconds"], 0.5, places=2)

            again = self.client.get("/api/slideshow")
            self.assertEqual(again.json()["demo"]["name"], "tour.mp4")
            media = self.client.get(payload["url"])
            self.assertEqual(media.status_code, 200)
            self.assertGreater(len(media.content), 100)

            rejected = self.client.post(
                "/api/slideshow/demo",
                files=[("demo", ("notes.txt", b"hello", "text/plain"))],
            )
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(self.client.get("/api/slideshow").json()["demo"]["name"], "tour.mp4")

            removed = self.client.delete("/api/slideshow/demo")
            self.assertEqual(removed.status_code, 200)
            self.assertIsNone(removed.json()["demo"])
            self.assertIsNone(self.client.get("/api/slideshow").json()["demo"])
            self.assertEqual(self.client.get("/api/media/slideshow-demo").status_code, 404)

    def test_render_uses_the_saved_demo_until_it_is_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_name:
            demo = Path(temp_name) / "end.mov"
            _write_demo(demo, seconds=0.5, audio=False)
            uploaded = self.client.post(
                "/api/slideshow/demo",
                files=[("demo", ("end.mov", demo.read_bytes(), "video/quicktime"))],
            )
            self.assertEqual(uploaded.status_code, 200, uploaded.text)

            output_path = OUTPUT_DIR / "slideshow_demo_endpoint.mp4"
            calls: list[tuple[object, bool]] = []

            def fake_render(image_paths, text="", image_seconds=0.417, music_path=None, progress_callback=None, **kwargs):
                calls.append((kwargs.get("demo_path"), kwargs.get("keep_demo_audio")))
                if progress_callback is not None:
                    progress_callback("Rendering test.")
                output_path.parent.mkdir(exist_ok=True)
                output_path.write_bytes(b"slideshow")
                return output_path

            try:
                with patch("web_app.render_slideshow", side_effect=fake_render):
                    response = self.client.post(
                        "/api/slideshow/render",
                        data={
                            "text": SAMPLE_TEXT,
                            "image_duration": "0.417",
                            "music_name": "none",
                            "use_demo": "true",
                            "keep_demo_audio": "true",
                        },
                        files=[("images", ("a.png", png_bytes((12, 12, 12)), "image/png"))],
                    )
                    self.assertEqual(response.status_code, 200, response.text)
                    body = response.json()
                    self.assertAlmostEqual(body["plan"]["durationSeconds"], 10 / 24 + 0.5, places=2)
                    self.assertAlmostEqual(body["plan"]["demoSeconds"], 0.5, places=2)
                    job = self._wait_for_job(body["job"]["id"])
                self.assertEqual(job["status"], "complete", job.get("error"))
                self.assertEqual(len(calls), 1)
                demo_path, keep_audio = calls[0]
                self.assertIsNotNone(demo_path)
                self.assertTrue(Path(demo_path).is_file())
                self.assertTrue(keep_audio)
            finally:
                output_path.unlink(missing_ok=True)

            web_app.clear_saved_demo()
            refused = self.client.post(
                "/api/slideshow/render",
                data={"image_duration": "0.417", "music_name": "none", "use_demo": "true"},
                files=[("images", ("a.png", png_bytes((10, 10, 10)), "image/png"))],
            )
            self.assertEqual(refused.status_code, 400)

    def _wait_for_job(self, job_id: str) -> dict:
        import time

        last = {}
        for _ in range(50):
            last = self.client.get(f"/api/jobs/{job_id}").json()["job"]
            if last["status"] in {"complete", "error"}:
                return last
            time.sleep(0.02)
        return last


if __name__ == "__main__":
    unittest.main()
