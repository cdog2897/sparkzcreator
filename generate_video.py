#!/usr/bin/env python3
"""
Interactively generate stitched text-overlay videos from structured clip folders.

The script is self-contained around this folder:
  videos/Horizontal/*_{Random,Ordered,Single}/
  videos/Square/*_{Random,Ordered,Single}/
  videos/Vertical/*_{Random,Ordered,Single}/
  music/
  Crimson_Text/CrimsonText-Regular.ttf
  outputs/
"""

from __future__ import annotations

import random
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps


ROOT = Path(__file__).resolve().parent
VIDEO_DIR = ROOT / "videos"
MUSIC_DIR = ROOT / "music"
FONT_PATH = ROOT / "Crimson_Text" / "CrimsonText-Regular.ttf"
FONT_LABEL = "Crimson Text Regular"
APPSTORE_PATH = ROOT / "appstore.jpg"
OUTPUT_DIR = ROOT / "outputs"

FPS = 30
TEXT_FONT_SIZE = 60
TEXT_LINE_SPACING = 0
TEXT_HORIZONTAL_PADDING = 240
TEXT_SHADOW_OFFSET = 2
TEXT_SHADOW_OPACITY = "0.45"
TEXT_SHADOW_BLUR_SIGMA = 6
TEXT_ABOVE_VIDEO_PADDING = 40
CTA_DURATION_SECONDS = 3.0
CTA_REFERENCE_WIDTH = 1080
CTA_REFERENCE_HEIGHT = 1350
CTA_APPSTORE_PADDING = 80
CTA_APPSTORE_WIDTH = 540
CTA_TEXT = "we highly recommend this app to find cute date ideas!"
CTA_TEXT_FONT_SIZE = 50
CTA_TEXT_TOP_PADDING = 34
CTA_TEXT_MAX_WIDTH = CTA_APPSTORE_WIDTH
MIN_CLIP_SECONDS = 0.5
TARGET_AVG_CLIP_SECONDS = 1.25
PREFERRED_MAX_CLIP_SECONDS = 2.5
LEADING_FRAME_SKIP_SECONDS = 0.25
TRAILING_FRAME_SKIP_SECONDS = 0.25
MIN_OUTPUT_SECONDS = 0.1
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".mov", ".mp4"}
SDR_COLOR_PARAMS = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
STRIP_AMBIENT_SIDE_DATA = "sidedata=mode=delete:type=AMBIENT_VIEWING_ENVIRONMENT"


class FolderMode(Enum):
    RANDOM = "Random"
    ORDERED = "Ordered"
    SINGLE = "Single"


class VideoLayout(Enum):
    SQUARE_LETTERBOX = "square_letterbox"
    FILL_FRAME = "fill_frame"
    FIT_FRAME = "fit_frame"


class ClipScaleMode(Enum):
    FIT = "fit"
    SQUARE = "square"
    FILL = "fill"


class TextPlacement(Enum):
    CENTER = "center"
    ABOVE_HORIZONTAL_CLIP = "above_horizontal_clip"


@dataclass(frozen=True)
class VideoFormat:
    option: int
    title: str
    source_root_name: str
    width: int
    height: int
    layout: VideoLayout
    text_placement: TextPlacement


@dataclass(frozen=True)
class SourceVideo:
    path: Path
    duration: float
    width: int | None = None
    height: int | None = None
    scale_mode: str = ClipScaleMode.FIT.value
    crop_x_percent: float = 50.0
    trim_start_seconds: float = 0.0
    trim_end_seconds: float | None = None


@dataclass(frozen=True)
class VideoMetadata:
    duration: float
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class ClipSegment:
    source: SourceVideo
    duration: float
    random_start: bool = False
    trim_edges: bool = False
    start_time: float | None = None


@dataclass(frozen=True)
class TextSizeOption:
    option: str
    label: str
    font_size: int


@dataclass(frozen=True)
class RandomClipCountOption:
    option: int
    clip_count: int
    average_duration: float


ProgressCallback = Callable[[str], None]


FORMATS = (
    VideoFormat(
        option=1,
        title="9:16",
        source_root_name="Videos",
        width=1080,
        height=1920,
        layout=VideoLayout.FIT_FRAME,
        text_placement=TextPlacement.CENTER,
    ),
    VideoFormat(
        option=2,
        title="16:9",
        source_root_name="Videos",
        width=1920,
        height=1080,
        layout=VideoLayout.FIT_FRAME,
        text_placement=TextPlacement.CENTER,
    ),
)
FORMATS_BY_OPTION = {video_format.option: video_format for video_format in FORMATS}
COVER_IMAGE_FORMAT = FORMATS_BY_OPTION[1]
TEXT_SIZE_OPTIONS = (
    TextSizeOption("1", "Smaller", 48),
    TextSizeOption("2", "Small", 54),
    TextSizeOption("3", "Standard", TEXT_FONT_SIZE),
    TextSizeOption("4", "Big", 72),
    TextSizeOption("5", "Bigger", 84),
)


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(command: list[str]) -> None:
    subprocess.run(command, check=True)


def natural_sort_key(value: str | Path) -> list[int | str]:
    text = value.name if isinstance(value, Path) else str(value)
    return [
        int(part) if part.isdigit() else part.lower()
        for part in re.split(r"(\d+)", text)
    ]


def parse_duration_from_ffmpeg(output: str, path: Path) -> float:
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        raise RuntimeError(f"Could not read duration for {path.name}.")

    hours, minutes, seconds = match.groups()
    return (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)


