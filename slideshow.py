#!/usr/bin/env python3
"""Fast-cut photo slideshows with a static text overlay.

Timing and text layout are measured from the reference TikTok screen
recording (960x576, 24fps). The default canvas is 1920x1080, the same
16:9 frame at twice the reference size. Photos cover that frame and are
center-cropped. The photos are stills: hard cuts, no zoom.
Each photo holds for 10 frames at 24fps, which is 0.417 seconds. Twenty
photos end at 8.333 seconds, the hard cut into the TikTok end card.
The yellow is TikTok's in-app yellow swatch, #F2CD46.
"""

from __future__ import annotations

import io
import math
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image, ImageFont, ImageOps, UnidentifiedImageError

from generate_video import (
    OUTPUT_DIR,
    ROOT,
    SDR_COLOR_PARAMS,
    STRIP_AMBIENT_SIDE_DATA,
    ffmpeg_exe,
)


SLIDESHOW_FPS = 24
DEFAULT_IMAGE_SECONDS = 0.417
MIN_IMAGE_SECONDS = 0.05
MAX_IMAGE_SECONDS = 5.0
MAX_IMAGES = 80
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 800

# 16:9, twice the 960x576 reference on both axes.
SLIDESHOW_WIDTH = 1920
SLIDESHOW_HEIGHT = 1080

# Glyph fit against the 960x576 reference frame, using ffmpeg drawtext.
# Size scales with frame width, so the caption keeps the same share of the
# width. The reference ink is centered (measured center at half the height),
# and the block stays centered on a 16:9 frame.
REFERENCE_FRAME_WIDTH = 960
REFERENCE_FRAME_HEIGHT = 576
REFERENCE_FONT_SIZE = 35
REFERENCE_LINE_SPACING = -2
# Distance between the tops of the two reference lines at that font size.
REFERENCE_LINE_TOP_DELTA = 42

TEXT_COLOR = "0xF2CD46"
TEXT_COLOR_HEX = "#F2CD46"

FONT_PATH = ROOT / "fonts" / "TikTokSans" / "TikTokSans-SemiBold.ttf"
FONT_LABEL = "TikTok Sans SemiBold"

ProgressCallback = Callable[[str], None]


def slide_frame_count(duration_seconds: float, fps: int = SLIDESHOW_FPS) -> int:
    """Nearest whole-frame hold, so a cut never lands between frames.

    Half frames round away from zero (floor(x + 0.5)), matching the
    preview's Math.round for positive durations. 0.417s at 24fps is
    10.008 frames, which is exactly 10.
    """
    if duration_seconds <= 0 or not math.isfinite(duration_seconds):
        raise ValueError("Image duration must be positive.")
    if fps <= 0:
        raise ValueError("Frame rate must be positive.")
    frames = int(math.floor(duration_seconds * fps + 0.5))
    return max(1, frames)


def quantized_slide_seconds(
    duration_seconds: float,
    fps: int = SLIDESHOW_FPS,
) -> float:
    return slide_frame_count(duration_seconds, fps) / fps


def slideshow_duration_seconds(
    image_count: int,
    duration_seconds: float,
    fps: int = SLIDESHOW_FPS,
) -> float:
    if image_count < 1:
        raise ValueError("Add at least one image.")
    return image_count * quantized_slide_seconds(duration_seconds, fps)


def slideshow_plan(
    image_count: int,
    duration_seconds: float = DEFAULT_IMAGE_SECONDS,
    fps: int = SLIDESHOW_FPS,
) -> dict[str, float | int]:
    frames = slide_frame_count(duration_seconds, fps)
    slide_seconds = frames / fps
    return {
        "fps": fps,
        "imageCount": image_count,
        "framesPerImage": frames,
        "slideSeconds": slide_seconds,
        "durationSeconds": image_count * slide_seconds,
    }


def validate_image_seconds(duration_seconds: float) -> float:
    if (
        not math.isfinite(duration_seconds)
        or duration_seconds < MIN_IMAGE_SECONDS
        or duration_seconds > MAX_IMAGE_SECONDS
    ):
        raise ValueError(
            "Image duration must be between "
            f"{MIN_IMAGE_SECONDS:g} and {MAX_IMAGE_SECONDS:g} seconds."
        )
    return duration_seconds


