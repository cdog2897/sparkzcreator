#!/usr/bin/env python3
"""Fast-cut photo slideshows with a static text overlay.

Timing and text layout are measured from the reference TikTok screen
recording (960x576, 24fps). The export is always 1080x1920. Photos are
center-cropped into a 16:9, 1:1, or 9:16 area and that area is fitted and
centered on the vertical frame, with black bars when it does not fill it.
The hook text stays inside the photo area. The photos are stills: hard
cuts, no zoom.
An optional demo video plays after the last photo, cover-cropped to the
full 1080x1920 frame. The hook text is drawn on the photos only, so it is
gone when the demo starts.
Each photo holds for 10 frames at 24fps, which is 0.417 seconds. Twenty
photos end at 8.333 seconds, the hard cut into the TikTok end card.
The yellow is TikTok's in-app yellow swatch, #F2CD46.
"""

from __future__ import annotations

import argparse
import io
import math
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image, ImageFont, ImageOps, UnidentifiedImageError

from generate_video import (
    OUTPUT_DIR,
    ROOT,
    SDR_COLOR_PARAMS,
    STRIP_AMBIENT_SIDE_DATA,
    ffmpeg_exe,
    natural_sort_key,
    parse_duration_from_ffmpeg,
)


SLIDESHOW_FPS = 24
DEFAULT_IMAGE_SECONDS = 0.417
MIN_IMAGE_SECONDS = 0.05
MAX_IMAGE_SECONDS = 5.0
MAX_IMAGES = 80
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_TEXT_CHARS = 800

# Every Fast Cut export is a vertical 9:16 frame.
SLIDESHOW_WIDTH = 1080
SLIDESHOW_HEIGHT = 1920
# Photo cuts are composed at one of these sizes, then fitted and centered
# in the 1080x1920 frame. 16:9 is 1080x608. 1:1 is 1080x1080. 9:16 fills it.
DEFAULT_PHOTO_FORMAT = "16:9"
PHOTO_FORMATS: dict[str, tuple[int, int]] = {
    "16:9": (1080, 608),
    "1:1": (1080, 1080),
    "9:16": (1080, 1920),
}

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

