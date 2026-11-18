#!/usr/bin/env python3
"""Portrait text slides: a photo, free-form text boxes, and image overlays.

The canvas is 1080x1920, the TikTok photo-carousel frame. Text metrics are
measured from the Brochi reference slides (1080px wide):

- Tip titles such as "1. dale espacio" match TikTok Sans SemiBold at 53px.
  On slide 2 the ascender ink starts at y=363. There is no stroke or shadow.
- Body copy matches TikTok Sans Medium at 43px. The first line's ascender
  ink starts at y=449, and the distance between line tops is 51px, which is
  one blank line between paragraphs (about 102px).
- Text is near-black #000000, horizontally centered. The body column is
  820px wide, which is the width that soft-wraps slide 2 the way the
  reference does. The box is centered on the 1080px frame (x=130).
- The "se llama ..." tag is TikTok Sans Medium at 41px, white on a black
  rounded rectangle. The pill is 546x64 at x=494, y=792, with a 12px corner
  radius. Padding from the glyph box is 23px horizontal and 16.5px vertical.
- The phone screenshot sits at x=62, y=765, 405x880, with slightly rounded
  corners (about 16px). The card blends into the sky, so that radius is the
  best fit of a subtle corner rather than a sharp pixel count.

y on a text box is the em-box top (Pillow's layout origin). Ascender ink
starts a few pixels below that, by the font's internal leading.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Mapping

from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

from generate_video import OUTPUT_DIR, ROOT
from slideshow import MAX_IMAGE_BYTES


CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1920

MAX_SLIDES = 20
MAX_ELEMENTS = 30
MAX_TEXT_CHARS = 4000
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 320
MIN_BOX_WIDTH = 40

TEXT_COLOR = (0, 0, 0)
TEXT_COLOR_HEX = "#000000"

# Slide 2, the cleanest sky. Ink tops are the first dark row of the ascenders.
TITLE_FONT_WEIGHT = "semibold"
TITLE_FONT_SIZE = 53
TITLE_INK_TOP = 366
BODY_FONT_WEIGHT = "medium"
BODY_FONT_SIZE = 43
BODY_INK_TOP = 451
BODY_LINE_PITCH = 51
LINE_HEIGHT = BODY_LINE_PITCH / BODY_FONT_SIZE

TEXT_BOX_WIDTH = 820
TEXT_BOX_X = (CANVAS_WIDTH - TEXT_BOX_WIDTH) // 2  # 130

# Cover hook on slide 1. Larger than the tip titles, and Medium rather than
# SemiBold: that pair won the glyph-mask comparison.
COVER_TITLE_WEIGHT = "medium"
COVER_TITLE_SIZE = 66
COVER_TITLE_INK_TOP = 371
COVER_SUBTITLE_WEIGHT = "medium"
COVER_SUBTITLE_SIZE = 49
COVER_SUBTITLE_INK_TOP = 456

# Black label on slide 4. Pill bounds were x=494, y=792, 546x64.
LABEL_FONT_WEIGHT = "medium"
LABEL_FONT_SIZE = 41
LABEL_COLOR_HEX = "#FFFFFF"
LABEL_BACKGROUND_HEX = "#000000"
LABEL_RADIUS = 12
# Padding is the gap from the glyph box to the pill. These values put a
# Medium 41 label on a 546x64 pill, the size measured on slide 4.
LABEL_PAD_X = 23
LABEL_PAD_Y = 16.5
LABEL_TEXT = "se llama \u201cbrochi bucket list\u201d"
# Em-box origin that places that pill at x=494, y=792.
LABEL_X = 517
LABEL_Y = 798.5
LABEL_WIDTH = 560

# Phone screenshot on slide 4.
SCREENSHOT_X = 62
SCREENSHOT_Y = 765
SCREENSHOT_WIDTH = 405
SCREENSHOT_HEIGHT = 880
SCREENSHOT_RADIUS = 16

FONT_DIR = ROOT / "fonts" / "TikTokSans"
FONT_FILES = {
    "regular": "TikTokSans-Regular.ttf",
    "medium": "TikTokSans-Medium.ttf",
    "semibold": "TikTokSans-SemiBold.ttf",
    "bold": "TikTokSans-Bold.ttf",
}
FONT_NAMES = {
    "regular": "Regular",
    "medium": "Medium",
    "semibold": "SemiBold",
    "bold": "Bold",
}
FONT_CSS_WEIGHT = {
    "regular": 400,
    "medium": 500,
    "semibold": 600,
    "bold": 700,
}
WEIGHT_ALIASES = {
    "regular": "regular",
    "400": "regular",
    "medium": "medium",
    "500": "medium",
    "semibold": "semibold",
    "semi-bold": "semibold",
    "600": "semibold",
    "bold": "bold",
    "700": "bold",
}

_HEX_COLOR = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def available_font_weights() -> list[str]:
    return [key for key, name in FONT_FILES.items() if (FONT_DIR / name).is_file()]


def font_path(weight: str) -> Path:
    key = resolve_weight(weight)
    path = FONT_DIR / FONT_FILES[key]
    if not path.is_file():
        raise FileNotFoundError(f"Missing TikTok Sans weight: {key}")
    return path


def resolve_weight(weight: str) -> str:
    key = WEIGHT_ALIASES.get(str(weight).strip().lower())
    if key is None or key not in FONT_FILES:
        known = ", ".join(available_font_weights())
        raise ValueError(f"Unknown font weight. Use one of: {known}.")
    return key


@lru_cache(maxsize=64)
def load_font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    if size < MIN_FONT_SIZE or size > MAX_FONT_SIZE:
        raise ValueError(
            f"Font size must be between {MIN_FONT_SIZE} and {MAX_FONT_SIZE}."
        )
    return ImageFont.truetype(str(font_path(weight)), size)


def em_top_for_ink_top(weight: str, size: int, ink_top: float) -> float:
    """Box y whose ascender ink lands on ink_top."""
    font = load_font(weight, size)
    return ink_top - font.getbbox("l")[1]


def title_box_y() -> float:
    return em_top_for_ink_top(TITLE_FONT_WEIGHT, TITLE_FONT_SIZE, TITLE_INK_TOP)


def body_box_y() -> float:
    return em_top_for_ink_top(BODY_FONT_WEIGHT, BODY_FONT_SIZE, BODY_INK_TOP)


def cover_title_box_y() -> float:
    return em_top_for_ink_top(COVER_TITLE_WEIGHT, COVER_TITLE_SIZE, COVER_TITLE_INK_TOP)


def cover_subtitle_box_y() -> float:
    return em_top_for_ink_top(
        COVER_SUBTITLE_WEIGHT,
        COVER_SUBTITLE_SIZE,
        COVER_SUBTITLE_INK_TOP,
    )


def parse_color(value: str) -> tuple[int, int, int, int]:
    if not isinstance(value, str) or not _HEX_COLOR.match(value.strip()):
        raise ValueError("Color must be a hex value like #000000.")
    hex_value = value.strip()[1:]
    if len(hex_value) == 3:
        hex_value = "".join(channel * 2 for channel in hex_value)
    if len(hex_value) == 6:
        hex_value += "ff"
    red = int(hex_value[0:2], 16)
    green = int(hex_value[2:4], 16)
    blue = int(hex_value[4:6], 16)
    alpha = int(hex_value[6:8], 16)
    return red, green, blue, alpha


def _number(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise ValueError(f"{name} must be a finite number.")
    return number


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: float) -> list[str]:
    """Wrap on spaces. Blank lines stay blank so a paragraph gap is one empty line."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized == "":
        return []
    lines: list[str] = []
    limit = max(1.0, max_width)
    for paragraph in normalized.split("\n"):
        if paragraph.strip() == "":
            lines.append("")
            continue
        current = ""
        for word in paragraph.split(" "):
            if word == "":
                continue
            trial = word if current == "" else f"{current} {word}"
            if current == "" or font.getlength(trial) <= limit + 0.51:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines


def open_slide_image(source: Path | bytes) -> Image.Image:
    return _open_rgba(source)


def _open_rgba(source: Path | bytes) -> Image.Image:
    try:
        if isinstance(source, bytes):
            if not source:
                raise ValueError("One of the images is empty.")
            if len(source) > MAX_IMAGE_BYTES:
                raise ValueError("Each image must be under 25 MB.")
            opened = Image.open(io.BytesIO(source))
        else:
            if source.stat().st_size > MAX_IMAGE_BYTES:
                raise ValueError("Each image must be under 25 MB.")
            opened = Image.open(source)
        with opened:
            opened.load()
            image = ImageOps.exif_transpose(opened)
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        if isinstance(error, ValueError) and "image" in str(error).lower():
            raise
        raise ValueError("One of the uploads is not a readable image.") from error


def cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scale to cover, then center-crop. No bars and no dimming."""
    if image.width < 1 or image.height < 1:
        raise ValueError("Image is empty.")
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (max(width, round(image.width * scale)), max(height, round(image.height * scale))),
        Image.Resampling.LANCZOS,
    )
    left = max(0, (resized.width - width) // 2)
    top = max(0, (resized.height - height) // 2)
    return resized.crop((left, top, left + width, top + height))


def vertical_gradient(
    width: int,
    height: int,
    top: tuple[int, int, int, int],
    bottom: tuple[int, int, int, int],
) -> Image.Image:
    column = Image.new("RGBA", (1, height))
    pixels = []
    span = max(1, height - 1)
    for y in range(height):
        blend = y / span
        pixels.append(
            tuple(
                round(top[channel] + (bottom[channel] - top[channel]) * blend)
                for channel in range(4)
            )
        )
    column.putdata(pixels)
    return column.resize((width, height), Image.Resampling.NEAREST)


def _rounded_mask(width: int, height: int, radius: float) -> Image.Image:
    mask = Image.new("L", (width, height), 0)
    if width < 1 or height < 1:
        return mask
    corner = max(0.0, min(float(radius), width / 2, height / 2))
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=corner,
        fill=255,
    )
    return mask


def _paste_rgba(base: Image.Image, sprite: Image.Image, x: int, y: int) -> None:
    """Paste an RGBA sprite, including when it hangs off the canvas."""
    if sprite.width < 1 or sprite.height < 1:
        return
    left = max(0, x)
    top = max(0, y)
    right = min(base.width, x + sprite.width)
    bottom = min(base.height, y + sprite.height)
    if right <= left or bottom <= top:
        return
    crop = sprite.crop((left - x, top - y, right - x, bottom - y))
    base.alpha_composite(crop, (left, top))


def _asset_image(
    asset_id: str,
    assets: Mapping[str, bytes | Path],
) -> Image.Image:
    if asset_id not in assets:
        raise ValueError(f"Missing image: {asset_id}.")
    return _open_rgba(assets[asset_id])


def _normalize_text(element: dict) -> dict:
    text = element.get("text", "")
    if not isinstance(text, str):
        raise ValueError("Text must be a string.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"Text must be {MAX_TEXT_CHARS} characters or fewer.")
    weight = resolve_weight(str(element.get("font_weight", TITLE_FONT_WEIGHT)))
    size = int(round(_number(element.get("font_size", TITLE_FONT_SIZE), "Font size")))
    if size < MIN_FONT_SIZE or size > MAX_FONT_SIZE:
        raise ValueError(
            f"Font size must be between {MIN_FONT_SIZE} and {MAX_FONT_SIZE}."
        )
    align = str(element.get("align", "center")).strip().lower()
    if align not in {"left", "center", "right"}:
        raise ValueError("Alignment must be left, center, or right.")
    line_height = _number(element.get("line_height", LINE_HEIGHT), "Line spacing")
    if line_height < 0.8 or line_height > 3:
        raise ValueError("Line spacing must be between 0.8 and 3.")
    width = _number(element.get("width", TEXT_BOX_WIDTH), "Text width")
    if width < MIN_BOX_WIDTH or width > CANVAS_WIDTH * 2:
        raise ValueError("Text box width is out of range.")
    highlight = bool(element.get("highlight", False))
    radius = _number(element.get("highlight_radius", LABEL_RADIUS), "Corner radius")
    pad_x = _number(element.get("highlight_pad_x", LABEL_PAD_X), "Highlight padding")
    pad_y = _number(element.get("highlight_pad_y", LABEL_PAD_Y), "Highlight padding")
    if radius < 0 or pad_x < 0 or pad_y < 0 or radius > 400 or pad_x > 400 or pad_y > 400:
        raise ValueError("Highlight padding is out of range.")
    return {
        "type": "text",
        "text": text,
        "x": _number(element.get("x", TEXT_BOX_X), "x"),
        "y": _number(element.get("y", 0), "y"),
        "width": width,
        "font_weight": weight,
        "font_size": size,
        "color": parse_color(str(element.get("color", TEXT_COLOR_HEX))),
        "align": align,
        "line_height": line_height,
        "highlight": highlight,
        "highlight_color": parse_color(
            str(element.get("highlight_color", LABEL_BACKGROUND_HEX))
        ),
        "highlight_radius": radius,
        "highlight_pad_x": pad_x,
        "highlight_pad_y": pad_y,
    }


def _normalize_image(element: dict) -> dict:
    asset_id = str(element.get("asset_id") or "").strip()
    if not asset_id:
        raise ValueError("An image overlay needs an uploaded image.")
    width = _number(element.get("width", 400), "Image width")
    height = _number(element.get("height", 400), "Image height")
    if width < 1 or height < 1 or width > 4000 or height > 4000:
        raise ValueError("Image overlay size is out of range.")
    radius = _number(element.get("corner_radius", 0), "Corner radius")
    if radius < 0 or radius > 2000:
        raise ValueError("Corner radius is out of range.")
    return {
        "type": "image",
        "asset_id": asset_id,
        "x": _number(element.get("x", 0), "x"),
        "y": _number(element.get("y", 0), "y"),
        "width": width,
        "height": height,
        "corner_radius": radius,
    }


def normalize_slide(slide: dict) -> dict:
    if not isinstance(slide, dict):
        raise ValueError("Each slide must be an object.")
    elements = slide.get("elements") or []
    if not isinstance(elements, list):
        raise ValueError("Slide elements must be a list.")
    if len(elements) > MAX_ELEMENTS:
        raise ValueError(f"Use {MAX_ELEMENTS} elements or fewer on a slide.")
    normalized = []
    for element in elements:
        if not isinstance(element, dict):
            raise ValueError("Each element must be an object.")
        kind = str(element.get("type", "text")).strip().lower()
        if kind == "text":
            normalized.append(_normalize_text(element))
        elif kind == "image":
            normalized.append(_normalize_image(element))
        else:
            raise ValueError("Element type must be text or image.")
    background_id = slide.get("background_asset_id") or None
    if background_id is not None:
        background_id = str(background_id).strip() or None
    color_2 = slide.get("background_color_2")
    return {
        "background_asset_id": background_id,
        "background_color": parse_color(str(slide.get("background_color", "#d5e4f5"))),
        "background_color_2": (
            None if not color_2 else parse_color(str(color_2))
        ),
        "elements": normalized,
    }


def _line_layout(element: dict, font: ImageFont.FreeTypeFont) -> list[dict]:
    lines = wrap_text(element["text"], font, element["width"])
    ascent, _descent = font.getmetrics()
    pitch = element["font_size"] * element["line_height"]
    anchor_x = {
        "left": element["x"],
        "right": element["x"] + element["width"],
        "center": element["x"] + element["width"] / 2,
    }[element["align"]]
    hanchor = {"left": "l", "center": "m", "right": "r"}[element["align"]]
    laid = []
    for index, line in enumerate(lines):
        baseline = element["y"] + ascent + index * pitch
        ink = None
        if line:
            bbox = font.getbbox(line)
            text_width = font.getlength(line)
            if element["align"] == "left":
                em_left = element["x"]
            elif element["align"] == "right":
                em_left = element["x"] + element["width"] - text_width
            else:
                em_left = element["x"] + (element["width"] - text_width) / 2
            em_top = baseline - ascent
            ink = (
                em_left + bbox[0],
                em_top + bbox[1],
                em_left + bbox[2],
                em_top + bbox[3],
            )
        laid.append(
            {
                "text": line,
                "baseline": baseline,
                "anchor": (anchor_x, baseline),
                "anchor_name": hanchor + "s",
                "ink": ink,
            }
        )
    return laid


def _draw_text(base: Image.Image, element: dict) -> dict:
    font = load_font(element["font_weight"], element["font_size"])
    laid = _line_layout(element, font)
    inks = [line["ink"] for line in laid if line["ink"] is not None]
    draw = ImageDraw.Draw(base)
    pill = None
    if element["highlight"] and inks:
        left = min(ink[0] for ink in inks) - element["highlight_pad_x"]
        top = min(ink[1] for ink in inks) - element["highlight_pad_y"]
        right = max(ink[2] for ink in inks) + element["highlight_pad_x"]
        bottom = max(ink[3] for ink in inks) + element["highlight_pad_y"]
        pill = (left, top, right, bottom)
        draw.rounded_rectangle(
            pill,
            radius=element["highlight_radius"],
            fill=element["highlight_color"],
        )
    fill = element["color"]
    for line in laid:
        if not line["text"]:
            continue
        draw.text(
            line["anchor"],
            line["text"],
            font=font,
            fill=fill,
            anchor=line["anchor_name"],
        )
    return {"type": "text", "lines": [line["text"] for line in laid], "pill": pill}


def _draw_image(
    base: Image.Image,
    element: dict,
    assets: Mapping[str, bytes | Path],
) -> None:
    image = _asset_image(element["asset_id"], assets)
    width = max(1, int(round(element["width"])))
    height = max(1, int(round(element["height"])))
    resized = image.resize((width, height), Image.Resampling.LANCZOS)
    radius = element["corner_radius"]
    if radius > 0:
        mask = _rounded_mask(width, height, radius)
        alpha = resized.getchannel("A")
        resized.putalpha(Image.composite(alpha, Image.new("L", (width, height), 0), mask))
    _paste_rgba(base, resized, int(round(element["x"])), int(round(element["y"])))


def render_slide(
    slide: dict,
    assets: Mapping[str, bytes | Path] | None = None,
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
) -> Image.Image:
    """Render one slide. Preview and export both call this."""
    if width < 16 or height < 16:
        raise ValueError("Slide size is too small.")
    spec = normalize_slide(slide)
    library = assets or {}
    if spec["background_color_2"] is None:
        top = spec["background_color"]
        bottom = spec["background_color"]
    else:
        top = spec["background_color"]
        bottom = spec["background_color_2"]
    canvas = vertical_gradient(width, height, top, bottom)
    if spec["background_asset_id"]:
        photo = cover_crop(
            _asset_image(spec["background_asset_id"], library),
            width,
            height,
        )
        canvas.alpha_composite(photo)
    for element in spec["elements"]:
        if element["type"] == "text":
            _draw_text(canvas, element)
        else:
            _draw_image(canvas, element, library)
    return canvas.convert("RGB")


def slide_png_bytes(slide: dict, assets: Mapping[str, bytes | Path] | None = None) -> bytes:
    image = render_slide(slide, assets)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def export_text_slideshow(
    slides: list[dict],
    assets: Mapping[str, bytes | Path] | None = None,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Write numbered PNGs and a zip. Returns the folder and the zip path."""
    if not slides:
        raise ValueError("Add at least one slide.")
    if len(slides) > MAX_SLIDES:
        raise ValueError(f"Use {MAX_SLIDES} slides or fewer.")
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_dir = OUTPUT_DIR / f"{timestamp}_textslides"
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for index, slide in enumerate(slides, start=1):
        image = render_slide(slide, assets)
        path = output_dir / f"{index:02d}.png"
        image.save(path, format="PNG")
        written.append(path)
    zip_path = output_dir.parent / f"{output_dir.name}.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in written:
            archive.write(path, arcname=path.name)
    return output_dir, zip_path