def parse_video_dimensions_from_ffmpeg(output: str) -> tuple[int | None, int | None]:
    for line in output.splitlines():
        if "Video:" not in line:
            continue

        match = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
        if match:
            width, height = match.groups()
            return int(width), int(height)

    return None, None


def probe_video_metadata(path: Path) -> VideoMetadata:
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    width, height = parse_video_dimensions_from_ffmpeg(result.stderr)
    return VideoMetadata(
        duration=parse_duration_from_ffmpeg(result.stderr, path),
        width=width,
        height=height,
    )


def probe_duration(path: Path) -> float:
    return probe_video_metadata(path).duration


def probe_video(path: Path) -> SourceVideo:
    metadata = probe_video_metadata(path)
    return SourceVideo(
        path=path,
        duration=metadata.duration,
        width=metadata.width,
        height=metadata.height,
    )


def source_root_for_format(video_format: VideoFormat) -> Path:
    if video_format.source_root_name in {"", "Videos", "MediaPool"}:
        return VIDEO_DIR

    return VIDEO_DIR / video_format.source_root_name


def list_format_folders(video_format: VideoFormat) -> list[Path]:
    source_root = source_root_for_format(video_format)
    if not source_root.is_dir():
        raise FileNotFoundError(f"Missing video source folder: {source_root}")

    folders = [
        path
        for path in source_root.iterdir()
        if path.is_dir()
    ]
    return sorted(folders, key=natural_sort_key)


def list_video_paths(folder: Path) -> list[Path]:
    paths = [
        path
        for path in folder.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    ]
    return sorted(paths, key=natural_sort_key)


def list_source_videos(folder: Path) -> list[SourceVideo]:
    paths = list_video_paths(folder)
    if not paths:
        raise FileNotFoundError(f"No video files found in {folder}.")

    videos = [probe_video(path) for path in paths]
    usable = [video for video in videos if video.duration >= MIN_OUTPUT_SECONDS]
    if not usable:
        raise RuntimeError(f"Every source video in {folder.name} is too short.")

    return usable


def list_music_tracks() -> list[Path]:
    tracks = [
        path
        for path in MUSIC_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    ]
    tracks = sorted(tracks, key=natural_sort_key)
    if not tracks:
        raise FileNotFoundError(f"No music tracks found in {MUSIC_DIR}.")

    return tracks


def parse_folder_mode(folder: Path) -> FolderMode:
    suffix = folder.name.rsplit("_", 1)[-1]
    for mode in FolderMode:
        if suffix.lower() == mode.value.lower():
            return mode

    expected = ", ".join(f"*_{mode.value}" for mode in FolderMode)
    raise ValueError(
        f"Folder '{folder.name}' must end with one of these suffixes: {expected}."
    )


def available_duration(video: SourceVideo, trim_edges: bool) -> float:
    if not trim_edges:
        return max(0.0, video.duration)

    return max(
        0.0,
        video.duration - LEADING_FRAME_SKIP_SECONDS - TRAILING_FRAME_SKIP_SECONDS,
    )


def random_clip_candidates(videos: list[SourceVideo]) -> list[tuple[SourceVideo, float]]:
    return [
        (video, available_duration(video, trim_edges=True))
        for video in videos
        if available_duration(video, trim_edges=True) >= MIN_OUTPUT_SECONDS
    ]


def random_output_target(
    candidates: list[tuple[SourceVideo, float]],
    target_seconds: float,
) -> float:
    return min(target_seconds, sum(duration for _, duration in candidates))