# Optional clip played after the last photo. The hook text is drawn on the
# photos only, then this clip is concatenated at the same size, fps, and
# pixel format.
DEMO_EXTENSIONS = {".mp4", ".mov"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
MAX_DEMO_BYTES = 200 * 1024 * 1024
MAX_DEMO_SECONDS = 10 * 60
AUDIO_FADE_SECONDS = 0.25
HOOK_FILENAMES = ("hook.txt", "text.txt")

ProgressCallback = Callable[[str], None]
_STREAM_KIND = re.compile(
    r"Stream #\d+:\d+(?:\[[^\]]+\])?(?:\([^)]*\))?:\s*(Video|Audio):"
)


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


def demo_play_seconds(duration_seconds: float, fps: int = SLIDESHOW_FPS) -> float:
    """Frame-snapped length of a demo clip at the slideshow frame rate."""
    return slide_frame_count(duration_seconds, fps) / fps


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


def fit_pad_filter(width: int, height: int) -> str:
    """Scale to fit, then center on black. Used to place a photo cut in the frame."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,"
        "setsar=1"
    )


def parse_photo_format(value: str) -> str:
    cleaned = value.strip()
    if cleaned not in PHOTO_FORMATS:
        allowed = ", ".join(PHOTO_FORMATS)
        raise ValueError(f"Photo format must be one of: {allowed}.")
    return cleaned


def photo_format_size(photo_format: str = DEFAULT_PHOTO_FORMAT) -> tuple[int, int]:
    return PHOTO_FORMATS[parse_photo_format(photo_format)]


def photo_content_box(photo_format: str = DEFAULT_PHOTO_FORMAT) -> tuple[int, int, int, int]:
    """x, y, width, height of the photo area inside the 1080x1920 frame."""
    width, height = photo_format_size(photo_format)
    x = (SLIDESHOW_WIDTH - width) // 2
    y = (SLIDESHOW_HEIGHT - height) // 2
    return x, y, width, height


def photo_format_payload() -> list[dict[str, int | str]]:
    return [
        {"id": name, "width": width, "height": height}
        for name, (width, height) in PHOTO_FORMATS.items()
    ]


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


def _probe_stderr(path: Path) -> str:
    completed = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stderr or ""


def probe_stream_kinds(path: Path) -> tuple[bool, bool]:
    """Return (has_video, has_audio) from ffmpeg's stream listing."""
    kinds = set(_STREAM_KIND.findall(_probe_stderr(path)))
    return "Video" in kinds, "Audio" in kinds


class DemoVideo:
    """A demo clip normalized to the slideshow frame rate."""

    def __init__(self, path: Path, duration: float, frames: int, has_audio: bool, fps: int) -> None:
        self.path = path
        self.duration = duration
        self.frames = frames
        self.has_audio = has_audio
        self.fps = fps

    @property
    def play_seconds(self) -> float:
        return self.frames / self.fps


def inspect_demo_video(path: Path, fps: int = SLIDESHOW_FPS) -> DemoVideo:
    """Reject anything that is not a short mp4 or mov with a video stream."""
    if not path.is_file():
        raise FileNotFoundError(f"Missing demo video: {path}")
    if path.suffix.lower() not in DEMO_EXTENSIONS:
        raise ValueError("End demo video must be an .mp4 or .mov file.")
    size = path.stat().st_size
    if size <= 0:
        raise ValueError("Demo video is empty.")
    if size > MAX_DEMO_BYTES:
        raise ValueError("Demo video must be under 200 MB.")
    if fps <= 0:
        raise ValueError("Frame rate must be positive.")

    stderr = _probe_stderr(path)
    kinds = set(_STREAM_KIND.findall(stderr))
    if "Video" not in kinds:
        raise ValueError("End demo file has no video stream.")
    try:
        duration = parse_duration_from_ffmpeg(stderr, path)
    except RuntimeError as error:
        raise ValueError("Could not read the demo video.") from error
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Demo video has no duration.")
    if duration > MAX_DEMO_SECONDS:
        raise ValueError("Demo video must be 10 minutes or shorter.")
    return DemoVideo(
        path=path,
        duration=duration,
        frames=slide_frame_count(duration, fps),
        has_audio="Audio" in kinds,
        fps=fps,
    )


def _audio_fade_suffix(total_seconds: float) -> str:
    fade = min(AUDIO_FADE_SECONDS, total_seconds * 0.25)
    if fade < 0.02:
        return ""
    start = max(0.0, total_seconds - fade)
    return f",afade=t=out:st={start:.6f}:d={fade:.6f}"


_STEREO = "aformat=sample_rates=44100:channel_layouts=stereo,"


def demo_audio_filters(
    *,
    music_index: int | None,
    demo_index: int,
    demo_has_audio: bool,
    keep_demo_audio: bool,
    photo_seconds: float,
    demo_seconds: float,
    total_seconds: float,
) -> list[str]:
    """Build filters that end in [aout].

    An empty list means the output should stay silent. Music, when present,
    runs for the whole video and fades at the end. Demo audio, when requested
    and actually present, is delayed until the demo starts and mixed under it.
    """
    mix_demo = keep_demo_audio and demo_has_audio
    if music_index is None and not mix_demo:
        return []

    fade = _audio_fade_suffix(total_seconds)
    filters: list[str] = []
    if music_index is not None:
        filters.append(
            f"[{music_index}:a:0]{_STEREO}"
            f"atrim=0:{total_seconds:.6f},asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={total_seconds:.6f}[music]"
        )
    if mix_demo:
        delay_ms = max(0, int(round(photo_seconds * 1000)))
        filters.append(
            f"[{demo_index}:a:0]{_STEREO}"
            f"atrim=0:{demo_seconds:.6f},asetpts=PTS-STARTPTS,"
            f"apad=whole_dur={demo_seconds:.6f},"
            f"adelay={delay_ms}|{delay_ms}[demoaudio]"
        )
    if music_index is not None and mix_demo:
        filters.append(
            "[music][demoaudio]amix=inputs=2:duration=longest:"
            f"dropout_transition=0:normalize=0{fade}[aout]"
        )
    elif music_index is not None:
        filters.append(f"[music]anull{fade}[aout]")
    else:
        filters.append(f"[demoaudio]anull{fade}[aout]")
    return filters


def render_slideshow(
    image_paths: list[Path],
    text: str = "",
    image_seconds: float = DEFAULT_IMAGE_SECONDS,
    music_path: Path | None = None,
    fps: int = SLIDESHOW_FPS,
    output_path: Path | None = None,
    progress_callback: ProgressCallback | None = None,
    demo_path: Path | None = None,
    keep_demo_audio: bool = False,
    preset: str = "medium",
    photo_format: str = DEFAULT_PHOTO_FORMAT,
) -> Path:
    """Render a hard-cut slideshow. Video length is images × frame-snapped duration.

    Photos are cropped to the chosen shape and centered in a 1080x1920 frame.
    An optional demo then fills that frame. Hook text is burned into the
    photo area before the join, so it is gone as soon as the demo starts.
    """
    if not image_paths:
        raise ValueError("Add at least one image.")
    if len(image_paths) > MAX_IMAGES:
        raise ValueError(f"Use {MAX_IMAGES} images or fewer.")
    if not FONT_PATH.is_file():
        raise FileNotFoundError(f"Missing slideshow font: {FONT_PATH}")
    photo_format = parse_photo_format(photo_format)
    photo_width, photo_height = photo_format_size(photo_format)
    width, height = SLIDESHOW_WIDTH, SLIDESHOW_HEIGHT

    image_seconds = validate_image_seconds(image_seconds)
    frames = slide_frame_count(image_seconds, fps)
    slide_seconds = frames / fps
    photo_seconds = len(image_paths) * slide_seconds
    demo: DemoVideo | None = None
    if demo_path is not None:
        demo = inspect_demo_video(demo_path, fps)
    demo_seconds = demo.play_seconds if demo is not None else 0.0
    duration = photo_seconds + demo_seconds
    overlay = text.replace("\r\n", "\n").replace("\r", "\n")
    if len(overlay) > MAX_TEXT_CHARS:
        raise ValueError(f"Text must be {MAX_TEXT_CHARS} characters or fewer.")

    def emit(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    font_size, line_spacing = fitted_text_metrics(overlay, photo_width)
    emit(
        f"Photos: {len(image_paths)} × {frames} frames "
        f"({slide_seconds:.3f}s) at {fps}fps"
    )
    emit(
        f"Photo format: {photo_format} ({photo_width}x{photo_height} "
        f"inside {width}x{height})"
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
    if demo is not None:
        emit(f"Demo: {demo.path.name} ({demo.play_seconds:.3f}s after the photos)")
        if keep_demo_audio and demo.has_audio:
            emit("Demo audio: mixed in during the demo")
        elif keep_demo_audio:
            emit("Demo audio: requested, but this file has no audio stream")
        else:
            emit("Demo audio: off")

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
        next_input = len(normalized)
        demo_index: int | None = None
        if demo is not None:
            demo_index = next_input
            next_input += 1
            command.extend(["-i", str(demo.path)])
        music_index: int | None = None
        if music_path is not None:
            music_index = next_input
            command.extend(["-i", str(music_path)])

        text_x, text_y = text_draw_position()
        filters: list[str] = []
        for index in range(len(normalized)):
            filters.append(
                f"[{index}:v]{cover_crop_filter(photo_width, photo_height)},"
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
        # Text is burned into the photo-sized frame, then that frame is fitted
        # and centered. A demo is cover-cropped to the full vertical frame
        # and appended, so the hook is gone the moment the demo starts.
        placed = (
            f"[{current}]{fit_pad_filter(width, height)},"
            f"{SDR_COLOR_PARAMS},{STRIP_AMBIENT_SIDE_DATA},format=yuv420p"
        )
        if demo is None:
            filters.append(f"{placed}[vout]")
            audio_filters: list[str] = []
        else:
            filters.append(f"{placed},settb=1/{fps}[photos]")
            filters.append(
                f"[{demo_index}:v]{cover_crop_filter(width, height)},"
                f"fps={fps},format=yuv420p,{SDR_COLOR_PARAMS},{STRIP_AMBIENT_SIDE_DATA},"
                f"trim=end_frame={demo.frames},setpts=PTS-STARTPTS,settb=1/{fps}[demov]"
            )
            filters.append(
                f"[photos][demov]concat=n=2:v=1:a=0,format=yuv420p[vout]"
            )
            audio_filters = demo_audio_filters(
                music_index=music_index,
                demo_index=demo_index if demo_index is not None else 0,
                demo_has_audio=demo.has_audio,
                keep_demo_audio=keep_demo_audio,
                photo_seconds=photo_seconds,
                demo_seconds=demo.play_seconds,
                total_seconds=duration,
            )
        filters.extend(audio_filters)

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
                preset,
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
        if audio_filters:
            command.extend(
                [
                    "-map",
                    "[aout]",
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
        elif music_index is not None:
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


def slideshow_images(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    paths = [
        path
        for path in folder.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in IMAGE_EXTENSIONS
    ]
    return sorted(paths, key=natural_sort_key)


def slideshow_image_sets(path: Path) -> list[Path]:
    """One folder of photos, or a folder whose children are those folders."""
    if not path.exists():
        raise ValueError(f"Nothing at {path}.")
    if not path.is_dir():
        raise ValueError(f"{path} is not a folder of photos.")
    if slideshow_images(path):
        return [path]
    children = [
        child
        for child in sorted(path.iterdir(), key=natural_sort_key)
        if child.is_dir() and slideshow_images(child)
    ]
    if not children:
        raise ValueError(
            f"No photos in {path}. Add images, or a folder of image folders."
        )
    return children


def read_hook_text(folder: Path, fallback: str) -> str:
    """Per-set overlay. hook.txt or text.txt wins over the shared fallback."""
    for name in HOOK_FILENAMES:
        candidate = folder / name
        if candidate.is_file():
            return (
                candidate.read_text(encoding="utf-8")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .rstrip("\n")
            )
    return fallback


def render_fast_cuts(
    path: Path,
    *,
    text: str = "",
    demo_path: Path | None = None,
    keep_demo_audio: bool = False,
    music_path: Path | None = None,
    image_seconds: float = DEFAULT_IMAGE_SECONDS,
    output_path: Path | None = None,
    output_dir: Path | None = None,
    photo_format: str = DEFAULT_PHOTO_FORMAT,
    preset: str = "medium",
) -> list[Path]:
    """Render one photo folder, or every photo folder inside a batch folder.

    The same demo, when given, is appended to every set. Each set can carry
    its own hook in hook.txt or text.txt.
    """
    sets = slideshow_image_sets(path)
    if output_path is not None and len(sets) != 1:
        raise ValueError("--output applies to a single folder of photos, not a batch.")
    if demo_path is not None:
        inspect_demo_video(demo_path)
    if music_path is not None and not music_path.is_file():
        raise FileNotFoundError(f"Missing music file: {music_path}")

    rendered: list[Path] = []
    for folder in sets:
        images = slideshow_images(folder)
        hook = read_hook_text(folder, text)
        if output_path is not None:
            destination: Path | None = output_path
        elif len(sets) > 1 or output_dir is not None:
            directory = output_dir or (OUTPUT_DIR / "fastcut")
            directory.mkdir(parents=True, exist_ok=True)
            destination = directory / f"{folder.name}.mp4"
        else:
            destination = None
        rendered.append(
            render_slideshow(
                images,
                text=hook,
                image_seconds=image_seconds,
                music_path=music_path,
                output_path=destination,
                photo_format=photo_format,
                demo_path=demo_path,
                keep_demo_audio=keep_demo_audio,
                preset=preset,
            )
        )
    return rendered


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slideshow.py",
        description=(
            "Render Fast Cut slideshows. Pass one folder of photos, or a folder "
            "of those folders, to end every cut in the same demo video."
        ),
    )
    parser.add_argument(
        "path",
        type=Path,
        help="A folder of photos, or a folder of photo folders.",
    )
    parser.add_argument(
        "--text",
        default="",
        help="Hook text. hook.txt or text.txt inside a set overrides this.",
    )
    parser.add_argument(
        "--demo",
        type=Path,
        default=None,
        help="Optional mp4 or mov played in full after the last photo cut.",
    )
    parser.add_argument(
        "--keep-demo-audio",
        action="store_true",
        help="Mix the demo file's own audio in during the demo. Default is off.",
    )
    parser.add_argument(
        "--music",
        type=Path,
        default=None,
        help="Optional music file. It plays under the photos and the demo, then ends with the video.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=DEFAULT_IMAGE_SECONDS,
        help=f"Seconds per photo before frame snapping (default {DEFAULT_IMAGE_SECONDS}).",
    )
    parser.add_argument(
        "--format",
        default=DEFAULT_PHOTO_FORMAT,
        choices=tuple(PHOTO_FORMATS),
        help=(
            "Photo cut shape inside the 1080x1920 frame. "
            "16:9 and 1:1 are centered with black bars. 9:16 fills the frame. "
            f"Default {DEFAULT_PHOTO_FORMAT}. The demo is always full frame."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="MP4 path for a single photo folder.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory for a batch. One mp4 per photo folder. Default is outputs/fastcut/.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.keep_demo_audio and args.demo is None:
            raise ValueError("--keep-demo-audio needs --demo.")
        results = render_fast_cuts(
            args.path,
            text=args.text,
            demo_path=args.demo,
            keep_demo_audio=args.keep_demo_audio,
            music_path=args.music,
            image_seconds=args.seconds,
            output_path=args.output,
            output_dir=args.output_dir,
            photo_format=args.format,
        )
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Could not render: {error}", file=sys.stderr)
        return 1

    for path in results:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