def slideshow_text_metrics(width: int) -> tuple[int, int]:
    """Font size and drawtext line_spacing scaled from the 960px reference."""
    if width < 16:
        raise ValueError("Slideshow width is too small.")
    font_size = max(1, round(REFERENCE_FONT_SIZE * width / REFERENCE_FRAME_WIDTH))
    line_spacing = int(round(REFERENCE_LINE_SPACING * font_size / REFERENCE_FONT_SIZE))
    return font_size, line_spacing


def fitted_text_metrics(text: str, width: int) -> tuple[int, int]:
    """Keep the reference size, and shrink only when a line would leave the frame."""
    font_size, line_spacing = slideshow_text_metrics(width)
    cleaned = text.strip("\n")
    if not cleaned.strip() or not FONT_PATH.is_file():
        return font_size, line_spacing

    max_line_width = max(1, width - 2 * max(16, round(width * 0.04)))
    while font_size > 8:
        font = ImageFont.truetype(str(FONT_PATH), font_size)
        widest = 0
        for line in cleaned.splitlines():
            if not line:
                continue
            bbox = font.getbbox(line)
            widest = max(widest, bbox[2] - bbox[0])
        if widest <= max_line_width:
            break
        font_size -= 1
    line_spacing = int(round(REFERENCE_LINE_SPACING * font_size / REFERENCE_FONT_SIZE))
    return font_size, line_spacing


def cover_crop_filter(width: int, height: int) -> str:
    """Scale to cover, then center-crop. No bars, no zoom, and no pan."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2,"
        "setsar=1"
    )


def text_draw_position() -> tuple[str, str]:
    """Horizontal and vertical center, the reference caption's place."""
    return "(w-text_w)/2", "(h-text_h)/2"


def filter_path(path: Path) -> str:
    value = path.resolve().as_posix()
    value = value.replace("\\", "\\\\").replace(":", "\\:").replace("'", r"\'")
    return f"'{value}'"


def read_image(source: Path | bytes) -> Image.Image:
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
            if image.mode in {"RGBA", "LA"} or (
                image.mode == "P" and "transparency" in image.info
            ):
                rgba = image.convert("RGBA")
                flattened = Image.new("RGB", rgba.size, (0, 0, 0))
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                return flattened
            return image.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError) as error:
        if isinstance(error, ValueError) and "image" in str(error).lower():
            raise
        raise ValueError("One of the uploads is not a readable image.") from error


def normalize_image(source: Path | bytes, destination: Path) -> Path:
    image = read_image(source)
    destination.parent.mkdir(parents=True, exist_ok=True)
    image.save(destination, format="PNG")
    return destination


def validate_image_payloads(payloads: list[bytes]) -> None:
    if not payloads:
        raise ValueError("Add at least one image.")
    if len(payloads) > MAX_IMAGES:
        raise ValueError(f"Use {MAX_IMAGES} images or fewer.")
    for content in payloads:
        read_image(content)