def viable_random_clip_counts_from_candidates(
    candidates: list[tuple[SourceVideo, float]],
    output_target: float,
) -> list[int]:
    if output_target < MIN_CLIP_SECONDS:
        return [1] if candidates else []

    target_units = round(output_target * 100)
    min_units = round(MIN_CLIP_SECONDS * 100)
    candidates_with_units = [
        (video, max(1, int(duration * 100)))
        for video, duration in candidates
    ]
    max_count = min(len(candidates_with_units), target_units // min_units)
    return [
        count
        for count in range(1, max_count + 1)
        if (count * min_units) <= target_units
        and sum(
            capacity
            for _, capacity in sorted(
                candidates_with_units,
                key=lambda item: item[1],
                reverse=True,
            )[:count]
        )
        >= target_units
    ]


def random_clip_count_options(
    videos: list[SourceVideo],
    target_seconds: float,
    max_options: int = 5,
) -> list[RandomClipCountOption]:
    candidates = random_clip_candidates(videos)
    if not candidates:
        return []

    total_available = sum(duration for _, duration in candidates)
    output_target = random_output_target(candidates, target_seconds)
    if total_available <= target_seconds or output_target < MIN_CLIP_SECONDS:
        return []

    viable_counts = viable_random_clip_counts_from_candidates(candidates, output_target)
    if len(viable_counts) < 2:
        return []

    if len(viable_counts) <= max_options:
        selected_counts = viable_counts
    else:
        indexes = [
            round(index * (len(viable_counts) - 1) / (max_options - 1))
            for index in range(max_options)
        ]
        selected_counts = sorted({viable_counts[index] for index in indexes})

    return [
        RandomClipCountOption(
            option=index,
            clip_count=count,
            average_duration=output_target / count,
        )
        for index, count in enumerate(selected_counts, start=1)
    ]


def plan_random_segments(
    videos: list[SourceVideo],
    target_seconds: float,
    clip_count: int | None = None,
) -> list[ClipSegment]:
    candidates = random_clip_candidates(videos)
    if not candidates:
        raise RuntimeError("No usable random clips are available after trim margins.")

    total_available = sum(duration for _, duration in candidates)
    output_target = random_output_target(candidates, target_seconds)
    if output_target < MIN_OUTPUT_SECONDS:
        raise RuntimeError("Selected music is too short to render a video.")

    if total_available <= target_seconds:
        shuffled = candidates[:]
        random.shuffle(shuffled)
        return [
            ClipSegment(
                source=video,
                duration=duration,
                random_start=True,
                trim_edges=True,
            )
            for video, duration in shuffled
        ]

    if output_target < MIN_CLIP_SECONDS:
        if clip_count not in (None, 1):
            raise ValueError("Only 1 clip is viable for this short output.")

        video, duration = random.choice(candidates)
        return [
            ClipSegment(
                source=video,
                duration=min(output_target, duration),
                random_start=True,
                trim_edges=True,
            )
        ]

    target_units = round(output_target * 100)
    min_units = round(MIN_CLIP_SECONDS * 100)
    candidates_with_units = [
        (video, max(1, int(duration * 100)))
        for video, duration in candidates
    ]
    viable_counts = viable_random_clip_counts_from_candidates(candidates, output_target)

    if not viable_counts:
        video, capacity = max(candidates_with_units, key=lambda item: item[1])
        return [
            ClipSegment(
                source=video,
                duration=min(output_target, capacity / 100),
                random_start=True,
                trim_edges=True,
            )
        ]

    if clip_count is not None:
        if clip_count not in viable_counts:
            available = ", ".join(str(count) for count in viable_counts)
            raise ValueError(
                f"{clip_count} clips is not viable for this duration. "
                f"Available counts: {available}."
            )
        count = clip_count
    else:
        ideal_count = target_units / round(TARGET_AVG_CLIP_SECONDS * 100)
        count_weights = [
            1 / ((abs(count - ideal_count) + 1) ** 2)
            for count in viable_counts
        ]
        count = random.choices(viable_counts, weights=count_weights, k=1)[0]

    selected: list[tuple[SourceVideo, int]] | None = None
    for _ in range(500):
        sample = random.sample(candidates_with_units, count)
        if sum(capacity for _, capacity in sample) >= target_units:
            selected = sample
            break

    if selected is None:
        selected = sorted(candidates_with_units, key=lambda item: item[1], reverse=True)[:count]

    random.shuffle(selected)
    durations = [min_units] * count
    leftover = target_units - (min_units * count)
    capacities = [capacity - min_units for _, capacity in selected]

    while leftover > 0:
        active_indexes = [
            index
            for index, capacity in enumerate(capacities)
            if capacity > 0
        ]
        if not active_indexes:
            break

        weights = [
            max(1.0, capacities[index])
            * max(
                0.1,
                random.triangular(
                    MIN_CLIP_SECONDS,
                    PREFERRED_MAX_CLIP_SECONDS,
                    TARGET_AVG_CLIP_SECONDS,
                ),
            )
            for index in active_indexes
        ]
        selected_index = random.choices(active_indexes, weights=weights, k=1)[0]
        durations[selected_index] += 1
        capacities[selected_index] -= 1
        leftover -= 1

    if leftover > 0:
        raise RuntimeError("Could not distribute the random clip durations.")

    return [
        ClipSegment(
            source=video,
            duration=duration_units / 100,
            random_start=True,
            trim_edges=True,
        )
        for (video, _), duration_units in zip(selected, durations)
    ]


def plan_ordered_segments(
    videos: list[SourceVideo],
    target_seconds: float,
) -> list[ClipSegment]:
    if target_seconds < MIN_OUTPUT_SECONDS:
        raise RuntimeError("Selected music is too short to render a video.")

    ordered_videos = [
        video
        for video in sorted(videos, key=lambda source: natural_sort_key(source.path))
        if video.duration >= MIN_OUTPUT_SECONDS
    ]
    if not ordered_videos:
        raise RuntimeError("No usable ordered clips are available.")

    capacities = [max(1, round(video.duration * 100)) for video in ordered_videos]
    target_units = min(round(target_seconds * 100), sum(capacities))
    if target_units < len(ordered_videos):
        raise RuntimeError(
            "Selected music is too short to include every ordered clip."
        )

    min_units = round(MIN_CLIP_SECONDS * 100)
    base_units = max(1, min(min_units, target_units // len(ordered_videos)))
    durations = [min(capacity, base_units) for capacity in capacities]
    leftover = target_units - sum(durations)

    while leftover > 0:
        active_indexes = [
            index
            for index, capacity in enumerate(capacities)
            if durations[index] < capacity
        ]
        if not active_indexes:
            break

        average_duration = target_units / len(ordered_videos) / 100
        weights = [
            max(1, capacities[index] - durations[index])
            * max(
                0.1,
                random.triangular(
                    MIN_OUTPUT_SECONDS,
                    max(PREFERRED_MAX_CLIP_SECONDS, average_duration),
                    max(MIN_OUTPUT_SECONDS, average_duration),
                ),
            )
            for index in active_indexes
        ]
        selected_index = random.choices(active_indexes, weights=weights, k=1)[0]
        durations[selected_index] += 1
        leftover -= 1

    if leftover > 0:
        raise RuntimeError("Could not distribute ordered clip durations.")

    return [
        ClipSegment(source=video, duration=duration_units / 100)
        for video, duration_units in zip(ordered_videos, durations)
    ]


def plan_selected_segments(
    videos: list[SourceVideo],
    target_seconds: float | None = None,
) -> list[ClipSegment]:
    # Timeline selections carry explicit source in/out points.  The music no
    # longer redistributes or shortens individual clips; render_segments trims
    # only the final stitched output when the music ends first.
    segments: list[ClipSegment] = []
    for video in videos:
        start = max(0.0, video.trim_start_seconds)
        end = video.duration if video.trim_end_seconds is None else video.trim_end_seconds
        end = min(video.duration, end)
        duration = end - start
        if duration < MIN_OUTPUT_SECONDS:
            raise RuntimeError(
                f"Selected range for {video.path.name} is too short to render."
            )
        segments.append(
            ClipSegment(
                source=video,
                duration=duration,
                start_time=start,
            )
        )

    if not segments:
        raise RuntimeError("No usable selected clips are available.")

    return segments


def plan_single_segment(video: SourceVideo, music_duration: float) -> list[ClipSegment]:
    duration = min(video.duration, music_duration)
    if duration < MIN_OUTPUT_SECONDS:
        raise RuntimeError("The selected clip or music track is too short.")

    return [ClipSegment(source=video, duration=duration)]


def plan_segments(
    mode: FolderMode,
    videos: list[SourceVideo],
    music_duration: float,
    selected_single_video: SourceVideo | None = None,
    random_clip_count: int | None = None,
) -> list[ClipSegment]:
    if mode is FolderMode.RANDOM:
        return plan_random_segments(videos, music_duration, random_clip_count)
    if mode is FolderMode.ORDERED:
        return plan_ordered_segments(videos, music_duration)
    if mode is FolderMode.SINGLE:
        if selected_single_video is None:
            raise ValueError("Single mode requires a selected clip.")
        return plan_single_segment(selected_single_video, music_duration)

    raise ValueError(f"Unsupported folder mode: {mode}")


def segment_plan_duration(segments: list[ClipSegment]) -> float:
    return sum(segment.duration for segment in segments)


def final_output_duration(
    segments: list[ClipSegment],
    music_duration: float | None,
) -> float:
    clip_duration = segment_plan_duration(segments)
    if music_duration is None:
        return clip_duration
    return min(clip_duration, max(0.0, music_duration))


def prompt_number(prompt: str, minimum: int, maximum: int) -> int:
    while True:
        value = input(prompt).strip()
        if value.isdigit():
            number = int(value)
            if minimum <= number <= maximum:
                return number

        print(f"Enter a number from {minimum} to {maximum}.")


def ask_for_format() -> VideoFormat:
    print("Available formats:")
    for video_format in FORMATS:
        print(f"{video_format.option}. {video_format.title}")

    option = prompt_number("Format to use: ", 1, len(FORMATS))
    return FORMATS_BY_OPTION[option]


def ask_for_folder(video_format: VideoFormat) -> Path:
    folders = list_format_folders(video_format)
    if not folders:
        raise FileNotFoundError(
            f"No subfolders found in {source_root_for_format(video_format)}."
        )

    print(f"Available {video_format.source_root_name} folders:")
    for index, folder in enumerate(folders, start=1):
        print(f"{index}. {folder.name}")

    while True:
        value = input("Folder to generate from: ").strip()
        if value.isdigit():
            index = int(value)
            if 1 <= index <= len(folders):
                return folders[index - 1]

        matches = [folder for folder in folders if folder.name == value]
        if matches:
            return matches[0]

        print("Enter a listed folder number or exact folder name.")


def ask_for_single_video(videos: list[SourceVideo]) -> SourceVideo:
    ordered_videos = sorted(videos, key=lambda source: natural_sort_key(source.path))
    print("Available clips:")
    for index, video in enumerate(ordered_videos, start=1):
        print(f"{index}. {video.path.name} ({video.duration:.2f}s)")

    while True:
        value = input("Clip to use: ").strip()
        if value.isdigit():
            index = int(value)
            if 1 <= index <= len(ordered_videos):
                return ordered_videos[index - 1]

        matches = [video for video in ordered_videos if video.path.name == value]
        if matches:
            return matches[0]

        print("Enter a listed clip number or exact file name.")


def ask_for_text() -> str:
    while True:
        text = input("Text to overlay: ").strip()
        if text:
            return text

        print("Please enter some text.")


def text_size_for_choice(value: str) -> int:
    requested = value.strip()
    if not requested:
        return TEXT_FONT_SIZE

    for option in TEXT_SIZE_OPTIONS:
        if requested == option.option:
            return option.font_size

    valid_options = ", ".join(option.option for option in TEXT_SIZE_OPTIONS)
    raise ValueError(f"Choose one of {valid_options}, or press Enter for Standard.")


def ask_for_text_size() -> int:
    print("Text size options:")
    for option in TEXT_SIZE_OPTIONS:
        default_marker = " (default)" if option.font_size == TEXT_FONT_SIZE else ""
        print(
            f"{option.option}. {option.label} "
            f"({option.font_size}px){default_marker}"
        )

    while True:
        value = input("Text size (press Enter for Standard): ")
        try:
            return text_size_for_choice(value)
        except ValueError as error:
            print(error)


def format_duration(seconds: float) -> str:
    return f"{seconds:.2f}s"


def ask_for_music_track(tracks: list[Path]) -> Path:
    print("Available music tracks:")
    for index, track in enumerate(tracks, start=1):
        print(f"{index}. {track.name} ({format_duration(probe_duration(track))})")

    while True:
        value = input("Music file to use (press Enter for random): ").strip()
        if not value:
            return random.choice(tracks)

        if value.isdigit():
            index = int(value)
            if 1 <= index <= len(tracks):
                return tracks[index - 1]

        matches = [track for track in tracks if track.name == value]
        if matches:
            return matches[0]

        print("Enter a listed music number, exact file name, or press Enter for random.")


def ask_for_random_clip_count(
    videos: list[SourceVideo],
    music_duration: float,
) -> int | None:
    options = random_clip_count_options(videos, music_duration)
    if not options:
        print("Random clip count: using default assignment.")
        return None

    print("Random clip count options:")
    print("- Press Enter for default random assignment.")
    for option in options:
        print(
            f"{option.option}. {option.clip_count} clips "
            f"(avg {format_duration(option.average_duration)} per clip)"
        )

    while True:
        value = input("Clip count option: ").strip()
        if not value:
            return None

        if value.isdigit():
            selected = int(value)
            matches = [
                option
                for option in options
                if option.option == selected or option.clip_count == selected
            ]
            if matches:
                return matches[0].clip_count

        print("Enter a listed option number, clip count, or press Enter for default.")


def wrap_words(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    max_width: int,
) -> list[str]:
    wrapped: list[str] = []
    for raw_line in text.splitlines() or [text]:
        words = raw_line.split()
        if not words:
            wrapped.append("")
            continue

        current = words[0]
        for word in words[1:]:
            candidate = f"{current} {word}"
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current = candidate
            else:
                wrapped.append(current)
                current = word
        wrapped.append(current)

    return wrapped


def prepare_text(
    text: str,
    video_format: VideoFormat,
    font_size: int,
    text_horizontal_padding: int = TEXT_HORIZONTAL_PADDING,
) -> tuple[str, int, int]:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"Missing Crimson Text font: {FONT_PATH}")

    image = Image.new("RGB", (video_format.width, video_format.height))
    draw = ImageDraw.Draw(image)
    max_width = max(1, video_format.width - (text_horizontal_padding * 2))
    font = ImageFont.truetype(str(FONT_PATH), font_size)
    lines = wrap_words(draw, text, font, max_width)
    return "\n".join(lines), font_size, TEXT_LINE_SPACING


def cta_layout_scale(video_format: VideoFormat) -> float:
    return min(
        video_format.width / CTA_REFERENCE_WIDTH,
        video_format.height / CTA_REFERENCE_HEIGHT,
    )


def scaled_cta_value(value: int, video_format: VideoFormat) -> int:
    return max(1, round(value * cta_layout_scale(video_format)))


def load_scaled_appstore_image(path: Path, width: int) -> Image.Image:
    if not path.exists():
        raise FileNotFoundError(f"Missing App Store image: {path}")

    image = Image.open(path)
    image = ImageOps.exif_transpose(image).convert("RGBA")
    scale = width / image.width
    size = (width, round(image.height * scale))
    return image.resize(size, Image.Resampling.LANCZOS)


def draw_cta_text_box(
    image: Image.Image,
    text: str,
    x: int,
    y: int,
    max_width: int,
    font_size: int,
    video_format: VideoFormat,
) -> Image.Image:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"Missing Crimson Text font: {FONT_PATH}")

    output = image.convert("RGBA")
    font = ImageFont.truetype(str(FONT_PATH), font_size)
    measurement_draw = ImageDraw.Draw(output)
    prepared_text = "\n".join(wrap_words(measurement_draw, text, font, max_width))

    shadow = Image.new("RGBA", output.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_alpha = round(255 * float(TEXT_SHADOW_OPACITY))
    shadow_offset = scaled_cta_value(TEXT_SHADOW_OFFSET, video_format)
    shadow_draw.multiline_text(
        (x + shadow_offset, y + shadow_offset),
        prepared_text,
        font=font,
        fill=(0, 0, 0, shadow_alpha),
        spacing=TEXT_LINE_SPACING,
        align="left",
    )
    shadow = shadow.filter(
        ImageFilter.GaussianBlur(
            radius=scaled_cta_value(TEXT_SHADOW_BLUR_SIGMA, video_format)
        )
    )

    output = Image.alpha_composite(output, shadow)
    text_draw = ImageDraw.Draw(output)
    text_draw.multiline_text(
        (x, y),
        prepared_text,
        font=font,
        fill=(255, 255, 255, 255),
        spacing=TEXT_LINE_SPACING,
        align="left",
    )
    return output


def create_cta_overlay_image(
    video_format: VideoFormat,
    text: str = CTA_TEXT,
) -> Image.Image:
    overlay = Image.new(
        "RGBA",
        (video_format.width, video_format.height),
        (0, 0, 0, 0),
    )
    padding = scaled_cta_value(CTA_APPSTORE_PADDING, video_format)
    appstore_width = scaled_cta_value(CTA_APPSTORE_WIDTH, video_format)
    appstore_image = load_scaled_appstore_image(APPSTORE_PATH, appstore_width)
    overlay.alpha_composite(appstore_image, (padding, padding))

    text_y = (
        padding
        + appstore_image.height
        + scaled_cta_value(CTA_TEXT_TOP_PADDING, video_format)
    )
    return draw_cta_text_box(
        overlay,
        text,
        x=padding,
        y=text_y,
        max_width=scaled_cta_value(CTA_TEXT_MAX_WIDTH, video_format),
        font_size=scaled_cta_value(CTA_TEXT_FONT_SIZE, video_format),
        video_format=video_format,
    )


def bounded_percent(value: float) -> float:
    return min(100.0, max(0.0, value))


def normalized_clip_scale_mode(value: str | None) -> ClipScaleMode:
    try:
        return ClipScaleMode(value or ClipScaleMode.FIT.value)
    except ValueError:
        return ClipScaleMode.FIT


def make_fit_filter(video_format: VideoFormat) -> str:
    return (
        f"scale={video_format.width}:{video_format.height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={video_format.width}:{video_format.height}:"
        "(ow-iw)/2:(oh-ih)/2:black"
    )


def make_square_filter(video_format: VideoFormat, crop_x_percent: float) -> str:
    square_size = r"min(iw\,ih)"
    crop_x_ratio = bounded_percent(crop_x_percent) / 100
    return (
        f"crop={square_size}:{square_size}:"
        f"(iw-{square_size})*{crop_x_ratio:.4f}:"
        f"(ih-{square_size})/2,"
        f"scale={video_format.width}:{video_format.height}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={video_format.width}:{video_format.height}:"
        "(ow-iw)/2:(oh-ih)/2:black"
    )


def make_fill_filter(video_format: VideoFormat, crop_x_percent: float) -> str:
    crop_x_ratio = bounded_percent(crop_x_percent) / 100
    return (
        f"scale={video_format.width}:{video_format.height}:"
        "force_original_aspect_ratio=increase,"
        f"crop={video_format.width}:{video_format.height}:"
        f"(iw-{video_format.width})*{crop_x_ratio:.4f}:"
        f"(ih-{video_format.height})/2"
    )


def make_video_filter(
    video_format: VideoFormat,
    scale_mode: str | None = None,
    crop_x_percent: float = 50.0,
) -> str:
    normalized_scale_mode = normalized_clip_scale_mode(scale_mode)
    if normalized_scale_mode is ClipScaleMode.SQUARE:
        fit_filter = make_square_filter(video_format, crop_x_percent)
    elif normalized_scale_mode is ClipScaleMode.FILL:
        fit_filter = make_fill_filter(video_format, crop_x_percent)
    else:
        fit_filter = make_fit_filter(video_format)

    return (
        f"{fit_filter},setsar=1,fps={FPS},{SDR_COLOR_PARAMS},"
        f"{STRIP_AMBIENT_SIDE_DATA},format=yuv420p"
    )


def make_cover_image_filter(
    crop_x_percent: float = 50.0,
) -> str:
    """Build the fixed 1080x1920 fill crop used for cover images."""
    fill_filter = make_fill_filter(COVER_IMAGE_FORMAT, crop_x_percent)

    # JPEG's mjpeg encoder expects full-range YUV. The regular video filter ends
    # in limited-range yuv420p and includes an fps stage, both of which are wrong
    # for extracting a single still near the end of a source clip.
    return f"{fill_filter},setsar=1,format=yuvj420p"


def segment_start(segment: ClipSegment) -> float:
    if segment.start_time is not None:
        return max(0.0, segment.start_time)

    if not segment.trim_edges:
        return 0.0

    max_start = max(
        0.0,
        segment.source.duration - TRAILING_FRAME_SKIP_SECONDS - segment.duration,
    )
    earliest_start = min(LEADING_FRAME_SKIP_SECONDS, max_start)
    if segment.random_start and max_start > earliest_start:
        return random.uniform(earliest_start, max_start)

    return earliest_start


def make_segment(
    segment: ClipSegment,
    video_format: VideoFormat,
    output_path: Path,
) -> None:
    start = segment_start(segment)
    command = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"]
    command.extend(["-i", str(segment.source.path)])
    if start > 0:
        command.extend(["-ss", f"{start:.3f}"])

    command.extend(
        [
            "-t",
            f"{segment.duration:.3f}",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            make_video_filter(
                video_format,
                segment.source.scale_mode,
                segment.source.crop_x_percent,
            ),
            "-r",
            str(FPS),
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
            "-avoid_negative_ts",
            "make_zero",
            "-reset_timestamps",
            "1",
            str(output_path),
        ]
    )
    run_ffmpeg(command)


def create_cover_image(
    source: SourceVideo,
    time_seconds: float,
    output_path: Path,
) -> Path:
    """Extract one selected frame as a 1080x1920 fill-cropped cover image."""
    if source.duration <= 0:
        raise ValueError("Cover image clip has no readable duration.")

    latest_frame = max(0.0, source.duration - (1 / FPS))
    frame_time = min(latest_frame, max(0.0, time_seconds))
    output_path.parent.mkdir(exist_ok=True)
    run_ffmpeg(
        [
            ffmpeg_exe(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source.path),
            "-ss",
            f"{frame_time:.3f}",
            "-map_metadata",
            "-1",
            "-map",
            "0:v:0",
            "-an",
            "-vf",
            make_cover_image_filter(source.crop_x_percent),
            "-frames:v",
            "1",
            "-update",
            "1",
            "-q:v",
            "2",
            str(output_path),
        ]
    )
    return output_path


def concat_segments(segment_paths: list[Path], output_path: Path, list_path: Path) -> None:
    lines = [f"file '{path.as_posix()}'" for path in segment_paths]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    run_ffmpeg(
        [
            ffmpeg_exe(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            "-an",
            str(output_path),
        ]
    )


def visible_clip_height_for(video_format: VideoFormat) -> int:
    if video_format.layout is VideoLayout.FIT_FRAME:
        return video_format.height

    if video_format.layout is VideoLayout.SQUARE_LETTERBOX:
        return video_format.width

    return video_format.height


def text_y_expression(video_format: VideoFormat, text_y_percent: float = 50.0) -> str:
    if video_format.text_placement is TextPlacement.ABOVE_HORIZONTAL_CLIP:
        top_band_height = (
            video_format.height - visible_clip_height_for(video_format)
        ) // 2
        return f"{top_band_height}-text_h-{TEXT_ABOVE_VIDEO_PADDING}"

    bounded_percent = min(100.0, max(0.0, text_y_percent))
    return f"h*{bounded_percent / 100:.4f}-text_h/2"


def overlay_text_and_music(
    input_path: Path,
    output_path: Path,
    text_path: Path | None,
    cta_overlay_path: Path | None,
    font_size: int,
    line_spacing: int,
    output_duration: float,
    music_path: Path | None,
    video_format: VideoFormat,
    text_y_percent: float = 50.0,
) -> None:
    cta_start = max(0.0, output_duration - CTA_DURATION_SECONDS)
    text_enable = (
        f":enable='lt(t\\,{cta_start:.3f})'"
        if cta_overlay_path is not None
        else ""
    )
    cta_enable = f"enable='gte(t\\,{cta_start:.3f})'"
    base_filter = (
        f"[0:v]setpts=PTS-STARTPTS,fps={FPS},{SDR_COLOR_PARAMS},"
        f"{STRIP_AMBIENT_SIDE_DATA},format=yuv420p"
    )
    filter_parts = [f"{base_filter}[base]"]
    current_video = "base"

    if text_path is not None:
        y_expression = text_y_expression(video_format, text_y_percent)
        text_options = (
            f"drawtext=fontfile={FONT_PATH.as_posix()}:"
            f"textfile={text_path.as_posix()}:"
            f"fontsize={font_size}:"
            f"line_spacing={line_spacing}:"
            "text_align=C"
        )
        shadow = (
            f"{text_options}:"
            f"fontcolor=black@{TEXT_SHADOW_OPACITY}:"
            f"x=(w-text_w)/2+{TEXT_SHADOW_OFFSET}:"
            f"y={y_expression}+{TEXT_SHADOW_OFFSET}"
            f"{text_enable}"
        )
        text = (
            f"{text_options}:"
            "fontcolor=white:"
            "x=(w-text_w)/2:"
            f"y={y_expression}"
            f"{text_enable}"
        )
        filter_parts.extend(
            [
                f"color=c=black@0.0:s={video_format.width}x{video_format.height}:"
                f"r={FPS}:d={output_duration:.3f},format=rgba,{shadow},"
                f"gblur=sigma={TEXT_SHADOW_BLUR_SIGMA}:steps=2[shadow]",
                f"[{current_video}][shadow]overlay=0:0:format=auto[with_shadow]",
                f"[with_shadow]{text}[with_text]",
            ]
        )
        current_video = "with_text"

    cta_input_index = 2 if music_path is not None else 1
    if cta_overlay_path is not None:
        filter_parts.append(
            f"[{current_video}][{cta_input_index}:v]"
            f"overlay=0:0:format=auto:{cta_enable}[with_cta]"
        )
        current_video = "with_cta"

    filter_parts.append(f"[{current_video}]format=yuv420p[v]")
    filter_complex = ";".join(filter_parts)

    command = [
        ffmpeg_exe(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(input_path),
    ]
    if music_path is not None:
        command.extend(["-i", str(music_path)])
    if cta_overlay_path is not None:
        command.extend(
            [
                "-loop",
                "1",
                "-framerate",
                str(FPS),
                "-i",
                str(cta_overlay_path),
            ]
        )

    command.extend(
        [
            "-t",
            f"{output_duration:.3f}",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-filter_complex",
            filter_complex,
            "-map",
            "[v]",
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
    if music_path is not None:
        command.extend(
            [
                "-map",
                "1:a:0",
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
                "-shortest",
            ]
        )
    else:
        command.append("-an")
    command.extend(["-movflags", "+faststart", str(output_path)])
    run_ffmpeg(command)


def safe_output_part(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("_") or "video"


def output_path_for(video_format: VideoFormat, folder: Path | str) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    folder_name = folder.name if isinstance(folder, Path) else folder
    name = f"format{video_format.option}_{safe_output_part(folder_name)}_{timestamp}.mp4"
    return OUTPUT_DIR / name


def render_segments(
    video_format: VideoFormat,
    output_label: Path | str,
    mode_label: str,
    segments: list[ClipSegment],
    text: str,
    font_size: int,
    music_path: Path | None,
    music_duration: float | None,
    text_y_percent: float = 50.0,
    text_horizontal_padding: int = TEXT_HORIZONTAL_PADDING,
    add_cta: bool = False,
    cta_text: str = CTA_TEXT,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    clip_duration = segment_plan_duration(segments)
    output_duration = final_output_duration(segments, music_duration)
    if output_duration < MIN_OUTPUT_SECONDS:
        raise RuntimeError("Planned output is too short to render.")

    overlay_text = text.strip()
    if overlay_text:
        fitted_text, font_size, line_spacing = prepare_text(
            overlay_text,
            video_format,
            font_size,
            text_horizontal_padding,
        )
    else:
        fitted_text = ""
        line_spacing = TEXT_LINE_SPACING
    output_path = output_path_for(video_format, output_label)
    OUTPUT_DIR.mkdir(exist_ok=True)

    def emit(message: str) -> None:
        print(message)
        if progress_callback is not None:
            progress_callback(message)

    output_name = output_label.name if isinstance(output_label, Path) else output_label
    emit(f"Format: {video_format.option} ({video_format.width}x{video_format.height})")
    emit(f"Folder: {output_name} ({mode_label})")
    if music_path is not None and music_duration is not None:
        emit(f"Music: {music_path.name} ({music_duration:.2f}s)")
    else:
        emit("Music: none")
    emit(f"Clip timeline: {clip_duration:.2f}s")
    emit(f"Output length: {output_duration:.2f}s")
    if overlay_text:
        emit(f"Text font: {FONT_LABEL}, {font_size}px")
        emit(f"Text position: {text_y_percent:.0f}%")
        emit(f"Text horizontal padding: {text_horizontal_padding}px")
    else:
        emit("Text: none")
    if add_cta:
        emit(f"CTA: last {min(CTA_DURATION_SECONDS, output_duration):.2f}s")

    with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        segment_paths: list[Path] = []

        for index, segment in enumerate(segments, start=1):
            segment_path = temp_dir / f"segment_{index:02d}.mp4"
            scale_mode = normalized_clip_scale_mode(segment.source.scale_mode)
            crop_label = (
                f", x={bounded_percent(segment.source.crop_x_percent):.0f}%"
                if scale_mode is not ClipScaleMode.FIT
                else ""
            )
            emit(
                f"Segment {index}: {segment.source.path.name} "
                f"({segment_start(segment):.2f}s-{segment_start(segment) + segment.duration:.2f}s, "
                f"{segment.duration:.2f}s, scale={scale_mode.value}{crop_label})"
            )
            make_segment(segment, video_format, segment_path)
            segment_paths.append(segment_path)

        stitched_path = temp_dir / "stitched.mp4"
        concat_list_path = temp_dir / "concat.txt"
        text_path: Path | None = None
        cta_overlay_path: Path | None = None
        if overlay_text:
            text_path = temp_dir / "overlay_text.txt"
            text_path.write_text(fitted_text, encoding="utf-8")
        if add_cta:
            cta_overlay_path = temp_dir / "cta_overlay.png"
            create_cta_overlay_image(video_format, cta_text).save(cta_overlay_path)

        concat_segments(segment_paths, stitched_path, concat_list_path)
        emit(f"Rendering: {output_path}")
        overlay_text_and_music(
            input_path=stitched_path,
            output_path=output_path,
            text_path=text_path,
            cta_overlay_path=cta_overlay_path,
            font_size=font_size,
            line_spacing=line_spacing,
            output_duration=output_duration,
            music_path=music_path,
            video_format=video_format,
            text_y_percent=text_y_percent,
        )

    return output_path


def render_video(
    video_format: VideoFormat,
    folder: Path,
    mode: FolderMode,
    videos: list[SourceVideo],
    selected_single_video: SourceVideo | None,
    text: str,
    font_size: int,
    music_path: Path,
    music_duration: float | None = None,
    random_clip_count: int | None = None,
    text_y_percent: float = 50.0,
    text_horizontal_padding: int = TEXT_HORIZONTAL_PADDING,
    add_cta: bool = False,
    cta_text: str = CTA_TEXT,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    if music_duration is None:
        music_duration = probe_duration(music_path)

    segments = plan_segments(
        mode,
        videos,
        music_duration,
        selected_single_video,
        random_clip_count,
    )
    return render_segments(
        video_format=video_format,
        output_label=folder,
        mode_label=mode.value,
        segments=segments,
        text=text,
        font_size=font_size,
        music_path=music_path,
        music_duration=music_duration,
        text_y_percent=text_y_percent,
        text_horizontal_padding=text_horizontal_padding,
        add_cta=add_cta,
        cta_text=cta_text,
        progress_callback=progress_callback,
    )


def render_selected_clips(
    video_format: VideoFormat,
    folder: Path | str,
    videos: list[SourceVideo],
    text: str,
    font_size: int,
    music_path: Path | None,
    music_duration: float | None = None,
    text_y_percent: float = 50.0,
    text_horizontal_padding: int = TEXT_HORIZONTAL_PADDING,
    add_cta: bool = False,
    cta_text: str = CTA_TEXT,
    progress_callback: ProgressCallback | None = None,
) -> Path:
    if music_path is not None and music_duration is None:
        music_duration = probe_duration(music_path)

    segments = plan_selected_segments(videos, music_duration)
    return render_segments(
        video_format=video_format,
        output_label=folder,
        mode_label="Selected",
        segments=segments,
        text=text,
        font_size=font_size,
        music_path=music_path,
        music_duration=music_duration,
        text_y_percent=text_y_percent,
        text_horizontal_padding=text_horizontal_padding,
        add_cta=add_cta,
        cta_text=cta_text,
        progress_callback=progress_callback,
    )


def main() -> None:
    video_format = ask_for_format()
    folder = ask_for_folder(video_format)
    mode = parse_folder_mode(folder)
    videos = list_source_videos(folder)
    selected_single_video = (
        ask_for_single_video(videos)
        if mode is FolderMode.SINGLE
        else None
    )
    text = ask_for_text()
    font_size = ask_for_text_size()
    music_path = ask_for_music_track(list_music_tracks())
    music_duration = probe_duration(music_path)
    random_clip_count = (
        ask_for_random_clip_count(videos, music_duration)
        if mode is FolderMode.RANDOM
        else None
    )
    output_path = render_video(
        video_format=video_format,
        folder=folder,
        mode=mode,
        videos=videos,
        selected_single_video=selected_single_video,
        text=text,
        font_size=font_size,
        music_path=music_path,
        music_duration=music_duration,
        random_clip_count=random_clip_count,
    )
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()
