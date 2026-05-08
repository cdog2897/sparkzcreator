#!/usr/bin/env python3
"""
Generate a random stitched 1080x1920 video with one text overlay.

The script is self-contained around this folder:
  videos/*.{mp4,mov,m4v}
  Crimson_Text/CrimsonText-Regular.ttf
  outputs/
"""

from __future__ import annotations

import argparse
import math
import random
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
VIDEO_DIR = ROOT / "videos"
MUSIC_DIR = ROOT / "music"
FONT_PATH = ROOT / "Crimson_Text" / "CrimsonText-Regular.ttf"
FONT_LABEL = "Crimson Text Regular"
OUTPUT_DIR = ROOT / "outputs"

WIDTH = 1080
HEIGHT = 1920
FPS = 30
DROP_INITIAL_FRAMES = 1
TEXT_FONT_SIZE = 50
TEXT_LINE_SPACING = 12
TEXT_HORIZONTAL_PADDING = 260
MIN_TOTAL_SECONDS = 8
MAX_TOTAL_SECONDS = 18
MIN_CLIP_SECONDS = 3.0
MAX_CLIP_SECONDS = 7.0
LEADING_FRAME_SKIP_SECONDS = 0.25
TRAILING_FRAME_SKIP_SECONDS = 0.25
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v"}
AUDIO_EXTENSIONS = {".mp3", ".m4a", ".wav", ".aac", ".mov", ".mp4"}
SDR_COLOR_PARAMS = "setparams=range=tv:color_primaries=bt709:color_trc=bt709:colorspace=bt709"
STRIP_AMBIENT_SIDE_DATA = "sidedata=mode=delete:type=AMBIENT_VIEWING_ENVIRONMENT"


@dataclass(frozen=True)
class SourceVideo:
    path: Path
    duration: float


def ffmpeg_exe() -> str:
    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(command: list[str]) -> None:
    subprocess.run(command, check=True)


def probe_video(path: Path) -> SourceVideo:
    result = subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-i", str(path)],
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stderr
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if not match:
        raise RuntimeError(f"Could not read duration for {path.name}.")

    hours, minutes, seconds = match.groups()
    duration = (int(hours) * 3600) + (int(minutes) * 60) + float(seconds)
    return SourceVideo(path=path, duration=duration)


def list_source_videos() -> list[SourceVideo]:
    paths = sorted(
        path
        for path in VIDEO_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
    )
    if not paths:
        raise FileNotFoundError(f"No video files found in {VIDEO_DIR}.")

    videos = [probe_video(path) for path in paths]
    usable = [video for video in videos if video.duration >= MIN_CLIP_SECONDS]
    if not usable:
        raise RuntimeError("Every source video is shorter than 3 seconds.")

    return usable


def list_music_tracks() -> list[Path]:
    tracks = sorted(
        path
        for path in MUSIC_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS
    )
    if not tracks:
        raise FileNotFoundError(f"No music tracks found in {MUSIC_DIR}.")

    return tracks