def style_payload() -> dict[str, object]:
    weights = [
        {
            "id": key,
            "name": FONT_NAMES[key],
            "cssWeight": FONT_CSS_WEIGHT[key],
        }
        for key in available_font_weights()
    ]
    shared = {
        "align": "center",
        "color": TEXT_COLOR_HEX,
        "lineHeight": LINE_HEIGHT,
        "x": TEXT_BOX_X,
        "width": TEXT_BOX_WIDTH,
        "highlight": False,
        "highlightColor": LABEL_BACKGROUND_HEX,
        "highlightRadius": LABEL_RADIUS,
        "highlightPadX": LABEL_PAD_X,
        "highlightPadY": LABEL_PAD_Y,
    }
    return {
        "width": CANVAS_WIDTH,
        "height": CANVAS_HEIGHT,
        "textColor": TEXT_COLOR_HEX,
        "lineHeight": LINE_HEIGHT,
        "fonts": weights,
        "title": {
            **shared,
            "fontWeight": TITLE_FONT_WEIGHT,
            "fontSize": TITLE_FONT_SIZE,
            "y": title_box_y(),
        },
        "body": {
            **shared,
            "fontWeight": BODY_FONT_WEIGHT,
            "fontSize": BODY_FONT_SIZE,
            "y": body_box_y(),
        },
        "coverTitle": {
            **shared,
            "fontWeight": COVER_TITLE_WEIGHT,
            "fontSize": COVER_TITLE_SIZE,
            "y": cover_title_box_y(),
        },
        "label": {
            "fontWeight": LABEL_FONT_WEIGHT,
            "fontSize": LABEL_FONT_SIZE,
            "color": LABEL_COLOR_HEX,
            "align": "left",
            "lineHeight": LINE_HEIGHT,
            "x": LABEL_X,
            "y": LABEL_Y,
            "width": LABEL_WIDTH,
            "highlight": True,
            "highlightColor": LABEL_BACKGROUND_HEX,
            "highlightRadius": LABEL_RADIUS,
            "highlightPadX": LABEL_PAD_X,
            "highlightPadY": LABEL_PAD_Y,
            "text": LABEL_TEXT,
        },
        "screenshot": {
            "x": SCREENSHOT_X,
            "y": SCREENSHOT_Y,
            "width": SCREENSHOT_WIDTH,
            "height": SCREENSHOT_HEIGHT,
            "cornerRadius": SCREENSHOT_RADIUS,
        },
    }


def default_title_element(text: str = "1. give them space") -> dict:
    style = style_payload()["title"]
    return {"type": "text", "text": text, **_style_to_element(style)}


def default_body_element(text: str) -> dict:
    style = style_payload()["body"]
    return {"type": "text", "text": text, **_style_to_element(style)}


def _style_to_element(style: dict) -> dict:
    return {
        "x": style["x"],
        "y": style["y"],
        "width": style["width"],
        "font_weight": style["fontWeight"],
        "font_size": style["fontSize"],
        "color": style["color"],
        "align": style["align"],
        "line_height": style["lineHeight"],
        "highlight": style["highlight"],
        "highlight_color": style["highlightColor"],
        "highlight_radius": style["highlightRadius"],
        "highlight_pad_x": style["highlightPadX"],
        "highlight_pad_y": style["highlightPadY"],
    }
