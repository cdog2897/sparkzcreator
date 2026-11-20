import io
import shutil
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient

from text_slideshow import (
    BODY_FONT_SIZE,
    BODY_FONT_WEIGHT,
    BODY_INK_TOP,
    BODY_LINE_PITCH,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    LABEL_PAD_X,
    LABEL_PAD_Y,
    LABEL_RADIUS,
    LABEL_TEXT,
    LABEL_X,
    LABEL_Y,
    LABEL_WIDTH,
    LINE_HEIGHT,
    TEXT_BOX_WIDTH,
    TEXT_BOX_X,
    TEXT_COLOR_HEX,
    TITLE_FONT_SIZE,
    TITLE_FONT_WEIGHT,
    TITLE_INK_TOP,
    body_box_y,
    cover_crop,
    default_body_element,
    default_title_element,
    export_text_slideshow,
    load_font,
    render_slide,
    title_box_y,
    wrap_text,
)
from generate_video import OUTPUT_DIR
from web_app import app


SLIDE_2_BODY = (
    "si no quiere hablar de algo en el momento, no le fuerces. déjale procesar.\n"
    "\n"
    "no todo tiene que pasar cuando tú quieres que pase. "
    "a veces necesita un rato a solas antes de poder explicarse bien.\n"
    "\n"
    "aprendí que insistir solo lo aleja más."
)
SLIDE_2_LINES = [
    "si no quiere hablar de algo en el momento,",
    "no le fuerces. déjale procesar.",
    "",
    "no todo tiene que pasar cuando tú quieres",
    "que pase. a veces necesita un rato a solas",
    "antes de poder explicarse bien.",
    "",
    "aprendí que insistir solo lo aleja más.",
]


