import io
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from fastapi.testclient import TestClient

from generate_video import OUTPUT_DIR, probe_duration
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
    fitted_text_metrics,
    quantized_slide_seconds,
    render_slideshow,
    slide_frame_count,
    slideshow_duration_seconds,
    slideshow_plan,
    slideshow_text_metrics,
    text_draw_position,
    validate_image_seconds,
)
from web_app import app


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


if __name__ == "__main__":
    unittest.main()
