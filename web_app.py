#!/usr/bin/env python3
"""FastAPI app for the short-form video creator."""

from __future__ import annotations

import json
import random
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError

from text_slideshow import (
    MAX_SLIDES as TEXT_SLIDE_MAX_SLIDES,
    export_text_slideshow,
    open_slide_image,
    slide_png_bytes,
    style_payload as text_slideshow_style_payload,
)
from text_slideshow_video import (
    DEFAULT_SLIDE_SECONDS,
    VIDEO_FILENAME,
    render_text_slideshow_video,
)
from slideshow import (
    DEFAULT_IMAGE_SECONDS,
    DEMO_EXTENSIONS,
    FONT_PATH as SLIDESHOW_FONT_PATH,
    MAX_DEMO_BYTES,
    MAX_IMAGE_SECONDS,
    MAX_IMAGES,
    MAX_TEXT_CHARS,
    MIN_IMAGE_SECONDS,
    REFERENCE_FONT_SIZE,
    REFERENCE_FRAME_WIDTH,
    REFERENCE_LINE_SPACING,
    REFERENCE_LINE_TOP_DELTA,
    SLIDESHOW_FPS,
    SLIDESHOW_HEIGHT,
    SLIDESHOW_WIDTH,
    TEXT_COLOR_HEX,
    inspect_demo_video,
    render_slideshow,
    slideshow_plan,
    validate_image_payloads,
    validate_image_seconds,
)
from generate_video import (
    COVER_IMAGE_FORMAT,
    CTA_TEXT,
    FORMATS,
    FORMATS_BY_OPTION,
    APPSTORE_PATH,
    AUDIO_EXTENSIONS,
    MUSIC_DIR,
    OUTPUT_DIR,
    ROOT,
    TEXT_FONT_SIZE,
    TEXT_HORIZONTAL_PADDING,
    TEXT_SIZE_OPTIONS,
    VIDEO_DIR,
    SourceVideo,
    create_cover_image,
    ffmpeg_exe,
    list_music_tracks,
    list_video_paths,
    probe_duration,
    probe_video,
    render_selected_clips,
    safe_output_part,
)


app = FastAPI(title="Short Form Video Creator")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Output-Folder", "X-Video-Duration", "Content-Disposition"],
)


class ClipSelection(BaseModel):
    folder_path: Optional[str] = None
    folder_name: Optional[str] = None
    clip_name: str = Field(min_length=1)
    scale_mode: str = "fit"
    crop_x_percent: float = Field(default=50.0, ge=0, le=100)
    trim_start_seconds: float = Field(default=0.0, ge=0)
    trim_end_seconds: Optional[float] = Field(default=None, ge=0)

    def media_folder_path(self) -> str:
        return self.folder_path or self.folder_name or ""


class CoverRenderRequest(BaseModel):
    clip: ClipSelection
    time_seconds: float = Field(default=0.0, ge=0)


class RenderRequest(BaseModel):
    format_option: int = Field(ge=1)
    text: str = ""
    font_size: int = TEXT_FONT_SIZE
    text_y_percent: float = Field(default=50.0, ge=0, le=100)
    text_horizontal_padding: int = Field(default=TEXT_HORIZONTAL_PADDING, ge=0)
    add_cta: bool = False
    cta_text: str = Field(default=CTA_TEXT, max_length=500)
    music_name: Optional[str] = None
    clips: Optional[list[ClipSelection]] = None
    folder_name: Optional[str] = None
    clip_names: Optional[list[str]] = None


class MusicImportRequest(BaseModel):
    url: str = Field(min_length=1)
    use_browser_cookies: bool = False
    browser: Literal["chrome"] = "chrome"


class MusicDeleteRequest(BaseModel):
    music_name: str = Field(min_length=1)


class TextSlideRequest(BaseModel):
    background_asset_id: Optional[str] = None
    background_color: str = "#d5e4f5"
    background_color_2: Optional[str] = "#f7e7ef"
    elements: list[dict] = Field(default_factory=list)


class TextSlideshowExportRequest(BaseModel):
    slides: list[TextSlideRequest]


@dataclass
class RenderJob:
    id: str
    status: str = "queued"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    logs: list[str] = field(default_factory=list)
    output_name: str | None = None
    output_url: str | None = None
    error: str | None = None


jobs: dict[str, RenderJob] = {}
jobs_lock = threading.Lock()

TEXT_SLIDE_ASSET_DIR = ROOT / "tmp_textslide_assets"
DEMO_DIR = ROOT / "uploads" / "demo"
_ASSET_ID = re.compile(r"^[0-9a-f]{32}$")


def validate_child_path(root: Path, child: Path) -> Path:
    root_path = root.resolve()
    child_path = child.resolve()
    if child_path != root_path and root_path not in child_path.parents:
        raise HTTPException(status_code=400, detail="Invalid path.")
    return child_path


def format_for_option(option: int):
    video_format = FORMATS_BY_OPTION.get(option)
    if video_format is None:
        raise HTTPException(status_code=404, detail="Unknown format.")
    return video_format