def random_segment_plan(videos: list[SourceVideo], target_seconds: int) -> list[tuple[SourceVideo, float]]:
    candidates = [
        (
            video,
            min(
                MAX_CLIP_SECONDS,
                video.duration - LEADING_FRAME_SKIP_SECONDS - TRAILING_FRAME_SKIP_SECONDS,
            ),
        )
        for video in videos
        if video.duration - LEADING_FRAME_SKIP_SECONDS - TRAILING_FRAME_SKIP_SECONDS >= MIN_CLIP_SECONDS
    ]
    if not candidates:
        raise RuntimeError("Every source video is shorter than 3 seconds.")

    target_units = target_seconds * 100
    min_units = round(MIN_CLIP_SECONDS * 100)
    candidates_with_units = [
        (video, math.floor(max_duration * 100)) for video, max_duration in candidates
    ]
    max_count = min(len(candidates_with_units), target_units // min_units)
    viable_counts = [
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

    if not viable_counts:
        raise RuntimeError(
            f"Cannot make a {target_seconds}s video with clips between "
            f"{MIN_CLIP_SECONDS:g}s and {MAX_CLIP_SECONDS:g}s using unique source clips."
        )

    count = random.choice(viable_counts)
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

    for index in range(count):
        remaining_slots = count - index - 1
        capacity = selected[index][1]
        remaining_capacity = sum(
            selected[future_index][1] - min_units
            for future_index in range(index + 1, count)
        )
        max_add = min(capacity - min_units, leftover)
        min_add = max(0, leftover - remaining_capacity)
        add = leftover if remaining_slots == 0 else random.randint(min_add, max_add)
        durations[index] += add
        leftover -= add

    return [
        (video, duration_units / 100)
        for (video, _), duration_units in zip(selected, durations)
    ]


def wrap_words(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
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


def prepare_text(text: str) -> tuple[str, int, int]:
    if not FONT_PATH.exists():
        raise FileNotFoundError(f"Missing Crimson Text font: {FONT_PATH}")

    image = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(image)
    max_width = WIDTH - (TEXT_HORIZONTAL_PADDING * 2)
    font = ImageFont.truetype(str(FONT_PATH), TEXT_FONT_SIZE)
    lines = wrap_words(draw, text, font, max_width)
    return "\n".join(lines), TEXT_FONT_SIZE, TEXT_LINE_SPACING


def make_segment(
    source: SourceVideo,
    duration: float,
    output_path: Path,
    use_random_start: bool,
) -> None:
    max_start = max(0.0, source.duration - TRAILING_FRAME_SKIP_SECONDS - duration)
    earliest_start = min(LEADING_FRAME_SKIP_SECONDS, max_start)
    start = (
        random.uniform(earliest_start, max_start)
        if use_random_start and max_start > earliest_start
        else earliest_start
    )
    video_filter = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},setsar=1,fps={FPS},{SDR_COLOR_PARAMS},"
        f"{STRIP_AMBIENT_SIDE_DATA},format=yuv420p"
    )

    command = [ffmpeg_exe(), "-y", "-hide_banner", "-loglevel", "error"]
    command.extend(["-i", str(source.path)])
    if start > 0:
        command.extend(["-ss", f"{start:.3f}"])
    command.extend(
        [
            "-t",
            f"{duration:.3f}",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-map",
            "0:v:0",
        ]
    )

    command.append("-an")

    command.extend(
        [
            "-vf",
            video_filter,
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


def overlay_text(
    input_path: Path,
    output_path: Path,
    text_path: Path,
    font_size: int,
    line_spacing: int,
    output_duration: float,
    music_path: Path,
) -> None:
    drawtext = (
        f"drawtext=fontfile={FONT_PATH.as_posix()}:"
        f"textfile={text_path.as_posix()}:"
        f"fontsize={font_size}:"
        f"line_spacing={line_spacing}:"
        "text_align=C:"
        "fontcolor=white:"
        "x=(w-text_w)/2:"
        "y=(h-text_h)/2"
    )
    video_filter = (
        f"trim=start_frame={DROP_INITIAL_FRAMES},setpts=PTS-STARTPTS,"
        f"fps={FPS},{SDR_COLOR_PARAMS},{STRIP_AMBIENT_SIDE_DATA},format=yuv420p,{drawtext}"
    )
    run_ffmpeg(
        [
            ffmpeg_exe(),
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(input_path),
            "-stream_loop",
            "-1",
            "-i",
            str(music_path),
            "-t",
            f"{output_duration:.3f}",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-vf",
            video_filter,
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
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-ar",
            "44100",
            "-ac",
            "2",
            "-metadata:s:v:0",
            "handler_name=VideoHandler",
            "-metadata:s:a:0",
            "handler_name=SoundHandler",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )


def ask_for_text() -> str:
    while True:
        text = input("Text to overlay: ").strip()
        if text:
            return text
        print("Please enter some text.")


def build_video(text: str, seed: int | None = None, random_starts: bool = False) -> Path:
    if seed is not None:
        random.seed(seed)

    OUTPUT_DIR.mkdir(exist_ok=True)
    videos = list_source_videos()
    music_tracks = list_music_tracks()
    target_seconds = random.randint(MIN_TOTAL_SECONDS, MAX_TOTAL_SECONDS)
    segment_plan = random_segment_plan(videos, target_seconds)
    selected_music = random.choice(music_tracks)
    fitted_text, font_size, line_spacing = prepare_text(text)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = OUTPUT_DIR / f"stitched_{timestamp}.mp4"

    print(f"Target length: {target_seconds}s")
    print(f"Segments: {', '.join(f'{duration:.2f}s' for _, duration in segment_plan)}")
    print(f"Music: {selected_music.name}")
    print(f"Text font: {FONT_LABEL}, {font_size}px")

    with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        segment_paths: list[Path] = []

        for index, (source, duration) in enumerate(segment_plan, start=1):
            segment_path = temp_dir / f"segment_{index:02d}.mp4"
            print(f"Segment {index}: {source.path.name} ({duration:.2f}s)")
            make_segment(source, duration, segment_path, random_starts)
            segment_paths.append(segment_path)

        stitched_path = temp_dir / "stitched.mp4"
        concat_list_path = temp_dir / "concat.txt"
        text_path = temp_dir / "overlay_text.txt"
        text_path.write_text(fitted_text, encoding="utf-8")

        concat_segments(segment_paths, stitched_path, concat_list_path)
        print(f"Rendering: {output_path}")
        output_duration = max(0.1, sum(duration for _, duration in segment_plan) - (DROP_INITIAL_FRAMES / FPS))
        overlay_text(
            stitched_path,
            output_path,
            text_path,
            font_size,
            line_spacing,
            output_duration,
            selected_music,
        )

    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a stitched 1080x1920 text-overlay video.")
    parser.add_argument("--text", help="Text to overlay. If omitted, the script asks for it.")
    parser.add_argument("--seed", type=int, default=None, help="Optional random seed for repeatable output.")
    parser.add_argument(
        "--random-starts",
        action="store_true",
        help="Cut random sections from source clips instead of starting each selected clip at 0:00.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    text = args.text.strip() if args.text else ask_for_text()
    if not text:
        raise ValueError("Text cannot be empty.")

    output_path = build_video(text=text, seed=args.seed, random_starts=args.random_starts)
    print(f"Done: {output_path}")


if __name__ == "__main__":
    main()