def _png(color: tuple[int, int, int], size: tuple[int, int] = (80, 120)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def _dark_mask(image: Image.Image, limit: int = 80) -> np.ndarray:
    return np.asarray(image).max(axis=2) < limit


def _line_bands(mask: np.ndarray, min_pixels: int = 30, merge_gap: int = 6) -> list[tuple[int, int]]:
    rows = mask.sum(axis=1)
    bands: list[tuple[int, int]] = []
    start = None
    for y, count in enumerate(rows):
        if count >= min_pixels and start is None:
            start = y
        elif count < min_pixels and start is not None:
            bands.append((start, y - 1))
            start = None
    if start is not None:
        bands.append((start, len(rows) - 1))
    merged: list[tuple[int, int]] = []
    for band in bands:
        if merged and band[0] - merged[-1][1] <= merge_gap:
            merged[-1] = (merged[-1][0], band[1])
        else:
            merged.append(band)
    return merged


class TextSlideshowMetricTests(unittest.TestCase):
    def test_canvas_and_measured_type_styles(self) -> None:
        self.assertEqual((CANVAS_WIDTH, CANVAS_HEIGHT), (1080, 1920))
        self.assertEqual(TEXT_COLOR_HEX, "#000000")
        self.assertEqual(TITLE_FONT_WEIGHT, "semibold")
        self.assertEqual(TITLE_FONT_SIZE, 53)
        self.assertEqual(BODY_FONT_WEIGHT, "medium")
        self.assertEqual(BODY_FONT_SIZE, 43)
        self.assertEqual(BODY_LINE_PITCH, 51)
        self.assertAlmostEqual(LINE_HEIGHT, 51 / 43)
        self.assertEqual(TEXT_BOX_WIDTH, 820)
        self.assertEqual(TEXT_BOX_X, 130)
        self.assertEqual(title_box_y(), 350)
        self.assertEqual(body_box_y(), 438)

    def test_slide_2_wraps_on_the_measured_column(self) -> None:
        font = load_font(BODY_FONT_WEIGHT, BODY_FONT_SIZE)
        self.assertEqual(wrap_text(SLIDE_2_BODY, font, TEXT_BOX_WIDTH), SLIDE_2_LINES)
        narrower = wrap_text(SLIDE_2_BODY, font, 700)
        self.assertGreater(
            len([line for line in narrower if line]),
            len([line for line in SLIDE_2_LINES if line]),
        )

    def test_reference_text_is_centered_black_and_unshadowed(self) -> None:
        background = "#8fb4d8"
        slide = {
            "background_color": background,
            "background_color_2": background,
            "elements": [
                default_title_element("1. dale espacio"),
                default_body_element(SLIDE_2_BODY),
            ],
        }
        image = render_slide(slide)
        self.assertEqual(image.size, (CANVAS_WIDTH, CANVAS_HEIGHT))
        mask = _dark_mask(image)
        bands = _line_bands(mask)
        self.assertGreaterEqual(len(bands), 6)
        title = bands[0]
        self.assertEqual(title[0], TITLE_INK_TOP)
        title_cols = np.where(mask[title[0]:title[1] + 1].any(axis=0))[0]
        self.assertAlmostEqual((title_cols[0] + title_cols[-1]) / 2, CANVAS_WIDTH / 2, delta=1.5)

        body_bands = [band for band in bands[1:] if band[1] - band[0] > 8]
        self.assertEqual(body_bands[0][0], BODY_INK_TOP)
        self.assertEqual(body_bands[1][0] - body_bands[0][0], BODY_LINE_PITCH)
        # A blank line skips one pitch. Antialiasing can shift a band edge by a pixel.
        self.assertAlmostEqual(
            body_bands[2][0] - body_bands[0][0],
            BODY_LINE_PITCH * 3,
            delta=1,
        )

        title_pixels = np.asarray(image)[title[0]:title[1] + 1, title_cols[0]:title_cols[-1] + 1]
        flat = title_pixels.reshape(-1, 3)
        darkest = flat[flat.max(axis=1).argmin()]
        self.assertLess(int(darkest.max()), 20)

        # The flat background stays flat beside the glyphs: no shadow or stroke.
        clear_x = max(0, int(title_cols[0]) - 8)
        clear = image.getpixel((clear_x, title[0] + 8))
        self.assertEqual(clear, (0x8F, 0xB4, 0xD8))

    def test_label_matches_the_reference_pill(self) -> None:
        image = render_slide({
            "background_color": "#d8c8b4",
            "background_color_2": "#d8c8b4",
            "elements": [{
                "type": "text",
                "text": LABEL_TEXT,
                "x": LABEL_X,
                "y": LABEL_Y,
                "width": LABEL_WIDTH,
                "font_weight": "medium",
                "font_size": 41,
                "color": "#ffffff",
                "align": "left",
                "line_height": LINE_HEIGHT,
                "highlight": True,
                "highlight_color": "#000000",
                "highlight_radius": LABEL_RADIUS,
                "highlight_pad_x": LABEL_PAD_X,
                "highlight_pad_y": LABEL_PAD_Y,
            }],
        })
        black = np.asarray(image).max(axis=2) < 30
        ys, xs = np.where(black)
        left, top, right, bottom = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
        self.assertEqual((left, top), (494, 792))
        self.assertAlmostEqual(right - left + 1, 546, delta=2)
        self.assertAlmostEqual(bottom - top + 1, 64, delta=2)
        # Rounded, not a square corner, with white glyphs inside the pill.
        self.assertFalse(bool(black[top, left]))
        self.assertTrue(bool(black[top + 20, left + 20]))
        white = np.asarray(image).min(axis=2) > 230
        self.assertTrue(bool(white[top:bottom, left:right].any()))

    def test_cover_crop_fills_the_portrait_frame(self) -> None:
        source = Image.new("RGBA", (200, 100), (220, 0, 0, 255))
        ImageDraw.Draw(source).rectangle((90, 40, 110, 60), fill=(0, 0, 210, 255))
        framed = cover_crop(source, CANVAS_WIDTH, CANVAS_HEIGHT).convert("RGB")
        self.assertEqual(framed.size, (CANVAS_WIDTH, CANVAS_HEIGHT))
        self.assertGreater(framed.getpixel((540, 960))[2], 150)
        self.assertGreater(framed.getpixel((0, 0))[0], 150)
        self.assertGreater(framed.getpixel((1079, 1919))[0], 150)

    def test_rounded_overlay_keeps_aspect_corners_clear(self) -> None:
        buffer = io.BytesIO()
        Image.new("RGB", (60, 40), (220, 20, 20)).save(buffer, format="PNG")
        image = render_slide(
            {
                "background_color": "#123456",
                "background_color_2": "#123456",
                "elements": [{
                    "type": "image",
                    "asset_id": "card",
                    "x": 100,
                    "y": 200,
                    "width": 100,
                    "height": 80,
                    "corner_radius": 18,
                }],
            },
            {"card": buffer.getvalue()},
        )
        self.assertEqual(image.getpixel((100, 200)), (0x12, 0x34, 0x56))
        self.assertEqual(image.getpixel((150, 240)), (220, 20, 20))

    def test_export_writes_numbered_pngs_and_a_zip(self) -> None:
        slides = [
            {
                "background_color": "#112233",
                "background_color_2": "#112233",
                "elements": [default_title_element("one")],
            },
            {
                "background_color": "#332211",
                "background_color_2": "#332211",
                "elements": [default_title_element("two")],
            },
        ]
        import tempfile

        with tempfile.TemporaryDirectory() as temp_name:
            folder = Path(temp_name) / "deck_textslides"
            written, zip_path = export_text_slideshow(slides, output_dir=folder)
            self.assertEqual(written, folder)
            self.assertEqual(sorted(path.name for path in folder.iterdir()), ["01.png", "02.png"])
            with Image.open(folder / "01.png") as first:
                self.assertEqual(first.size, (1080, 1920))
            with zipfile.ZipFile(zip_path) as archive:
                self.assertEqual(archive.namelist(), ["01.png", "02.png"])

    def test_rejects_bad_color_unknown_weight_and_empty_export(self) -> None:
        with self.assertRaises(ValueError):
            render_slide({
                "elements": [{
                    "type": "text",
                    "text": "hi",
                    "color": "black",
                    "font_size": 40,
                    "width": 400,
                    "x": 0,
                    "y": 0,
                }],
            })
        with self.assertRaises(ValueError):
            render_slide({
                "elements": [{
                    "type": "text",
                    "text": "hi",
                    "font_weight": "comic",
                    "font_size": 40,
                    "width": 400,
                    "x": 0,
                    "y": 0,
                }],
            })
        with self.assertRaises(ValueError):
            export_text_slideshow([])


class TextSlideshowApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_defaults_expose_the_measured_styles(self) -> None:
        response = self.client.get("/api/text-slideshow")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["width"], 1080)
        self.assertEqual(payload["height"], 1920)
        self.assertEqual(payload["textColor"], "#000000")
        self.assertEqual(payload["title"]["fontWeight"], "semibold")
        self.assertEqual(payload["title"]["fontSize"], 53)
        self.assertEqual(payload["body"]["fontWeight"], "medium")
        self.assertEqual(payload["body"]["fontSize"], 43)
        self.assertEqual(payload["body"]["width"], 820)
        self.assertEqual(payload["label"]["highlightRadius"], 12)
        self.assertIn("semibold", [font["id"] for font in payload["fonts"]])
        self.assertIn("medium", [font["id"] for font in payload["fonts"]])

    def test_preview_returns_a_portrait_png(self) -> None:
        response = self.client.post(
            "/api/text-slideshow/preview",
            json={
                "background_color": "#101820",
                "background_color_2": "#101820",
                "elements": [default_title_element("preview")],
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")
        with Image.open(io.BytesIO(response.content)) as image:
            self.assertEqual(image.size, (1080, 1920))

    def test_preview_rejects_an_unknown_image(self) -> None:
        response = self.client.post(
            "/api/text-slideshow/preview",
            json={
                "elements": [{
                    "type": "image",
                    "asset_id": "missing",
                    "x": 0,
                    "y": 0,
                    "width": 100,
                    "height": 100,
                }],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_upload_is_reused_by_preview_and_export(self) -> None:
        uploaded = self.client.post(
            "/api/text-slideshow/assets",
            files={"file": ("photo.png", _png((10, 180, 40), (30, 80)), "image/png")},
        )
        self.assertEqual(uploaded.status_code, 200)
        asset_id = uploaded.json()["id"]
        slide = {
            "background_asset_id": asset_id,
            "elements": [default_body_element("on a photo")],
        }
        preview = self.client.post("/api/text-slideshow/preview", json=slide)
        self.assertEqual(preview.status_code, 200)
        exported = self.client.post(
            "/api/text-slideshow/export",
            json={"slides": [slide, slide]},
        )
        self.assertEqual(exported.status_code, 200)
        folder_name = exported.headers["x-output-folder"]
        self.assertIn("_textslides", folder_name)
        try:
            with zipfile.ZipFile(io.BytesIO(exported.content)) as archive:
                self.assertEqual(archive.namelist(), ["01.png", "02.png"])
                with Image.open(io.BytesIO(archive.read("01.png"))) as image:
                    self.assertEqual(image.size, (1080, 1920))
                    # Cover-crop of a tiny green photo fills the frame.
                    self.assertGreater(image.getpixel((10, 10))[1], 100)
            folder = OUTPUT_DIR / folder_name
            self.assertTrue((folder / "01.png").is_file())
            self.assertTrue((folder / "02.png").is_file())
        finally:
            shutil.rmtree(OUTPUT_DIR / folder_name, ignore_errors=True)
            (OUTPUT_DIR / f"{folder_name}.zip").unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