def media_folder_path(folder: Path) -> str:
    return folder.relative_to(VIDEO_DIR).as_posix()


def media_directory_for_path(folder_path: str = "") -> Path:
    folder = validate_child_path(VIDEO_DIR, VIDEO_DIR / folder_path)
    if not folder.is_dir():
        raise HTTPException(status_code=404, detail="Unknown media folder.")
    return folder


def folder_for_media_path(folder_path: str) -> Path:
    if not folder_path.strip():
        raise HTTPException(status_code=400, detail="Missing media folder.")

    return media_directory_for_path(folder_path)


def list_media_pool_folders() -> list[Path]:
    if not VIDEO_DIR.is_dir():
        raise FileNotFoundError(f"Missing video source folder: {VIDEO_DIR}")

    folders = [
        path
        for path in VIDEO_DIR.rglob("*")
        if path.is_dir() and list_video_paths(path)
    ]
    return sorted(folders, key=lambda folder: media_folder_path(folder).lower())


def list_child_media_folders(folder: Path) -> list[Path]:
    return sorted(
        [path for path in folder.iterdir() if path.is_dir()],
        key=lambda path: path.name.lower(),
    )


def breadcrumb_entries(folder_path: str) -> list[dict[str, str]]:
    breadcrumbs = [{"name": "Media Pool", "path": ""}]
    current_parts: list[str] = []
    for part in Path(folder_path).parts:
        current_parts.append(part)
        breadcrumbs.append({
            "name": part,
            "path": Path(*current_parts).as_posix(),
        })
    return breadcrumbs


def folder_from_query(
    folder_path: Optional[str] = None,
    folder_name: Optional[str] = None,
) -> Path:
    if folder_path:
        return folder_for_media_path(folder_path)

    if folder_name:
        matches = [
            folder
            for folder in list_media_pool_folders()
            if folder.name == folder_name or media_folder_path(folder) == folder_name
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise HTTPException(
                status_code=400,
                detail="Folder name is ambiguous; use folder_path.",
            )

    raise HTTPException(status_code=400, detail="Missing media folder.")


def music_for_request(music_name: str) -> Path:
    music_path = validate_child_path(MUSIC_DIR, MUSIC_DIR / music_name)
    if not music_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown music track.")
    return music_path


def output_for_request(output_name: str) -> Path:
    output_path = validate_child_path(OUTPUT_DIR, OUTPUT_DIR / output_name)
    if not output_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown output.")
    return output_path


def media_query(**params: object) -> str:
    return f"/api/media/clip?{urlencode(params)}"


def serialize_format(video_format) -> dict[str, object]:
    return {
        "option": video_format.option,
        "title": video_format.title,
        "width": video_format.width,
        "height": video_format.height,
        "aspectRatio": video_format.title,
    }


def serialize_folder(folder: Path) -> dict[str, object]:
    path = media_folder_path(folder)

    return {
        "key": path,
        "path": path,
        "name": folder.name,
        "label": path.replace("/", " / "),
        "clipCount": len(list_video_paths(folder)),
        "url": f"/api/clips?{urlencode({'folder_path': path})}",
    }


def serialize_directory(folder: Path) -> dict[str, object]:
    path = "" if folder == VIDEO_DIR else media_folder_path(folder)
    clip_count = len(list_video_paths(folder))
    child_count = len(list_child_media_folders(folder))

    return {
        "key": path or "root",
        "path": path,
        "name": "Media Pool" if not path else folder.name,
        "label": "Media Pool" if not path else path.replace("/", " / "),
        "clipCount": clip_count,
        "folderCount": child_count,
        "url": f"/api/media-pool?{urlencode({'path': path})}",
    }


def serialize_clip(folder: Path, video: SourceVideo) -> dict[str, object]:
    folder_path = media_folder_path(folder)
    aspect_ratio = (
        video.width / video.height
        if video.width and video.height
        else None
    )
    return {
        "key": f"{folder_path}/{video.path.name}",
        "folderPath": folder_path,
        "folderName": folder.name,
        "folderLabel": folder_path.replace("/", " / "),
        "name": video.path.name,
        "duration": video.duration,
        "width": video.width,
        "height": video.height,
        "aspectRatio": aspect_ratio,
        "url": media_query(
            folder_path=folder_path,
            clip_name=video.path.name,
        ),
    }


def serialize_clip_path(folder: Path, video_path: Path) -> dict[str, object]:
    return serialize_clip(
        folder,
        SourceVideo(
            path=video_path,
            duration=0.0,
            width=None,
            height=None,
        ),
    )


def serialize_music(track: Path) -> dict[str, object]:
    return {
        "name": track.name,
        "duration": probe_duration(track),
        "url": f"/api/media/music?{urlencode({'music_name': track.name})}",
    }


def serialize_music_tracks() -> list[dict[str, object]]:
    try:
        tracks = list_music_tracks()
    except FileNotFoundError:
        return []

    return [serialize_music(track) for track in tracks]


def music_library_payload(track: Path | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "music": serialize_music_tracks(),
    }
    if track is not None:
        payload["track"] = serialize_music(track)
    return payload


def safe_music_filename(filename: str) -> str:
    source_name = Path(filename).name
    suffix = Path(source_name).suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        allowed = ", ".join(sorted(AUDIO_EXTENSIONS))
        raise ValueError(f"Audio file must use one of these extensions: {allowed}.")

    stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(source_name).stem).strip("._-")
    if not stem:
        stem = "track"
    return f"{stem}{suffix}"


