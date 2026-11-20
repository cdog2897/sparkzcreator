#!/usr/bin/env python3
"""Render a Text Slideshow deck as a 1080x1920 Instagram Reel.

The picture is the same Pillow render as the PNG export. ffmpeg turns those
stills into an H.264 MP4: yuv420p, 30fps, AAC, and the moov atom at the front.
Cuts are hard unless a crossfade is requested. Audio is optional; with no
file, the video still carries a silent AAC track. A shorter track is looped,
a longer one is trimmed, and either way the end fades out.

A deck is a folder with ``deck.json`` (and any images it names) or a folder
of numbered PNG slides (``01.png``, ``02.png``, …) such as an existing export.
``video.mp4`` is written in that deck's output folder: ``output/video.mp4``
for a JSON deck, or beside the PNGs when the folder is already an export.

Batch example::

    python3 text_slideshow_video.py path/to/decks
    python3 text_slideshow_video.py path/to/one-deck --seconds 2.75 --first-seconds 4
    python3 text_slideshow_video.py path/to/one-deck --audio song.mp3 --fade 0.35
    python3 text_slideshow_video.py path/to/decks --audio-url "https://www.instagram.com/reel/XXXX/"
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Mapping, Sequence

import imageio_ffmpeg

from generate_video import (
    AUDIO_EXTENSIONS,
    OUTPUT_DIR,
    SDR_COLOR_PARAMS,
    natural_sort_key,
)
from text_slideshow import CANVAS_HEIGHT, CANVAS_WIDTH, MAX_SLIDES, render_slide


REEL_WIDTH = CANVAS_WIDTH
REEL_HEIGHT = CANVAS_HEIGHT
REEL_FPS = 30
VIDEO_FILENAME = "video.mp4"

DEFAULT_SLIDE_SECONDS = 2.75
MIN_SLIDE_SECONDS = 0.5
MAX_SLIDE_SECONDS = 20.0
MAX_CROSSFADE_SECONDS = 1.5
AUDIO_FADE_OUT_SECONDS = 0.5
AUDIO_SAMPLE_RATE = 44100
AUDIO_BITRATE = "192k"
VIDEO_CRF = "18"
VIDEO_PRESET = "medium"

_SLIDE_PNG = re.compile(r"^\d+\.png$", re.IGNORECASE)
_DECK_JSON = "deck.json"


@dataclass(frozen=True)
class ReelPlan:
    slide_durations: tuple[float, ...]
    crossfade_seconds: float
    duration: float
    fps: int = REEL_FPS
    width: int = REEL_WIDTH
    height: int = REEL_HEIGHT

    @property
    def slide_frames(self) -> tuple[int, ...]:
        return tuple(int(round(seconds * self.fps)) for seconds in self.slide_durations)

    @property
    def crossfade_frames(self) -> int:
        return int(round(self.crossfade_seconds * self.fps))


@dataclass(frozen=True)
class ReelResult:
    path: Path
    output_dir: Path
    plan: ReelPlan


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number.")
    number = float(value)
    if number != number or number in {float("inf"), float("-inf")}:
        raise ValueError(f"{label} must be a finite number.")
    return number


def validate_slide_seconds(value: object, label: str = "Slide duration") -> float:
    number = _finite_number(value, label)
    if number < MIN_SLIDE_SECONDS or number > MAX_SLIDE_SECONDS:
        raise ValueError(
            f"{label} must be between {MIN_SLIDE_SECONDS:.1f} and "
            f"{MAX_SLIDE_SECONDS:.0f} seconds."
        )
    return number


def frames_for_seconds(seconds: float, fps: int = REEL_FPS) -> int:
    """Round to a whole frame. 2.75s at 30fps is 83 frames (2.7667s)."""
    if fps <= 0:
        raise ValueError("Frame rate must be positive.")
    return max(1, int(math.floor(seconds * fps + 0.5)))


def quantize_seconds(seconds: float, fps: int = REEL_FPS) -> float:
    return frames_for_seconds(seconds, fps) / fps


def plan_reel(
    slide_count: int,
    seconds_per_slide: float = DEFAULT_SLIDE_SECONDS,
    first_slide_seconds: float | None = None,
    crossfade_seconds: float = 0.0,
    fps: int = REEL_FPS,
) -> ReelPlan:
    if slide_count < 1:
        raise ValueError("Add at least one slide.")
    if slide_count > MAX_SLIDES:
        raise ValueError(f"Use {MAX_SLIDES} slides or fewer.")

    per_slide = quantize_seconds(validate_slide_seconds(seconds_per_slide), fps)
    if first_slide_seconds is None:
        first = per_slide
    else:
        first = quantize_seconds(
            validate_slide_seconds(first_slide_seconds, "First slide duration"),
            fps,
        )
    durations = (first,) + (per_slide,) * (slide_count - 1)
    fade = _quantize_crossfade(durations, crossfade_seconds, fps)
    overlaps = fade * (slide_count - 1)
    duration = sum(durations) - overlaps
    return ReelPlan(
        slide_durations=durations,
        crossfade_seconds=fade,
        duration=duration,
        fps=fps,
    )


def _quantize_crossfade(
    durations: Sequence[float],
    requested: object,
    fps: int,
) -> float:
    number = _finite_number(requested, "Crossfade")
    if number < 0:
        raise ValueError("Crossfade must be zero or a positive length.")
    if number == 0 or len(durations) < 2:
        return 0.0
    if number > MAX_CROSSFADE_SECONDS:
        raise ValueError(
            f"Crossfade must be {MAX_CROSSFADE_SECONDS:.1f} seconds or less."
        )
    frames = max(1, frames_for_seconds(number, fps))
    fade = frames / fps
    shortest = min(durations)
    if fade >= shortest - 1e-9:
        raise ValueError("Crossfade must be shorter than every slide.")
    return fade


def audio_fade_out_seconds(
    duration: float,
    requested: float = AUDIO_FADE_OUT_SECONDS,
) -> float:
    if duration <= 0 or requested <= 0:
        return 0.0
    return min(requested, duration / 2)


@lru_cache(maxsize=8)
def _ffmpeg_supports_reel(binary: str) -> bool:
    try:
        completed = subprocess.run(
            [binary, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    listing = completed.stdout
    has_h264 = "libx264" in listing
    has_aac = re.search(r"\baac\b", listing) is not None
    return has_h264 and has_aac and completed.returncode == 0


def resolve_ffmpeg() -> str:
    """Prefer FFMPEG_PATH, then ffmpeg on PATH, then the imageio-ffmpeg binary."""
    candidates: list[str] = []
    override = os.environ.get("FFMPEG_PATH", "").strip()
    if override:
        candidates.append(override)
    found = shutil.which("ffmpeg")
    if found:
        candidates.append(found)
    try:
        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        if Path(candidate).is_file() and _ffmpeg_supports_reel(candidate):
            return candidate

    raise RuntimeError(
        "ffmpeg with libx264 and AAC was not found. Install ffmpeg and make "
        "sure it is on PATH, or set FFMPEG_PATH. Installing Python dependencies "
        "also bundles one through imageio-ffmpeg "
        "(`python3 -m pip install -r requirements.txt`)."
    )


def _run_ffmpeg(command: list[str]) -> None:
    completed = subprocess.run(command, capture_output=True, text=True)
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        tail = detail[-2000:]
        raise RuntimeError(tail or f"ffmpeg exited with status {completed.returncode}.")


def build_reel_command(
    image_paths: Sequence[Path],
    output_path: Path,
    plan: ReelPlan,
    audio_path: Path | None = None,
    *,
    ffmpeg: str | None = None,
    preset: str = VIDEO_PRESET,
) -> list[str]:
    """Assemble the ffmpeg argv. Does not check that the images exist."""
    if len(image_paths) != len(plan.slide_durations):
        raise ValueError("Each slide needs one image.")
    if not image_paths:
        raise ValueError("Add at least one slide.")

    binary = ffmpeg or resolve_ffmpeg()
    frames = plan.slide_frames
    command: list[str] = [binary, "-y", "-hide_banner", "-loglevel", "error"]
    for image_path, frame_count in zip(image_paths, frames, strict=True):
        feed_seconds = (frame_count + 2) / plan.fps
        command.extend(
            [
                "-loop",
                "1",
                "-framerate",
                str(plan.fps),
                "-t",
                f"{feed_seconds:.6f}",
                "-i",
                str(image_path),
            ]
        )

    audio_index = len(image_paths)
    if audio_path is not None:
        command.extend(["-stream_loop", "-1", "-i", str(audio_path)])
    else:
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{plan.duration:.6f}",
                "-i",
                f"anullsrc=channel_layout=stereo:sample_rate={AUDIO_SAMPLE_RATE}",
            ]
        )

    filters = [
        _still_filter(index, frame_count, plan)
        for index, frame_count in enumerate(frames)
    ]
    filters.append(_join_filter(plan))
    filters.append(f"[joined]{SDR_COLOR_PARAMS},format=yuv420p[vout]")
    filters.append(_audio_filter(audio_index, plan, fade=audio_path is not None))

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-map",
            "[aout]",
            "-t",
            f"{plan.duration:.6f}",
            "-r",
            str(plan.fps),
            "-fps_mode",
            "cfr",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-crf",
            VIDEO_CRF,
            "-profile:v",
            "high",
            "-level:v",
            "4.0",
            "-pix_fmt",
            "yuv420p",
            "-tag:v",
            "avc1",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-g",
            str(plan.fps * 2),
            "-metadata:s:v:0",
            "handler_name=VideoHandler",
            "-c:a",
            "aac",
            "-b:a",
            AUDIO_BITRATE,
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-ac",
            "2",
            "-metadata:s:a:0",
            "handler_name=SoundHandler",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )
    return command


def _still_filter(index: int, frame_count: int, plan: ReelPlan) -> str:
    return (
        f"[{index}:v]scale={plan.width}:{plan.height}:force_original_aspect_ratio=increase,"
        f"crop={plan.width}:{plan.height},setsar=1,fps={plan.fps},format=yuv420p,"
        f"trim=end_frame={frame_count},setpts=PTS-STARTPTS[v{index}]"
    )


def _join_filter(plan: ReelPlan) -> str:
    count = len(plan.slide_durations)
    if count == 1:
        return "[v0]setpts=PTS-STARTPTS[joined]"
    if plan.crossfade_frames <= 0:
        inputs = "".join(f"[v{index}]" for index in range(count))
        return f"{inputs}concat=n={count}:v=1:a=0,setpts=PTS-STARTPTS[joined]"

    fade = plan.crossfade_seconds
    lines: list[str] = []
    incoming = "v0"
    elapsed_frames = plan.slide_frames[0]
    for index in range(1, count):
        outgoing = "joined" if index == count - 1 else f"xf{index}"
        offset_frames = elapsed_frames - plan.crossfade_frames
        lines.append(
            f"[{incoming}][v{index}]xfade=transition=fade:"
            f"duration={fade:.6f}:offset={offset_frames / plan.fps:.6f}[{outgoing}]"
        )
        incoming = outgoing
        elapsed_frames += plan.slide_frames[index] - plan.crossfade_frames
    return ";".join(lines)


def _audio_filter(audio_index: int, plan: ReelPlan, *, fade: bool) -> str:
    fade_seconds = audio_fade_out_seconds(plan.duration) if fade else 0.0
    parts = [
        f"[{audio_index}:a]aformat=sample_rates={AUDIO_SAMPLE_RATE}:channel_layouts=stereo",
        f"atrim=end={plan.duration:.6f}",
        "asetpts=PTS-STARTPTS",
    ]
    if fade_seconds > 0:
        start = max(0.0, plan.duration - fade_seconds)
        parts.append(f"afade=t=out:st={start:.6f}:d={fade_seconds:.6f}")
    return ",".join(parts) + "[aout]"


def encode_slide_images(
    image_paths: Sequence[Path],
    output_path: Path,
    plan: ReelPlan,
    audio_path: Path | None = None,
    *,
    preset: str = VIDEO_PRESET,
) -> None:
    missing = [path.name for path in image_paths if not path.is_file()]
    if missing:
        raise ValueError(f"Missing slide images: {', '.join(missing)}.")
    if audio_path is not None:
        if not audio_path.is_file():
            raise ValueError(f"Missing audio file: {audio_path.name}.")
        if audio_path.suffix.lower() not in AUDIO_EXTENSIONS:
            allowed = ", ".join(sorted(AUDIO_EXTENSIONS))
            raise ValueError(f"Audio file must use one of these extensions: {allowed}.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = build_reel_command(
        image_paths,
        output_path,
        plan,
        audio_path,
        preset=preset,
    )
    _run_ffmpeg(command)


def _clear_previous_slides(output_dir: Path) -> None:
    if not output_dir.is_dir():
        return
    for path in output_dir.iterdir():
        if path.is_file() and (_SLIDE_PNG.match(path.name) or path.name == VIDEO_FILENAME):
            path.unlink()


def render_text_slideshow_video(
    slides: list[dict],
    assets: Mapping[str, bytes | Path] | None = None,
    output_dir: Path | None = None,
    *,
    seconds_per_slide: float = DEFAULT_SLIDE_SECONDS,
    first_slide_seconds: float | None = None,
    crossfade_seconds: float = 0.0,
    audio_path: Path | None = None,
    preset: str = VIDEO_PRESET,
) -> ReelResult:
    """Render slides to PNGs and write ``video.mp4`` in the output folder."""
    plan = plan_reel(
        len(slides),
        seconds_per_slide,
        first_slide_seconds,
        crossfade_seconds,
    )
    if output_dir is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        output_dir = OUTPUT_DIR / f"{timestamp}_textslides"
    output_dir.mkdir(parents=True, exist_ok=True)
    _clear_previous_slides(output_dir)

    image_paths: list[Path] = []
    for index, slide in enumerate(slides, start=1):
        image = render_slide(slide, assets)
        if image.size != (REEL_WIDTH, REEL_HEIGHT):
            raise ValueError("Slides must render at 1080x1920.")
        path = output_dir / f"{index:02d}.png"
        image.save(path, format="PNG")
        image_paths.append(path)

    video_path = output_dir / VIDEO_FILENAME
    encode_slide_images(
        image_paths,
        video_path,
        plan,
        audio_path,
        preset=preset,
    )
    return ReelResult(path=video_path, output_dir=output_dir, plan=plan)


def slide_pngs(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    paths = [
        path
        for path in folder.iterdir()
        if path.is_file() and _SLIDE_PNG.match(path.name)
    ]
    return sorted(paths, key=natural_sort_key)


def deck_kind(folder: Path) -> str | None:
    if (folder / _DECK_JSON).is_file():
        return "json"
    if slide_pngs(folder):
        return "png"
    return None


def collect_decks(path: Path) -> list[Path]:
    """One deck, or every deck inside a folder of decks."""
    if path.is_file() and path.name == _DECK_JSON:
        return [path.parent]
    if not path.exists():
        raise ValueError(f"Nothing at {path}.")
    if not path.is_dir():
        raise ValueError(f"{path} is not a deck folder or deck.json.")

    kind = deck_kind(path)
    if kind is not None:
        return [path]

    children = [
        child
        for child in sorted(path.iterdir(), key=natural_sort_key)
        if child.is_dir() and deck_kind(child) is not None
    ]
    if not children:
        raise ValueError(
            f"No decks in {path}. Each deck needs deck.json or numbered PNG slides."
        )
    return children


def _load_deck_document(path: Path) -> dict:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"{path.name} is not valid JSON ({error}).") from error
    if not isinstance(document, dict) or not isinstance(document.get("slides"), list):
        raise ValueError(f"{path.name} must be an object with a slides list.")
    if not document["slides"]:
        raise ValueError(f"{path.name} has no slides.")
    return document


def _document_float(document: dict, key: str) -> float | None:
    if key not in document or document[key] is None:
        return None
    return _finite_number(document[key], key)


def resolve_deck_assets(deck_dir: Path, document: dict) -> dict[str, Path]:
    mapping = document.get("assets") or {}
    if not isinstance(mapping, dict):
        raise ValueError("assets must be an object of id to file path.")
    needed: list[str] = []
    for slide in document["slides"]:
        if not isinstance(slide, dict):
            continue
        background = slide.get("background_asset_id")
        if background:
            needed.append(str(background))
        for element in slide.get("elements") or []:
            if isinstance(element, dict) and element.get("type") == "image" and element.get("asset_id"):
                needed.append(str(element["asset_id"]))

    resolved: dict[str, Path] = {}
    for asset_id in needed:
        if asset_id in resolved:
            continue
        raw = mapping.get(asset_id)
        if raw:
            candidate = Path(str(raw))
            if not candidate.is_absolute():
                candidate = deck_dir / candidate
        else:
            options = [
                deck_dir / "assets" / f"{asset_id}.png",
                deck_dir / f"{asset_id}.png",
                deck_dir / "assets" / asset_id,
            ]
            candidate = next((option for option in options if option.is_file()), None)
        if candidate is None or not candidate.is_file():
            raise ValueError(f"Missing image '{asset_id}' for {deck_dir.name}.")
        resolved[asset_id] = candidate
    return resolved


def audio_from_url(url: str, *, browser: str | None = None) -> Path:
    """Save an Instagram reel's audio with the app's existing music importer.

    ``import_instagram_music`` downloads the video, converts the audio to MP3,
    and stores it in ``music/``. This wrapper only checks the URL is present
    and forwards the same Chrome-login option the editor already uses.
    """
    cleaned = url.strip()
    if not cleaned:
        raise ValueError("Enter an Instagram video URL.")
    from web_app import import_instagram_music

    return import_instagram_music(cleaned, browser=browser)


def _resolve_audio(deck_dir: Path, document: dict, override: Path | None) -> Path | None:
    if override is not None:
        if not override.is_file():
            raise ValueError(f"Missing audio file: {override}.")
        return override
    raw = document.get("audio")
    if raw in (None, ""):
        return None
    if not isinstance(raw, str):
        raise ValueError("audio must be a file path.")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = deck_dir / candidate
    if not candidate.is_file():
        raise ValueError(f"Missing audio file: {candidate.name}.")
    return candidate


def export_deck(
    deck_dir: Path,
    *,
    seconds_per_slide: float | None = None,
    first_slide_seconds: float | None = None,
    crossfade_seconds: float | None = None,
    audio_path: Path | None = None,
    output_path: Path | None = None,
    preset: str = VIDEO_PRESET,
) -> ReelResult:
    """Write ``video.mp4`` for one deck. JSON decks also write their PNGs."""
    kind = deck_kind(deck_dir)
    if kind is None:
        raise ValueError(f"{deck_dir} is not a deck.")

    if kind == "png":
        images = slide_pngs(deck_dir)
        destination = output_path or (deck_dir / VIDEO_FILENAME)
        if destination.is_dir():
            destination = destination / VIDEO_FILENAME
        plan = plan_reel(
            len(images),
            DEFAULT_SLIDE_SECONDS if seconds_per_slide is None else seconds_per_slide,
            first_slide_seconds,
            0.0 if crossfade_seconds is None else crossfade_seconds,
        )
        encode_slide_images(images, destination, plan, audio_path, preset=preset)
        return ReelResult(path=destination, output_dir=destination.parent, plan=plan)

    document = _load_deck_document(deck_dir / _DECK_JSON)
    seconds = (
        seconds_per_slide
        if seconds_per_slide is not None
        else document.get("seconds_per_slide", DEFAULT_SLIDE_SECONDS)
    )
    first = (
        first_slide_seconds
        if first_slide_seconds is not None
        else _document_float(document, "first_slide_seconds")
    )
    fade = (
        crossfade_seconds
        if crossfade_seconds is not None
        else document.get("crossfade_seconds", 0.0)
    )
    audio = _resolve_audio(deck_dir, document, audio_path)
    destination = output_path or (deck_dir / "output" / VIDEO_FILENAME)
    if destination.is_dir():
        destination = destination / VIDEO_FILENAME
    assets = resolve_deck_assets(deck_dir, document)
    return render_text_slideshow_video(
        document["slides"],
        assets,
        destination.parent,
        seconds_per_slide=seconds,
        first_slide_seconds=first,
        crossfade_seconds=fade if fade is not None else 0.0,
        audio_path=audio,
        preset=preset,
    )


def export_decks(
    path: Path,
    *,
    seconds_per_slide: float | None = None,
    first_slide_seconds: float | None = None,
    crossfade_seconds: float | None = None,
    audio_path: Path | None = None,
    output_path: Path | None = None,
    preset: str = VIDEO_PRESET,
) -> list[ReelResult]:
    decks = collect_decks(path)
    if output_path is not None and len(decks) != 1:
        raise ValueError("--output applies to a single deck, not a folder of them.")
    results: list[ReelResult] = []
    for deck in decks:
        results.append(
            export_deck(
                deck,
                seconds_per_slide=seconds_per_slide,
                first_slide_seconds=first_slide_seconds,
                crossfade_seconds=crossfade_seconds,
                audio_path=audio_path,
                output_path=output_path,
                preset=preset,
            )
        )
    return results


def _format_seconds(value: float) -> str:
    return f"{value:.2f}s"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="text_slideshow_video.py",
        description=(
            "Render Text Slideshow decks to 1080x1920 H.264 Reels. "
            "Pass one deck folder or a folder that contains many decks."
        ),
    )
    parser.add_argument(
        "path",
        type=Path,
        help="A deck folder, a deck.json file, or a directory of decks.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help=f"Seconds each slide stays up (default {DEFAULT_SLIDE_SECONDS}).",
    )
    parser.add_argument(
        "--first-seconds",
        type=float,
        default=None,
        help="Optional longer hold for slide 1. Omit to use the same duration.",
    )
    parser.add_argument(
        "--fade",
        type=float,
        default=None,
        help="Crossfade seconds. 0 (the default) is a hard cut.",
    )
    parser.add_argument(
        "--audio",
        type=Path,
        default=None,
        help="Optional music file. Looped or trimmed to the video, with a short fade out.",
    )
    parser.add_argument(
        "--audio-url",
        default=None,
        help=(
            "Instagram reel or video URL. The existing importer saves an MP3 in "
            "music/ and that file is the Reel track (looped or trimmed, then faded)."
        ),
    )
    parser.add_argument(
        "--chrome-login",
        action="store_true",
        help="With --audio-url, read the Chrome login when Instagram blocks anonymous access.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="video.mp4 path, for a single deck. The default is the deck's output folder.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    audio_path = args.audio
    imported_audio = False
    try:
        if args.audio is not None and args.audio_url:
            raise ValueError("Pass --audio or --audio-url, not both.")
        if args.audio_url:
            collect_decks(args.path)
            audio_path = audio_from_url(
                args.audio_url,
                browser="chrome" if args.chrome_login else None,
            )
            imported_audio = True
        results = export_decks(
            args.path,
            seconds_per_slide=args.seconds,
            first_slide_seconds=args.first_seconds,
            crossfade_seconds=args.fade,
            audio_path=audio_path,
            output_path=args.output,
        )
    except (ValueError, RuntimeError, OSError) as error:
        print(f"Could not export: {error}", file=sys.stderr)
        return 1

    if imported_audio and audio_path is not None:
        print(f"Saved {audio_path}")

    for result in results:
        holds = ", ".join(_format_seconds(seconds) for seconds in result.plan.slide_durations)
        transition = (
            "hard cuts"
            if result.plan.crossfade_seconds <= 0
            else f"{_format_seconds(result.plan.crossfade_seconds)} crossfade"
        )
        print(
            f"{result.path}  {_format_seconds(result.plan.duration)}  "
            f"{result.plan.width}x{result.plan.height}  {result.plan.fps}fps  "
            f"{transition}  holds {holds}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