def run_slideshow_ffmpeg(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        tail = detail[-2000:]
        raise RuntimeError(tail or f"ffmpeg exited with status {completed.returncode}.")


def render_slideshow(
    image_paths: list[Path],
    text: str = "",
    image_seconds: float = DEFAULT_IMAGE_SECONDS,
    music_path: Path | None = None,
    width: int = SLIDESHOW_WIDTH,
    height: int = SLIDESHOW_HEIGHT,
    fps: int = SLIDESHOW_FPS,
    output_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    """Render a hard-cut slideshow. Video length is images × frame-snapped duration."""
    if not image_paths:
        raise ValueError("Add at least one image.")
    if len(image_paths) > MAX_IMAGES:
        raise ValueError(f"Use {MAX_IMAGES} images or fewer.")
    if width < 16 or height < 16:
        raise ValueError("Slideshow size is too small.")
    if not FONT_PATH.is_file():
        raise FileNotFoundError(f"Missing slideshow font: {FONT_PATH}")

    image_seconds = validate_image_seconds(image_seconds)
    frames = slide_frame_count(image_seconds, fps)
    slide_seconds = frames / fps
    duration = len(image_paths) * slide_seconds
    overlay = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(overlay) > MAX_TEXT_CHARS:
        raise ValueError(f"Text must be {MAX_TEXT_CHARS} characters or fewer.")

    def emit(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    font_size, line_spacing = fitted_text_metrics(overlay, width)
    emit(
        f"Photos: {len(image_paths)} × {frames} frames "
        f"({slide_seconds:.3f}s) at {fps}fps"
    )
    emit(f"Output length: {duration:.3f}s ({width}x{height})")
    if overlay.strip():
        emit(f"Text font: {FONT_LABEL}, {font_size}px, color {TEXT_COLOR_HEX}")
    else:
        emit("Text: none")
    if music_path is not None:
        emit(f"Music: {music_path.name}")
    else:
        emit("Music: none")

    OUTPUT_DIR.mkdir(exist_ok=True)
    if output_path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_path = OUTPUT_DIR / f"slideshow_{width}x{height}_{timestamp}.mp4"
    else:
        output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=ROOT) as temp_name:
        temp_dir = Path(temp_name)
        normalized: list[Path] = []
        for index, source in enumerate(image_paths, start=1):
            destination = temp_dir / f"image_{index:03d}.png"
            emit(f"Image {index}: {Path(source).name}")
            normalize_image(source, destination)
            normalized.append(destination)

        text_path: Path | None = None
        if overlay.strip():
            text_path = temp_dir / "overlay.txt"
            text_path.write_text(overlay, encoding="utf-8")

        # Feed a couple of extra frames, then trim to an exact frame count
        # so the hold cannot come out short from duration rounding.
        input_seconds = (frames + 2) / fps
        command = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"]
        for image_path in normalized:
            command.extend(
                [
                    "-loop",
                    "1",
                    "-framerate",
                    str(fps),
                    "-t",
                    f"{input_seconds:.6f}",
                    "-i",
                    str(image_path),
                ]
            )
        music_index: int | None = None
        if music_path is not None:
            music_index = len(normalized)
            command.extend(["-i", str(music_path)])

        text_x, text_y = text_draw_position()
        filters: list[str] = []
        for index in range(len(normalized)):
            filters.append(
                f"[{index}:v]{cover_crop_filter(width, height)},"
                f"fps={fps},format=yuv420p,"
                f"trim=end_frame={frames},setpts=PTS-STARTPTS[v{index}]"
            )
        concat_inputs = "".join(f"[v{index}]" for index in range(len(normalized)))
        filters.append(
            f"{concat_inputs}concat=n={len(normalized)}:v=1:a=0,setpts=PTS-STARTPTS[base]"
        )
        if text_path is not None:
            filters.append(
                "[base]"
                f"drawtext=fontfile={filter_path(FONT_PATH)}:"
                f"textfile={filter_path(text_path)}:"
                f"fontsize={font_size}:"
                f"line_spacing={line_spacing}:"
                f"fontcolor={TEXT_COLOR}:"
                "text_align=C:"
                f"x={text_x}:"
                f"y={text_y}[with_text]"
            )
            current = "with_text"
        else:
            current = "base"
        filters.append(
            f"[{current}]{SDR_COLOR_PARAMS},{STRIP_AMBIENT_SIDE_DATA},format=yuv420p[vout]"
        )

        command.extend(
            [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[vout]",
                "-t",
                f"{duration:.6f}",
                "-r",
                str(fps),
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-c:v",
                "libx264",
                "-preset",
                "medium",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-colorspace",
                "bt709",
                "-metadata:s:v:0",
                "handler_name=VideoHandler",
            ]
        )
        if music_index is not None:
            command.extend(
                [
                    "-map",
                    f"{music_index}:a:0",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    "-metadata:s:a:0",
                    "handler_name=SoundHandler",
                ]
            )
        else:
            command.append("-an")
        command.extend(["-movflags", "+faststart", str(output_path)])
        emit(f"Rendering: {output_path.name}")
        run_slideshow_ffmpeg(command)

    return output_path