def unique_music_path(filename: str) -> Path:
    MUSIC_DIR.mkdir(exist_ok=True)
    safe_name = safe_music_filename(filename)
    candidate = MUSIC_DIR / safe_name
    if not candidate.exists():
        return candidate

    stem = candidate.stem
    suffix = candidate.suffix
    for index in range(1, 1000):
        next_candidate = MUSIC_DIR / f"{stem}-{index}{suffix}"
        if not next_candidate.exists():
            return next_candidate

    return MUSIC_DIR / f"{stem}-{uuid4().hex[:8]}{suffix}"


def save_uploaded_music(filename: str, content: bytes) -> Path:
    if not content:
        raise ValueError("Choose an audio file to upload.")
    if len(content) > 100 * 1024 * 1024:
        raise ValueError("Audio file is too large. Keep uploads under 100 MB.")

    output_path = unique_music_path(filename)
    output_path.write_bytes(content)
    try:
        probe_duration(output_path)
    except Exception as error:
        output_path.unlink(missing_ok=True)
        raise ValueError("Uploaded file is not a readable audio track.") from error

    return output_path


def is_instagram_url(url: str) -> bool:
    parsed_url = urlparse(url.strip())
    hostname = (parsed_url.hostname or "").lower()
    return parsed_url.scheme in {"http", "https"} and (
        hostname == "instagram.com" or hostname.endswith(".instagram.com")
    )


def instagram_download_options(
    video_template: Path,
    browser: str | None = None,
) -> dict[str, object]:
    options: dict[str, object] = {
        "format": "best[ext=mp4]/best",
        "noplaylist": True,
        "outtmpl": str(video_template),
        "quiet": True,
        "no_warnings": True,
    }
    if browser:
        # yt-dlp reads the browser cookie database directly; it does not create
        # or retain a plaintext cookie export.
        options["cookiesfrombrowser"] = (browser, None, None, None)
    return options


def instagram_import_error(error: Exception, browser: str | None = None) -> str:
    details = str(error)
    details_lower = details.lower()

    if "empty media response" in details_lower:
        if browser:
            return (
                "Instagram did not return media using the Chrome login. Open this post "
                "in Chrome, make sure you are signed in and can play it, then try again."
            )
        return (
            "Instagram blocked anonymous access to this post. Turn on “Use Chrome login” "
            "below the URL and try again."
        )

    if browser and (
        "failed to load cookies" in details_lower
        or "could not copy chrome cookie database" in details_lower
        or "keyring" in details_lower
    ):
        return (
            "Could not read the Chrome login. Close Chrome, reopen Sparkz Creator, "
            "allow any macOS Keychain prompt, and try again."
        )

    return f"Could not import audio: {details}"


def import_instagram_music(url: str, browser: str | None = None) -> Path:
    if not is_instagram_url(url):
        raise ValueError("Enter a valid Instagram video URL.")

    try:
        import yt_dlp
    except ModuleNotFoundError as error:
        raise RuntimeError("Missing dependency: yt-dlp.") from error

    MUSIC_DIR.mkdir(exist_ok=True)
    file_stem = f"reel-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}-{uuid4().hex[:8]}"
    audio_path = MUSIC_DIR / f"{file_stem}.mp3"

    with tempfile.TemporaryDirectory(dir=ROOT) as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        video_template = temp_dir / f"{file_stem}.%(ext)s"
        ydl_opts = instagram_download_options(video_template, browser)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                video_path = Path(ydl.prepare_filename(info))

            subprocess.run(
                [
                    ffmpeg_exe(),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(video_path),
                    "-vn",
                    "-q:a",
                    "0",
                    "-map",
                    "a",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
        except Exception as error:
            if audio_path.exists():
                audio_path.unlink()
            raise RuntimeError(instagram_import_error(error, browser)) from error

    if not audio_path.is_file():
        raise RuntimeError("Could not create MP3 from the Instagram video.")

    return audio_path


def serialize_output(output_path: Path) -> dict[str, object]:
    return {
        "name": output_path.name,
        "url": f"/api/media/output?{urlencode({'output_name': output_path.name})}",
        "modifiedAt": datetime.fromtimestamp(output_path.stat().st_mtime).isoformat(),
    }


def list_outputs() -> list[dict[str, object]]:
    OUTPUT_DIR.mkdir(exist_ok=True)
    outputs = [
        path
        for path in OUTPUT_DIR.iterdir()
        if path.is_file() and path.suffix.lower() == ".mp4"
    ]
    return [
        serialize_output(path)
        for path in sorted(outputs, key=lambda item: item.stat().st_mtime, reverse=True)
    ]


def job_payload(job: RenderJob) -> dict[str, object]:
    return {
        "id": job.id,
        "status": job.status,
        "createdAt": job.created_at,
        "logs": job.logs,
        "outputName": job.output_name,
        "outputUrl": job.output_url,
        "error": job.error,
    }


def append_job_log(job_id: str, message: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.logs.append(message)


def set_job_status(job_id: str, status: str, **updates: str | None) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.status = status
        for key, value in updates.items():
            setattr(job, key, value)


def selected_videos_for(folder: Path, clip_names: list[str]) -> list[SourceVideo]:
    available = {path.name: path for path in list_video_paths(folder)}
    selected: list[SourceVideo] = []
    missing: list[str] = []

    for clip_name in clip_names:
        clip_path = available.get(clip_name)
        if clip_path is None:
            missing.append(clip_name)
            continue
        selected.append(SourceVideo(path=clip_path, duration=probe_duration(clip_path)))

    if missing:
        raise ValueError(f"Unknown clips: {', '.join(missing)}")
    if not selected:
        raise ValueError("Select at least one clip.")

    return selected


def selected_videos_from_references(
    clip_references: list[ClipSelection],
) -> list[SourceVideo]:
    selected: list[SourceVideo] = []
    missing: list[str] = []

    for reference in clip_references:
        folder_reference = reference.media_folder_path()
        folder = folder_for_media_path(folder_reference)
        available = {path.name: path for path in list_video_paths(folder)}
        clip_path = available.get(reference.clip_name)
        if clip_path is None:
            missing.append(f"{folder_reference}/{reference.clip_name}")
            continue
        video = probe_video(clip_path)
        trim_start = reference.trim_start_seconds
        trim_end = (
            video.duration
            if reference.trim_end_seconds is None
            else reference.trim_end_seconds
        )
        if trim_start >= video.duration:
            raise ValueError(
                f"Trim start is past the end of {reference.clip_name}."
            )
        if trim_end > video.duration + 0.001:
            raise ValueError(
                f"Trim end is past the end of {reference.clip_name}."
            )
        if trim_end - trim_start < 0.1:
            raise ValueError(
                f"Selected range for {reference.clip_name} must be at least 0.10 seconds."
            )
        selected.append(
            SourceVideo(
                path=video.path,
                duration=video.duration,
                width=video.width,
                height=video.height,
                scale_mode=reference.scale_mode,
                crop_x_percent=reference.crop_x_percent,
                trim_start_seconds=trim_start,
                trim_end_seconds=trim_end,
            )
        )

    if missing:
        raise ValueError(f"Unknown clips: {', '.join(missing)}")
    if not selected:
        raise ValueError("Select at least one clip.")

    return selected


def selected_videos_for_request(request: RenderRequest) -> tuple[Path, list[SourceVideo]]:
    if request.clips:
        return Path("MediaPool"), selected_videos_from_references(request.clips)

    if request.folder_name and request.clip_names:
        folder = folder_from_query(folder_name=request.folder_name)
        return folder, selected_videos_for(folder, request.clip_names)

    raise ValueError("Select at least one clip.")


def render_cover_for_request(request: CoverRenderRequest) -> Path:
    cover_video = selected_videos_from_references([request.clip])[0]
    if request.time_seconds > cover_video.duration:
        raise ValueError("The selected cover frame is past the end of its clip.")

    OUTPUT_DIR.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    output_path = OUTPUT_DIR / (
        f"cover_{COVER_IMAGE_FORMAT.width}x{COVER_IMAGE_FORMAT.height}_"
        f"{safe_output_part(cover_video.path.stem)}_{timestamp}.jpg"
    )
    return create_cover_image(
        source=cover_video,
        time_seconds=request.time_seconds,
        output_path=output_path,
    )


def run_render_job(job_id: str, request: RenderRequest) -> None:
    set_job_status(job_id, "running")
    append_job_log(job_id, "Preparing render inputs.")

    try:
        video_format = format_for_option(request.format_option)
        output_label, videos = selected_videos_for_request(request)

        music_path = music_for_request(request.music_name) if request.music_name else None

        if request.font_size < 24 or request.font_size > 140:
            raise ValueError("Text size must be between 24 and 140.")
        if request.text_horizontal_padding * 2 >= video_format.width:
            raise ValueError("Text horizontal padding is too large for this format.")

        music_duration = probe_duration(music_path) if music_path is not None else None
        output_path = render_selected_clips(
            video_format=video_format,
            folder=output_label,
            videos=videos,
            text=request.text.strip(),
            font_size=request.font_size,
            music_path=music_path,
            music_duration=music_duration,
            text_y_percent=request.text_y_percent,
            text_horizontal_padding=request.text_horizontal_padding,
            add_cta=request.add_cta,
            cta_text=request.cta_text.strip(),
            progress_callback=lambda message: append_job_log(job_id, message),
        )
        output_url = f"/api/media/output?{urlencode({'output_name': output_path.name})}"
        append_job_log(job_id, f"Done: {output_path}")
        set_job_status(
            job_id,
            "complete",
            output_name=output_path.name,
            output_url=output_url,
            error=None,
        )
    except Exception as error:  # noqa: BLE001 - surface render failures to the UI.
        append_job_log(job_id, f"Error: {error}")
        set_job_status(job_id, "error", error=str(error))


@app.get("/api/bootstrap")
def bootstrap() -> dict[str, object]:
    return {
        "formats": [serialize_format(video_format) for video_format in FORMATS],
        "folders": [serialize_folder(folder) for folder in list_media_pool_folders()],
        "textSizes": [
            {
                "option": option.option,
                "label": option.label,
                "fontSize": option.font_size,
                "default": option.font_size == TEXT_FONT_SIZE,
            }
            for option in TEXT_SIZE_OPTIONS
        ],
        "music": serialize_music_tracks(),
    }


@app.get("/api/folders")
def folders(format_option: Optional[int] = Query(None, ge=1)) -> dict[str, object]:
    return {
        "folders": [
            serialize_folder(folder)
            for folder in list_media_pool_folders()
        ],
    }


@app.get("/api/media-pool")
def media_pool(path: str = Query("", alias="path")) -> dict[str, object]:
    folder = media_directory_for_path(path)
    folder_path = "" if folder == VIDEO_DIR else media_folder_path(folder)
    video_paths = list_video_paths(folder)

    return {
        "current": serialize_directory(folder),
        "parentPath": (
            media_folder_path(folder.parent)
            if folder != VIDEO_DIR and folder.parent != VIDEO_DIR
            else ""
        ),
        "breadcrumbs": breadcrumb_entries(folder_path),
        "folders": [
            serialize_directory(child_folder)
            for child_folder in list_child_media_folders(folder)
        ],
        "clips": [
            serialize_clip_path(folder, video_path)
            for video_path in video_paths
        ],
    }


@app.get("/api/clips")
def clips(
    folder_path: Optional[str] = Query(None, min_length=1),
    folder_name: Optional[str] = Query(None, min_length=1),
) -> dict[str, object]:
    folder = folder_from_query(folder_path=folder_path, folder_name=folder_name)
    video_paths = list_video_paths(folder)
    return {
        "folder": serialize_folder(folder),
        "clips": [
            serialize_clip_path(folder, video_path)
            for video_path in video_paths
        ],
    }


@app.get("/api/music")
def music() -> dict[str, object]:
    return music_library_payload()


@app.post("/api/music/import")
def import_music(request: MusicImportRequest) -> dict[str, object]:
    try:
        browser = request.browser if request.use_browser_cookies else None
        track = import_instagram_music(request.url, browser=browser)
    except Exception as error:  # noqa: BLE001 - surface import errors to the UI.
        raise HTTPException(status_code=400, detail=str(error)) from error

    return music_library_payload(track)


@app.post("/api/music/upload")
async def upload_music(
    request: Request,
    filename: str = Query(..., min_length=1),
) -> dict[str, object]:
    try:
        track = save_uploaded_music(filename, await request.body())
    except Exception as error:  # noqa: BLE001 - surface upload errors to the UI.
        raise HTTPException(status_code=400, detail=str(error)) from error

    return music_library_payload(track)


@app.post("/api/music/delete")
def delete_music(request: MusicDeleteRequest) -> dict[str, object]:
    music_path = music_for_request(request.music_name)
    try:
        music_path.unlink()
    except OSError as error:
        raise HTTPException(status_code=400, detail=f"Could not delete track: {error}") from error

    return music_library_payload()


@app.get("/api/outputs")
def outputs() -> dict[str, object]:
    return {"outputs": list_outputs()}


def slideshow_music_path(music_name: str) -> Path | None:
    cleaned = music_name.strip()
    if cleaned.lower() in {"", "random"}:
        try:
            tracks = list_music_tracks()
        except FileNotFoundError:
            return None
        return random.choice(tracks)
    if cleaned.lower() == "none":
        return None
    return music_for_request(cleaned)


def saved_demo_path() -> Path | None:
    if not DEMO_DIR.is_dir():
        return None
    matches = [
        path
        for path in DEMO_DIR.iterdir()
        if path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in DEMO_EXTENSIONS
    ]
    if not matches:
        return None
    return max(matches, key=lambda path: path.stat().st_mtime)


def demo_public_payload() -> dict[str, object] | None:
    path = saved_demo_path()
    if path is None:
        return None
    meta_path = DEMO_DIR / "meta.json"
    meta: dict[str, object] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict):
            meta = loaded
    duration = meta.get("durationSeconds")
    has_audio = meta.get("hasAudio")
    if not isinstance(duration, (int, float)) or not isinstance(has_audio, bool):
        info = inspect_demo_video(path)
        duration = info.duration
        has_audio = info.has_audio
    return {
        "name": str(meta.get("name") or path.name),
        "url": f"/api/media/slideshow-demo?v={int(path.stat().st_mtime)}",
        "durationSeconds": float(duration),
        "hasAudio": bool(has_audio),
    }


def save_demo_upload(filename: str, content: bytes) -> dict[str, object]:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in DEMO_EXTENSIONS:
        raise ValueError("End demo video must be an .mp4 or .mov file.")
    if not content:
        raise ValueError("Choose a demo video to upload.")
    if len(content) > MAX_DEMO_BYTES:
        raise ValueError("Demo video must be under 200 MB.")

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    temporary = DEMO_DIR / f".incoming-{uuid4().hex}{suffix}"
    temporary.write_bytes(content)
    try:
        info = inspect_demo_video(temporary)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    destination = DEMO_DIR / f"end-demo{suffix}"
    for old in list(DEMO_DIR.iterdir()):
        if old == temporary or not old.is_file():
            continue
        if old.suffix.lower() in DEMO_EXTENSIONS or old.name == "meta.json":
            old.unlink()
    temporary.replace(destination)
    meta = {
        "name": Path(filename).name or destination.name,
        "durationSeconds": info.duration,
        "hasAudio": info.has_audio,
    }
    (DEMO_DIR / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    payload = demo_public_payload()
    if payload is None:
        raise ValueError("Could not store the demo video.")
    return payload


def clear_saved_demo() -> None:
    if not DEMO_DIR.is_dir():
        return
    for path in DEMO_DIR.iterdir():
        if path.is_file():
            path.unlink()


def run_slideshow_job(
    job_id: str,
    image_payloads: list[bytes],
    text: str,
    image_seconds: float,
    music_path: Path | None,
    demo_path: Path | None = None,
    keep_demo_audio: bool = False,
) -> None:
    set_job_status(job_id, "running")
    temp_dir = Path(tempfile.mkdtemp(dir=ROOT))
    try:
        append_job_log(job_id, "Preparing photos.")
        image_paths: list[Path] = []
        for index, content in enumerate(image_payloads, start=1):
            path = temp_dir / f"upload_{index:03d}.img"
            path.write_bytes(content)
            image_paths.append(path)
        output_path = render_slideshow(
            image_paths,
            text=text,
            image_seconds=image_seconds,
            music_path=music_path,
            demo_path=demo_path,
            keep_demo_audio=keep_demo_audio,
            progress_callback=lambda message: append_job_log(job_id, message),
        )
        output_url = f"/api/media/output?{urlencode({'output_name': output_path.name})}"
        append_job_log(job_id, f"Done: {output_path.name}")
        set_job_status(
            job_id,
            "complete",
            output_name=output_path.name,
            output_url=output_url,
            error=None,
        )
    except Exception as error:  # noqa: BLE001 - surface slideshow failures to the UI.
        append_job_log(job_id, f"Error: {error}")
        set_job_status(job_id, "error", error=str(error))
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/api/slideshow")
def slideshow_defaults() -> dict[str, object]:
    return {
        "fps": SLIDESHOW_FPS,
        "defaultImageDuration": DEFAULT_IMAGE_SECONDS,
        "minImageDuration": MIN_IMAGE_SECONDS,
        "maxImageDuration": MAX_IMAGE_SECONDS,
        "maxImages": MAX_IMAGES,
        "width": SLIDESHOW_WIDTH,
        "height": SLIDESHOW_HEIGHT,
        "textColor": TEXT_COLOR_HEX,
        "referenceWidth": REFERENCE_FRAME_WIDTH,
        "fontSizeAtReference": REFERENCE_FONT_SIZE,
        "lineSpacingAtReference": REFERENCE_LINE_SPACING,
        "lineTopDeltaAtReference": REFERENCE_LINE_TOP_DELTA,
        "demo": demo_public_payload(),
    }


@app.post("/api/slideshow/demo")
async def upload_slideshow_demo(demo: UploadFile = File(...)) -> dict[str, object]:
    try:
        payload = save_demo_upload(demo.filename or "", await demo.read())
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return {"demo": payload}


@app.delete("/api/slideshow/demo")
def delete_slideshow_demo() -> dict[str, object]:
    clear_saved_demo()
    return {"demo": None}


async def _demo_for_render(use_demo: bool, upload: UploadFile | None) -> Path | None:
    if upload is not None and (upload.filename or ""):
        content = await upload.read()
        if content:
            try:
                save_demo_upload(upload.filename or "", content)
            except ValueError as error:
                raise HTTPException(status_code=400, detail=str(error)) from error
            path = saved_demo_path()
            if path is None:
                raise HTTPException(status_code=400, detail="Could not store the demo video.")
            return path
    if not use_demo:
        return None
    path = saved_demo_path()
    if path is None:
        raise HTTPException(
            status_code=400,
            detail="Upload an end demo video first, or remove it.",
        )
    try:
        inspect_demo_video(path)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return path


@app.post("/api/slideshow/render")
async def start_slideshow_render(
    images: list[UploadFile] = File(...),
    text: str = Form(""),
    image_duration: float = Form(DEFAULT_IMAGE_SECONDS),
    music_name: str = Form("random"),
    use_demo: bool = Form(False),
    keep_demo_audio: bool = Form(False),
    demo: UploadFile | None = File(None),
) -> dict[str, object]:
    if not images:
        raise HTTPException(status_code=400, detail="Add at least one image.")
    if len(images) > MAX_IMAGES:
        raise HTTPException(status_code=400, detail=f"Use {MAX_IMAGES} images or fewer.")
    if len(text) > MAX_TEXT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Text must be {MAX_TEXT_CHARS} characters or fewer.",
        )
    try:
        checked_duration = validate_image_seconds(image_duration)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    music_path = slideshow_music_path(music_name)
    payloads = [await upload.read() for upload in images]
    try:
        validate_image_payloads(payloads)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    demo_path = await _demo_for_render(use_demo, demo)
    plan = slideshow_plan(len(payloads), checked_duration)
    if demo_path is not None:
        demo_info = inspect_demo_video(demo_path)
        plan = {
            **plan,
            "demoSeconds": demo_info.play_seconds,
            "durationSeconds": float(plan["durationSeconds"]) + demo_info.play_seconds,
        }

    job_id = uuid4().hex
    with jobs_lock:
        jobs[job_id] = RenderJob(id=job_id)

    thread = threading.Thread(
        target=run_slideshow_job,
        args=(
            job_id,
            payloads,
            text,
            checked_duration,
            music_path,
            demo_path,
            keep_demo_audio and demo_path is not None,
        ),
        daemon=True,
    )
    thread.start()
    return {
        "job": job_payload(jobs[job_id]),
        "plan": plan,
    }


def text_slide_asset_ids(slides: list[dict]) -> list[str]:
    found: list[str] = []
    for slide in slides:
        background_id = slide.get("background_asset_id")
        if background_id:
            found.append(str(background_id))
        for element in slide.get("elements") or []:
            if element.get("type") == "image" and element.get("asset_id"):
                found.append(str(element["asset_id"]))
    return found


def load_text_slide_assets(slides: list[dict]) -> dict[str, Path]:
    assets: dict[str, Path] = {}
    for asset_id in text_slide_asset_ids(slides):
        if asset_id in assets:
            continue
        if not _ASSET_ID.match(asset_id):
            raise ValueError("Unknown image.")
        path = TEXT_SLIDE_ASSET_DIR / f"{asset_id}.png"
        if not path.is_file():
            raise ValueError("Unknown image.")
        assets[asset_id] = path
    return assets


@app.get("/api/text-slideshow")
def text_slideshow_defaults() -> dict[str, object]:
    return text_slideshow_style_payload()


@app.post("/api/text-slideshow/assets")
async def upload_text_slide_asset(file: UploadFile = File(...)) -> dict[str, object]:
    content = await file.read()
    try:
        image = open_slide_image(content)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    asset_id = uuid4().hex
    TEXT_SLIDE_ASSET_DIR.mkdir(exist_ok=True)
    path = TEXT_SLIDE_ASSET_DIR / f"{asset_id}.png"
    image.save(path, format="PNG")
    return {
        "id": asset_id,
        "width": image.width,
        "height": image.height,
        "url": f"/api/text-slideshow/assets/{asset_id}",
    }


@app.get("/api/text-slideshow/assets/{asset_id}")
def text_slide_asset(asset_id: str) -> FileResponse:
    if not _ASSET_ID.match(asset_id):
        raise HTTPException(status_code=404, detail="Unknown image.")
    path = TEXT_SLIDE_ASSET_DIR / f"{asset_id}.png"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Unknown image.")
    return FileResponse(path, media_type="image/png")


@app.post("/api/text-slideshow/preview")
def preview_text_slide(slide: TextSlideRequest) -> Response:
    payload = slide.model_dump()
    try:
        png = slide_png_bytes(payload, load_text_slide_assets([payload]))
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return Response(content=png, media_type="image/png")


@app.post("/api/text-slideshow/export")
def export_text_slides(request: TextSlideshowExportRequest) -> FileResponse:
    if not request.slides:
        raise HTTPException(status_code=400, detail="Add at least one slide.")
    if len(request.slides) > TEXT_SLIDE_MAX_SLIDES:
        raise HTTPException(
            status_code=400,
            detail=f"Use {TEXT_SLIDE_MAX_SLIDES} slides or fewer.",
        )
    slides = [slide.model_dump() for slide in request.slides]
    try:
        assets = load_text_slide_assets(slides)
        folder, zip_path = export_text_slideshow(slides, assets)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=zip_path.name,
        headers={"X-Output-Folder": folder.name},
    )


def _optional_form_seconds(value: str, label: str) -> float | None:
    text = value.strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError as error:
        raise ValueError(f"{label} must be a number.") from error


@app.post("/api/text-slideshow/export-video")
async def export_text_slideshow_video(
    slides: str = Form(...),
    seconds_per_slide: float = Form(DEFAULT_SLIDE_SECONDS),
    first_slide_seconds: str = Form(""),
    crossfade_seconds: float = Form(0.0),
    music_name: str = Form(""),
    audio: UploadFile | None = File(None),
) -> FileResponse:
    """Render the deck to video.mp4 in a new text-slideshow output folder."""
    try:
        payload = json.loads(slides)
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=400, detail="Slides must be JSON.") from error
    if not isinstance(payload, list) or not payload:
        raise HTTPException(status_code=400, detail="Add at least one slide.")
    if len(payload) > TEXT_SLIDE_MAX_SLIDES:
        raise HTTPException(
            status_code=400,
            detail=f"Use {TEXT_SLIDE_MAX_SLIDES} slides or fewer.",
        )

    temp_audio: Path | None = None
    try:
        slide_models = [TextSlideRequest.model_validate(item).model_dump() for item in payload]
        assets = load_text_slide_assets(slide_models)
        first_hold = _optional_form_seconds(first_slide_seconds, "First slide duration")
        audio_path: Path | None = None
        if audio is not None and audio.filename:
            content = await audio.read()
            if not content:
                raise ValueError("The audio file is empty.")
            if len(content) > 100 * 1024 * 1024:
                raise ValueError("Audio file is too large. Keep it under 100 MB.")
            suffix = Path(audio.filename).suffix.lower()
            if suffix not in AUDIO_EXTENSIONS:
                allowed = ", ".join(sorted(AUDIO_EXTENSIONS))
                raise ValueError(f"Audio file must use one of these extensions: {allowed}.")
            handle = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            handle.write(content)
            handle.close()
            temp_audio = Path(handle.name)
            try:
                probe_duration(temp_audio)
            except RuntimeError as error:
                raise ValueError("That audio file could not be read.") from error
            audio_path = temp_audio
        elif music_name.strip():
            audio_path = music_for_request(music_name.strip())

        result = render_text_slideshow_video(
            slide_models,
            assets,
            seconds_per_slide=seconds_per_slide,
            first_slide_seconds=first_hold,
            crossfade_seconds=crossfade_seconds,
            audio_path=audio_path,
        )
    except (ValueError, RuntimeError, ValidationError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        if temp_audio is not None:
            temp_audio.unlink(missing_ok=True)

    return FileResponse(
        result.path,
        media_type="video/mp4",
        filename=VIDEO_FILENAME,
        headers={
            "X-Output-Folder": result.output_dir.name,
            "X-Video-Duration": f"{result.plan.duration:.3f}",
        },
    )


@app.post("/api/render")
def start_render(request: RenderRequest) -> dict[str, object]:
    job_id = uuid4().hex
    with jobs_lock:
        jobs[job_id] = RenderJob(id=job_id)

    thread = threading.Thread(
        target=run_render_job,
        args=(job_id, request),
        daemon=True,
    )
    thread.start()
    return {"job": job_payload(jobs[job_id])}


@app.post("/api/render-cover")
def render_cover(request: CoverRenderRequest) -> dict[str, object]:
    try:
        output_path = render_cover_for_request(request)
    except Exception as error:  # noqa: BLE001 - surface cover render failures to the UI.
        raise HTTPException(status_code=400, detail=str(error)) from error

    return {"cover": serialize_output(output_path)}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    with jobs_lock:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown render job.")
        return {"job": job_payload(job)}


@app.get("/api/media/clip")
def clip_media(
    folder_path: Optional[str] = Query(None, min_length=1),
    folder_name: Optional[str] = Query(None, min_length=1),
    clip_name: str = Query(..., min_length=1),
) -> FileResponse:
    folder = folder_from_query(folder_path=folder_path, folder_name=folder_name)
    clip_path = validate_child_path(folder, folder / clip_name)
    if not clip_path.is_file():
        raise HTTPException(status_code=404, detail="Unknown clip.")
    return FileResponse(clip_path)


@app.get("/api/media/music")
def music_media(music_name: str = Query(..., min_length=1)) -> FileResponse:
    return FileResponse(music_for_request(music_name))


@app.get("/api/media/slideshow-demo")
def slideshow_demo_media() -> FileResponse:
    path = saved_demo_path()
    if path is None:
        raise HTTPException(status_code=404, detail="No end demo video is saved.")
    media_type = "video/quicktime" if path.suffix.lower() == ".mov" else "video/mp4"
    payload = demo_public_payload()
    filename = str(payload["name"]) if payload else path.name
    return FileResponse(path, media_type=media_type, filename=filename)


@app.get("/api/media/slideshow-font")
def slideshow_font_media() -> FileResponse:
    if not SLIDESHOW_FONT_PATH.is_file():
        raise HTTPException(status_code=404, detail="Missing slideshow font.")
    return FileResponse(SLIDESHOW_FONT_PATH, media_type="font/ttf")


@app.get("/api/media/appstore")
def appstore_media() -> FileResponse:
    if not APPSTORE_PATH.is_file():
        raise HTTPException(status_code=404, detail="Missing App Store image.")
    return FileResponse(APPSTORE_PATH)


@app.get("/api/media/output")
def output_media(output_name: str = Query(..., min_length=1)) -> FileResponse:
    return FileResponse(output_for_request(output_name))


frontend_dist = ROOT / "frontend" / "dist"
frontend_assets = frontend_dist / "assets"
if frontend_assets.is_dir():
    app.mount("/assets", StaticFiles(directory=frontend_assets), name="assets")


@app.get("/")
def serve_frontend():
    index_path = frontend_dist / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"message": "Run the Vite dev server or build frontend/dist."}


@app.get("/{full_path:path}")
def serve_frontend_path(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="Not found.")

    index_path = frontend_dist / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"message": "Run the Vite dev server or build frontend/dist."}
